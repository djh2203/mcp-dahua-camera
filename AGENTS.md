# AGENTS.md — mcp-dahua-camera

纯设备层 MCP server:把大华/Imou 摄像头(Netsdk)暴露成 MCP 工具,无任何 AI 依赖。AI 能力在兄弟仓库 `cam-agent` 里,本仓库只负责设备控制。

## 关键命令

```bash
uv run python -m mcp_server     # 启动 MCP server (stdio 协议) — 唯一入口
uv run python scripts/test_ptz.py        # 云台测试
uv run python scripts/search_devices.py  # 局域网搜设备
uv run python scripts/test_mic.py        # 麦克风测试
uv run python scripts/diag_talk.py --file <16k g711> --rate 16000 --interval 40   # 看发送节奏
uv run python scripts/diag_talk.py --file <16k g711> --rate 16000 --interval 40 --move   # 播放时转云台对照
uv run python scripts/query_caps.py      # 查设备能力(PTZ/智能动检/扬声器)
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
- **扬声器播放卡顿: 两个我们能修的根因 + 一个修不了的**(都实测过, 用 `scripts/diag_talk.py` 复查):
  1. **采样率是第一根因**: 本设备按 **16kHz** 消费音频。喂 8kHz 内容 = 只有一半数据量, 设备持续饿死 → 断句/卡顿, 而且**怎么调发送速率都没用**(实测 8kHz 时 89% 和 175% 实时都还卡)。`speak_file` 默认 `sample_rate=16000`, 调用方必须按内容如实声明(cam-agent 的 TTS 已改产出 16kHz)。判别方法: 同一段语音分别按 8k/16k 声明播一遍, 正常语速且流畅的那个才是对的。
  2. **发送间隔**: `NET_IN_TALK_SEND_DATA_FILE.dwSendInterval` 是"默认发送间隔的**百分比**"(0/100=SDK 默认, <100 更快, >100 更慢), **不是毫秒**。对讲是实时通道 + 设备端小缓冲: 太慢饿死断句(60 = 0.89 倍实时, 实测丢字), 太快则先到的被覆盖(20 = 2.7 倍实时, 实测只能听到句子后半段)。现用 `_SEND_INTERVAL_PERCENT = 40`(1.34 倍实时)。重标: `uv run python scripts/diag_talk.py --file <16k g711> --rate 16000 --sweep`
- **剩下的卡顿不是我们能修的**: 同一配置连播 5 遍实测 **1 遍略卡 / 3 遍很卡 / 1 遍很顺**, 而 5 遍的发送节奏逐 0.5 秒完全一致(21.8 KB/s ≈ 1.36 倍实时); 甚至播放期间让云台不停转动, 发送节奏也一模一样(`--move` 对照)。结论: **数据是均匀送到的, 是设备自己在视频/跟踪负载下播放抖动**, 与摄像头转动、画面变化强相关。判据脚本:
  `uv run python scripts/diag_talk.py --file <16k g711> --rate 16000 --interval 40`(看"发送节奏")+ 加 `--move` 复现"一动就断音"。听懂感必须**多测几遍**(单遍结论会被随机性骗)。
- 试过但此型号不支持的"根治"方向: `speak.getCaps` 查询失败(设备不支持扬声器音频文件播放能力), 所以**没法把音频传进设备让它自己播**; `CLIENT_IpSpeaker*`/`TalkChannel`/上传接口在 Python 绑定里也都没有, 硬要用得盲猜 ctypes 结构体, 风险太高。
- 未验证的下一步(按性价比): ①减少设备负载 —— 关掉自动跟踪、关掉手机上正在看的实时画面、降视频码率, 再重跑上面的 5 遍测试; ②试更省设备资源的编码(`EM_A_TALK_CODING_TYPE` 里还有 AMR/G726/AAC/G722.1, 但设备是否支持未知, 只能用听感验证)。
- 编码与音频头: 本设备**只有 `mulaw`(G.711u)+ `bNeedHead=1` 出声**; 实测 `alaw` 和不加音频头都是**静音**。
- `TalkSendDataByFile` 是**异步**的:必须靠 `cbSendPos` 回调判断真的发完,提前 `StopTalkSendDataByFile` 会把话截断。`speak_file` 现在等回调报满,再补上剩余播放时长才停。
- 对讲通道被占用报 `talk has opened by other client` 时会重试,最长 120 秒后报错。其中"是否重启摄像头"的 `input()` 只在**终端**(`sys.stdin.isatty()`)才弹——MCP stdio 下 stdin 是协议管道,弹了会挂起并污染协议,所以被挡掉了。想重启设备:先 `StopTalkEx` 释放通道,或人工调用 `Camera.reboot()`(`RebootDev` 报 `Protocol error` 属正常,设备实际已重启)。
- `play_audio` 的 `path` 必须是**绝对路径**:MCP server 的 cwd 是本仓库,客户端进程的 cwd 可能是 cam-agent,相对路径会找不到文件。
- 抓图/录音返回**文件路径**而非二进制:Agent 端拿路径自己做识别。产物在 `media/`(gitignored)。
- 部分能力不同设备差异大(采样率、云台精度、`ptz_get_position` 在本 IMOU 上报 `There is no such error code`),改代码要拿真机验证。

## 工具清单(13 个)

设备管理:`search_devices`, `select_camera`, `list_cameras`

设备控制:`camera_status`, `camera_info`(型号/版本/预置点/智能跟踪), `ptz_move`(up/down/left/right/对角, speed 1-255, seconds 0.2-10), `ptz_stop`, `ptz_abs_move`(pan_deg 0=正面/90=左/180=背/270=右, tilt 被忽略), `ptz_get_position`, `preset`(set/goto/del), `capture`, `record_audio`(seconds 1-30), `play_audio`(绝对 path + codec 默认 mulaw + sample_rate 默认 16000)

## 凭据本地保存

`select_camera` 成功连接后,凭据自动保存到 `cameras.json`(JSON,gitignored)。再次 `select_camera` 时只需传 `ip`。该文件由 `config.py` 读写(`load_credentials`/`save_credential`),删掉不影响运行(会退回用 `config.ini` 的默认凭据)。
