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
uv run python scripts/diag_mic.py --seconds 6 --file <16k g711>   # 麦克风两条路对比 + 全双工
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

- 麦克风有**两条路**, 别再用错(`record_audio` 的 `source` 参数选, 默认 `stream`):
  1. **`stream`(媒体流音频轨, 推荐, 默认)**: `RealPlayByDataType`(`rType=Realplay_1` 从码流1, `emDataType=FLV_STREAM`, `emAudioType=G711A`) + 攒 N 秒 + ffmpeg 解 PCM16。实测 **6 秒采集 -> 6.14 秒音频(连续)**, 设备实际给 AAC 16kHz, ffmpeg 也能直接吃回调里的大华私有封装(`DHAV`)。**语音识别必须用这条**。代价: 从码流的 h264 白占约 27 KB/s。
  2. **`talk`(对讲通道)**: 有静音抑制/VAD 门控, 实测 6 秒只回传 **0.3 秒**(5%), 只在需要"边听边说"的老逻辑里用。安静房间里"3 秒只有 0.2-0.3 秒"是这条路的天性, 不是故障。
  - 回归检查: `uv run python scripts/diag_mic.py --seconds 6 --file <16k g711>`(两条路对比 + 全双工检查)
  - **全双工可行**: 一边 `stream` 采集、一边 `talk` 播报, 两边都成功(实测采到 6.14 秒 + 播报 ok)—— 所以"边听边说/打断"在设备层是支持的。
  - 健壮性: 停流时最后一帧常被截断, 且开头偶尔混杂字节 -> 先按 `DHAV`/`FLV` magic 截齐, 失败再用 `-err_detect ignore_err` 重试一次。
  - 回调里 `dwDataType` 取值: 0=私有码流, 1=带帧信息, 2=YUV, 3=PCM 音频(还有实测见到的 1005=音频帧); `SetRealDataCallBackEx2` 的 `dwFlag` 可用 `EM_REALDATA_FLAG.PCM_AUDIO_DATA=0x08` 过滤(但本设备**抽不出 PCM**, 只给私有封装, 所以走上面第 1 条)。
- **扬声器播放卡顿: 两个我们能修的根因 + 一个修不了的**(都实测过, 用 `scripts/diag_talk.py` 复查):
  1. **采样率是第一根因**: 本设备按 **16kHz** 消费音频。喂 8kHz 内容 = 只有一半数据量, 设备持续饿死 → 断句/卡顿, 而且**怎么调发送速率都没用**(实测 8kHz 时 89% 和 175% 实时都还卡)。`speak_file` 默认 `sample_rate=16000`, 调用方必须按内容如实声明(cam-agent 的 TTS 已改产出 16kHz)。判别方法: 同一段语音分别按 8k/16k 声明播一遍, 正常语速且流畅的那个才是对的。
  2. **发送间隔**: `NET_IN_TALK_SEND_DATA_FILE.dwSendInterval` 是"默认发送间隔的**百分比**"(0/100=SDK 默认, <100 更快, >100 更慢), **不是毫秒**。对讲是实时通道 + 设备端小缓冲: 太慢饿死断句(60 = 0.89 倍实时, 实测丢字), 太快则先到的被覆盖(20 = 2.7 倍实时, 实测只能听到句子后半段)。现用 `_SEND_INTERVAL_PERCENT = 40`(1.34 倍实时)。重标: `uv run python scripts/diag_talk.py --file <16k g711> --rate 16000 --sweep`
- **剩下的卡顿不是我们能修的**: 同一配置连播 5 遍实测 **1 遍略卡 / 3 遍很卡 / 1 遍很顺**, 而 5 遍的发送节奏逐 0.5 秒完全一致(21.8 KB/s ≈ 1.36 倍实时); 甚至播放期间让云台不停转动, 发送节奏也一模一样(`--move` 对照)。结论: **数据是均匀送到的, 是设备自己在视频/跟踪负载下播放抖动**, 与摄像头转动、画面变化强相关。判据脚本:
  `uv run python scripts/diag_talk.py --file <16k g711> --rate 16000 --interval 40`(看"发送节奏")+ 加 `--move` 复现"一动就断音"。听懂感必须**多测几遍**(单遍结论会被随机性骗)。
