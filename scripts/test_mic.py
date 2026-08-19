import sys
import time
import ctypes

sys.path.insert(0, ".")
from config import load_config
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

cfg = load_config()
cam = Camera(
    cfg["camera"]["ip"],
    cfg["camera"]["port"],
    cfg["camera"]["username"],
    cfg["camera"]["password"],
    cfg["camera"]["channel"],
)
cam.login()
print("logged in", flush=True)

received = [0]
total = [0]

@TALK_CB
def audio_cb(lTalkHandle, pInfo, emAudioCode, dwUser):
    if not pInfo:
        return
    info = pInfo.contents
    n = info.dwRawBufSize
    received[0] += 1
    total[0] += n
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

print("监听 8 秒, 请对摄像头说话/拍手...", flush=True)
time.sleep(8)

cam.sdk.StopTalkEx(handle)
print(f"\n收到 {received[0]} 次回调, 共 {total[0]} 字节裸音频", flush=True)
if total[0] > 0:
    print("结论: 设备麦克风可采集!", flush=True)
else:
    print("结论: 未采到音频数据", flush=True)

cam.cleanup()
print("done", flush=True)