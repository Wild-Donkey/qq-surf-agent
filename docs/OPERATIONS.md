# 运行与排障手册

本文记录对当前代码库的运行时研究：进程拓扑、端口与网络边界、存储地图、worker tick 管线、
健康检查与重启方法，以及 2026-10-23 卡死事故的复盘。所有结论锚定仓库内可见代码，
行为配置以 `workflow.json` / `config.json` / `llm.local.json` 当前值为准（真实配置被 Git 忽略）。
数据与记忆分层规范见 [CONTEXT.md](CONTEXT.md)，终端操作见 [../CONSOLE.md](../CONSOLE.md)。

## 1. 进程拓扑

常驻服务由 `app/supervisor.py` 单实例守护（`flock` 锁 `runtime/workflow-supervisor.lock`），
启动后拉起三个子进程：

| 进程 | 入口 | 职责 |
| --- | --- | --- |
| supervisor | `app/supervisor.py` | 单实例锁、`workflow.log` 超 2MB 轮转、子进程退出退避重启（2s 起翻倍，封顶 60s）、处理 `workflow.restart` / `workflow.stop` 标记 |
| worker | `-m app.workflow run` | 监听事件的 HTTP 服务 + 每秒级决策主循环；bot 的核心 |
| diagnostics | `app/diagnostics.py`（`diagnostics.enabled`） | 独立旁路排障进程；告警进入后做快照归因，单任务、180s 超时，同类 1 小时合并；不修改在线代码 |
| profile_service | `-m app.profile_service run`（`profiling.enabled`） | 入群批量建档，分页补读近 30 天历史写画像；不产生聊天和群消息 |

注意区分：

- **`-m app.console --lite` / `app/lite_console.py` 只是终端 UI**，tail 日志、转发操作命令，不参与决策；关掉界面不停服务。它通过 `console.lock` 保证只开一个。
- **NapCat（OneBot 桥）跑在 QQ 进程内**，不在本仓库进程树里；`app/helper_service.py` 只是 QQ 主程序的启动/日志包装器（PID 文件 `runtime/qq-helper-service.pid`）。
- `-m app.chatgpt_auth login` 是一次性 OAuth 授权流程，不是常驻服务。

网络端口：

| 端口 | 归属 | 方向 |
| --- | --- | --- |
| 3000 | NapCat OneBot HTTP（`config.json` → `sender.url`） | worker 主动调用 OneBot API |
| 8767 | worker HTTP 服务（`workflow.json` → `port`） | NapCat POST `/events`，另有 `GET /health` |
| 8768 | ChatGPT OAuth 本地回调（`app/chatgpt_auth.py`） | 仅登录授权时使用 |

网络与输入边界（代码强制，非约定）：

- OneBot 调用只允许 `127.0.0.1/localhost/::1`，且 action 必须在
  [`agent.onebot_call`](../app/agent.py) 的白名单内：`get_record/get_image/get_msg/get_status/get_login_info/get_group_list/get_group_member_list/get_group_member_info/get_friend_list/get_group_msg_history/send_group_msg/send_private_msg/send_group_forward_msg/set_group_add_request/group_poke/send_poke/delete_msg`。
  新增 OneBot 接口时必须同步加白名单，否则工具"可达但不可见"。
- 事件入口 `POST /events` 校验 `X-Signature: sha1=<HMAC-SHA1(secret, body)>`，
  body 上限 262144 字节（支持 Content-Length 与 chunked 两种读法），见 `app/workflow.py` 内 `handler()`。
- 模型联网工具（`app/internet.py`）DNS 解析后校验目标为全球单播地址（拒绝本机/私网），
  单次连接 10s 超时、响应 2MB 上限、最多 5 次重定向。

## 2. 配置与存储地图

配置三件套（每轮 tick 重新读取 `workflow.json`，改行为参数一般不用重启；改端口/数据库路径/桥 Token 需重启）：

- `config.json`：OneBot 桥地址、Token 文件、期望自检号 `expected_self_id`。
- `workflow.json`：群列表、模式（`mode=live`）、所有限频/冷却/开关。当前关键值：
  `tick_seconds=1`、五秒静默窗口（`hold_seconds=5`，上限 `max_hold_seconds=20`）、
  模型最小间隔 5s（`interaction.max_qps=0.2`）、事件有效龄 300s（`max_event_age_seconds`）、
  原始上下文 48h、每群观察 200 条、分享冷却 120s、每群每天 8 次（`max_shares_per_group_day`）、
  合并转发冷却 120s、告警主人 `alerts.owner_qq`、告警合并 1800s、用量报告 10800s。
