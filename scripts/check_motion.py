# coding=utf-8
"""判断摄像头当前在不在动, 并测试能不能用 ptz_stop 把它"冻住".

原理: 连续抓两帧算 PSNR。画面整体平移(云台在转)时 PSNR 会掉到 10~15dB;
静止时只有噪声/画面内小动静, PSNR 通常 > 30dB。

用法(本仓库目录下):
    uv run python scripts/check_motion.py --seconds 8              # 只看它在不在动
    uv run python scripts/check_motion.py --seconds 8 --stop-loop  # 边测边持续 ptz_stop
"""
import argparse
import ctypes
import os
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import resolve_camera
from camera import Camera


def _psnr(f1, f2):
    """两张 jpg 的 PSNR(dB), 越大越"没动"。返回 None 表示算不出来。"""
    p = subprocess.run(
        ["ffmpeg", "-loglevel", "info", "-i", f1, "-i", f2, "-lavfi", "psnr", "-f", "null", "-"],
        capture_output=True, text=True, timeout=30,
    )
    m = re.search(r"average:([\d.]+|inf)", p.stderr)
    if not m:
        return None
    return 99.0 if m.group(1) == "inf" else float(m.group(1))


def _smart_motion(cam, channel):
    """读 SMART_MOTION_DETECT 配置(设备是否报"智能跟踪"开着)"""
    try:
        from NetSDK.SDK_Enum import NET_EM_CFG_OPERATE_TYPE
        from NetSDK.SDK_Struct import NET_CFG_SMART_MOTION_DETECT

        st = NET_CFG_SMART_MOTION_DETECT()
        st.dwSize = ctypes.sizeof(NET_CFG_SMART_MOTION_DETECT)
        ret = cam.sdk.sdk.CLIENT_GetConfig(
            ctypes.c_void_p(cam.loginID), NET_EM_CFG_OPERATE_TYPE.SMART_MOTION_DETECT,
            channel, ctypes.byref(st), ctypes.sizeof(st), 3000,
        )
        if not ret:
            return f"通道{channel}: 读取失败"
        return (f"通道{channel}: 动检={'开' if st.bEnable else '关'} "
                f"智能跟踪={'开' if st.bSmartTrack else '关'}")
    except Exception as e:
        return f"通道{channel}: 异常 {e}"


def _sampler(cam, stop_evt, out, t0):
    """后台采样: 每 0.7s 抓一帧算 PSNR, 结果存 (相对时间, dB)"""
    tmp = tempfile.mkdtemp(prefix="motion_")
    prev = None
    n = 0
    while not stop_evt.is_set():
        f = os.path.join(tmp, f"f{n}.jpg")
        try:
            with open(f, "wb") as fh:
                fh.write(cam.capture())
        except Exception:
            time.sleep(0.7)
            continue
        if prev:
            v = _psnr(prev, f)
            if v is not None:
                out.append((time.time() - t0, v))
        prev = f
        n += 1
        time.sleep(0.7)


def _speak_test(cam, path):
    """播放音频, 同时测"播报期间摄像头有没有在动" + 播完配置有没有恢复"""
    import threading

    before = _smart_motion(cam, 0)
    print("  播报前 " + before, flush=True)
    t0 = time.time()
    samples = []
    stop_evt = threading.Event()
    th = threading.Thread(target=_sampler, args=(cam, stop_evt, samples, t0), daemon=True)
    th.start()
    time.sleep(2.0)                      # 先采两秒基线
    t_play = time.time() - t0
    try:
        cam.speak_file(path, sample_rate=16000, codec="mulaw")
    finally:
        t_done = time.time() - t0
        time.sleep(1.5)                  # 再采一点播后
        stop_evt.set()
        th.join(timeout=5)

    print(f"\n  播报窗口 {t_play:.1f}s ~ {t_done:.1f}s", flush=True)
    moved_in = moved_out = n_in = n_out = 0
    for t, v in samples:
        where = "播报中" if t_play <= t <= t_done else "播报外"
        if v < 20:
            if where == "播报中":
                moved_in += 1
            else:
                moved_out += 1
        if where == "播报中":
            n_in += 1
        else:
            n_out += 1
        print(f"    {t:5.1f}s PSNR={v:5.1f}dB {where} {'← 在动!' if v < 20 else ''}", flush=True)
    print(f"\n  播报中: {n_in} 次采样, 在动 {moved_in} 次", flush=True)
    print(f"  播报外: {n_out} 次采样, 在动 {moved_out} 次", flush=True)
    print("  播报后 " + _smart_motion(cam, 0) + "   <- 应和你开播前一致(恢复)", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=8, help="观测时长")
    ap.add_argument("--stop-loop", action="store_true", help="期间持续发 ptz_stop")
    ap.add_argument("--speak", help="播放这个 G.711 文件, 并测播报期间在不在动")
    ap.add_argument("--ip", help="摄像头 IP")
    args = ap.parse_args()

    c = resolve_camera(args.ip)
    cam = Camera(c["ip"], c["port"], c["username"], c["password"], c["channel"])
    cam.login()
    print(f"已登录 {c['ip']}", flush=True)
    for ch in (0, 1, 2):
        print("  " + _smart_motion(cam, ch), flush=True)

    if args.speak:
        try:
            _speak_test(cam, args.speak)
        finally:
            cam.cleanup()
        return

    tmp = tempfile.mkdtemp(prefix="motion_")
    prev = None
    moved = 0
    shots = 0
    t_end = time.time() + args.seconds
    print(f"\n观测 {args.seconds}s"
          f"{' (持续 ptz_stop)' if args.stop_loop else ''}; >30dB=静止, <20dB=在转", flush=True)
    try:
        while time.time() < t_end:
            f = os.path.join(tmp, f"f{shots}.jpg")
            with open(f, "wb") as fh:
                fh.write(cam.capture())
            if prev:
                v = _psnr(prev, f)
                shots += 1
                if v is None:
                    print(f"  #{shots} PSNR=? (算不出)", flush=True)
                else:
                    state = "静止" if v >= 30 else ("在转/大幅变化" if v < 20 else "小变化")
                    if v < 20:
                        moved += 1
                    print(f"  #{shots} PSNR={v:5.1f}dB  {state}", flush=True)
            prev = f
            if args.stop_loop:
                cam.ptz_stop()
            time.sleep(0.7)
    finally:
        cam.cleanup()
    if shots:
        print(f"\n结论: {shots} 次采样里 {moved} 次判定为「在动」"
              f" ({moved/shots:.0%})", flush=True)


if __name__ == "__main__":
    main()
