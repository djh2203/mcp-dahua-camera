# mcp-dahua-camera

把大华/Imou 网络摄像头(私有协议 37777)变成标准的 **MCP Server**,暴露纯设备能力,无任何 AI 依赖。

适用于任何支持 MCP 的客户端(Claude Desktop / Cursor / opencode / 自建 AI 助手)。

## 工具

| 工具 | 说明 |
|------|------|
| `camera_status` | 摄像头在线状态 |
| `camera_info` | 设备信息(型号/序列号/通道数/类型/软件版本/硬件版本/预置点) |
| `ptz_move` | 云台方向移动(上下左右/对角), speed 1-255, seconds 移动时长 |
| `ptz_stop` | 停止云台移动 |
| `ptz_abs_move` | 绝对定位(水平角 pan_deg) |
| `ptz_get_position` | 读取云台角度 |
| `preset` | 预置点 set/goto/del |
| `capture` | 抓一帧画面保存到 media/ |
| `record_audio` | 采集麦克风音频保存为 wav, 返回路径/采样率 |
| `play_audio` | 播放音频文件到摄像头扬声器 |

> 语音识别(ASR)、文字转语音(TTS)、看图(视觉)等 AI 能力不在本仓库, 由 AI 助手仓库(cam-agent)通过本 MCP server 组合实现。

## 快速开始

```bash
uv sync
cp config.example.ini config.ini   # 填入摄像头 ip/账号密码
uv run mcp-dahua-camera            # 启动 MCP server (stdio)
```

配置也可用环境变量覆盖: `CAM_IP` / `CAM_PORT` / `CAM_USER` / `CAM_PASS` / `CAM_CHANNEL`

## 作为 MCP client 接入

任意支持 MCP 的客户端, 配置 stdio server:

```jsonc
{
  "mcpServers": {
    "dahua-camera": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/mcp-dahua-camera", "python", "-m", "mcp_server"]
    }
  }
}
```

## 目录

```
camera.py        # 摄像头 SDK 封装(NetSDK)
mcp_server.py    # MCP server (10 个工具)
config.py        # 配置加载(支持环境变量覆盖)
scripts/         # 设备调试脚本(搜设备/测云台/测麦克风/查能力)
vendor/          # NetSDK wheel
media/           # 抓图/录音产物
```