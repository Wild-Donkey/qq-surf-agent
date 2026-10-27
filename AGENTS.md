# 仓库协作 Agent 上手指南

本文件写给在本仓库工作的编码 Agent（Trae / Codex / Cursor 等）：说明系统是什么、代码在哪、如何运行测试、哪些边界不能碰。人类用户的完整说明以 [README.md](README.md) 为准。

> **文件命名注意**：根目录的 [`agent.md`](agent.md) 不是开发文档，而是线上 bot worker（Luna）每轮模型请求都会加载的**运行时操作手册**（[app/social.py](app/social.py) 中 `guide=ROOT/'agent.md'`，[app/diagnostics.py](app/diagnostics.py) 排障快照也会打包它）。修改它等于直接修改线上 bot 的行为准则，切勿当作说明文档覆盖。本文件采用跨工具通用的 `AGENTS.md` 标准文件名；两者仅大小写不同，在**大小写不敏感**的 APFS 卷上检出会互相覆盖，克隆本仓库需使用大小写敏感卷（本机工作卷为大小写敏感 APFS）。AI 编码资产统一放在 [.agents/](.agents/)（技能与交接 prompt，见文末索引）。

## 1. 项目是什么

QQ 群友型 bot「Luna」：通过本机 OneBot 11 HTTP 桥接（NapCat）收发 QQ 群消息，观察并沉淀离线记忆，由 LLM 在每个消息窗口决定「安静 / 接话 / 表情 / 跨群分享」。

- 语言/运行时：**Python 3.10+**（代码使用 `dict | None` 等运行时求值注解，且全仓库无 `from __future__ import annotations`；`__pycache__` 为 cpython-312，开发/生产用 3.12）。注意 macOS 自带 `/usr/bin/python3` 可能是 3.9.6，直接跑会在 import 阶段报 `TypeError: unsupported operand type(s) for |`，开工前先确认解释器版本（各 `.command` 启动器优先使用 codex-runtime 下的 3.12，缺失时回退 PATH 中的 `python3`，仍需保证为 3.10+）。**主程序仅用标准库**；可选依赖一律安装到 `runtime/` 下的独立 target 目录，不污染全局环境。
- 模型：OpenAI Chat Completions 兼容接口（`provider=compatible` 可接 DeepSeek/火山等），或官方 Sign in with ChatGPT 订阅授权。
- Git 仓库根目录就是本文件所在目录；所有命令默认在此目录执行。
- 目录约定：业务代码全部位于 `app/` 包（根目录不放业务 `.py`），入口一律 `python3 -m app.<模块>`，cwd 保持仓库根；`scripts/` 存放一次性运维脚本（直接 `python3 scripts/<name>.py` 运行，脚本自带仓库根引导），`tests/`、`schemas/`、`docs/` 在根目录。
- 服务只监听 `127.0.0.1`：HTTP 接收事件端口默认 8767（`workflow.json` 的 `port`），NapCat 侧默认 3000。

## 2. 架构与消息流

```text
NapCat(OneBot 11)
  └─ POST /events（Array 格式 + Token）→ app/workflow.py 的 ThreadingHTTPServer
       验签/去重 → 持久化 → 5 秒静默窗口（持续来消息最多 20 秒）
         → app/social.py 组装上下文并调用 LLM（worker 只输出 {"call":{...}} 信封）
           → app/application.py 校验动作/权限/冷却/去重/真人在场/群活跃
             → replies / merged_forward / interactions 构造消息
               → agent.onebot_call 白名单 → NapCat → QQ
```

核心边界：**应用（本仓库代码）拥有一切权限，LLM worker 没有任何文件、shell、网络、账号、任意 OneBot 权限**。worker 只能调用 `app/application.py` 暴露的接口；收件群由代码绑定，模型不能指定群号、URL 或文件路径。

## 3. 模块地图

