# coding=utf-8
import socket
import sys
import time
from ctypes import *

sys.path.insert(0, ".")

from NetSDK.NetSDK import NetClient
from NetSDK.SDK_Enum import EM_SEND_SEARCH_TYPE
from NetSDK.SDK_Struct import (
    NET_IN_STARTSERACH_DEVICE,
    NET_OUT_STARTSERACH_DEVICE,
    DEVICE_NET_INFO_EX2,
    C_LLONG,
)
from NetSDK.SDK_Callback import CB_FUNCTYPE


def get_local_ips():
    """获取本机所有IPv4地址"""
    ips = set()
    try:
        hostname = socket.gethostname()
        for info in socket.gethostbyname_ex(hostname):
            if isinstance(info, list):
                for ip in info:
                    if ":" not in ip:
                        ips.add(ip)
    except Exception:
        pass
    for iface, addrs in socket.if_nameindex():
        pass
    try:
        import subprocess

        out = subprocess.check_output(["hostname", "-I"], text=True)
        for ip in out.split():
            if ":" not in ip:
                ips.add(ip)
    except Exception:
        pass
    return list(ips)


found = []


@CB_FUNCTYPE(None, C_LLONG, POINTER(DEVICE_NET_INFO_EX2), c_void_p)
def search_cb(lSearchHandle, pDevNetInfo, pUserData):
    try:
        buf = cast(pDevNetInfo, POINTER(DEVICE_NET_INFO_EX2)).contents
        dev = buf.stuDevInfo
        if dev.iIPVersion == 4:
            info = {
                "ip": dev.szIP.decode(errors="ignore"),
                "port": dev.nPort,
                "http_port": dev.nHttpPort,
                "mac": dev.szMac.decode(errors="ignore"),
                "type": dev.szDeviceType.decode(errors="ignore"),
                "detail": dev.szDetailType.decode(errors="ignore"),
                "mask": dev.szSubmask.decode(errors="ignore"),
                "gateway": dev.szGateway.decode(errors="ignore"),
                "init_status": dev.byInitStatus,
                "local_ip": buf.szLocalIP.decode(errors="ignore"),
            }
            found.append(info)
    except Exception as e:
        print("callback error:", e)


def main():
    sdk = NetClient()
    sdk.InitEx(None, 0)

    local_ips = get_local_ips()
    print("本机IP:", local_ips)

    if not local_ips:
        print("未找到本机IP, 无法搜索")
        return

    handles = []
    for lip in local_ips:
        start_in = NET_IN_STARTSERACH_DEVICE()
        start_in.dwSize = sizeof(NET_IN_STARTSERACH_DEVICE)
        start_in.emSendType = EM_SEND_SEARCH_TYPE.MULTICAST_AND_BROADCAST
        start_in.cbSearchDevices = search_cb
        start_in.szLocalIp = lip.encode()
        start_out = NET_OUT_STARTSERACH_DEVICE()
        start_out.dwSize = sizeof(NET_OUT_STARTSERACH_DEVICE)
        handle = sdk.StartSearchDevicesEx(start_in, start_out)
        if handle != 0:
            handles.append(handle)
            print(f"在 {lip} 上发起搜索...")
        else:
            print(f"{lip} 搜索失败: {sdk.GetLastErrorMessage()}")

    print("搜索中, 等待8秒...")
    time.sleep(8)

    for h in handles:
        sdk.StopSearchDevices(h)

    if not found:
        print("未发现大华设备。")
        print("提示: 请确保摄像头与本机在同一局域网, 或用网线直连。")
        return

    print(f"\n发现 {len(found)} 台设备:")
    for i, f in enumerate(found):
        print(f"  [{i}] IP={f['ip']} 端口={f['port']} HTTP={f['http_port']} "
              f"MAC={f['mac']} 类型={f['type']} 详情={f['detail']}")

    sdk.Cleanup()


if __name__ == "__main__":
    main()
