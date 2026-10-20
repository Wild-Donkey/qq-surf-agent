# 记忆规范 v1

规范定义在 [schemas/memory.schema.json](schemas/memory.schema.json)。应用按这份规范校验写入，Markdown 仅是固定格式的可读视图。

| 对象 | 固定字段 | 归属 |
| --- | --- | --- |
| 群 | group_id、name、portrait、state、coverage | 单个群 |
| 成员 | user_id、account、portrait、state、coverage、groups | 账号主画像；groups只存少量明确局部例外 |
| 群画像 | summary、topics、communication、rules、relationships、boundaries、uncertainties | 长期观察 |
| 成员画像 | summary、aliases、style、interests、relationships、boundaries、uncertainties | 以人为主体的跨群观察 |
| 近期状态 | summary、current_topics、open_threads、decisions、boundaries | 当前会话与待办 |
| 观察事实 | text、evidence、confidence、first_seen、last_seen、source、status | 每条观察 |
| 关系 | subject_id、object_id、relation，及证据/置信度/来源/时间 | 可追溯的互动关系 |
| 覆盖 | message_count、time_start、time_end、complete、note | 本次可见取样，不代表所有历史 |

所有文档都有 schema_version、revision、updated_at、manual_notes。账号类型、修订号、时间、来源与覆盖由应用管理，worker 不能伪造。

## 写入规则

worker 通过 finish_turn 或 update_states 提交结构化状态。省略字段保留原值；显式空数组清空对应的当前状态字段。每条事实提交 text、evidence 和 confidence；应用补齐时间、来源、状态。证据必须是当前群归档中存在的 event_key，高置信度观察不能没有证据。未知字段、跨群证据和错误类型会被拒绝。

历史画像单独通过 publish_group_profile / publish_member_profile 接口提交 profile 对象。先完成全群画像，再逐成员完成画像。缺少证据的推测进入 uncertainties，不混入已支持的事实。

旧版字符串状态只作兼容输入，转换为 summary；新 worker 应提交字段对象，而不是随意追加 Markdown 段落。

## 存储与恢复

context/structured_memory.sqlite3 是事务性的权威存储。一次群与多成员状态更新先整体校验，再在同一事务提交；changes 表保留每次修订。group.json、people.json 和对应 Markdown 是可重新生成的投影。写文件中断后，应用从已提交的 dirty 文档恢复；并发写入使用修订检查，不能覆盖别的进程刚写好的状态。

人物主画像和一般状态跨群累积；groups 只保存显式提交的特殊群关系、称呼或边界。members[].scope 默认 person，仅明确局部例外使用 group。注入 worker 时给完整主画像，并仅附当前群的局部例外。每个群自己的 group.md 仍独立维护。长期原话仍保存在 archive.sqlite3 与按月 JSONL，工作缓存清理不会删除它们。

迁移前的 Markdown 完整保存在 context/legacy_memory。旧段落被归入固定字段，但不冒充重新验证的事实：来源为 migration、置信度保守、状态为 tentative。新一轮批量蒸馏将直接产出符合 schema 的画像。
