# 终端控制台

双击 `Surf Console.command`，或在项目目录运行 `python3 console.py`，默认启动 Lite。需要彩色完整界面时使用 `python3 console.py --full`。Lite 的并行输入组件安装到项目本地：`python3 -m pip install --target runtime/deps -r requirements-lite.txt`。完整界面仍使用标准库 curses。

QQ Helper 消息、应用处理日志、局面理解、发送回执和命令结果按时间混排在同一信息流，底部始终可以输入。Tab 补全命令、服务子命令、群名和群号；首次显示多个候选，再按 Tab 循环选中。↑/↓ 按显示行滚动，鼠标滚轮滚动，PgUp/PgDn 翻页，End 回到实时；左右键编辑输入，Ctrl-P/Ctrl-N 找回本次输入历史。Ctrl-U 清空输入。`/quit` 或 Ctrl-C 仅退出界面，两个后台服务继续运行。

## 常用操作

```text
/groups
/use 棺材房
/takeover
/say 我来接一句
/resume
/worker 先读最新聊天和人物记忆，看看这张图有没有值得搬运的地方，没必要就不发
/jobs
/status
```

选择群后，直接输入文字会通过 bot 账号发送，作为 bot owner 的人工发言进入 worker 上下文，不自动暂停 bot。所有通过人工 CLI 发出的消息都会加上 `【人工接管】` 前缀；worker 自动回复不会带这个标记。`/say` 后的内容始终按文字发送，不会执行其中的斜杠命令。`/use` 的选择保存在本机，下次打开沿用。`/resume` 恢复自动发言。

`/worker` 将本机指令写入数据库，由当前群的正常 worker 连同最新消息、人物和群记忆读取。指令不会作为群友消息入档或直接转发给群。`/jobs` 显示 queued/applied/blocked 状态；applied 表示 worker 已读取并作出决策，可能保持安静，不等同于已经发送。`/takeover` 保持 bot 自动运行；只有显式 `/pause 分钟` 才会暂停，`/resume` 解除暂停。不活跃群仍受原来的限制。

## 服务管理

```text
/bot start
/bot restart
/bot stop
/helper status
/helper start
/helper wrap
/helper stop
```

`helper start` 不会重复启动在线 QQ；`helper wrap` 先核验当前 bot 账号和唯一 QQ 主进程，再短暂重启 QQ，转为后台记录标准输出。`helper stop` 只停止封装管理的进程，不终止外部 QQ。首次封装后可能需要在 QQ 窗口重新登录。默认沿用本机 NapCat 启动方式 `QQ --no-sandbox -q bot账号`，可以在忽略的 `workflow.json` 配置 `console.helper_command` 参数数组。

QQ Helper 封装运行在独立后台进程，持有单实例锁；控制台不是常驻服务的父进程。QQ 退出会记录退出状态，不自动反复拉起登录窗口。

## 本地日志和状态

- `runtime/logs/qq-helper.log`：QQ/NapCat 标准输出和错误，持续记录。
- `runtime/logs/surf-bot.log`：收件、worker 开始/决策、人工接管、prompt 队列、投递回执。
- `runtime/logs/console.log`：控制命令的执行结果。
- 每路主日志最多约 5 MB，另保留 5 份轮转文件。常见 token/key 和 URL 查询参数会脱敏；日志目录仅本机用户可访问。QQ 输出可能包含聊天内容，请按聊天记录保管。
- 原有 `runtime/workflow.log` 仍保留并汇入同一信息流。
- `operator_prompts` 数据表保留本机 worker 指令及处理状态；`runtime/console-state.json` 保留选中的群。这些都被 gitignore 排除。

也可执行单条命令，便于脚本调用：

```text
python3 console.py --command /status
python3 console.py --command '/use 棺材房'
python3 console.py --command '/worker 观察最新话题，不需要强行回复'
```

## 聊天日志和截图

信息流将 Helper 原始输出整理成聊天记录：时间只显示一次，群名和人名最多5个字符，不显示身份号码。群用不同底色、人用头像主色派生的文字颜色；本机缓存头像颜色，后台读取不阻塞输入，失败时使用稳定备用色。256色终端下文字与底色的对比度至少4.5:1。原始日志文件保持完整。

先把图片复制到剪贴板，再在控制台输入 `/paste`，会以 `【人工接管】 截图` 加图片发到当前选择的群。Tab 也能补全 `/paste`。只读取图片，不执行剪贴板文字；空剪贴板或不支持的格式不会发送。图片上限15MB、1600万像素，发送结果不确定时不重试。截图会进入本机 worker 的图片上下文。`runtime/outbox/` 保存发送文件，`context/ui-colors.sqlite3` 保存头像配色，均被gitignore排除。