- `llm.local.json`：模型凭据。当前为 `provider=compatible`、火山兼容网关、
  模型 `deepseek-v4.1-flash`、`reasoning_effort=none`。

运行态目录 `runtime/`（全部 gitignore）：

| 路径 | 内容 |
| --- | --- |
| `workflow.sqlite3`(+wal/shm) | 主库：events/social_jobs/deliveries/candidates/model_calls/alerts/metrics/operator_pause/user_requests 等 |
| `logs/surf-bot.log` | 应用 service_log（决策、工具审计、告警），5MB×5 轮转，写盘带文件锁串行化 |
| `logs/qq-helper.log` | QQ/NapCat 侧日志（控制台的 `[QQ]` 流） |
| `workflow.log` / `.previous.log` | worker stdout/stderr（启动行、"工作周期失败"堆栈）；>2MB 被 supervisor 轮转为 previous |
| `raw_events.log` | **DEBUG 全量事件明文**，每个事件在 `ingest` 前追加截断 2000 字符的 JSON；目前无轮转，排障后可清空 |
| `*-supervisor.pid` / `*-child.pid` / `*.lock` | PID 与单实例锁；进程异常死亡后可能残留，只作参考，存活以锁/进程为准 |
| `workflow.restart` / `workflow.stop` | 触摸即触发重启/停止的标记文件，消费后删除 |
| `diagnostics/`、`outbox/` | 旁路排障快照与持久发件箱（戳一戳等） |

长期数据在 `context/`（归档、结构化记忆、表情、群/人投影），分层规范见 [CONTEXT.md](CONTEXT.md)。

## 3. 启动、重启与停止

```bash
python3 -m app.supervisor            # 直接前台启动（调试）；正常用 start.command
python3 -m app.botctl status         # 群数/模式/人物记忆数/待发告警
python3 -m app.botctl model-check    # 不带真实群聊的模型连通性测试
python3 -m app.botctl stop           # 触摸 workflow.stop，请求守护进程退出
```

控制台内等价命令：`/bot start|restart|stop`、`/status`、`/helper status|start|wrap|stop`。

要点：

- supervisor 与 worker 是**父子进程组**。worker 崩溃时 supervisor 自动退避重启；
  但 **supervisor 自身被杀后没有任何上层拉起机制**（项目没有安装 launchd 登录自启），
  电脑休眠/关机、QQ 退出期间服务同样不运行。
- 服务应在用户自己的终端（`start.command` / Surf Console）启动；从短命会话里
  `spawn` 出的进程组可能随会话结束被回收。
- worker 启动时会做恢复：把 `processing` 的 social_jobs 重置为 `pending`、
  补读各活跃群最近 100 条历史（仅 ≤180s 的重新 ingest）、为每群入队一次 startup 记忆整理。
- 重启后前几分钟看起来"响应慢"通常是在补积压：模型启动预算全局 5s 一次（`model_gate.reserve`），
  聊天与后台建档/记忆整理共享这一预算，后台任务在有聊天 job 时让行。

## 4. worker tick 管线（`app/workflow.py` 的 `main()` → `run`）

启动阶段：HTTP 服务跑在 daemon 线程（`ThreadingHTTPServer`，`daemon_threads=True`），
主线程进入循环。每轮：

1. 重新读配置、`prune`、`usage_report.tick`；
2. **先探活**：`get_status`。失败或 `online=false` → 记 `qq_offline` 告警，
   跳过所有依赖 OneBot 的步骤，只保留 `social.tick`（只打模型，不碰桥），
   避免离线期告警风暴；
3. 在线时依次：`moderation.check_recovery`（禁言恢复，30 分钟级）→
   `private_chat.tick`（好友私信队列）→ `social.tick`（群聊决策核心）→
   `dispatch`（跨群搬运/合并转发投递）→ `onboarding.tick`（新群发现与介绍）；
4. `alerts.flush`（合并后私信主人）、`export_inbox`（重生成 `workflow-inbox.html`）、
   写 metric `last_tick`；
5. 单轮整体被 try/except 包住：异常打 `工作周期失败` 堆栈到 `workflow.log`、
   记 `worker_<异常类型>` 告警，然后下一轮继续，不退出；
6. 轮末 `stop.wait(tick_seconds)`。

事件入口（HTTP 线程，与主循环并发）：验签 → 追加 `raw_events.log` →
用独立连接 `ingest()`（去重、入 social_jobs/request 队列、真人存在判断等）→
写 `last_event`。HTTP 线程与 tick 线程靠 SQLite WAL + `model_gate` 的写事务串行化。

