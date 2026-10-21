# Context 与存储规范

本项目不训练个人语言模型，也不为每个人另存一份发言流水。群归档保存原始证据；人物记忆只描述这个人，群记忆描述这个群；当前聊天和模型观察属于会话层。

## 四层数据

| 层 | 存什么 | 不存什么 |
| --- | --- | --- |
| 原始证据 | 本群原始消息、作者、时间、引用、已有图片理解 | 不把 bot 的转述升级为原始事实 |
| 长期记忆 | 称呼、可复用事实、偏好、关系、边界，附证据与置信度 | 不存逐句引语合集，不推测稳定人格，不复制个人聊天流水 |
| 会话背景 | 当前话题、指代、待续互动、覆盖范围 | 不把“可能指谁”变成永久身份事实 |
| 工作视图 | 去重消息时间线、当前话题的相关记忆、待办 | 不同时塞入多份相同聊天或整个人物档案 |

## 文件结构与唯一事实源

实际路径以 workflow.json 的 context_dir 为根。路径清单见 [storage-manifest.json](../schemas/storage-manifest.json)。

```text
context/
  archive.sqlite3                   原始群消息与来源；回溯的证据库
  structured_memory.sqlite3         群/人物记忆唯一事实源，含修订与变更记录
  identities.sqlite3                QQ身份与全局代号映射，不按群复制
  groups/<group_id>/
    group.json                      群记忆 JSON 投影
    group.md                        人可读投影，含手工补充入口
    orientation.json                最近一次会话回溯背景
    requests/<request_id>.md         每窗口理解的阅读投影
  people/<user_id>/
    people.json                     全局人物记忆投影
    people.md                       人可读人物画像，不是该人的消息列表
  worker-understandings.sqlite3     各处理窗口的观察记录
  self/persona.md                   bot自身身份和风格背景
  memory.sqlite3                   旧记忆兼容层、窗口索引与短期观察缓存
  memory-maintenance.sqlite3        记忆整理队列、进度与失败记录
  legacy_memory/structured/         旧结构迁移前的只读快照
  maintenance/                     治理前数据库及原 Markdown 备份
  media/, emoticons.sqlite3         媒体原件、表情语义索引
  profiling/, quarantine/          建档引用进度、异常原始响应；不是权威记忆
  ui-colors.sqlite3                 可重建的终端配色缓存
```

当前没有活动的根目录 `structured/`。`legacy_memory/structured/` 是旧人物结构合并前的快照，由 memory_store.py 写入，用于恢复，不参与 worker 检索，不应继续充当第二份记忆目录。`structured_memory.sqlite3` 的 structured 表示内部有 schema，不代表还需要一个同名文件目录。

不通过移动整个 context 目录来解决语义混乱。先明确写入者和事实源，旧快照保持可恢复；临时视图不得反向覆盖权威记忆。

## 版本与 schema

- [memory.schema.json](../schemas/memory.schema.json)：`Group`、`Person` 及事实、关系、画像、状态的输入结构。记忆含 `schema_version`、`revision`、`updated_at`；更新使用修订号检测冲突。
- [context.schema.json](../schemas/context.schema.json)：`Orientation`、`Understanding`、`RecallBundle`、`WorkerContext` 和 `ProfileMemberPlan`，写入/生成边界执行校验。
- 数据库中的消息、队列和账号映射由各 owner 模块的 SQL DDL 定义，增量迁移使用现有列检查；这部分尚未统一成一个数据库 migration 版本，不能声称所有 SQLite 都有同一个 schema_version。
- Markdown 是投影，不是第二个 schema。除“手工补充”外，编辑正文可能被重建覆盖。治理前会备份 Markdown，并将当前“手工补充”收回结构化记忆。
- 新增字段要同步 schema、写入者、投影和测试；不能让 worker 自由创建文件或字段。旧版本必须显式转换，不能靠提示词假定兼容。

## 群聊 worker 的固定输入结构

