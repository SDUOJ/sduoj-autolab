# Problem Stack 离线迁移

迁移只针对已导出的本地 Prisma JSON 与文件副本。转换器不会连接原数据库、调用模型或改变生产。当前源格式为 `problem-stack-prisma-json-v1`，形如 `{"format":"problem-stack-prisma-json-v1","tables":{"Question":[],"User":[]}}`，表名与旧 `prisma/schema.prisma` 对应。

## 转换

```bash
python scripts/convert_problem_stack_export.py \
  --input /absolute/legacy.json \
  --identity-map /absolute/identities.json \
  --course-map /absolute/courses.json \
  --asset-source /absolute/legacy-assets
```

默认只审计。身份映射使用旧 owner ID 为键，值为已核实的 OJ `userId/username`。验收学生和考试考生另使用 `InterviewSession:<id>:student` / `ExamCandidate:<id>:student` 映射，不能把旧栈 UUID 或外部学号直接视作 OJ userId。课程映射使用 `CourseKnowledgeBase:<id>` 等来源 ID，值仅能包含一个有效 `course_id` 或 `psid`。

确认报告无缺失身份/课程/附件、关联断裂或非空未覆盖表后，添加 `--write --output /absolute/bundle/bundle.json --asset-output /absolute/bundle` 生成中立包与校验后的资产树。输出必须是新目录。未知字段会列在报告中供审查；新增未知非空表会阻止进入可导入状态。

## 导入

```bash
SDUOJ_MODE=debug python -m auto_task.migrate \
  --problem-stack-export /absolute/bundle/bundle.json \
  --identity-map /absolute/identities.json \
  --asset-root /absolute/bundle \
  --workers-stopped
```

默认只生成计划。添加 `--apply` 才会写入，且必须为 loopback debug 数据库并确认旧消费者已停止。稳定源 ID 生成确定的资源/文件 ID；相同输入重放复用，来源内容、身份映射、作用域或文件哈希变化会冲突。复制前后校验 SHA-256/大小，事务失败移除本轮新复制的文件；进程硬退出留下的孤儿由存储审计单独处理。

## 归属

| 源内容 | 当前去向 |
| --- | --- |
| 题目、标签、题集/稳定题号 | question/collection JSON、来源和用户收藏/标签历史 |
| 命题、消息、IDE/运行/指导 | authoring/ide 资源与历史 |
| 课程文档、分片、会话 | document/course/conversation 资源，课程映射和来源附件；需重建当前索引 |
| 验收、逐轮回答、录像、事件 | interview 资源和 AiFile 关联，保留候选与人工复核历史 |
| 考试、考场、考生 | exam 草案和 legacy_plan；不自动创建原生考试或临时账号 |
| 主观批阅、代码查重及匹配对 | grading/plagiarism 候选与历史证据；无当前原生答题卡写权限 |
| 账本、AiRun、WorkflowJob、ContentReview | 中立包的审计区，不再次扣费、停用账号或入队 |
| Intake、SolveJob、CallbackAttempt、AiSetting | 脱敏控制历史归档；不恢复模型会话/回调/密钥/任务执行 |

密码、API key、访问 token、旧模型 session、workspace 绝对路径和 callback URL 会被排除并在报告中记录字段路径。源码转换器覆盖旧 schema 的 42 个模型，但“可转换或归档”不等于真实生产数据已迁移或新系统可以直接采用旧分数。正式余额结转需用审核后的期初调整并单独对账，旧候选必须重新取得原生来源快照才能采用。

本轮使用合成脱敏导出和临时资产目录实测；未读取真实历史数据。真实全量导入仍需要提供本地源导出、资产根目录及核实后的映射，并审查转换报告。
