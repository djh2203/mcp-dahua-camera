# AGENTS.md — mcp-dahua-camera

纯设备层 MCP server:把大华/Imou 摄像头(Netsdk)暴露成 MCP 工具,无任何 AI 依赖。AI 能力在兄弟仓库 `cam-agent` 里,本仓库只负责设备控制。

## 关键命令

```bash
uv run python -m mcp_server     # 启动 MCP server (stdio 协议) — 唯一入口
uv run python scripts/test_ptz.py        # 云台测试
uv run python scripts/search_devices.py  # 局域网搜设备
uv run python scripts/test_mic.py        # 麦克风测试
uv run python scripts/diag_talk.py --file <g711> --sweep   # 扬声器播放节奏诊断(卡顿排查)
```

- `pyproject.toml` 是 `package = false`,**没有 console script**,只能用 `python -m mcp_server` 启动;且必须在**仓库目录下**跑(`-m` 靠 cwd 找模块)。从别处启动用 `uv run --directory <本仓库> python -m mcp_server`——`--project` 只切依赖不切 cwd,配错会报 `No module named mcp_server`。
- 依赖 `netsdk` 从 `vendor/NetSDK-*.whl` 本地安装(`[tool.uv.sources]` 指向 vendor wheel),`uv sync` 即可,无需联网。
- 设备控制需要真机,`scripts/` 下脚本直接连摄像头。仓库内无单元测试。

## 与 cam-agent 的关系

`cam-agent/cam_agent/mcp_client.py` 通过 stdio 启动本仓库:`uv run --project <本仓库路径> python -m mcp_server`(cwd 设为本仓库)。**摄像头凭据只配在本仓库**:`config.ini` 存默认凭据,`cameras.json` 存每台已连接设备的凭据,两者都 gitignored。

## 配置

- `config.ini`([camera] port/username/password/channel,ip 可选)= **没在 `cameras.json` 里记录过的新设备**的默认凭据,也是脚本没指定 IP 时的兜底。
- 环境变量可覆盖:`CAM_IP` / `CAM_PORT` / `CAM_USER` / `CAM_PASS` / `CAM_CHANNEL`。
- 设备解析统一走 `config.resolve_camera(ip=None)`:cameras.json 已保存的凭据优先 → config.ini 默认值 → 都没有就报错(**不猜地址**)。`mcp_server.py` 和各 `scripts/*.py` 都用它,别再各写一份。
- 当前 `cameras.json` 里是 IMOU `192.168.10.56`(16kHz 麦克风);`scripts/*.py` 不带参数时用它。

## 设备/协议坑(重要)

- 摄像头麦克风**静音时也持续回传小数据块**,无法用"数据有无/能量"判断说话结束,`record_audio` 只能按固定时长(`time.sleep(seconds)`)采集(见 `camera.py` record_audio)。安静房间里上行被**静音抑制/VAD 门控**,3 秒只回传约 0.2-0.3 秒有效音频属正常;检测麦克风是否真通,用 `scripts/test_mic.py` 对着设备出声测。
- **扬声器播放卡顿的根因**:`NET_IN_TALK_SEND_DATA_FILE.dwSendInterval` 是"默认发送间隔的**百分比**"(0/100=SDK 默认, <100 更快, >100 更慢),**不是毫秒**。实测本设备默认(=100)只有 4238 B/s,而 8kHz G.711 实时播放需要 8000 B/s,喂不饱设备就会断句/卡顿;历史上代码传的 60 也只有 7094 B/s(89%),照样卡。现在 `camera.py` 用 `_SEND_INTERVAL_PERCENT = 30`(实测 ~1.75 倍实时)。换设备/换采样率后重测:
  `uv run python scripts/diag_talk.py --file <g711裸音频> --sweep`
- `TalkSendDataByFile` 是**异步**的:必须靠 `cbSendPos` 回调判断真的发完,提前 `StopTalkSendDataByFile` 会把话截断。`speak_file` 现在等回调报满,再补上剩余播放时长才停。
- 对讲通道被占用报 `talk has opened by other client` 时会重试,最长 120 秒后报错。其中"是否重启摄像头"的 `input()` 只在**终端**(`sys.stdin.isatty()`)才弹——MCP stdio 下 stdin 是协议管道,弹了会挂起并污染协议,所以被挡掉了。想重启设备:先 `StopTalkEx` 释放通道,或人工调用 `Camera.reboot()`(`RebootDev` 报 `Protocol error` 属正常,设备实际已重启)。
- `play_audio` 的 `path` 必须是**绝对路径**:MCP server 的 cwd 是本仓库,客户端进程的 cwd 可能是 cam-agent,相对路径会找不到文件。
- 抓图/录音返回**文件路径**而非二进制:Agent 端拿路径自己做识别。产物在 `media/`(gitignored)。
- 部分能力不同设备差异大(采样率、云台精度、`ptz_get_position` 在本 IMOU 上报 `There is no such error code`),改代码要拿真机验证。

## 工具清单(13 个)

设备管理:`search_devices`, `select_camera`, `list_cameras`

设备控制:`camera_status`, `camera_info`(型号/版本/预置点/智能跟踪), `ptz_move`(up/down/left/right/对角, speed 1-255, seconds 0.2-10), `ptz_stop`, `ptz_abs_move`(pan_deg 0=正面/90=左/180=背/270=右, tilt 被忽略), `ptz_get_position`, `preset`(set/goto/del), `capture`, `record_audio`(seconds 1-30), `play_audio`(path + codec 默认 mulaw)

## 凭据本地保存

`select_camera` 成功连接后,凭据自动保存到 `cameras.json`(JSON,gitignored)。再次 `select_camera` 时只需传 `ip`。该文件由 `config.py` 读写(`load_credentials`/`save_credential`),删掉不影响运行(会退回用 `config.ini` 的默认凭据)。