```text
context_version: 1
bot_identity                  自身背景、运营者代号
conversation
  timeline                    单份去重时间线：代号：内容；unread标记本窗口新增
  reference_evidence          引用关联的原话及来源
  history_coverage            可见记录的范围与缺口
group_context
  id / name / participants    群身份与精简角色表
  portrait                    本群画像
  orientation                 预读得到的话题、指代和待续互动
  memory
    facts                     有依据的相关事实
    boundaries                相关成员和群的互动边界
    recent_state              仍有用的近期事项
    uncertainties             相关推测与未知，不作为已知事实
    manual_notes              手工补充，仍作为数据而非系统权限
  older_context               相关旧窗口与原话，按需核对
pending_requests              明确待办及可能的补充回答
request_slot                  当前5秒/20秒消息窗口的身份
```

每条消息不再附一张身份卡。QQ号、群名片、用户名和完整档案通过 get_member 查询。别名、角色、背景与原始发送者的区别不能丢失。

## 自动读记忆与主动澄清

1. 应用每轮按当前话题和当前发言人检索结构化记忆，自动填入 group_context.memory；不是只有群友问“还记得吗”才读取。
2. worker 需要更具体的事实时可调用 recall_memory(query, alias)。需要前因后果时调用 retrace_context(query)，补读原话后更新 orientation。
3. 不懂且确有必要弄清时，可以用 ask(alias, text, request_ids, quality="natural") 问当事人；明确待办缺关键信息不能仅写 uncertainties 后沉默。
4. ask 会按当前群成员校验对象、通过普通消息队列发出 @ 和问题，并将关联请求标成 needs_info；发送成功后为 waiting_info，而不是完成。
5. 对方后续发言使请求重新进入待处理，并保留补充回答的 event_key。没有答复就不把猜测改成事实，同一请求不连续盘问。
6. 一项不明确的请求不应吞掉另一项可独立处理的请求。

## 写入与治理

事实最少携带 text、evidence、confidence；应用补齐来源、首次/最后时间和 supported/tentative。同一事实合并证据；明确纠错应替换过时解释；未知没有证实前不能升级。人物的共性放全局人物记忆，只有本群独有的关系/边界放群作用域。

当前治理只自动合并完全相同的事实，合并证据并保存时间范围；不以字符串相似度擅自合并含义不同的事实。原始群归档不被删掉。数据库和原 Markdown 先备份，再按修订号提交，重新生成投影。语义上的旧错误仍需根据原话修订，不能把机械去重说成“全部画像已经正确”。

画像整理 worker 的输出应是可使用的知识，不是“某人先说A、又说B”。窗口理解只记录本轮新增变化；已经完成的事项不长期留在 open_threads。

## QQ 交互约束

- @：reply_parts 中使用 `{at: "成员代号"}`，应用解析为当前群成员；不开放 @全体。
- 引用回复：首段 `{reply_to: "本会话真实message_id"}`，应用校验归属，再发送 OneBot reply 段；通知没有可引用的 QQ 消息ID。
- 戳一戳：poke(alias) 或 finish_turn 的 poke 动作，进入持久 outbox；同成员有冷却，发送结果不明不自动重戳。
- 收到戳一戳：接收群/好友的 notify.poke，作为交互事件进入窗口，不把它写成对方说过的话，不自动对戳循环。
- 上述行为服从现有禁言、人工暂停、停止互动、群活跃和发送结果核对规则。

主服务数据库另有 speaker_events，作为约48小时的活跃判断/账号类型判别缓存和证据索引，不是长期人物档案；不向默认 worker 上下文注入个人发言样本。长期原话仍以群归档为准。

建档进度的每个成员仅保留 chunk_keys（群归档 event_key 引用）、覆盖范围、提取笔记和处理游标。整理画像时临时读取归档，不在进度文件重复保存每个成员的消息正文分块；旧进度在转换前备份。

措辞在生成时决定：只发新增反应，省略群友已读过的前提，短评可以只有两三个字。应用不通过文本相似度拦截、删字或代写回复，只校验接口、会话范围、收件人、权限和冷却等边界。