模型调用链与预算：

- `social.tick` 把同群未读合成一批（≤30 条/12000 字符），五秒静默窗口聚合，
  二十秒强制收口；`social.call_model` → `social.generate_text`：
  - `provider=compatible/openai` 走 `agent.request`（`urlopen` **30s 超时**）打 `/chat/completions`，
    `response_format=json_object`（兼容网关要求提示词里出现 "json" 字样）；
  - `provider=chatgpt` 走 `chatgpt_auth.infer`（**90s 超时**、流式、store=false）；
  - JSON 校验失败时用独立预算重试一次慢请求；
- 每次模型调用先经 `model_gate.reserve`：`BEGIN IMMEDIATE`，距上次调用不足 5s 不放行，
  插入 `model_calls(status='reserved')`，完成置 `ok` / 失败置 `failed`。
  记忆整理（`memory_maintenance.tick`）共用这个门控。
- worker 的系统指令在 `app/social.py` 的 `SYSTEM` + 仓库根 `agent.md`（每轮读前 8000 字）；
  worker 只能调用 `application.TOOLS` 暴露的接口，收件群由代码固定。

关键模块索引：

| 模块 | 作用 |
| --- | --- |
| `app/social.py` | 批处理窗口、模型决策、quiet 指令硬跳过、工具调用与回复动作 |
| `app/request_queue.py` | 明确 @ 请求的持久待办；超过 `max_reply_age_seconds`（默认 180s）的 pending 自动 cancel，防止旧请求反复重置 job |
| `app/archive.py` | 原始消息归档；`render_text` 把 at 段还原成 `@昵称` |
| `app/merged_forward.py` | 跨群合并转发卡片（保留作者与顺序） |
| `app/moderation.py` | 禁言感知、申诉（仅好友管理员、固定文案）、解禁恢复 |
| `app/private_chat.py` | 好友私信独立队列（不与群聊互灌） |
| `app/memory_maintenance.py` | 增量结构化记忆 job 队列、游标与失败重试 |
| `app/internet.py` / `app/vision.py` / `app/emoticons.py` | 联网工具、多模态图片处理、表情库 |
| `app/alerts.py` | 告警指纹去重、合并冷却、uncertain 不盲目重发 |
| `app/qq.py` | 抢话 CLI：send/pause/resume/recall（撤回限 120s 内、软删除留痕） |

## 5. 健康检查 Runbook

按这个顺序判断"bot 死没死"：

```bash
# 1) 端口与健康检查
curl -s http://127.0.0.1:8767/health      # {"ok":true,"mode":"live","groups":N}

# 2) 进程树（supervisor + 三个子进程）
ps -p $(cat runtime/workflow-supervisor.pid) -o pid,stat,etime,command
ps -p $(cat runtime/workflow-child.pid)     -o pid,stat,etime,command

# 3) 心跳指标（只读打开主库）
python3 - <<'PY'
import sqlite3,time
db=sqlite3.connect('runtime/workflow.sqlite3')
now=time.time()
for k in ('last_tick','last_bridge_check','last_event','model_state'):
    r=db.execute('SELECT value FROM metrics WHERE key=?',(k,)).fetchone()
    if r and r[0].replace('.','').isdigit():
        v=float(r[0]);print(k,time.strftime('%H:%M:%S',time.localtime(v)),'%.0fs ago'%(now-v))
    elif r:print(k,r[0])
last=db.execute("SELECT id,status,created FROM model_calls ORDER BY id DESC LIMIT 1").fetchone()
if last and last[1]=='reserved' and now-last[2]>120:
    print('HANG: latest call #%s reserved %.0fs'%(last[0],now-last[2]))
else:
    print('latest call ok:',last)
print('jobs:',db.execute("SELECT status,count(*) FROM social_jobs WHERE status IN ('pending','processing') GROUP BY status").fetchall())
PY

# 4) OneBot 桥是否在线（worker 视角）
python3 -m app.console --command '/status'   # 或直接 get_status / get_login_info
```

判读：

- `last_tick` 持续推进 = 主循环活着；停在几十分钟前且进程 0% CPU = 主循环挂死。
- `last_bridge_check` 推进但 `last_event` 不动，只是没人说话；桥离线时会有 `qq_offline` 告警。
- **最新一条 `model_calls` 是 `reserved` 且超过 2 分钟** = 模型调用悬挂（见下节事故）。
  注意历史崩溃会永久留下 `reserved` 残行（当前库里就有数条），只看最新一条，别用总数判断。
