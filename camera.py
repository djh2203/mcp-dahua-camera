# coding=utf-8
import ctypes
import os
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


class CameraError(Exception):
    pass


def _noop_audio_cb(lTalkHandle, pDataBuf, dwBufSize, byAudioEncodeType, dwUser):
    """对讲数据回调(用文件下发时不需要处理)"""
    pass


def _noop_sendpos_cb(lTalkHandle, dwTotalSize, dwSendSize, dwUser):
    """音频文件发送进度回调"""
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

    def ptz_start(self, action, speed=5):
        cmd = PTZ_ACTION_MAP.get(action)
        if cmd is None:
            raise CameraError(f"未知云台动作: {action}")
        if action in _DIAGONAL:
            p1, p2, p3 = speed, speed, 0
        else:
            p1, p2, p3 = 0, speed, 0
        if not self.sdk.PTZControlEx2(self.loginID, self.channel, cmd, p1, p2, p3, False):
            raise CameraError(f"云台启动失败: {self.sdk.GetLastErrorMessage()}")
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
        from NetSDK.SDK_Struct import NET_CFG_SMART_MOTION_DETECT

        # 先获取原始配置
        buf_size = 1024 * 1024  # 1MB buffer
        raw_buf = ctypes.create_string_buffer(buf_size)
        error = ctypes.c_int(0)
        ret = self.sdk.GetNewDevConfig(self.loginID, "SmartMotionDetect", self.channel, raw_buf, buf_size, error, 3000)
        if not ret:
            return {"supported": False, "enabled": False}

        # 解析到结构体
        cfg = NET_CFG_SMART_MOTION_DETECT()
        cfg.dwSize = ctypes.sizeof(cfg)
        ret = self.sdk.ParseData("SmartMotionDetect", raw_buf, cfg, ctypes.sizeof(cfg))
        if not ret:
            return {"supported": True, "enabled": False}

        return {
            "supported": True,
            "enabled": bool(cfg.bSmartTrack),
            "motion_detect_enabled": bool(cfg.bEnable),
            "tracking_zoom": bool(cfg.bTrackingZoomEnable),
        }

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

    # ---------- 扬声器 / 对讲 ----------
    def speak_file(self, g711a_path, sample_rate=8000, audio_bit=8, codec="mulaw"):
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
        talk_in.dwSendInterval = 60
        self.cb_sendpos = fTalkSendPosCallBack(_noop_sendpos_cb)
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
            if not self.sdk.TalkSendDataByFile(handle, talk_in, talk_out):
                raise CameraError(f"发送语音失败: {self.sdk.GetLastErrorMessage()}")
            # 等待发送完成(以音频长度估算)
            import os

            size = os.path.getsize(g711a_path)
            duration = size / sample_rate  # G711 1字节=1采样, 8kHz -> 每秒8000字节
            time.sleep(duration + 1.0)
        finally:
            self.sdk.StopTalkSendDataByFile(handle)
            self.sdk.StopTalkEx(handle)
        return True

    # ---------- 麦克风采集 ----------
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
                    # 连续 3 次仍被占用 -> 询问是否重启摄像头
                    if attempts >= 3:
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
