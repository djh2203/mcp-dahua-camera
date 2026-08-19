# coding=utf-8
import os
import sys
import time
from ctypes import *

sys.path.insert(0, ".")

from NetSDK.NetSDK import NetClient
from NetSDK.SDK_Enum import EM_LOGIN_SPAC_CAP_TYPE, SDK_PTZ_ControlType
from NetSDK.SDK_Struct import (
    NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY,
    NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY,
    LOG_SET_PRINT_INFO,
)
from NetSDK.SDK_Callback import fDisConnect, fHaveReConnect


class PtzTest:
    def __init__(self, ip, port, user, pwd, channel=0):
        self.ip = ip
        self.port = port
        self.user = user
        self.pwd = pwd
        self.channel = channel
        self.loginID = 0

        self.cb_disconnect = fDisConnect(self.on_disconnect)
        self.cb_reconnect = fHaveReConnect(self.on_reconnect)
        self.sdk = NetClient()
        self.sdk.InitEx(self.cb_disconnect)
        self.sdk.SetAutoReconnect(self.cb_reconnect)

    def on_disconnect(self, lLoginID, pchDVRIP, nDVRPort, dwUser):
        print(">> 设备断线")

    def on_reconnect(self, lLoginID, pchDVRIP, nDVRPort, dwUser):
        print(">> 设备重连")

    def login(self):
        stuIn = NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY()
        stuIn.dwSize = sizeof(NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY)
        stuIn.szIP = self.ip.encode()
        stuIn.nPort = self.port
        stuIn.szUserName = self.user.encode()
        stuIn.szPassword = self.pwd.encode()
        stuIn.emSpecCap = EM_LOGIN_SPAC_CAP_TYPE.TCP
        stuIn.pCapParam = None

        stuOut = NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY()
        stuOut.dwSize = sizeof(NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY)

        self.loginID, dev, err = self.sdk.LoginWithHighLevelSecurity(stuIn, stuOut)
        if self.loginID:
            print(f"登录成功! 通道数={dev.nChanNum} 序列号={dev.sSerialNumber.decode(errors='ignore')}")
            return True
        print(f"登录失败: {err}")
        return False

    def ptz_start(self, cmd, speed=5):
        """开始云台移动"""
        ret = self.sdk.PTZControlEx2(
            self.loginID, self.channel, cmd, 0, speed, 0, False)
        if not ret:
            print(f"  PTZ启动失败: {self.sdk.GetLastErrorMessage()}")
        return ret

    def ptz_stop(self, cmd, speed=5):
        """停止云台移动"""
        ret = self.sdk.PTZControlEx2(
            self.loginID, self.channel, cmd, 0, speed, 0, True)
        if not ret:
            print(f"  PTZ停止失败: {self.sdk.GetLastErrorMessage()}")
        return ret

    def move(self, cmd, speed=5, seconds=1.0):
        """移动并自动停止"""
        name = cmd.name
        print(f"→ {name} (速度{speed}, {seconds}s)")
        if not self.ptz_start(cmd, speed):
            return
        time.sleep(seconds)
        self.ptz_stop(cmd, speed)

    def logout(self):
        if self.loginID:
            self.sdk.Logout(self.loginID)
            self.loginID = 0


def main():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import load_config

    cfg = load_config()
    ip = cfg["camera"]["ip"]
    port = int(cfg["camera"]["port"])
    user = cfg["camera"]["username"]
    pwd = cfg["camera"]["password"]

    t = PtzTest(ip, port, user, pwd)
    if not t.login():
        t.sdk.Cleanup()
        return

    speed = 5
    cmds = [
        SDK_PTZ_ControlType.UP_CONTROL,
        SDK_PTZ_ControlType.DOWN_CONTROL,
        SDK_PTZ_ControlType.LEFT_CONTROL,
        SDK_PTZ_ControlType.RIGHT_CONTROL,
        SDK_PTZ_ControlType.ZOOM_ADD_CONTROL,
        SDK_PTZ_ControlType.ZOOM_DEC_CONTROL,
    ]
    for c in cmds:
        t.move(c, speed, 0.8)
        time.sleep(0.5)

    t.logout()
    t.sdk.Cleanup()
    print("测试完成")


if __name__ == "__main__":
    main()