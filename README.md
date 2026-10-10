# mcp-dahua-camera

把大华/Imou 网络摄像头(私有协议 37777)变成标准的 **MCP Server**,暴露纯设备能力,无任何 AI 依赖。

适用于任何支持 MCP 的客户端(Claude Desktop / Cursor / opencode / 自建 AI 助手)。

## 工具

| 工具 | 说明 |
|------|------|
| **设备管理** | |
| `search_devices` | 局域网搜索摄像头(返回 IP/型号/序列号等) |
| `select_camera` | 选择要控制的摄像头(需提供 IP 和凭据) |
| `list_cameras` | 列出已发现/已连接的摄像头 |
| **设备控制** | |
| `camera_status` | 摄像头在线状态 |
| `camera_info` | 设备信息(型号/序列号/版本/智能跟踪等) |
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
cp config.example.ini config.ini   # 填入默认凭据(端口/用户名/密码)
uv run python -m mcp_server        # 启动 MCP server (stdio) — 需在本仓库目录下运行
```

配置也可用环境变量覆盖: `CAM_IP` / `CAM_PORT` / `CAM_USER` / `CAM_PASS` / `CAM_CHANNEL`

`config.ini` 只是**新设备的默认凭据**;`select_camera` 连上后该设备凭据会存进 `cameras.json`(gitignored),之后切换只传 IP 即可。

## 作为 MCP client 接入

任意支持 MCP 的客户端, 配置 stdio server:

```jsonc
{
  "mcpServers": {
    "dahua-camera": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-dahua-camera", "python", "-m", "mcp_server"]
    }
  }
}
```

> 用 `--directory` 而不是 `--project`:后者只切依赖环境、不切工作目录,而 `python -m mcp_server` 要靠 cwd 找到模块,配错会报 `No module named mcp_server`。

## 目录

```
camera.py        # 摄像头 SDK 封装(NetSDK) + 设备搜索
mcp_server.py    # MCP server (13 个工具)
config.py        # 配置加载(支持环境变量覆盖)
cameras.json     # 已连接设备的凭据(自动生成, gitignored)
scripts/         # 设备调试脚本(搜设备/测云台/测麦克风/查能力)
vendor/          # NetSDK wheel
media/           # 抓图/录音产物
```
