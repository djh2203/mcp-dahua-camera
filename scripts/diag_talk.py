# coding=utf-8
"""对讲播放诊断: 测 TalkSendDataByFile 的实际发送节奏, 用来定位扬声器播放卡顿.

用法(在本仓库目录下):
    uv run python scripts/diag_talk.py --file <g711裸音频> --codec mulaw --interval 60
    uv run python scripts/diag_talk.py --sweep --file ...   # 扫多个 dwSendInterval

要点: dwSendInterval 是"发送间隔百分比"(0/100=SDK 默认, <100 更快, >100 更慢),
不是毫秒。播放 8kHz G.711 需要稳定 >= 8000 字节/秒, 低于此值设备会饿死 -> 卡顿。
"""
import argparse
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import resolve_camera
from camera import Camera
from NetSDK.SDK_Struct import (
    NET_IN_TALK_SEND_DATA_FILE,
    NET_OUT_TALK_SEND_DATA_FILE,
    C_LLONG,
    C_LDWORD,
)
from NetSDK.SDK_Enum import EM_A_TALK_CODING_TYPE
from NetSDK.SDK_Callback import fTalkSendPosCallBack, pfAudioDataCallBack


def _noop_audio_cb(lTalkHandle, pDataBuf, dwBufSize, byAudioEncodeType, dwUser):
    pass


def play_once(cam, path, codec, interval, sample_rate=8000, audio_bit=8, tail=2.0, need_head=1):
    """播放一次, 返回测量结果 dict"""
    size = os.path.getsize(path)
    need = size / (sample_rate * (audio_bit / 8))  # 实时播放所需秒数

    marks = []
    t0 = time.time()

    @fTalkSendPosCallBack
    def _pos(lTalkHandle, dwTotalSize, dwSendSize, dwUser):
        marks.append((time.time() - t0, dwTotalSize, dwSendSize))

    encode = (
        EM_A_TALK_CODING_TYPE.EM_A_TALK_G711u
        if codec == "mulaw"
        else EM_A_TALK_CODING_TYPE.EM_A_TALK_G711a
    )

    talk_in = NET_IN_TALK_SEND_DATA_FILE()
    talk_in.dwSize = ctypes.sizeof(NET_IN_TALK_SEND_DATA_FILE)
    talk_in.pFilePath = ctypes.cast(
        ctypes.c_char_p(path.encode()), ctypes.POINTER(ctypes.c_char)
    )
    talk_in.bNeedHead = need_head
    talk_in.emEncodeType = encode
    talk_in.nAudioBit = audio_bit
    talk_in.dwSampleRate = sample_rate
    talk_in.dwSendInterval = interval
    talk_in.cbSendPos = _pos
    talk_in.dwUser = C_LDWORD(0)

    talk_out = NET_OUT_TALK_SEND_DATA_FILE()
    talk_out.dwSize = ctypes.sizeof(NET_OUT_TALK_SEND_DATA_FILE)

    cb_audio = pfAudioDataCallBack(_noop_audio_cb)
    sdk = cam.sdk.sdk
    sdk.CLIENT_StartTalkEx.restype = C_LLONG
    handle = sdk.CLIENT_StartTalkEx(C_LLONG(cam.loginID), cb_audio, C_LDWORD(0))
    if not handle:
        raise RuntimeError(f"StartTalkEx 失败: {cam.sdk.GetLastErrorMessage()}")

    result = {"file": os.path.basename(path), "codec": codec, "interval": interval,
              "rate": sample_rate, "bit": audio_bit, "head": need_head,
              "bytes": size, "need_seconds": round(need, 2)}
    try:
        t_start = time.time()
        ret = cam.sdk.TalkSendDataByFile(handle, talk_in, talk_out)
        block = time.time() - t_start
        result["block_seconds"] = round(block, 3)   # ~0 = 异步发送
        result["call_ok"] = bool(ret)

        # 等发送完成(callback 报满)或超时
        deadline = time.time() + need * 3 + 10
        while time.time() < deadline:
            if marks and marks[-1][2] >= marks[-1][1] and marks[-1][1] > 0:
                break
            time.sleep(0.02)

        if marks:
            done_t, total, sent = marks[-1]
            result["send_seconds"] = round(done_t, 2)
            result["send_bytes_per_s"] = round(sent / done_t, 0) if done_t > 0 else None
            result["send_ok"] = sent >= total
            result["callbacks"] = len(marks)
        else:
            result["send_seconds"] = None
            result["send_ok"] = False    # 回调没触发
            result["callbacks"] = 0

        # 让设备把缓冲里的音频播完, 再停(提前停会截断)
        sent_t = marks[-1][0] if marks else 0.0
        wait = max(0.0, need - sent_t) + tail
        time.sleep(wait)
        result["total_seconds"] = round(time.time() - t0, 2)
    finally:
        cam.sdk.StopTalkSendDataByFile(handle)
        cam.sdk.StopTalkEx(handle)
    return result


def _resolve(ip):
    """凭据解析和 mcp_server 一致(cameras.json 优先, 其次 config.ini)"""
    try:
        return resolve_camera(ip)
    except ValueError as e:
        raise SystemExit(str(e))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="G.711 裸音频文件")
    ap.add_argument("--ip", help="摄像头 IP, 默认 cameras.json 里第一台")
    ap.add_argument("--codec", default="mulaw", choices=["mulaw", "alaw"])
    ap.add_argument("--interval", type=int, default=60, help="dwSendInterval 百分比")
    ap.add_argument("--sweep", action="store_true", help="扫 100/60/20 三档")
    ap.add_argument("--tail", type=float, default=2.0, help="播完后再等几秒")
    ap.add_argument("--rate", type=int, default=8000, help="声明采样率(8k/16k), 必须和音频内容一致")
    ap.add_argument("--needhead", type=int, default=1, choices=[0, 1], help="bNeedHead: 1=SDK加音频头")
    args = ap.parse_args()

    c = _resolve(args.ip)
    cam = Camera(c["ip"], c["port"], c["username"], c["password"], c["channel"])
    cam.login()
    print(f"已登录 {c['ip']}", flush=True)

    size = os.path.getsize(args.file)
    realtime = size / float(args.rate)
    print(f"文件 {args.file}: {size} 字节 -> 声明 {args.rate}Hz G.711 实时时长 {realtime:.2f}s "
          f"(低于 {args.rate} 字节/秒 = 设备饿死 = 卡顿)\n", flush=True)

    intervals = [100, 60, 20] if args.sweep else [args.interval]
    try:
        for iv in intervals:
            print(f"--- dwSendInterval={iv} ({'SDK默认' if iv in (0, 100) else str(iv)+'%'}) ---", flush=True)
            r = play_once(cam, args.file, args.codec, iv, sample_rate=args.rate,
                          tail=args.tail, need_head=args.needhead)
            for k in ("call_ok", "block_seconds", "send_seconds", "send_bytes_per_s",
                      "send_ok", "callbacks", "total_seconds"):
                print(f"    {k:20} {r.get(k)}", flush=True)
            rate = r.get("send_bytes_per_s")
            if rate:
                target = args.rate   # G.711: 1 字节 = 1 采样
                verdict = "达标" if rate >= target else f"偏慢({rate/target:.0%} 实时) -> 会卡顿"
                print(f"    {'结论':20} {verdict}", flush=True)
            print(flush=True)
    finally:
        cam.cleanup()


if __name__ == "__main__":
    main()
