# coding=utf-8
"""麦克风两条路对比 + 全双工能力检查.

用法(本仓库目录下):
    uv run python scripts/diag_mic.py [--seconds 6]

判据:
  1) stream(媒体流音频轨): 有效音频时长应 ≈ 采集时长(连续) -> 语音识别用这条
  2) talk(对讲通道): 有静音抑制, 有效时长通常只有零头(3 秒里 ~0.3 秒)
  3) 全双工: 一边用 stream 采集、一边用 talk 播报, 两项都应成功
     (能同时, 才谈得上"边听边说"/打断)
"""
import argparse
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import resolve_camera
from camera import Camera


def _seconds_of(pcm, rate, bit):
    return len(pcm) / (rate * (bit / 8))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=6, help="每种方式采集时长")
    ap.add_argument("--file", help="全双工测试要播的 G.711 文件(16kHz mulaw)")
    ap.add_argument("--ip")
    args = ap.parse_args()

    c = resolve_camera(args.ip)
    cam = Camera(c["ip"], c["port"], c["username"], c["password"], c["channel"])
    cam.login()
    print(f"已登录 {c['ip']}, 每种方式采集 {args.seconds} 秒\n", flush=True)
    fails = []
    try:
        for label, fn, want_continuous in (
            ("stream(媒体流音频轨)", cam.record_audio_stream, True),
            ("talk(对讲通道)", cam.record_audio, False),
        ):
            t0 = time.time()
            try:
                pcm, rate, bit = fn(args.seconds)
            except Exception as e:
                print(f"  {label:22s} 失败: {e}", flush=True)
                fails.append(label)
                continue
            got = _seconds_of(pcm, rate, bit)
            ratio = got / args.seconds
            verdict = ("连续 ✓" if ratio >= 0.8 else f"只有零头({ratio:.0%})")
            print(f"  {label:22s} {time.time()-t0:4.1f}s 得到 {got:5.2f} 秒 @{rate}Hz/{bit}bit  {verdict}",
                  flush=True)
            if want_continuous and ratio < 0.8:
                fails.append(f"{label} 不连续({ratio:.0%})")

        print("\n全双工检查: 一边 stream 采集, 一边 talk 播报", flush=True)
        result = {}

        def _rec():
            try:
                result["rec"] = cam.record_audio_stream(args.seconds)
            except Exception as e:
                result["rec_err"] = str(e)

        th = threading.Thread(target=_rec, daemon=True)
        th.start()
        time.sleep(1.0)   # 让采集先跑起来
        try:
            if args.file and os.path.exists(args.file):
                cam.speak_file(args.file, sample_rate=16000, codec="mulaw")
                result["play"] = "ok"
            else:
                cam._read_track_cfg()      # 没给文件就只发个轻量请求占位
                result["play"] = "skipped"
        except Exception as e:
            result["play"] = f"失败: {e}"
        th.join(timeout=args.seconds + 10)
        rec_ok = result.get("rec") and _seconds_of(*result["rec"]) >= args.seconds * 0.8
        print(f"  采集: {'成功 ' + format(_seconds_of(*result['rec']), '.2f') + ' 秒' if rec_ok else result.get('rec_err', '失败')}",
              flush=True)
        print(f"  播报: {result.get('play')}", flush=True)
        if not rec_ok or str(result.get("play", "")).startswith("失败"):
            fails.append("全双工")
    finally:
        cam.cleanup()

    print("\n" + ("全部通过 ✓" if not fails else f"有问题: {fails}"), flush=True)


if __name__ == "__main__":
    main()