- 试过但此型号不支持的"根治"方向: `speak.getCaps` 查询失败(设备不支持扬声器音频文件播放能力), 所以**没法把音频传进设备让它自己播**; `CLIENT_IpSpeaker*`/`TalkChannel`/上传接口在 Python 绑定里也都没有, 硬要用得盲猜 ctypes 结构体, 风险太高。
- **"智能跟踪"在 SDK 侧关不掉(实测, 别再走这条路)**: app 里"画面变化"= `MotionDetect`(开着不影响音频), "智能跟踪" = `SmartMotionDetect.Enable`。但 **SDK 写这个配置在这台设备上不生效**: `SetNewDevConfig` 返回成功、`GetNewDevConfig` 回读不变、**云台行为也不变** —— 判据是让用户站在镜头前乱动, 云台一动不动。曾经据此实现过"播报时暂停跟踪", 已删除(它每句话还白等 0.8 秒)。跟踪由 IMOU app/云驱动, app 对讲时能暂停它是因为那是 app/云自己的行为, 不是设备固件能力。
- **实测的因果关系(以 app 开关为准的 A/B)**: app 里跟踪**关** → 20 秒采样 **0%** 在动、5 遍播报**全顺**; 跟踪**开** → **50%~69%** 在动、5 遍里 3 遍很卡。跟踪**只在画面里出现可跟踪目标时才动**, 所以单次"在不在动"采样毫无意义(同一状态出现过 50% / 69% / 38% / 0%), 判定必须多轮且确认当时有目标 —— 用 `scripts/check_motion.py`(连续两帧 PSNR: >30dB 静止 / <20dB 正在转)。
- `speak_file` 播报前会发一次 `STOP_MOVE`(=139, 停移动+停巡航; 结构体 `NET_IN_STOP_MOVE_INFO` 在 Python 里有, 这版 `PTZControlEx2` 支持 `param4` 结构指针)。它只能停住**当下**的转动, 拦不住 app 的跟踪。
- **`User parameter is illegal` 是正常的**: 无进行中的移动时调 `ptz_stop`(八向停止)就返回参数非法, `camera.py` 本来就容忍 —— 那句 print 来自 SDK 包装层, 不是故障。
- **屏(本机是带通话屏的机型, 屏上默认显示时间)在本地 SDK 里没有任何可写接口** —— 三种查法都验证过, 别再试: ① SDK 认识的配置名共 44 个(`SDK_Enum.py` 的 `CFG_CMD_TYPE`, 权威清单)里**没有** screen/display/lcd 项; ② 手工试 22 个屏相关名字(`ScreenText`/`Screen`/`Display`/`LCD`/`ScreenSaver`/`VideoCall`/`Caller`…)全部返回 `The current SDK does not support this funcntion`; ③ `CTRL_SET_PARK_CONTROL_INFO`(唯一带屏文本的结构体 `NET_SCREEN_SHOW_INFO`: 文本/颜色/滚动/二维码)在设备上返回 `device not found com interface`。唯一与"通话屏"有关的绑定是云端视频通话族(`OnRingVideoTalk2Cloud`/`AttachVideoTalk2Cloud`/`HangupVideoTalk2Cloud`/`DetachVideoTalk2Cloud`, 结构体齐全, 需要 `NET_OVER_SEAS_CLOUD`=乐橙云账号 + CallId), 只能"呼一通视频通话让屏显示画面", 写不了任意内容, 且依赖云账号。=> 屏上的显示设置和"智能跟踪"一样属于乐橙 app/云侧。
- 可用的本地配置旋钮(44 个名字里对本项目有用的): `AudioOutputVolume` / `AudioInputVolume`(本机都=100=最大, 所以"声音小"不是调这里的问题)、`General`(设备名 `S2V-DCE7`, 登录锁定策略)、`Lighting`(补光灯)、`NTP`、`Snap`、`Record`、`VideoWidget`(视频 OSD 叠加, 不是那块物理屏)。
- 若要拿到 app 那种控制力(暂停跟踪、屏显示、云端对讲), 官方路径是[乐橙云开放平台](https://open.imou.com/document/introduction/): 注册开发者 → 建应用拿 AppId/AppSecret → 把设备绑到该乐橙账号 → 用 OpenSDK(移动/PC)或 HTTP 接口; 个人版 10 路/1M 带宽, 超出按量付费。**这会给项目引入云依赖, 与"全本地"定位冲突, 用户说暂不做。**
- 编码与音频头: 本设备**只有 `mulaw`(G.711u)+ `bNeedHead=1` 出声**; 实测 `alaw` 和不加音频头都是**静音**。
- 唯一还没试的本地招(若要"跟踪开着也不卡"): 换更省设备资源的编码 —— `EM_A_TALK_CODING_TYPE` 里还有 `AMR`(3)/`G726`(5)/`AAC`(8)/`G722_1`(13), AMR 数据量约为 G.711 的 1/10, 设备解码负担与 WiFi 占用都小得多。设备是否支持未知, 试法: 用 ffmpeg 编码成对应格式(`-f amr`/`-c:a aac -f adts`)、`emEncodeType` 传对应枚举、`bNeedHead=1`, 然后听有没有声音(alaw 就是"没声音"的先例)。
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
