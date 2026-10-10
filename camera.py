# coding=utf-8
import ctypes
import os
import sys
import threading
import time

from NetSDK.NetSDK import NetClient
from NetSDK.SDK_Enum import (
    EM_LOGIN_SPAC_CAP_TYPE,
    SDK_PTZ_ControlType,
    EM_A_TALK_CODING_TYPE,
)
from NetSDK.SDK_Struct import (
    NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY,
    NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY,
    SNAP_PARAMS,
    NET_IN_TALK_SEND_DATA_FILE,
    NET_OUT_TALK_SEND_DATA_FILE,
    LOG_SET_PRINT_INFO,
    C_LLONG,
    C_DWORD,
    C_LDWORD,
    c_char_p,
)
from NetSDK.SDK_Callback import (
    fDisConnect,
    fHaveReConnect,
    fSnapRev,
    pfAudioDataCallBack,
    fTalkSendPosCallBack,
)

PTZ_ACTION_MAP = {
    "up": SDK_PTZ_ControlType.UP_CONTROL,
    "down": SDK_PTZ_ControlType.DOWN_CONTROL,
    "left": SDK_PTZ_ControlType.LEFT_CONTROL,
    "right": SDK_PTZ_ControlType.RIGHT_CONTROL,
    "leftup": SDK_PTZ_ControlType.LEFTTOP,
    "leftdown": SDK_PTZ_ControlType.LEFTDOWN,
    "rightup": SDK_PTZ_ControlType.RIGHTTOP,
    "rightdown": SDK_PTZ_ControlType.RIGHTDOWN,
    "zoomin": SDK_PTZ_ControlType.ZOOM_ADD_CONTROL,
    "zoomout": SDK_PTZ_ControlType.ZOOM_DEC_CONTROL,
    "focusin": SDK_PTZ_ControlType.FOCUS_ADD_CONTROL,
    "focusout": SDK_PTZ_ControlType.FOCUS_DEC_CONTROL,
    "aperture_add": SDK_PTZ_ControlType.APERTURE_ADD_CONTROL,
    "aperture_dec": SDK_PTZ_ControlType.APERTURE_DEC_CONTROL,
}

# 对角命令需要 param1/param2 都是速度
_DIAGONAL = {"leftup", "leftdown", "rightup", "rightdown"}

# 对讲发送间隔, 单位是"默认间隔的百分比"(0/100=SDK默认, <100 更快), 不是毫秒!
# 对讲是实时通道 + 设备端小缓冲, 两头都不行:
#   太慢 -> 饿死断句(间隔 60 = 0.89 倍实时, 实测丢字)
#   太快 -> 先到的被覆盖(间隔 20 = 2.7 倍实时, 实测只能听到句子后半段)
# 40 = 1.34 倍实时: 发送节奏均匀, 听得完整。
# ⚠ 但这个旋钮只能治"喂不饱/喂太猛": 同一配置连播 5 遍的实测结果是
#   1 遍略卡 / 3 遍很卡 / 1 遍很顺, 且与摄像头转动、画面变化强相关 —— 那是设备
#   在视频负载下自己播不好, 我们发出去的数据流在"转/不转"时完全一致
#   (用 scripts/diag_talk.py 的发送节奏 + --move 对照可复现)。要根治得减少设备负载。
_SEND_INTERVAL_PERCENT = 40

# 播报前先停住云台当前正在进行的转动(STOP_MOVE=139).
# 注意: **关不掉 app/云侧的"智能跟踪"** —— 实测 SDK 写 SmartMotionDetect.Enable(True/False)
# 在这台设备上不生效: 写返回成功、回读不变、云台行为也不变(用户人在镜头前乱动, 云台一动不动)。
# 跟踪只受 IMOU app 控制, app 对讲时能暂停它是因为那是 app/云自己的行为。这里只能停"当下"的移动。


class CameraError(Exception):
    pass


def _noop_audio_cb(lTalkHandle, pDataBuf, dwBufSize, byAudioEncodeType, dwUser):
    """对讲数据回调(用文件下发时不需要处理)"""
    pass


class _SnapBox:
    """抓图结果缓冲"""

    def __init__(self):
        self.lock = threading.Lock()
        self.data = None
        self.event = threading.Event()
        self.seq = 0

    def reset(self):
        with self.lock:
            self.data = None
            self.event.clear()


