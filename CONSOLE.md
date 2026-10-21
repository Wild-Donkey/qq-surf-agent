# 终端控制台

双击 `Surf Console.command`，或在项目目录运行 `python3 console.py`。不需要额外 UI 依赖。

左窗显示 QQ Helper 原始输出，右窗显示 surf bot 收件、worker 决策、发送回执和命令结果，底部始终可以输入。Tab 补全命令、服务子命令、群名和群号；首次显示多个候选，再按 Tab 循环选中。F6 选择日志窗，PgUp/PgDn 翻页，End 回到实时，左右键编辑，↑/↓ 找回本次输入历史。Ctrl-U 清空输入。`/quit` 或 Ctrl-C 仅退出界面，两个后台服务继续运行。

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
- 原有 `runtime/workflow.log` 仍保留并在右窗显示。
- `operator_prompts` 数据表保留本机 worker 指令及处理状态；`runtime/console-state.json` 保留选中的群。这些都被 gitignore 排除。

也可执行单条命令，便于脚本调用：

```text
python3 console.py --command /status
python3 console.py --command '/use 棺材房'
python3 console.py --command '/worker 观察最新话题，不需要强行回复'
```

## 聊天日志和截图

左侧将 Helper 原始输出整理成聊天记录：时间只显示一次，群名和人名最多5个字符，不显示身份号码。群用不同底色、人用头像主色派生的文字颜色；本机缓存头像颜色，后台读取不阻塞输入，失败时使用稳定备用色。256色终端下文字与底色的对比度至少4.5:1。原始日志文件保持完整。

先把图片复制到剪贴板，再在控制台输入 `/paste`，会以 `【人工接管】 截图` 加图片发到当前选择的群。Tab 也能补全 `/paste`。只读取图片，不执行剪贴板文字；空剪贴板或不支持的格式不会发送。图片上限15MB、1600万像素，发送结果不确定时不重试。截图会进入本机 worker 的图片上下文。`runtime/outbox/` 保存发送文件，`context/ui-colors.sqlite3` 保存头像配色，均被gitignore排除。

## Lite 模式

终端不方便使用全屏界面时，运行 `python3 console.py --lite`，或双击 `Surf Lite.command`。Lite 使用普通逐行输出，不切换全屏、不设置颜色、不读取头像、不显示 surf bot 日志或 Helper 的系统杂项，只显示同样简化后的群聊和私聊记录；使用终端自身的滚动历史即可。

仍可输入 `/use`、`/say`、`/paste`、`/worker`、`/pause` 等命令，命令结果会就地显示。readline 可用时支持 Tab 补全和终端原生行编辑；输入未提交的命令时先缓存新的聊天行，提交后继续显示，避免覆盖半截输入。`/help` 列出可用命令，`/quit` 只退出界面。
