# coding=utf-8
"""MCP server: 把大华/Imou 摄像头控制能力暴露为 MCP tools (纯设备能力, 无 AI 依赖).

任何支持 MCP 的 agent(Claude Desktop / Cursor / opencode / AI 助手仓库)都可以直接接入:
云台移动/绝对定位/预置点/抓图/采集麦克风/扬声器播放音频文件。

工具(13 个):
  search_devices, select_camera, list_cameras,
  camera_status, camera_info, ptz_move, ptz_stop, ptz_abs_move,
  ptz_get_position, preset, capture, record_audio, play_audio
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
)

from config import load_config
from camera import Camera, CameraError

cfg = load_config()
MEDIA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media")
os.makedirs(MEDIA_DIR, exist_ok=True)

# ---------- 多摄像头状态管理 ----------
_cameras = {}       # IP -> Camera 实例
_active_ip = None   # 当前激活的摄像头 IP


def _get_cam():
    """获取当前激活的摄像头, 未选择则报错."""
    global _active_ip
    if _active_ip is None or _active_ip not in _cameras:
        raise CameraError("未选择摄像头, 请先调用 select_camera")
    cam = _cameras[_active_ip]
    if not cam.online:
        raise CameraError(f"摄像头 {_active_ip} 已离线")
    return cam


def _add_camera(ip, port, username, password, channel=0):
    """创建并登录摄像头, 存入 _cameras, 设为激活."""
    global _active_ip
    if ip in _cameras:
        cam = _cameras[ip]
        if cam.online:
            _active_ip = ip
            return cam
        # 离线了, 重新连接
        try:
            cam.logout()
        except Exception:
            pass
        del _cameras[ip]

    cam = Camera(ip, port, username, password, channel)
    cam.login()
    _cameras[ip] = cam
    _active_ip = ip
    return cam


def _ok(**kw):
    return {"ok": True, **kw}


def _fail(e):
    return {"ok": False, "error": str(e)}


def _result(obj) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(obj, ensure_ascii=False))])


# ---------- 设备管理工具 ----------
def t_search_devices(args):
    timeout = float(args.get("timeout", 8))
    try:
        devices = Camera.search_devices(timeout=timeout)
        return _ok(devices=devices, count=len(devices))
    except Exception as e:
        return _fail(e)


def t_select_camera(args):
    ip = args.get("ip")
    if not ip:
        return _fail("缺少 ip 参数")
    port = int(args.get("port", cfg["camera"]["port"]))
    username = args.get("username", cfg["camera"]["username"])
    password = args.get("password", cfg["camera"]["password"])
    channel = int(args.get("channel", cfg["camera"]["channel"]))
    try:
        cam = _add_camera(ip, port, username, password, channel)
        info = {"ip": cam.ip, "online": cam.online}
        if cam.device_info:
            info["serial"] = cam.device_info.sSerialNumber.decode(errors="ignore")
            info["channel_num"] = cam.device_info.nChanNum
        try:
            ver = cam.get_device_version()
            info["model"] = ver["detail_type"] or ver["device_type"]
        except Exception:
            pass
        return _ok(**info)
    except CameraError as e:
        return _fail(e)


def t_list_cameras(args):
    result = []
    for ip, cam in _cameras.items():
        item = {
            "ip": ip,
            "online": cam.online,
            "active": ip == _active_ip,
        }
        if cam.device_info:
            item["serial"] = cam.device_info.sSerialNumber.decode(errors="ignore")
        try:
            ver = cam.get_device_version()
            item["model"] = ver["detail_type"] or ver["device_type"]
        except Exception:
            pass
        result.append(item)
    return _ok(cameras=result, active_ip=_active_ip)


# ---------- 设备控制工具 ----------
def t_camera_status(args):
    cam = _get_cam()
    return _ok(online=cam.online, ip=cam.ip)


def t_camera_info(args):
    cam = _get_cam()
    dev = cam.device_info
    if not dev:
        return _fail("未登录")

    info = {
        "ok": True,
        "ip": cam.ip,
        "channel_num": dev.nChanNum,
        "serial": dev.sSerialNumber.decode(errors="ignore"),
        "device_type": dev.nDVRType,
    }

    try:
        ver = cam.get_device_version()
        info.update({
            "model": ver["detail_type"] or ver["device_type"],
            "software_version": ver["software_version"],
            "hardware_version": ver["hardware_version"],
        })
    except Exception:
        pass

    try:
        presets = cam.get_ptz_capabilities()
        info["ptz_preset_used"] = presets
        info["ptz_max_preset"] = max(presets) if presets else 0
    except Exception:
        pass

    try:
        track = cam.get_smart_track()
        info["smart_track_supported"] = track["supported"]
        info["smart_track_enabled"] = track["enabled"]
        info["motion_detect_enabled"] = track.get("motion_detect_enabled", False)
        info["tracking_zoom"] = track.get("tracking_zoom", False)
    except Exception:
        info["smart_track_supported"] = False
        info["smart_track_enabled"] = False

    return info


def t_ptz_move(args):
    action = args.get("action")
    speed = int(args.get("speed", 5))
    seconds = float(args.get("seconds", 0.5))
    try:
        _get_cam().ptz_move(action, speed=speed, seconds=seconds)
        return _ok(action=action, speed=speed, seconds=seconds)
    except CameraError as e:
        return _fail(e)


def t_ptz_stop(args):
    try:
        _get_cam().ptz_stop()
        return _ok(message="已停止")
    except CameraError as e:
        return _fail(e)


def t_ptz_abs_move(args):
    pan_deg = float(args.get("pan_deg", 0))
    tilt_deg = args.get("tilt_deg")
    try:
        _get_cam().ptz_abs_move(pan_deg, tilt_deg)
        return _ok(pan_deg=pan_deg, tilt_deg=tilt_deg)
    except CameraError as e:
        return _fail(e)


def t_ptz_get_position(args):
    try:
        pos = _get_cam().ptz_get_position()
        return _ok(position=pos)
    except CameraError as e:
        return _fail(e)


def t_preset(args):
    op = args.get("op")
    index = int(args.get("index", 1))
    try:
        _get_cam().ptz_preset(op, index)
        return _ok(op=op, index=index)
    except CameraError as e:
        return _fail(e)


def t_capture(args):
    try:
        frame = _get_cam().capture()
        fname = f"cap_{int(time.time()*1000)}.jpg"
        fpath = os.path.join(MEDIA_DIR, fname)
        with open(fpath, "wb") as f:
            f.write(frame)
        return _ok(path=fpath, size=len(frame))
    except CameraError as e:
        return _fail(e)


def t_record_audio(args):
    """采集摄像头麦克风 PCM, 保存为 wav 文件并返回路径/采样率"""
    seconds = float(args.get("seconds", 5))
    try:
        pcm, rate, bit = _get_cam().record_audio(seconds)
        import wave

        fd, fpath = tempfile.mkstemp(suffix=".wav", prefix="rec_", dir=MEDIA_DIR)
        os.close(fd)
        with wave.open(fpath, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(bit // 8)
            w.setframerate(rate)
            w.writeframes(pcm)
        return _ok(path=fpath, sample_rate=rate, audio_bit=bit, audio_seconds=round(len(pcm) / 2 / rate, 2))
    except CameraError as e:
        return _fail(e)


def t_play_audio(args):
    """把音频文件通过摄像头扬声器播放. path: wav/mp3 等音频文件路径; codec 默认 mulaw"""
    path = args.get("path", "")
    codec = args.get("codec", "mulaw")
    if not path:
        return _fail("缺少 path")
    if not os.path.exists(path):
        return _fail(f"文件不存在: {path}")
    try:
        _get_cam().speak_file(path, codec=codec)
        return _ok(path=path, message="已播放")
    except CameraError as e:
        return _fail(e)


# ---------- 工具元数据 ----------
TOOLS = [
    Tool(
        name="search_devices",
        description="局域网搜索大华/Imou 摄像头, 返回设备列表",
        inputSchema={
            "type": "object",
            "properties": {
                "timeout": {"type": "number", "description": "搜索超时(秒), 默认8", "minimum": 3, "maximum": 30},
            },
            "required": [],
        },
    ),
    Tool(
        name="select_camera",
        description="选择要控制的摄像头(需提供IP和凭据)",
        inputSchema={
            "type": "object",
            "properties": {
                "ip": {"type": "string", "description": "摄像头IP地址"},
                "port": {"type": "integer", "description": "端口, 默认37777"},
                "username": {"type": "string", "description": "用户名"},
                "password": {"type": "string", "description": "密码"},
                "channel": {"type": "integer", "description": "通道号, 默认0"},
            },
            "required": ["ip"],
        },
    ),
    Tool(
        name="list_cameras",
        description="列出已发现/已连接的摄像头",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="camera_status",
        description="查询当前选中摄像头的在线状态",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="camera_info",
        description="查询当前选中摄像头的详细信息(型号/序列号/版本/智能跟踪等)",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="ptz_move",
        description="控制云台移动。action: up/down/left/right/leftup/leftdown/rightup/rightdown; speed: 1-255; seconds: 移动时长(秒)",
        inputSchema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["up", "down", "left", "right", "leftup", "leftdown", "rightup", "rightdown"]},
                "speed": {"type": "integer", "minimum": 1, "maximum": 255},
                "seconds": {"type": "number", "minimum": 0.2, "maximum": 10},
            },
            "required": ["action"],
        },
    ),
    Tool(
        name="ptz_stop",
        description="立即停止云台所有移动",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="ptz_abs_move",
        description="绝对定位水平角(pan_deg): 0=正面/90=左/180=背面/270=右。tilt_deg 可选",
        inputSchema={
            "type": "object",
            "properties": {
                "pan_deg": {"type": "number", "minimum": 0, "maximum": 360},
                "tilt_deg": {"type": "number", "minimum": 0, "maximum": 360},
            },
            "required": ["pan_deg"],
        },
    ),
    Tool(
        name="ptz_get_position",
        description="读取云台当前水平/垂直角度",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="preset",
        description="操作云台预置点。op: set/goto/del; index: 预置点序号",
        inputSchema={
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["set", "goto", "del"]},
                "index": {"type": "integer", "minimum": 1},
            },
            "required": ["op", "index"],
        },
    ),
    Tool(
        name="capture",
        description="抓取一帧画面, 返回保存路径",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="record_audio",
        description="采集摄像头麦克风音频, 保存为 wav 文件并返回路径/采样率。seconds: 录音时长(默认5)",
        inputSchema={
            "type": "object",
            "properties": {"seconds": {"type": "number", "minimum": 1, "maximum": 30, "default": 5}},
            "required": [],
        },
    ),
    Tool(
        name="play_audio",
        description="把音频文件通过摄像头扬声器播放。path: 音频文件路径(如 wav/mp3/g711 文件)",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "音频文件路径"},
                "codec": {"type": "string", "description": "音频编码, 默认 mulaw"},
            },
            "required": ["path"],
        },
    ),
]

HANDLERS = {
    "search_devices": t_search_devices,
    "select_camera": t_select_camera,
    "list_cameras": t_list_cameras,
    "camera_status": t_camera_status,
    "camera_info": t_camera_info,
    "ptz_move": t_ptz_move,
    "ptz_stop": t_ptz_stop,
    "ptz_abs_move": t_ptz_abs_move,
    "ptz_get_position": t_ptz_get_position,
    "preset": t_preset,
    "capture": t_capture,
    "record_audio": t_record_audio,
    "play_audio": t_play_audio,
}


async def _list_tools(ctx, params: PaginatedRequestParams | None):
    return ListToolsResult(tools=TOOLS)


async def _call_tool(ctx, params):
    import anyio

    name = params.name
    args = params.arguments or {}
    handler = HANDLERS.get(name)
    if handler is None:
        return _result(_fail(f"未知工具: {name}"))
    try:
        result = await anyio.to_thread.run_sync(handler, args)
    except Exception as e:
        result = _fail(e)
    return _result(result)


def main():
    import anyio

    server = Server("mcp-dahua-camera", on_list_tools=_list_tools, on_call_tool=_call_tool)

    async def _run():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(_run)


if __name__ == "__main__":
    main()