| 模块 | 职责 |
| --- | --- |
| [app/workflow.py](app/workflow.py) | 主服务：HTTP 事件入口、worker 主循环、路由/去重/投递队列/发送策略；CLI 见下 |
| [app/supervisor.py](app/supervisor.py) | 单实例守护：轮转日志、异常退避重启 |
| [app/agent.py](app/agent.py) | OneBot 调用封装（含 **API 白名单**）、账号核对、独立 RSS/Atom 采集与本地导入 |
| [app/social.py](app/social.py) | 群聊决策：组装上下文、SYSTEM prompt、加载 `agent.md` 运行时手册、调用模型 |
| [app/application.py](app/application.py) | worker 侧应用接口：状态更新、回复/分享提交与全部校验，落盘由应用负责 |
| [app/protocol.py](app/protocol.py) | worker 协议：严格 JSON 信封、空输出/内容过滤等故障分类（repairs 固定入口） |
| [app/sessions.py](app/sessions.py) | 每群一个有界 GroupSession 与短任务 reaction agent |
| [app/private_chat.py](app/private_chat.py) | 好友私信独立队列，与群聊严格隔离 |
| [app/memory.py](app/memory.py) | 离线 Markdown 记忆、有界工作集、增量检查点 |
| [app/memory_store.py](app/memory_store.py) | 结构化记忆权威存储（SQLite 事务先于 JSON/Markdown 投影） |
| [app/memory_maintenance.py](app/memory_maintenance.py) | 不依赖 worker 是否发言的增量语义整理任务 |
| [app/context_memory.py](app/context_memory.py) | 话题驱动的结构化召回 |
| [app/archive.py](app/archive.py) | 追加写的长期原话归档（含 `@` 还原为 `@昵称` 的 `render_text`） |
| [app/orientation.py](app/orientation.py) / [app/understanding.py](app/understanding.py) | 进群预读回溯 / 每窗口局面理解 |
| [app/participants.py](app/participants.py) | human/bot/unknown 判断与 5 分钟真人在场硬门槛 |
| [app/request_queue.py](app/request_queue.py) / [app/clarification.py](app/clarification.py) | 持久待办（不随聊天窗口截断）/ 主动提问澄清 |
| [app/replies.py](app/replies.py) | 文字、单表情、文字+表情组合回复（容错校验） |
| [app/interactions.py](app/interactions.py) | @、引用回复、戳一戳等会话内互动 |
| [app/merged_forward.py](app/merged_forward.py) / [app/image_sharing.py](app/image_sharing.py) | 跨群合并转发卡片 / 单图分享 |
| [app/emoticons.py](app/emoticons.py) | QQ 原生 face 与已理解大表情库（按群隔离） |
| [app/vision.py](app/vision.py) / [app/speech.py](app/speech.py) | QQ CDN 图片解码为真实多模态输入 / 本地 faster-whisper 转写 |
| [app/internet.py](app/internet.py) | worker 联网工具（只读公开 GET、公网 IP 固定、无登录态） |
| [app/moderation.py](app/moderation.py) | 禁言事件、申诉（默认关闭）、解禁回归 |
| [app/onboarding.py](app/onboarding.py) / [app/profiling.py](app/profiling.py) / [app/profile_service.py](app/profile_service.py) | 新群发现与画像蒸馏、可恢复的批量建档独立进程 |
| [app/alerts.py](app/alerts.py) / [app/diagnostics.py](app/diagnostics.py) / [app/repairs.py](app/repairs.py) | 去重告警私信 / 独立排障旁路 / 受控恢复动作 |
| [app/model_gate.py](app/model_gate.py) | 各服务共享的原子模型启动预算 |
| [app/chatgpt_auth.py](app/chatgpt_auth.py) | Sign in with ChatGPT（PKCE、0600 凭据、token 轮换） |
| [app/qq.py](app/qq.py) / [app/console.py](app/console.py) / [app/lite_console.py](app/lite_console.py) / [app/helper_service.py](app/helper_service.py) | 抢话 CLI / 终端控制台 / Lite 界面 / NapCat 封装管理 |
| [app/qq_events.py](app/qq_events.py) | 本地事件接收（绝不把私聊转发给模型） |
| [app/usage_report.py](app/usage_report.py) | 模型用量定期汇总私信 |
| [app/service_log.py](app/service_log.py) | 有界、脱敏、轮转的本地日志 |
| [app/bot_identity.py](app/bot_identity.py) / [app/identities.py](app/identities.py) | bot 公开身份（含源码链接） / QQ 身份与全局代号 |

## 4. 本地运行

```bash
# 1) 从示例复制三份真实配置（均被 gitignore）
cp config.example.json config.json
cp workflow.example.json workflow.json
cp llm.example.json llm.local.json

# 2) 填写 bot QQ、群列表、owner QQ、OneBot token、模型密钥
#    NapCat：HTTP 服务绑 127.0.0.1:3000（关 CORS、设 Token）；
#    HTTP 上报指向 http://127.0.0.1:8767/events，消息格式 Array，Token 一致，关闭上报自身消息。

python3 -m app.botctl bootstrap      # 只观察近 14 天样本，不产生历史投递
python3 -m app.botctl model-check    # 模型连通性测试
python3 -m app.supervisor            # 正式守护运行（也可双击 start.command）
```

