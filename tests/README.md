# 测试分类与维护

运行完整套件（在项目根目录，使用已安装图片和终端依赖的 Python）：

```sh
python3 -m unittest discover -s tests -v
```

运行某一功能，例如协议：

```sh
python3 -m unittest discover -s tests -p 'test_protocol.py' -v
```

## 功能归属

| 文件 | 覆盖范围 |
| --- | --- |
| test_agent.py | 素材导入、抓取与基础发送 |
| test_qq.py | QQ 事件解析、鉴权与持久化 |
| test_workflow.py | 路由、去重、发送策略与回执 |
| test_protocol.py | worker 协议、信封冲突、空输出、内容过滤与恢复 |
| test_application.py | 应用接口权限、状态更新与成员证据 |
| test_model.py | ChatGPT 鉴权、模型重试计费、互联网工具 |
| test_memory.py | 记忆存储、维护与原话归档 |
| test_social.py | 群聊处理、窗口合并、草稿时效与任务优先级 |
| test_context_memory.py | 话题驱动检索、去重视图、治理备份和证据合并 |
| test_clarification.py | 主动澄清、等待补充和回答后恢复待办 |
| test_interactions.py | @、引用、群/好友双向戳一戳与会话范围校验 |
| test_orientation.py | 群会话预读、按需回溯、来源隔离与发言门槛 |
| test_context.py | 上下文检索、引用、局面理解与群隔离 |
| test_participants.py | 人类活跃门槛、成员类型、稳定代号与历史画像 |
| test_replies.py | 组合回复、原生表情、大表情收藏与反应推荐 |
| test_sharing.py | 合并转发、单图跨群分享与共同发送限制 |
| test_operations.py | 告警、诊断、用量报告与入群初始化 |
| test_console.py | 控制台、补全、人工接管与会话生命周期 |
| test_terminal.py | 聊天展示、轻量界面、滚动与鼠标输入 |
| test_moderation.py | 内容审核与互动边界 |
| test_private_chat.py | 私聊处理 |
| test_request_queue.py | 显式请求待办与执行结果 |
| test_vision.py | 图片输入与格式处理 |
| test_speech.py | 语音任务 |
| test_clipboard_owner.py | 剪贴板图片与主人操作，需要 Pillow |
| test_http_chunks.py | 本地 HTTP 回调集成，需要回环端口权限 |
| test_lite_pty.py | 真实终端集成，需要 POSIX PTY 和 prompt_toolkit |

`test_protocol.py` 的路径是 repairs.py 自动恢复流程的固定入口，需保留。
平台或依赖相关集成测试独立存放，避免其导入失败影响其他功能测试。
环境报错应修复依赖或权限，不能通过删除或跳过断言来制造通过结果。

## 本次清理

50 个文件收拢为 23 个；294 个测试方法保留 293 个。其余测试方法的语法树逐一核对，内容未变。中文宽度用例虽然输入相同，但覆盖不同实现，均保留。

删除一项重复测试：

- `test_old_normal_draft_is_not_force_sent`：由 `test_social.py` 中的
  `test_new_message_cancels_old_reply_before_send` 覆盖相同场景，后者还核验发送调用次数。


按单次修复命名的 `test_review_fixes.py` 已拆回协议、模型重试和转发分类。
新增测试优先放入现有功能文件；只有独立的功能域或运行环境才新建文件。
不同权限边界、错误分支与持久化行为即使使用相似初始化，也保留独立覆盖。