## Lite 模式

终端不方便使用全屏界面时，运行 `python3 console.py --lite`，或双击 `Surf Lite.command`。Lite 使用单列信息流加输入栏，显示同样的 QQ 消息、应用日志与局面理解；不设置群色/人色、不读取头像。滚动由应用管理，支持鼠标双向滚轮、↑↓、PageUp/PageDown，End回实时；读历史时新消息不抢阅读位置。

仍可输入 `/use`、`/say`、`/paste`、`/worker`、`/pause` 等命令，命令结果会就地显示。正常交互终端使用 prompt_toolkit 保留输入并原地刷新：打字期间聊天仍会更新，Tab 补全不受影响。启动时清除遗留终端状态，再由Lite接管聊天区域的滚动；不设置群色、人色。简单管道或 TERM=dumb 环境保留逐行输入模式。`/help` 列出可用命令，`/quit` 只退出界面。

标准界面向上查看历史时，新消息不会把当前阅读位置推走；顶部的“历史”状态显示距离实时的行数。按 End 恢复跟随最新消息。信息流保存滚动位置，长消息也能逐行查看。Lite 有独立的单列历史滚动，不依赖终端原生回滚。

macOS 旧版 curses 缺少第5按钮常量时，控制台直接解析 SGR 鼠标事件以支持双向滚轮；不把鼠标协议字节插入命令输入。关闭控制台会恢复终端鼠标模式。

控制台默认以 Lite 启动。需要彩色完整界面时使用 `python3 console.py --full`。交互控制台设有单实例锁；重复启动会提示复用已有窗口，不影响QQ Helper和bot服务。单条 `--command` 操作不受此限制。

### 结构化记忆更新

应用独立维护 `context/memory-maintenance.sqlite3` 中的增量任务，不依赖 worker 是否发言。

- 关键信息：聊天 worker 发现事实、偏好、边界、约定或纠错时，本轮即提交状态，并优先排入群和人物画像整理任务。
- 定时：`memory_updates.interval_seconds` 默认 300 秒，检查新增记录；没有新增记录不调用模型。
- 压缩前、空闲退出、服务关闭前：先保存会话检查点并持久化整理任务；不会为了等待模型阻塞退出。未完成的语义整理在后台或下次启动继续。
- 重启：扫描未完成任务，按归档 ID 断点续读；模型或校验失败不会推进已读位置。

整理结果遵守现有 schema，群与人物分别更新，人物画像跨群累积。带来源的新增事实必须能在本批记录中核验；整批记忆事务提交后才推进游标。增量画像保留未被本批提及的旧事实。结构化数据提交后生成对应 JSON 和 Markdown。此任务不发送聊天消息。


### 联网与群名补全

群聊和好友私信 worker 可调用 `web_search(query)`、`read_webpage(url)` 搜索并读取公开网页，每轮最多三次读取；返回真实来源链接、读取时间和错误信息。无需另配搜索 API key。只使用公开 GET 请求，限制响应大小及重定向，每跳校验并固定公网 IP，不带 QQ 或浏览器登录态。网页数据不作为系统指令，搜索词不得包含群历史或人物记忆。可通过 `internet.enabled=false` 关闭。

Lite 和标准界面 `/use` 的 Tab 补全均优先填入群名；重名群使用 `群名 [群号]` 区分，仍可输入群号筛选。所选聊天跨重启保留。


服务商以 `content_filter` 拒绝的聊天批次标记为 `content_filtered`，相关待办标为 `blocked`；记忆批次在 `memory-maintenance.sqlite3/blocked_ranges` 中记录未完成的语义整理区间。原始归档保留，不把拒绝当作成功摘要，也不自动反复重发同一批内容。`/status` 的 `memory.blocked_ranges` 显示缺口数量。其他新增样本继续处理。此类拒绝不拉起代码排障；真正的格式、网络或校验错误仍保留重试和排障链路。

记忆整理的每次失败另存于 `memory-maintenance.sqlite3/failures`，包含群、归档区间、校验错误码和时间；重试成功不会删除失败记录，便于追溯，避免只剩泛化的 ValueError。


### 整体局面与回复形式