- `workflow.log` 出现 `工作周期失败` 是单轮异常（会自愈）；连续出现才需要看堆栈。
- `runtime/logs/surf-bot.log` 看决策与工具审计；`runtime/logs/qq-helper.log` 看桥；
  `runtime/raw_events.log` 尾部还在更新 = HTTP 入口活着（但不代表 tick 活着）。

恢复动作分级：

1. 桥离线：worker 会自己等恢复，先查 NapCat/QQ（`/helper status`），不要重启 worker。
2. worker 挂死但 supervisor 活着：`touch runtime/workflow.restart` 或控制台 `/bot restart`。
3. supervisor 都没了：控制台 `/bot start`，或在用户终端跑 `python3 -m app.supervisor`。
4. 重启后确认 `/health`、`last_tick` 推进、近期 `model_calls` 以 `ok` 落盘。

## 6. 事故复盘：2026-10-23 22:41 全局卡死

**现象**：群里 @ bot 无反应；控制台不动；8767 最终无监听。

**证据链（均来自本机日志与主库）**：

- 22:41:56 最后一次 `last_tick` / `last_bridge_check`；同秒模型调用
  `model_calls.id=4787` 停在 `reserved`，此后再无 `ok/failed` 落盘；
- 但 HTTP 入口线程仍存活到至少 22:45:49：`raw_events.log` 持续收到群消息
  （含一条手工 `group_id=1` 的 `hello` 探测），而 `last_event` metric 停在 22:41:50——
  即事件能进 HTTP 线程、写了 debug 日志，却没走完 `ingest` 提交；
- 22:52 排查时 worker（PID 50064）与 supervisor（PID 38020）均已不存在，
  8767 CLOSED，只剩残留 PID 文件；`workflow.log` 无退出堆栈，
  `~/Library/Logs/DiagnosticReports` 无 Python 崩溃报告；
- OneBot 桥全程正常：3000 在线、`get_status.online=true`、自检号 1550068162 正确。

**结论**：主循环 tick 线程在 22:41:56 开始的模型轮次悬挂（预留了预算但调用未返回/未超时释放）；
同一时段 HTTP 线程仍能收事件并写 debug 日志，却没有新的 `ingest` 提交落盘
（具体被阻塞点未留下堆栈，最可能是主循环长期占着事务导致写锁等待）。随后整个 supervisor
进程组被外部结束（无 traceback、无崩溃转储，具体信号来源无法从现有日志确定），
supervisor 自身没有上层守护，服务就此彻底下线。NapCat 在 8767 关闭期间的上报直接连接失败。

**恢复**：重新拉起 supervisor → worker 监听恢复；启动补积压阶段（约 5 分钟，受 5s 模型门控限制）
后 tick 稳定推进，模型调用恢复 `ok`，跨群投递恢复。期间还观察到启动早期两个旁路子进程
（diagnostics/profile_service）也会持有主库 WAL 句柄，属正常现象。

**本次确认的工程缺陷（尚未修代码）**：

1. **单轮模型决策没有总时限看门狗**。单次 HTTP 有 30/90s 超时，但一轮内可能串行
   多个模型请求与多个 OneBot/CDN 请求，累积悬挂时无人回收该轮，tick 线程永久卡住。
2. **悬挂的 `reserved` model_calls 不清理**。启动恢复只重置 `social_jobs` /
   `private_messages` 的 processing 状态；靠"预留时间已远去"不影响门控，但该行永远停留，
   监控上应把它视为挂死信号。
3. **supervisor 是单点**：worker 死了它能拉起，它自己死了服务就没了，且无告警通道
   （告警也跑在 worker 里）。
4. `raw_events.log` 为 DEBUG 明文全量追加且无轮转，只应短期保留。

建议的修复方向（如需实施再动代码）：给 `social.tick` 单轮加硬超时并在超时后把该轮
`reserved` 标记为 `failed`；supervisor 增加 launchd 用户级 agent 或外层保活；
为 `raw_events.log` 加轮转/开关。

## 7. 日常速查

```bash
python3 -m app.workflow pending          # 待搬运候选
python3 -m app.workflow inbox            # 重生成收件箱 HTML
python3 -m app.qq groups                 # 桥返回的群列表
python3 -m app.qq recall <群> [消息ID]    # 撤回 120s 内自身消息
python3 -m unittest discover -s tests   # 回归测试
```

配置热改：`workflow.json` 保存即生效（下一轮 tick 重读）；`llm.local.json`、
端口、数据库、Token 类变更需要 `/bot restart`。
