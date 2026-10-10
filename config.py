# coding=utf-8
import configparser
import json
import os
import sys
from pathlib import Path

# 已连接设备的凭据(select_camera 成功后自动写入, gitignored)
CREDENTIALS_FILE = Path(__file__).parent / "cameras.json"

DEFAULT_CONFIG = {
    "camera": {
        "port": "37777",
        "username": "admin",
        "password": "admin",
        "channel": "0",
    },
}


def _env(name, default):
    return os.environ.get(name, default)


def load_config(path=None):
    """加载 config.ini, 环境变量可覆盖: CAM_IP, CAM_PORT, CAM_USER, CAM_PASS, CAM_CHANNEL"""
    cfg = configparser.ConfigParser()
    cfg.read_dict(DEFAULT_CONFIG)

    if path is None:
        candidates = [
            Path("config.ini"),
            Path(__file__).parent / "config.ini",
        ]
    else:
        candidates = [Path(path)]

    for cand in candidates:
        if cand.exists():
            cfg.read(cand)
            print(f"加载配置: {cand}", file=sys.stderr)
            break
    else:
        print("未找到 config.ini, 使用默认值", file=sys.stderr)

    overrides = {
        "camera": {
            "ip": ("CAM_IP", "ip"),
            "port": ("CAM_PORT", "port"),
            "username": ("CAM_USER", "username"),
            "password": ("CAM_PASS", "password"),
            "channel": ("CAM_CHANNEL", "channel"),
        },
    }
    for section, fields in overrides.items():
        for field, (envname, key) in fields.items():
            val = _env(envname, None)
            if val is not None:
                cfg.set(section, key, val)

    return cfg


def load_credentials():
    """cameras.json 里的每台设备凭据 {ip: {port,username,password,channel,model}}"""
    try:
        with open(CREDENTIALS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_credential(ip, port, username, password, channel, model=""):
    creds = load_credentials()
    creds[ip] = {
        "port": port,
        "username": username,
        "password": password,
        "channel": channel,
        "model": model,
    }
    with open(CREDENTIALS_FILE, "w") as f:
        json.dump(creds, f, ensure_ascii=False, indent=2)


def resolve_camera(ip=None):
    """解析要操作的摄像头: cameras.json 已保存的凭据优先, 其次 config.ini 的默认值.

    不传 ip 时取 cameras.json 里第一台; 两边都没 ip 就报错, 不去猜一个地址。
    返回 dict(ip/port/username/password/channel)。
    """
    cfg = load_config()
    creds = load_credentials()
    ip = ip or next(iter(creds), None) or cfg["camera"].get("ip")
    if not ip:
        raise ValueError(
            "未指定摄像头 IP: 传入 ip, 或先在 mcp_server 里 select_camera 一次, "
            "或在 config.ini 的 [camera] 填 ip"
        )
    c = creds.get(ip, {})
    return {
        "ip": ip,
        "port": int(c.get("port", cfg["camera"]["port"])),
        "username": c.get("username", cfg["camera"]["username"]),
        "password": c.get("password", cfg["camera"]["password"]),
        "channel": int(c.get("channel", cfg["camera"]["channel"])),
    }