- 配置热更新：内容与模型密钥保存后下一工作周期重读；改端口、数据库路径、桥接 Token 必须重启。
- `workflow.json` 的 `mode` 默认 `preview`（只记录不发送），确认无误后改 `live` 才真正发消息。
- 可选依赖：Lite 界面 `requirements-lite.txt`、ChatGPT 授权/视觉 `requirements-chatgpt.txt`、语音 `requirements-speech.txt`，分别安装到 `runtime/deps`、`runtime/speech-deps`（Whisper 模型放 `runtime/speech-models`）。
- 健康检查：`http://127.0.0.1:8767/health`。

## 5. 常用命令

```bash
# 服务与记忆
python3 -m app.botctl status         # metrics、群数、模式、待发告警
python3 -m app.botctl flush          # 立即写回 Markdown 记忆
python3 -m app.botctl stop           # 请求守护进程停止（runtime/workflow.stop）

# workflow CLI
python3 -m app.workflow status       # 投递队列状态统计
python3 -m app.workflow pending      # 最近 pending 候选
python3 -m app.workflow tick         # 手动跑一次发送周期
python3 -m app.workflow inbox        # 导出 workflow-inbox.html
python3 -m app.workflow context <group>
python3 -m app.workflow approve|reject <id>

# 抢话 / 人工接管（退出终端不影响服务）
python3 -m app.qq groups
python3 -m app.qq chat [群名关键词]
python3 -m app.qq say "群名关键词" "内容" [--dry-run]
python3 -m app.qq dm "内容"          # 固定发给 owner
python3 -m app.qq pause "群名关键词" [分钟]
python3 -m app.qq resume "群名关键词"
python3 -m app.qq recall "群名关键词" [message_id]   # 撤回 120 秒内的消息

# 控制台与批量建档
python3 -m app.console [--full|--lite] [--command '/status']
python3 -m app.profile_service run|status|enqueue [--group G] [--reset]

# ChatGPT 订阅接入
python3 -m app.chatgpt_auth login [--new]
python3 -m app.chatgpt_auth models
python3 -m app.chatgpt_auth activate --model <slug>
```

## 6. 测试

```bash
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s tests -p 'test_protocol.py' -v   # 单文件
```

注意：必须用 3.10+ 解释器（开发用 3.12）。若 `python3` 指向系统 3.9，先切换解释器，不要为此修改业务代码或跳过用例。

- 功能归属与环境要求见 [tests/README.md](tests/README.md)。`test_http_chunks.py`、`test_lite_pty.py`、`test_clipboard_owner.py` 属集成测试，需要回环端口/PTY/Pillow，缺失依赖时应修复环境，**不许删除或跳过断言制造通过**。
- `test_protocol.py` 是 [app/repairs.py](app/repairs.py) 自动恢复流程的固定入口，必须保留。
- 新增测试优先并入现有功能文件；仅在出现独立功能域或新运行环境时才新建文件。
- 改动后至少跑通相关测试文件；涉及协议/发送/记忆的改动跑完整套件（tests/README 记录为 293 个用例，随功能增加）。

## 7. 配置、密钥与 Git 红线

- 被忽略的真实资产：`config.json`、`workflow.json`、`llm.local.json`、`context/`、`runtime/`、`work/`、`*.sqlite3*`、`*.log`、`*.token`、`*.key`、`.env*`。提交前用 `git ls-files` 复核，防止曾被跟踪的私密文件进入历史。
- 文档、代码、报错、测试中**禁止出现真实 QQ 号之外的敏感凭据**（API key、token、密码一律用环境变量或占位说明）。
- 告警与排障数据不得包含密钥或聊天原文；[app/diagnostics.py](app/diagnostics.py) 的代码快照是显式白名单，新增文件需评估后再加入。
- 日志中的 token/key/URL 查询参数要脱敏（[app/service_log.py](app/service_log.py)）。

## 8. 开发约定与硬性边界