触发事件是唤醒信号，不是指定回复对象。应用在5秒无新消息、或连续消息达到20秒时固定本轮未读窗口；worker结合最新聊天、群和人物记忆、历史及待办，一次判断整个局面是否需要回应，然后选择文字、单表情或文字/表情组合。不按消息逐条补答。没有值得说的文字时先考虑贴切表情，没有合适反应才沉默；停止互动的意愿和防刷屏规则仍生效。

组合接口：`finish_turn` 的 `action=reply, reply_parts=[{"text":"短句"},{"emoticon":{"kind":"face","id":"实际ID"}}], quality=natural`。不再同时填写顶层 text/emoticon。最多8段、4个表情、1200字，应用校验当前聊天素材归属和重复限制，按顺序组成一条QQ消息；一个回执代表整个组合，不由worker多次发消息。单表情继续使用空text加emoticon，既有文字接口兼容。组合及其顺序持久化，重启去重，结果不确定不自动重发。


### 引用与人物来源

worker的每条未读记录包含实际发言人ID、称呼、消息ID、event_key和时间；引用块另外带原作者ID/称呼、时间和可见原文。应用会针对引用补读本群相关原始记录，并把引用作者、@对象、相关历史发言者加入人物记忆加载列表。引用bot转述时标明self，优先补真实成员原话，不把转述当原始证据。找不到引用则标未知；检索命中是相关证据，不代表应用已推断人物指代。讲述者与其提到的邻居等第三方仍须区分。


### 每个处理窗口的局面理解槽

每个5秒静默/20秒上限触发的窗口有稳定的 `request_slot.request_id`，与单条 `pending_requests` 待办不同。无论是否有图、是否公开回复，worker均可提交独立的 `understanding={summary,images,uncertainties}`。优先调用 `record_understanding` 先存笔记并拿回执，再决定公开回应；也可随 `finish_turn` 一并提交，`wait`同样保存。

应用将其保存到 `context/worker-understandings.sqlite3` 和 `context/groups/<群号>/requests/<request_id>.md`，并写入 `runtime/logs/surf-bot.log`，不会发送到QQ。下一轮注入同群最近的有限摘要，完整观察仍落盘。图片说明只接受真正附过的引用，可同时更新已有图片归档和表情库。与人物状态校验独立，缺少理解或校验失败会记录 missing/rejected，模型调用失败标 failed；不伪造理解。公开 text/reply_parts 与内部理解分开，只有明确要求解释或总结时才公开说明。

群聊 worker 的发言格式为 `代号：内容`。全局代号保存在 context/identities.sqlite3，初始取账号用户名（5个汉字/10个字母的显示宽度），同名自动区分。引用与历史统一使用代号；完整成员信息通过 get_member(alias) 查询，观察到稳定称呼后可带真实发言证据更新。

每个窗口的内部理解以 `[理解][群名]` 展开显示局面摘要、图片理解和待确认事项，保留时间并与聊天混排；这些信息只显示在本机控制台，不向 QQ 发送。原始结构化记录仍完整保存在日志及 context 中。

### 选择复制

Lite 和全量界面均可按 F2 进入复制模式：停止刷新并释放鼠标给终端，直接拖选文字，使用终端复制快捷键（macOS 为 Cmd-C）。再按 F2 恢复实时更新和应用滚轮；期间后台服务、消息记录和持久化日志照常运行。复制模式下使用终端原生选区，避免拖选被应用鼠标处理吞掉。

Worker 的身份背景集中在 bot_identity，群名/群画像/成员基础角色集中在 group_context；发言行仅为代号和内容，必要时附引用/图片关联，完整人物档案按需查询。措辞由worker在生成时决定：只写新增反应，短评可只有两三个字；应用不通过文字相似度拦截、删改或代写回复。

记忆分层、固定输入结构、schema、文件职责和主动澄清流程见 [docs/CONTEXT.md](docs/CONTEXT.md)。

### 群会话预读与按需回溯

启用 interaction.orientation_enabled 后，新建/重建群会话先预读本群最近两小时原话（最多300条、24000字，附已有图片理解），通过 set_group_context 建立话题、带原话证据的指代与未完成互动，完成前不公开发言。背景集中保存在 group_context.orientation，并落盘 groups/<群号>/orientation.json。持续活跃时默认每15分钟重新核对，空闲会话过期或服务重启后重新预读。覆盖不足会明确标记，不把有限记录当成完整历史。

worker 任何时候发现看不懂话题或指代，可调用 retrace_context(query)，应用补读本群近期原话，并用关键词检索更早的本群相关记录。回溯阶段先更新群背景再参与；无证据的解释保留 unknown，不硬塞特定身份结论。回溯不自动把旧话题逐条补答。
