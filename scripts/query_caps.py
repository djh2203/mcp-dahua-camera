# coding=utf-8
"""查询设备能力: PTZ 云台能力集 + 智能分析能力集"""
import ctypes
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import load_config
from camera import Camera
from NetSDK.SDK_Enum import CFG_CAP_CMD_TYPE
from NetSDK.SDK_Struct import CFG_PTZ_PROTOCOL_CAPS_INFO

cfg = load_config()
cam = Camera(cfg["camera"]["ip"], cfg["camera"]["port"], cfg["camera"]["username"], cfg["camera"]["password"], cfg["camera"]["channel"])
cam.login()

print("===== PTZ 云台能力集 =====")
caps = CFG_PTZ_PROTOCOL_CAPS_INFO()
caps.nStructSize = ctypes.sizeof(CFG_PTZ_PROTOCOL_CAPS_INFO)
buf = ctypes.create_string_buffer(ctypes.sizeof(caps), ctypes.sizeof(caps))
err = ctypes.c_int(0)
cmd = ctypes.c_char_p(b"ptz.getCurrentProtocolCaps")
ret = cam.sdk.sdk.CLIENT_QueryNewSystemInfo(
    ctypes.c_void_p(cam.loginID), cmd, cam.channel, buf,
    ctypes.sizeof(caps), ctypes.byref(err), 3000,
)
if ret:
    ctypes.memmove(ctypes.addressof(caps), buf, ctypes.sizeof(caps))
    fields = [
        ("bPan", "水平旋转"),
        ("bTile", "垂直俯仰"),
        ("bZoom", "变倍"),
        ("bIris", "光圈调节"),
        ("bPreset", "预置点"),
        ("bRemovePreset", "删除预置点"),
        ("bTour", "自动巡航"),
        ("bRemoveTour", "清除巡航"),
        ("bPattern", "轨迹录制"),
        ("bAutoPan", "自动水平扫描"),
        ("bAutoScan", "自动扫描"),
        ("bAux", "辅助功能"),
        ("bAlarm", "报警功能"),
        ("bLight", "灯光"),
        ("bWiper", "雨刷"),
        ("bFlip", "镜头翻转"),
        ("bMenu", "云台内置菜单"),
        ("bMoveRelatively", "相对坐标定位"),
        ("bMoveAbsolutely", "绝对坐标定位"),
        ("bMoveDirectly", "三维坐标定位"),
        ("bReset", "云台复位"),
        ("bGetStatus", "获取方位坐标"),
        ("bSupportLimit", "限位"),
        ("bIsSupportViewRange", "可视域"),
        ("bFocus", "变焦"),
    ]
    for field, name in fields:
        val = getattr(caps, field)
        if isinstance(val, (bool, int)):
            print(f"  {'✓' if val else '✗'} {name}")
    print(f"  预置点范围: {caps.wPresetMin}-{caps.wPresetMax}")
    print(f"  巡航线路: {caps.wTourMin}-{caps.wTourMax}")
    print(f"  轨迹线路: {caps.wPatternMin}-{caps.wPatternMax}")
    print(f"  水平速度: {caps.wPanSpeedMin}-{caps.wPanSpeedMax}, 垂直速度: {caps.wTileSpeedMin}-{caps.wTileSpeedMax}")
    print(f"  协议: {caps.szName.decode(errors='ignore')}, dwType={caps.dwType}")
else:
    print("  查询 PTZ 能力失败")

print("\n===== 智能动检(SmartMotionDetect) =====")
try:
    from NetSDK.SDK_Struct import NET_CFG_SMART_MOTION_DETECT
    from NetSDK.SDK_Enum import NET_EM_CFG_OPERATE_TYPE

    st = NET_CFG_SMART_MOTION_DETECT()
    st.dwSize = ctypes.sizeof(NET_CFG_SMART_MOTION_DETECT)
    ret = cam.sdk.sdk.CLIENT_GetConfig(
        ctypes.c_void_p(cam.loginID),
        NET_EM_CFG_OPERATE_TYPE.SMART_MOTION_DETECT,
        cam.channel,
        ctypes.byref(st),
        ctypes.sizeof(st),
        3000,
    )
    if ret:
        obj = st.stuMotionDetectObject
        print(f"  使能: {'✓' if st.bEnable else '✗'}")
        print(f"  检测对象: 人{'✓' if obj.bHuman else '✗'} 车{'✓' if obj.bVehicle else '✗'} 动物{'✓' if obj.bAnimal else '✗'}")
        print(f"  智能跟踪: {'✓' if st.bSmartTrack else '✗'}")
        print(f"  检测区域数: {st.nDetectRegionsNum}")
    else:
        print("  查询智能动检失败(设备可能不支持)")
except Exception as e:
    print(f"  查询智能动检异常: {e}")

cam.cleanup()