1. **OneBot 白名单**：所有 QQ API 动作必须在 [app/agent.py](app/agent.py) 的 `onebot_call` allowed 集合中（如 `send_group_msg`、`delete_msg`、`group_poke`）。新增 OneBot 能力时先加白名单，再写应用层封装和测试，否则会在线上静默失败。
2. **新增 worker 工具必须三处同步**：`app/application.py` 接口与校验 → [app/social.py](app/social.py) SYSTEM prompt/运行时手册 `agent.md` 的工具说明 → 测试。只加代码不引导，会出现工具「可达但模型不可见」。
3. **worker 协议**：输出只能是 `{"call":{"name":"...","arguments":{...}}}`；不接受 `{"action":...}`、纯文本等格式（[app/protocol.py](app/protocol.py)）。终结动作 `finish_turn` 每轮一次，额外读取最多三次。
4. **发送前策略 gate（改发送逻辑时不能绕过）**：14 天群活跃、近 5 分钟真人发言（`human_presence`）、人工暂停、禁言、回复/分享冷却与每日配额、跨群与图片指纹去重、`preview` 模式、生成期间有新消息则草稿作废（superseded）、发送结果不确定绝不自动重发。`share`/`merged_forward` 有独立冷却且豁免部分真人 gate 时，以代码现状为准并同步改测试断言。
5. **结构化记忆**：`context/structured_memory.sqlite3` 是权威存储，JSON/Markdown 只是可重建投影。新增字段要同步 [schemas/](schemas/) 下 JSON Schema、写入者、投影和测试；事实必须带 `evidence`（真实 event_key）与 `confidence`；旧数据需显式迁移，不靠 prompt 假定兼容。规范见 [MEMORY_SCHEMA.md](MEMORY_SCHEMA.md) 与 [docs/CONTEXT.md](docs/CONTEXT.md)。
6. **主程序保持零三方依赖**；新可选能力放入独立 requirements 并在运行处做可选导入。
7. **兼容供应商注意**：`response_format: json_object` 时 prompt 必须显式要求输出 JSON；供应商支持的请求体参数（如 `reasoning_effort`）按配置透传。
8. **排障纪律**：不假定走本地 Codex CLI；诊断旁路使用应用自身的 ChatGPT 授权与公共 Responses 接口。恢复动作只能来自 [app/repairs.py](app/repairs.py) 的受控接口，禁止执行模型给出的任意 shell；未经验证不得宣称「已修复」。
9. OneBot 离线等外部依赖故障要有前置探测与去重告警，避免 worker 空转反复调用模型（告警 scope 固定、冷却合并）。
10. 人设约束以运行时 `agent.md` 与 `context/self/persona.md` 为准；修改行为类措辞属于产品改动，需在真实配置环境验证。

## 9. 数据与运行态位置

```text
context/                       # 全部 gitignore：归档、记忆、媒体
  archive.sqlite3              # 原始群消息证据库（按月另有 JSONL，清理缓存不删归档）
  structured_memory.sqlite3    # 群/人物记忆权威库（修订审计）
  identities.sqlite3           # QQ 身份与全局代号
  groups/<gid>/                # group.json/.md、orientation.json、requests/
  people/<uid>/                # people.json/.md（跨群主画像）
  self/persona.md
  memory-maintenance.sqlite3 / worker-understandings.sqlite3 / emoticons.sqlite3 ...
runtime/                       # 数据库、日志、PID、token、可选依赖（gitignore）
  logs/surf-bot.log            # 收件、worker 决策、投递回执（约 5MB×5 轮转）
  logs/qq-helper.log           # NapCat 输出（可能含聊天内容，按聊天记录保管）
  diagnostics/<job_id>/        # 排障快照
schemas/                       # memory.schema.json / context.schema.json / storage-manifest.json
docs/CONTEXT.md                # 四层数据模型、固定输入结构、回溯与澄清流程
```

## 10. 常见任务排查入口

- bot 不说话：`-m app.botctl status` → `/health` → 群是否在 14 天活跃/近 5 分钟真人 → `mode` 是否 preview → `runtime/logs/surf-bot.log` 中的 gate 与决策日志。
- 模型不调工具：查 `app/social.py` prompt 是否引导、`agent.md` 是否声明该工具、`service_log`/tool_results 是否真实返回（模型可能谎称已调用）。
- 发送失败/重复：查 `deliveries` 表状态、去重指纹与冷却配置；结果不明的发送靠人工对账，不自动补发。
- 记忆异常：先区分层（原始归档 / 结构化记忆 / 会话背景 / 工作视图），权威数据以 SQLite 事务为准，不要直接手改投影 Markdown 当作修复。

## 11. 文档索引

- [README.md](README.md)：完整产品与运行说明（消息流、发言规则、各功能特性）
- [CONSOLE.md](CONSOLE.md)：终端控制台命令、日志位置、Lite 模式
- [MEMORY_SCHEMA.md](MEMORY_SCHEMA.md)：结构化记忆字段与写入规则
- [docs/CONTEXT.md](docs/CONTEXT.md)：上下文分层、存储唯一事实源、worker 固定输入
- [tests/README.md](tests/README.md)：测试分类与维护纪律
- [`agent.md`](agent.md)：**Luna worker 运行时手册（产品资产，非开发文档）**
- [.agents/skills/surf-agent-incident-triage/](.agents/skills/surf-agent-incident-triage/SKILL.md)：线上事故取证排障技能（复读、答非所问、不发言、模型超时/失败激增、token 异常膨胀）
- [.agents/handoffs/](.agents/handoffs/)：历次跨 Agent 交接 prompt（历史背景，部分状态可能已过期，以代码与数据现状为准）