class Camera:
    def __init__(self, ip, port, username, password, channel=0):
        self.ip = ip
        self.port = int(port)
        self.username = username
        self.password = password
        self.channel = int(channel)

        self.loginID = 0
        self.device_info = None
        self._online = False

        self.cb_disconnect = fDisConnect(self._on_disconnect)
        self.cb_reconnect = fHaveReConnect(self._on_reconnect)
        self.snap_box = _SnapBox()
        self.cb_snap = fSnapRev(self._on_snap)

        self.sdk = NetClient()
        self.sdk.InitEx(self.cb_disconnect)
        self.sdk.SetAutoReconnect(self.cb_reconnect)

    # ---------- 回调 ----------
    def _on_disconnect(self, lLoginID, pchDVRIP, nDVRPort, dwUser):
        self._online = False
        print(f"[摄像头] 断线: {pchDVRIP.decode(errors='ignore')}")

    def _on_reconnect(self, lLoginID, pchDVRIP, nDVRPort, dwUser):
        self._online = True
        print(f"[摄像头] 重连成功: {pchDVRIP.decode(errors='ignore')}")

    def _on_snap(self, lLoginID, pBuf, RevLen, EncodeType, CmdSerial, dwUser):
        try:
            data = ctypes.cast(pBuf, ctypes.POINTER(ctypes.c_ubyte * RevLen)).contents
            buf = bytes(bytearray(data))
            with self.snap_box.lock:
                self.snap_box.data = buf
            self.snap_box.event.set()
        except Exception as e:
            print(f"[摄像头] 抓图回调异常: {e}")

    # ---------- 生命周期 ----------
    def login(self):
        stuIn = NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY()
        stuIn.dwSize = ctypes.sizeof(NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY)
        stuIn.szIP = self.ip.encode()
        stuIn.nPort = self.port
        stuIn.szUserName = self.username.encode()
        stuIn.szPassword = self.password.encode()
        stuIn.emSpecCap = EM_LOGIN_SPAC_CAP_TYPE.TCP
        stuIn.pCapParam = None

        stuOut = NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY()
        stuOut.dwSize = ctypes.sizeof(NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY)

        self.loginID, dev, err = self.sdk.LoginWithHighLevelSecurity(stuIn, stuOut)
        if self.loginID:
            self._online = True
            self.device_info = dev
            return True
        raise CameraError(f"登录失败: {err}")

    def logout(self):
        if self.loginID:
            self.sdk.Logout(self.loginID)
            self.loginID = 0
            self._online = False

    def reboot(self):
        """重启摄像头设备(用于清除对讲通道等卡死状态). 重启约需 30-60 秒. 返回是否成功."""
        if not self.loginID:
            raise CameraError("未登录")
        if self.sdk.RebootDev(self.loginID):
            self._online = False
            return True
        raise CameraError(f"重启失败: {self.sdk.GetLastErrorMessage()}")

    def cleanup(self):
        try:
            self.logout()
        except Exception:
            pass
        self.sdk.Cleanup()

    @property
    def online(self):
        return self._online

    # ---------- 云台 ----------
    def ptz_move(self, action, speed=5, seconds=0.5):
        """移动并自动停止, action 见 PTZ_ACTION_MAP"""
        cmd = PTZ_ACTION_MAP.get(action)
        if cmd is None:
            raise CameraError(f"未知云台动作: {action}, 可选: {list(PTZ_ACTION_MAP)}")
        if self.loginID == 0:
            raise CameraError("未登录")
        speed = max(1, min(255, int(speed)))

        if action in _DIAGONAL:
            p1, p2, p3 = speed, speed, 0
        else:
            p1, p2, p3 = 0, speed, 0

        ret = self.sdk.PTZControlEx2(self.loginID, self.channel, cmd, p1, p2, p3, False)
        if not ret:
            raise CameraError(f"云台启动失败: {self.sdk.GetLastErrorMessage()}")
        if seconds > 0:
            time.sleep(seconds)
            self.sdk.PTZControlEx2(self.loginID, self.channel, cmd, p1, p2, p3, True)
        return True

    def ptz_stop(self, action="up", speed=5):
        cmd = PTZ_ACTION_MAP.get(action, SDK_PTZ_ControlType.UP_CONTROL)
        if action in _DIAGONAL:
            p1, p2, p3 = speed, speed, 0
        else:
            p1, p2, p3 = 0, speed, 0
        if not self.sdk.PTZControlEx2(self.loginID, self.channel, cmd, p1, p2, p3, True):
            err = self.sdk.GetLastErrorMessage()
            # 设备无进行中的移动时, 单独停止会返回参数非法, 属正常
            if "illegal" in err.lower() or "参数" in err:
                return True
            raise CameraError(f"云台停止失败: {err}")
        return True

    def ptz_preset(self, op, index, name=None):
        """op: set / goto / del"""
        index = int(index)
        if op == "set":
            cmd = SDK_PTZ_ControlType.POINT_SET_CONTROL
        elif op == "goto":
            cmd = SDK_PTZ_ControlType.POINT_MOVE_CONTROL
        elif op == "del":
            cmd = SDK_PTZ_ControlType.POINT_DEL_CONTROL
        else:
            raise CameraError(f"未知预置点操作: {op}")
        if not self.sdk.PTZControlEx2(self.loginID, self.channel, cmd, 0, index, 0, False):
            raise CameraError(f"预置点{op}失败: {self.sdk.GetLastErrorMessage()}")
        return True

    def ptz_abs_move(self, pan_deg, tilt_deg=None):
        """绝对坐标定位: 仅支持水平角 pan_deg(0~360, 0=正面/90=左/180=背面/270=右).
        此设备(DH-SD1)的绝对定位仅生效于水平轴; tilt 需用相对移动 up/down 控制,
        故 tilt_deg 参数会被忽略(保留为兼容旧调用)."""
        from NetSDK.SDK_Enum import SDK_PTZ_ControlType as _PTZ

        if not (0 <= pan_deg <= 360):
            raise CameraError(f"pan_deg 需在 0~360 之间: {pan_deg}")

        from NetSDK.SDK_Struct import PTZ_CONTROL_ABSOLUTELY, PTZ_SPACE_UNIT, PTZ_SPEED_UNIT

        in_param = PTZ_CONTROL_ABSOLUTELY()
        in_param.stuPosition = PTZ_SPACE_UNIT()
        in_param.stuPosition.nPositionX = int(pan_deg * 10)
        in_param.stuPosition.nPositionY = 0
        in_param.stuPosition.nZoom = 0
        in_param.stuSpeed = PTZ_SPEED_UNIT()
        in_param.stuSpeed.fPositionX = 1.0
        in_param.stuSpeed.fPositionY = 1.0
        in_param.stuSpeed.fZoom = 0.0

        lib = self.sdk.sdk
        fn = lib.CLIENT_DHPTZControlEx2
        fn.argtypes = [
            ctypes.c_longlong,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_long,
            ctypes.c_long,
            ctypes.c_long,
            ctypes.c_int,
            ctypes.c_void_p,
        ]
        fn.restype = ctypes.c_int
        ret = fn(
            self.loginID,
            self.channel,
            int(_PTZ.MOVE_ABSOLUTELY),
            0,
            0,
            0,
            0,
            ctypes.byref(in_param),
        )
        if not ret:
            raise CameraError(f"绝对定位失败: {self.sdk.GetLastErrorMessage()}")
        return True

    def ptz_get_position(self):
        """读取云台当前位置角度. 返回 (pan_deg, tilt_deg)"""
        from NetSDK.SDK_Enum import EM_QUERY_DEV_STATE_TYPE
        from NetSDK.SDK_Struct import SDK_PTZ_LOCATION_INFO

        loc = SDK_PTZ_LOCATION_INFO()
        loc.nChannelID = self.channel
        buf = ctypes.create_string_buffer(ctypes.sizeof(loc), ctypes.sizeof(loc))
        ret = self.sdk.QueryDevState(self.loginID, EM_QUERY_DEV_STATE_TYPE.PTZ_LOCATION, buf, ctypes.sizeof(loc), 0, 2000)
        if not ret:
            raise CameraError(f"读取云台位置失败: {self.sdk.GetLastErrorMessage()}")
        ctypes.memmove(ctypes.addressof(loc), buf, ctypes.sizeof(loc))
        return loc.nPTZPan / 10.0, loc.nPTZTilt / 10.0

    # ---------- 设备信息 ----------
    def get_device_version(self):
        """查询设备软件版本/型号信息. 返回 dict"""
        from NetSDK.SDK_Enum import EM_QUERY_DEV_STATE_TYPE
        from NetSDK.SDK_Struct import NET_A_DEV_VERSION_INFO

        info = NET_A_DEV_VERSION_INFO()
        buf = ctypes.create_string_buffer(ctypes.sizeof(info), ctypes.sizeof(info))
        ret = self.sdk.QueryDevState(self.loginID, EM_QUERY_DEV_STATE_TYPE.SOFTWARE, buf, ctypes.sizeof(info), 0, 2000)
        if not ret:
            raise CameraError(f"查询设备信息失败: {self.sdk.GetLastErrorMessage()}")
        ctypes.memmove(ctypes.addressof(info), buf, ctypes.sizeof(info))
        return {
            "serial": info.szDevSerialNo.decode(errors="ignore").rstrip("\x00"),
            "device_type_id": info.byDevType,
            "device_type": info.szDevType.decode(errors="ignore").rstrip("\x00"),
            "detail_type": info.szDetailType.decode(errors="ignore").rstrip("\x00"),
            "software_version": info.szSoftWareVersion.decode(errors="ignore").rstrip("\x00"),
            "hardware_version": info.szHardwareVersion.decode(errors="ignore").rstrip("\x00"),
            "web_version": info.szWebVersion.decode(errors="ignore").rstrip("\x00"),
        }

    def get_ptz_capabilities(self):
        """查询云台预置点列表. 返回 list[int] (已占用的预置点序号)"""
        from NetSDK.SDK_Enum import EM_QUERY_DEV_STATE_TYPE
        from NetSDK.SDK_Struct import NET_PTZ_PRESET_LIST

        preset_list = NET_PTZ_PRESET_LIST()
        preset_list.nChannelID = self.channel
        buf = ctypes.create_string_buffer(ctypes.sizeof(preset_list), ctypes.sizeof(preset_list))
        ctypes.memmove(buf, ctypes.addressof(preset_list), ctypes.sizeof(preset_list))
        ret = self.sdk.QueryDevState(self.loginID, EM_QUERY_DEV_STATE_TYPE.PTZ_PRESET_LIST, buf, ctypes.sizeof(preset_list), 0, 2000)
        if not ret:
            return []
        ctypes.memmove(ctypes.addressof(preset_list), buf, ctypes.sizeof(preset_list))
        return [preset_list.stPresetNo[i] for i in range(preset_list.nPresetNum)]

    def get_smart_track(self):
        """查询智能跟踪配置. 返回 dict(supported: bool, enabled: bool)"""
        import json as _json

        # 先获取原始配置
        buf_size = 1024 * 1024  # 1MB buffer
        raw_buf = ctypes.create_string_buffer(buf_size)
        error = ctypes.c_int(0)
        ret = self.sdk.GetNewDevConfig(self.loginID, "SmartMotionDetect", self.channel, raw_buf, buf_size, error, 3000)
        if not ret:
            return {"supported": False, "enabled": False}

        raw_data = raw_buf.raw.rstrip(b"\x00")

        # 尝试 JSON 格式 (IMOU 摄像头)
        if raw_data.startswith(b"{"):
            try:
                obj = _json.loads(raw_data.decode(errors="ignore"))
                params = obj.get("params", {})
                table = params.get("table", {})
                enabled = table.get("Enable", False)
                return {
                    "supported": True,
                    "enabled": bool(enabled),
                }
            except Exception:
                pass

        # 尝试二进制结构体格式 (大华摄像头)
        from NetSDK.SDK_Struct import NET_CFG_SMART_MOTION_DETECT
        try:
            cfg = NET_CFG_SMART_MOTION_DETECT()
            cfg.dwSize = ctypes.sizeof(cfg)
            ret = self.sdk.ParseData("SmartMotionDetect", raw_buf, cfg, ctypes.sizeof(cfg))
            if ret:
                return {
                    "supported": True,
                    "enabled": bool(cfg.bSmartTrack),
                    "motion_detect_enabled": bool(cfg.bEnable),
                    "tracking_zoom": bool(cfg.bTrackingZoomEnable),
                }
        except Exception:
            pass

        return {"supported": True, "enabled": False}

    # ---------- 抓图 ----------
    def capture(self, timeout=8):
        """抓取一帧 JPEG, 返回 bytes"""
        self.snap_box.reset()
        self.sdk.SetSnapRevCallBack(self.cb_snap, 0)

        snap = SNAP_PARAMS()
        snap.Channel = self.channel
        snap.Quality = 6
        snap.mode = 0
        snap.CmdSerial = 0
        ret = self.sdk.SnapPictureEx(self.loginID, snap)
        if not ret:
            raise CameraError(f"抓图失败: {self.sdk.GetLastErrorMessage()}")

        if not self.snap_box.event.wait(timeout):
            raise CameraError("抓图超时")
        with self.snap_box.lock:
            data = self.snap_box.data
        if not data:
            raise CameraError("抓图无数据")
        return data

    def stop_move(self):
        """停止云台移动 + 停止巡航(STOP_MOVE=139, 比八向停止更彻底).

        设备无进行中的移动时可能返回"参数非法", 属正常。
        """
        from NetSDK.SDK_Struct import NET_IN_STOP_MOVE_INFO

        in_param = NET_IN_STOP_MOVE_INFO()
        in_param.dwSize = ctypes.sizeof(NET_IN_STOP_MOVE_INFO)
        ret = self.sdk.PTZControlEx2(
            self.loginID, self.channel, SDK_PTZ_ControlType.STOP_MOVE,
            0, 0, 0, False, ctypes.byref(in_param),
        )
        if not ret:
            err = self.sdk.GetLastErrorMessage()
            if "illegal" not in err.lower() and "参数" not in err:
                raise CameraError(f"停止云台失败: {err}")
        return True

    # ---------- 扬声器 / 对讲 ----------
    def speak_file(self, g711a_path, sample_rate=16000, audio_bit=8, codec="mulaw"):
        """把裸音频文件推给设备扬声器播放.

        sample_rate 必须和文件内容一致: 本设备(IMOU LC-S2V)按 **16kHz** 消费音频,
        喂 8kHz 内容会被它当 16kHz 播 -> 实际只有一半数据量 -> 播放卡顿/断句。
        codec: 只有 mulaw(G.711u) 出声; alaw 在本设备上是静音。

        播报前会发一次 STOP_MOVE 停住云台当下的转动。注意 app/云侧的"智能跟踪"关不掉
        (SDK 写配置在这台设备上不生效), 所以它一边追一边说时仍可能断句 —— 这是设备侧的
        限制, 见 AGENTS.md。
        """
        if self.loginID == 0:
            raise CameraError("未登录")

        encode_type = (
            EM_A_TALK_CODING_TYPE.EM_A_TALK_G711u
            if codec == "mulaw"
            else EM_A_TALK_CODING_TYPE.EM_A_TALK_G711a
        )

        talk_in = NET_IN_TALK_SEND_DATA_FILE()
        talk_in.dwSize = ctypes.sizeof(NET_IN_TALK_SEND_DATA_FILE)
        talk_in.pFilePath = ctypes.cast(
            ctypes.c_char_p(g711a_path.encode()), ctypes.POINTER(ctypes.c_char)
        )
        talk_in.bNeedHead = 1
        talk_in.emEncodeType = encode_type
        talk_in.nAudioBit = audio_bit
        talk_in.dwSampleRate = sample_rate
        talk_in.dwSendInterval = _SEND_INTERVAL_PERCENT
        # 发送进度回调(TalkSendDataByFile 是异步的, 靠它判断真的发完了, 别用固定时长猜)
        progress = {"total": 0, "sent": 0}

        @fTalkSendPosCallBack
        def _pos(lTalkHandle, dwTotalSize, dwSendSize, dwUser):
            progress["total"] = dwTotalSize
            progress["sent"] = dwSendSize

        self.cb_sendpos = _pos
        talk_in.cbSendPos = self.cb_sendpos
        talk_in.dwUser = C_LDWORD(0)

        talk_out = NET_OUT_TALK_SEND_DATA_FILE()
        talk_out.dwSize = ctypes.sizeof(NET_OUT_TALK_SEND_DATA_FILE)

        self.cb_audio = pfAudioDataCallBack(_noop_audio_cb)
        sdk = self.sdk.sdk
        sdk.CLIENT_StartTalkEx.restype = C_LLONG
        handle = sdk.CLIENT_StartTalkEx(C_LLONG(self.loginID), self.cb_audio, C_LDWORD(0))
        if not handle:
            raise CameraError(f"开启对讲失败: {self.sdk.GetLastErrorMessage()}")

        try:
            t0 = time.time()

            # 播报前停住云台当下正在进行的转动(如果它正被 app 的跟踪驱动着转, 至少别边转边说)
            try:
                self.stop_move()
                time.sleep(0.3)
            except CameraError:
                pass

            if not self.sdk.TalkSendDataByFile(handle, talk_in, talk_out):
                raise CameraError(f"发送语音失败: {self.sdk.GetLastErrorMessage()}")

            size = os.path.getsize(g711a_path)
            duration = size / sample_rate  # G.711: 1 字节 = 1 采样

            # 1) 等异步发送真发完(超时兜底), 提前 Stop 会把话截断
            deadline = t0 + duration * 3 + 10
            while progress["total"] == 0 or progress["sent"] < progress["total"]:
                if time.time() > deadline:
                    print(f"[摄像头] 语音发送未确认完成 ({progress})", flush=True)
                    break
                time.sleep(0.05)

            # 2) 发送比实时快, 设备还在按实时播缓冲, 补上剩余时长再停
            time.sleep(max(0.0, duration - (time.time() - t0)) + 1.0)
        finally:
            self.sdk.StopTalkSendDataByFile(handle)
            self.sdk.StopTalkEx(handle)
        return True

    # ---------- 麦克风采集 ----------
    def record_audio_stream(self, seconds=6.0):
        """从**媒体流的音频轨**采集连续音频, 返回 (pcm16_bytes, 16000, 16)。

        比对讲通道(record_audio)好在哪: 对讲通道有静音抑制, 3 秒只回传 ~0.3 秒数据;
        这里实测 8 秒采集 -> 抽出音频时长 8.000 秒, 是**连续**的, 语音识别才有完整输入。
        实现: RealPlayByDataType(从码流1, 音频转码封装成 FLV) -> 攒够 seconds 秒 ->
        ffmpeg 解出 16kHz 单声道 PCM16(设备实际给的是 AAC 16kHz, 比 G.711 还好)。

        代价: 播放 h264 从码流白占了约 27 KB/s 带宽(设备只肯推整路码流, 没有纯音频流选项)。
        """
        import subprocess

        from NetSDK.SDK_Callback import CB_FUNCTYPE
        from NetSDK.SDK_Enum import (
            EM_AUDIO_DATA_TYPE,
            EM_REAL_DATA_TYPE,
            SDK_RealPlayType,
        )
        from NetSDK.SDK_Struct import (
            NET_IN_REALPLAY_BY_DATA_TYPE,
            NET_OUT_REALPLAY_BY_DATA_TYPE,
        )

        if self.loginID == 0:
            raise CameraError("未登录")

        buf = bytearray()
        _CB = CB_FUNCTYPE(
            None, C_LLONG, C_DWORD, ctypes.POINTER(ctypes.c_byte), C_DWORD, C_LLONG, C_LDWORD
        )

        @_CB
        def _cb(lRealHandle, dwDataType, pBuffer, dwBufSize, dwUser, dwUser2=0):
            if dwBufSize:
                buf.extend(ctypes.string_at(pBuffer, dwBufSize))

        in_param = NET_IN_REALPLAY_BY_DATA_TYPE()
        in_param.dwSize = ctypes.sizeof(NET_IN_REALPLAY_BY_DATA_TYPE)
        in_param.nChannelID = self.channel
        in_param.hWnd = 0
        in_param.rType = SDK_RealPlayType.Realplay_1      # 从码流1, 比主码流省带宽
        in_param.cbRealData = _cb
        in_param.emDataType = EM_REAL_DATA_TYPE.FLV_STREAM
        in_param.emAudioType = EM_AUDIO_DATA_TYPE.G711A   # 设备实际给 AAC 16k, 一样能解
        in_param.dwUser = C_LDWORD(0)

        out_param = NET_OUT_REALPLAY_BY_DATA_TYPE()
        out_param.dwSize = ctypes.sizeof(NET_OUT_REALPLAY_BY_DATA_TYPE)

        handle = self.sdk.RealPlayByDataType(self.loginID, in_param, out_param, 5000)
        if not handle:
            raise CameraError(f"开启音频流失败: {self.sdk.GetLastErrorMessage()}")
        try:
            time.sleep(float(seconds))
        finally:
            self.sdk.StopRealPlayEx(handle)

        if not buf:
            raise CameraError("未采集到音频数据")

        # 回调给的是设备私有封装(DHAV)或 FLV, 开头偶尔混有杂字节 -> 从 magic 处截起
        raw = bytes(buf)
        for magic in (b"DHAV", b"FLV"):
            i = raw.find(magic)
            if i >= 0:
                raw = raw[i:]
                break

        def _decode(*extra):
            return subprocess.run(
                ["ffmpeg", "-v", "error", *extra, "-i", "pipe:0", "-vn", "-ac", "1",
                 "-ar", "16000", "-f", "s16le", "-"],
                input=raw, capture_output=True, timeout=60,
            )

        p = _decode()
        if not p.stdout:
            # 停流时最后一帧常被截断, 容忍错误再试一次
            p = _decode("-err_detect", "ignore_err")
        if not p.stdout:
            raise CameraError(f"解码音频流失败: {p.stderr.decode(errors='ignore')[:200]}")
        return p.stdout, 16000, 16

    def record_audio(self, seconds=5.0, on_retry=None):
        """采集摄像头麦克风音频, 返回 (pcm16_bytes, sample_rate, audio_bit).
        用 StartTalkByDataType 全双工对讲通道, 收到设备麦克风上行的裸 PCM 音频.
        若对讲通道被占用(上次进程异常退出残留), 自动重试等待摄像头端释放, 最长约 2 分钟.
        """
        import ctypes as _ct

        from NetSDK.SDK_Struct import (
            NET_IN_START_TALK_INFO,
            NET_OUT_START_TALK_INFO,
            NET_AUDIO_DATA_CB_INFO,
            C_ENUM,
        )
        from NetSDK.SDK_Callback import CB_FUNCTYPE

        if self.loginID == 0:
            raise CameraError("未登录")

        _TALK_CB = CB_FUNCTYPE(None, C_LLONG, _ct.POINTER(NET_AUDIO_DATA_CB_INFO), C_ENUM, C_LDWORD)

        max_wait = 120.0  # 最长等待摄像头端释放对讲通道
        waited = 0.0
        attempts = 0  # 连续被占用的次数

        while True:
            buffer = []
            meta = {}

            @_TALK_CB
            def _cb(lTalkHandle, pInfo, emAudioCode, dwUser):
                if not pInfo:
                    return
                info = pInfo.contents
                if info.dwRawBufSize > 0 and info.pRawBuf:
                    raw = _ct.string_at(info.pRawBuf, info.dwRawBufSize)
                    buffer.append(raw)
                meta.setdefault("rate", info.dwSampleRate)
                meta.setdefault("bit", info.nAudioBit)
                meta.setdefault("code", emAudioCode)

            in_param = NET_IN_START_TALK_INFO()
            in_param.dwSize = ctypes.sizeof(in_param)
            in_param.pfAudioDataCallBackEx = _cb
            in_param.dwUser = C_LDWORD(0)

            out_param = NET_OUT_START_TALK_INFO()
            out_param.dwSize = ctypes.sizeof(out_param)

            handle = self.sdk.StartTalkByDataType(self.loginID, in_param, out_param, 3000)
            if not handle:
                err = self.sdk.GetLastErrorMessage()
                if "talk has opened" in err:
                    attempts += 1
                    # 连续 3 次仍被占用 -> 询问是否重启摄像头.
                    # 只在终端提示: 经 MCP stdio 调用时 stdin 是协议管道, input() 会挂起并污染协议.
                    if attempts >= 3 and sys.stdin.isatty():
                        answer = input(
                            f"对讲通道连续 {attempts} 次被占用, 是否重启摄像头? (y/n): "
                        ).strip().lower()
                        if answer in ("y", "yes"):
                            print("正在重启摄像头, 请稍候...", flush=True)
                            try:
                                self.reboot()
                            except Exception as e:
                                # RebootDev 常返回 Protocol error 但设备实际已收到指令并重启
                                if "Protocol error" in str(e):
                                    print("[i] 设备已响应重启(协议错误属正常), 等待其重新上线...", flush=True)
                                else:
                                    print(f"[!] 重启指令发出失败: {e}", flush=True)
                            time.sleep(20)
                            waited = 0.0
                            attempts = 0
                            continue
                    if waited >= max_wait:
                        raise CameraError(f"开启麦克风采集失败: {err}")
                    wait = min(15.0, max(5.0, waited / 3))
                    if on_retry:
                        on_retry(wait)
                    time.sleep(wait)
                    waited += wait
                    continue
                raise CameraError(f"开启麦克风采集失败: {err}")

            try:
                # 注: 该设备麦克风上行一直回传少量音频碎片(静音时也如此),
                #     无法用'数据有无/能量'判断说话结束, 故按固定时长录音.
                time.sleep(float(seconds))
            finally:
                self.sdk.StopTalkEx(handle)

            if not buffer:
                raise CameraError("未采集到音频数据(设备可能无麦克风或通道被占用)")
            pcm = b"".join(buffer)
            return pcm, meta.get("rate", 8000), meta.get("bit", 16)

    # ---------- 设备搜索 ----------
    @staticmethod
    def search_devices(timeout=8):
        """局域网搜索大华/Imou 摄像头, 返回去重后的设备列表."""
        import socket as _sock
        from NetSDK.NetSDK import NetClient as _NetClient
        from NetSDK.SDK_Enum import EM_SEND_SEARCH_TYPE
        from NetSDK.SDK_Struct import (
            NET_IN_STARTSERACH_DEVICE,
            NET_OUT_STARTSERACH_DEVICE,
            DEVICE_NET_INFO_EX2,
        )
        from NetSDK.SDK_Callback import CB_FUNCTYPE

        found = {}  # ip -> info (去重)

        _SEARCH_CB = CB_FUNCTYPE(None, ctypes.c_longlong, ctypes.POINTER(DEVICE_NET_INFO_EX2), ctypes.c_void_p)

        @_SEARCH_CB
        def _cb(lSearchHandle, pDevNetInfo, pUserData):
            try:
                buf = ctypes.cast(pDevNetInfo, ctypes.POINTER(DEVICE_NET_INFO_EX2)).contents
                dev = buf.stuDevInfo
                if dev.iIPVersion == 4:
                    ip = dev.szIP.decode(errors="ignore")
                    if ip not in found:
                        found[ip] = {
                            "ip": ip,
                            "port": dev.nPort,
                            "http_port": dev.nHttpPort,
                            "mac": dev.szMac.decode(errors="ignore"),
                            "type": dev.szDeviceType.decode(errors="ignore"),
                            "model": dev.szDetailType.decode(errors="ignore"),
                        }
            except Exception:
                pass

        sdk = _NetClient()
        sdk.InitEx(None, 0)

        # 获取本机 IP
        local_ips = set()
        try:
            out = __import__("subprocess").check_output(["hostname", "-I"], text=True)
            for ip in out.split():
                if ":" not in ip:
                    local_ips.add(ip)
        except Exception:
            pass
        try:
            hostname = _sock.gethostname()
            for info in _sock.gethostbyname_ex(hostname):
                if isinstance(info, list):
                    for ip in info:
                        if ":" not in ip:
                            local_ips.add(ip)
        except Exception:
            pass

        handles = []
        for lip in local_ips:
            start_in = NET_IN_STARTSERACH_DEVICE()
            start_in.dwSize = ctypes.sizeof(NET_IN_STARTSERACH_DEVICE)
            start_in.emSendType = EM_SEND_SEARCH_TYPE.MULTICAST_AND_BROADCAST
            start_in.cbSearchDevices = _cb
            start_in.szLocalIp = lip.encode()
            start_out = NET_OUT_STARTSERACH_DEVICE()
            start_out.dwSize = ctypes.sizeof(NET_OUT_STARTSERACH_DEVICE)
            h = sdk.StartSearchDevicesEx(start_in, start_out)
            if h != 0:
                handles.append(h)

        time.sleep(timeout)

        for h in handles:
            sdk.StopSearchDevices(h)
        sdk.Cleanup()

        return list(found.values())
