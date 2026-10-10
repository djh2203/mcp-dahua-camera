import sys
import time
import ctypes

sys.path.insert(0, ".")
from config import resolve_camera
from camera import Camera
from NetSDK.SDK_Struct import (
    NET_IN_START_TALK_INFO,
    NET_OUT_START_TALK_INFO,
    NET_AUDIO_DATA_CB_INFO,
)
from NetSDK.SDK_Struct import C_LLONG, C_ENUM, C_LDWORD

TALK_CB = ctypes.CFUNCTYPE(
    None, C_LLONG, ctypes.POINTER(NET_AUDIO_DATA_CB_INFO), C_ENUM, C_LDWORD
)

_c = resolve_camera()
cam = Camera(_c["ip"], _c["port"], _c["username"], _c["password"], _c["channel"])
cam.login()
print("logged in", flush=True)

received = [0]
total = [0]

SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 8
t_start = [0.0]
per_second = [0] * (SECONDS + 2)   # 每秒收到的字节, 用来看"出声那一下有没有打上去"

@TALK_CB
def audio_cb(lTalkHandle, pInfo, emAudioCode, dwUser):
    if not pInfo:
        return
    info = pInfo.contents
    n = info.dwRawBufSize
    received[0] += 1
    total[0] += n
    i = int(time.time() - t_start[0])
    if 0 <= i < len(per_second):
        per_second[i] += n
    if received[0] <= 3:
        print(
            f"  回调#{received[0]}: code={emAudioCode} rate={info.dwSampleRate} "
            f"bit={info.nAudioBit} rawSize={n}",
            flush=True,
        )


in_param = NET_IN_START_TALK_INFO()
in_param.dwSize = ctypes.sizeof(in_param)
in_param.pfAudioDataCallBackEx = audio_cb
in_param.dwUser = 0

out_param = NET_OUT_START_TALK_INFO()
out_param.dwSize = ctypes.sizeof(out_param)

handle = cam.sdk.StartTalkByDataType(cam.loginID, in_param, out_param, 3000)
print("StartTalkByDataType handle:", handle, flush=True)
if not handle:
    print("错误码:", cam.sdk.GetLastError(), "-", cam.sdk.GetLastErrorMessage(), flush=True)
    cam.cleanup()
    sys.exit(1)

print(f"监听 {SECONDS} 秒, 请对摄像头大声说话/拍手(每秒字节数会打出来)...", flush=True)
t_start[0] = time.time()
time.sleep(SECONDS)

cam.sdk.StopTalkEx(handle)
print(f"\n收到 {received[0]} 次回调, 共 {total[0]} 字节裸音频", flush=True)
print("每秒字节数 (16kHz/16bit 实时=32000 B/s):", flush=True)
for i, n in enumerate(per_second[:SECONDS]):
    bar = "#" * min(60, n // 400)
    print(f"  {i+1:2d}s {n:7d}  {bar}", flush=True)
if total[0] > 0:
    print("结论: 设备麦克风可采集!", flush=True)
else:
    print("结论: 未采到音频数据", flush=True)

cam.cleanup()
print("done", flush=True)