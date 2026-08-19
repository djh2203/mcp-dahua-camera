# coding=utf-8
import configparser
import os
import sys
from pathlib import Path

DEFAULT_CONFIG = {
    "camera": {
        "ip": "192.168.1.108",
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
            Path.home() / ".camgate" / "config.ini",
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