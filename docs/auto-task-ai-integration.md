# Autolab AI 异步任务后端合入

> 跨端项目进度以 [共享进度与实现说明](../../sduoj-deploy/docs/AI_PLATFORM_PROGRESS.md) 为准。本文介绍已有基础实现和接口操作，不表示原 Problem Stack 全部能力已经完整迁移、前端接通或生产验收。

更新：2026-09-06。本文记录 Autolab 后端；Web2 的工作由独立任务维护，本轮 P0 续推未修改前端或生产服务。

## 架构与范围

题栈的后端能力合入现有 FastAPI/SQLAlchemy/MySQL 工程，统一扩展 auto_task 这一条线，不再部署独立题栈 Web/API、PostgreSQL 或本地用户系统。Codex 仅作为受控模型执行程序。Web 进程与任务 Worker 使用同一份 Autolab 制品，但分别启动。

- 浏览器沿用 SDUOJ Gateway 的身份和 /api/ps/** 路由。
- 新接口实际外部前缀为 /api/ps/auto-task/ai，应用内部为 /auto-task/ai。
- 所有新接口遵循 GET/POST 与 code/message/data/timestamp 响应结构。
- 浏览器复用 OJ Cookie，不自行设置 SDUOJUserInfo；身份 header 由可信 Gateway 注入。
- 原 /auto-task/list、detail、subjective/review、summary/export、rerun、delete 保留。
- 任务状态以 MySQL 为权威，Redis pub/sub 仅通知唤醒。Redis 断开不会丢失已提交任务。
- 正式题单、课程、答题卡、用户和最终分数继续属于原有 OJ/Autolab 模型。
- AiAccount 只保存稳定 OJ userId 的积分、访问状态和用户名快照，没有密码或登录能力。

## 能力与实现位置

| 能力 | 后端实现 | 行为 |
| --- | --- | --- |
| 文字/多图/PDF/DOCX/PPTX 录题 | question.extract、documents、assets | 异步生成双语 Markdown、选项、答案、解释、标签、题图裁切与经过验证的 SVG；PPTX 保留基础 slide 顺序 |
| 全文与模糊检索 | resources/list | MySQL FULLTEXT ngram + 转义后的子串匹配；权限过滤后分页 |
| 去重 | canonical_question、apply_result | 按访问范围去重，选项顺序不参与题目身份，保留数学/代码大小写 |
| 自动解题、独立验证 | question.solve / question.verify | 中文答案；要求运行验证时实际调用隔离执行器，缺少执行条件不能标记验证成功 |
| 追问、纠错、题集 | question-workflows、question.chat、资源版本 CRUD | 稳定题号、批次幂等、拆分/恢复、增量合并、可选自动解题/验证；旧任务不能覆盖新版本 |
| 标签治理 | tags、tags/upsert | Unicode 规范化、别名和相似名称归并，自动登记概念标签，管理员维护别名 |
| 命题与测试 | authoring.generate、native-plan/finalize、authoring/bundle | 迭代修订题面、标程、generator/validator 与测试；复查原生 checkpoint 字节后才绑定题组 |
| 原生非编程题发布 | questions/{id}/publish | 复用 ProblemObjective/ProblemSubjective/ProblemGroup，支持 dry-run 和发布重放 |
| 原生三类题导入/程序题绑定 | native/import、native/program/* | 客观/主观题双向映射；程序题由 Java 原生创建并经 source/题面/题组复查后绑定 |
| 名单与考场编排 | exams/roster/parse、exam.plan | CSV 校验、可选 OJ 用户解析、最少考场和座位分配；不创建独立账号/密码 |
| 原生考试发布与考务 | exams/{id}/prepare/publish/bind-course/validate-test | 教师审核后创建 ProblemSet.type=1；绑定原生教室座位，真实 TEST 答题卡完成后签发验证证书，并提供监控/CSV |
| 主观题视觉批阅 | subjective.grade、原 subjective_review 适配器 | 原文档/图片解析接入新模型与计费上下文；候选分不自动覆盖人工分 |
| 教师修订/采用评分 | grading/{id}/edit/adopt | 候选逐项改分不直接修改原成绩；采用时复查原始答题、量表、当前分数及版本 |
| JPlag + AI | native/plagiarism/create、plagiarism.scan/review/writeback | 冻结每名学生的一份原生提交，保留完整 JPlag 候选，AI 解释后逐对复核并幂等回写原反作弊字段 |
| 四语言 IDE | ide.execute / ide.tutor | C/C++/Python/Java 沙箱；诊断式指导；考试资源禁止签发教学执行 |
| 课程资料与 RAG | course.index / course.ask | 复用 Autolab 课程授权，分块/引用/版本失效；显式开启才查询 Crossref |
| 实验验收 | interviews/*、interview.advance | 邀请绑定 OJ 学生与原答题快照，校验媒体片段清单、逐轮回答/失败恢复、教师复核并显式采用 |
| 费用和审核 | AiRun、AiLedger、AiReview、AiAccount | token/价格快照、实际成本、幂等流水；违规/七日三次警告停用 AI 功能，人工复核恢复 |
| 持久任务 | TaskJob、worker、store | 幂等提交、租约、心跳、attempt fencing、退避、取消、资源串行、并发/排队限额 |
| 任务观测 | jobs/detail、events、admin/queue | 游标日志、SSE、执行事件、模型用量、费用和队列统计 |
| 文件一致性 | admin/storage/orphans | 只读报告磁盘/数据库差异；显式、带原因、宽限期和批量上限的孤儿清理 |
| 原成绩报告 | SummaryReportTask | 沿用现有导出实现，改由持久 Worker 调度 |

编程题的正式创建和评测仍走现有 Java problem/submission 服务及 Web2 的原生发布流程；AI 后端输出可验证的命题包，不绕过 Java 权限直接写其数据库。账号和密码条仍属于 OJ 账号体系。这些边界是既定身份/资源归属要求，不重新引入题栈用户系统。

## 数据库

新增 13 张扩展表，均位于 Autolab 原 MySQL 数据库：

- auto_task_job：与原 auto_task_run 一对一，保存输入、幂等键、资源版本、租约和结果。
- auto_task_account / auto_task_ledger：外部主体的使用资格与不可变积分流水。
- auto_task_resource：题库、草案、知识库、会话及候选结果；引用原 course_id/psid。
- auto_task_review：内容审核和复核。
- auto_task_setting：模型参数及加密 API key。
- auto_task_ai_run：模型、token、价格快照、成本和 Codex session。
- auto_task_file / auto_task_file_link：媒体元数据与资源授权关系。
- auto_task_input：任务创建时的资源内容、版本、kind 与 SHA-256 快照。
- auto_task_revision：资源变更历史、删除标志、操作者及来源 task 审计。
- auto_task_plagiarism_snapshot：原生查重任务的不可变题单/题目/选择策略与整体哈希。
- auto_task_plagiarism_submission：快照内逐学生提交、代码哈希及原生判题信息。

SQL 审查稿见 [20260905_auto_task_ai.sql](sql/20260905_auto_task_ai.sql)，接口机器契约见 [auto-task-ai-openapi.json](auto-task-ai-openapi.json)。SQL 文件供审查，重复执行应使用带表/索引检查的迁移命令。

沿用 auto_task_run / auto_task_run_log，以保持原任务页面的数据协议。删除任务改为留痕隐藏与取消，不再清空审计日志。

任务内部时间使用 UTC，精度为微秒；原任务表的起止时间保留现有 UTC+8 序列化约定。金额用 Decimal，不使用浮点余额。原数据库只需增加表和索引，不移动或重建原业务表。

## 部署与本地开发

使用现有 oj_problem_set Python 环境和本地开发配置。先检查：

    SDUOJ_MODE=debug python -m auto_task.migrate

该命令默认只列出缺少的表/索引。明确确认数据库是本机开发库后执行：

    SDUOJ_MODE=debug python -m auto_task.migrate --apply

生产只能由用户在备份、审查和变更窗口后手工执行对应迁移，本次未执行生产迁移。不要依赖 Web 启动时自动建表；metadata.create_all 不能代替将来的列级版本迁移。

需要配置：

| 环境变量 | 说明 |
| --- | --- |
| AUTOLAB_AI_SECRET_KEY | Fernet 格式主密钥，来自受控密钥存储，不进 Git；必须备份以便解密已保存模型 key |
| AUTOLAB_AI_DATA_DIR | 持久数据根目录，建议置于代码版本目录之外 |
| AUTOLAB_CODEX_BIN | 固定版本 Codex CLI 的绝对路径 |
| AUTOLAB_START_TASK_WORKER | 默认不启动嵌入式消费者；仅本地调试可显式设 1 |
| AUTOLAB_SANDBOX_ENABLED | 默认关闭；隔离执行环境通过验证后设 1 |
| AUTOLAB_DOCKER_BIN / DOCKER_HOST | 专用隔离执行器；不能给生产 Web/Worker 宿主 root Docker 权限 |
| AUTOLAB_SANDBOX_C_IMAGE 等 | C/CPP/PYTHON/JAVA 运行镜像，生产固定验证后的 digest |
| AUTOLAB_JPLAG_JAR / AUTOLAB_JPLAG_IMAGE | JPlag 路径和 Java 25 JDK 执行镜像 |
| AUTOLAB_AI_WAKE | 默认 1；设 0 只使用数据库轮询，测试环境使用 0 |
| AUTOLAB_AI_TRUSTED_GATEWAY_CIDRS | release 必填的直连 Gateway CIDR；拒绝空值、无效值和全网段 |
| AUTOLAB_AI_VALIDATE_IDENTITY | debug 可显式设 0 跳过 Worker 回查；release 无论该值为何都强制回查 |

Web 继续使用现有启动入口。独立 Worker：

    SDUOJ_MODE=debug python -m auto_task.worker

生产 Worker 由 systemd 管理，使用与 Web 相同的代码和配置；需要多个 Worker 时启动多个实例，数据库统一控制并发。Web 的 Gunicorn 进程数不再决定任务消费者数量。不要同时保留旧 BLPOP 消费者。

通过管理员 settings/update 配置 provider、API 根地址、模型、思考强度、价格和并发；保存时必须回传 `GET /settings` 的 `version` 为 `expected_version`，陈旧编辑返回 409。切换 provider/base URL 必须同时提交新 key，防止旧 key 被误发到新地址；非 loopback 地址只接受 HTTPS，所有携带 provider Authorization 的请求禁用重定向。支持 Codex CLI 与兼容 Chat Completions；模型列表通过 settings/models 获取。provider 为 codex 时使用 Responses 协议。未配置密钥、密钥不可解密或未启用沙箱时，相关任务会明确失败，不伪造生成/执行结果。

生产仍须保证 Autolab 用户 API 仅经可信 Gateway 可达。应用同时要求唯一的 `SDUOJUserInfo` 与 `SDUOJUserInfo-UserId`，校验规范十进制 ID 与 JSON userId 一致，并拒绝重复 JSON key/异常 claim；已修正 Java form 编码中加号的解析。release 还会按直连 peer 检查 Gateway CIDR；若 Uvicorn/Gunicorn 已用 `X-Forwarded-For` 覆盖直连 peer，应用会返回 503，因此部署必须保留真实 Gateway TCP peer。身份 header 本身不是签名，不能把 5005 端口任意开放。新能力不要求另加一套 Nginx 页面路由或 Nacos 服务注册。生产切换前必须在预生产验证 Gateway 注入/清理双 header、进程代理头配置、Nacos 服务发现、同域路由和回滚，不在本轮执行。

## 主要 API

下面均相对 /api/ps/auto-task/ai，采用既有 OJ 响应包裹：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | /capabilities、/account | 能力和本人 AI 使用资格；停用用户仅能读取 /account 的原因/状态 |
| POST | /resources/create、/resources/list | 创建/筛选业务资源 |
| GET | /resources/{id} | 资源、版本与结果 |
| POST | /resources/{id}/update、/delete、/review | 更新、删除及候选复核 |
| POST | /question-workflows/intake、/questions/{id}/followup、/questions/{id}/restore | 题集批次、解题/验证/追问与显式版本恢复 |
| POST | /jobs/create、/jobs/list | 创建幂等任务、查询本人任务 |
| GET | /jobs/{id}、/jobs/{id}/events | 结果、游标日志、SSE |
| POST | /jobs/{id}/cancel、/retry | 取消；新操作键重试失败任务 |
| POST / GET | /files/upload、/files/{id} | 分片幂等上传与受授权下载 |
| GET / POST | /settings、/settings/update、/settings/models | 管理员模型配置，不返回明文 key |
| GET / POST | /points/ledger、/points/adjust | 本人流水、管理员调整 |
| GET / POST | /reviews、/reviews/{id}/update | 审核、复核与资格恢复 |
| GET | /admin/queue | 队列、费用和配置概要 |
| GET / POST | /admin/storage/orphans、/admin/storage/orphans/cleanup | 管理员文件一致性审计与默认 dry-run 的有界补偿 |
| GET / POST | /tags、/tags/upsert | 标签和别名 |
| POST | /questions/{id}/publish | 非编程题发布到原生题组 |
| GET / POST | /authoring/{id}/bundle、/generate、/native-plan、/native-finalize | 迭代命题、导出与原生 checkpoint 复核发布 |
| POST | /native/import、/native/program/*、/native/plagiarism/create | 原生三类题库与查重输入适配 |
| POST | /exams/roster/parse、/exams/{id}/prepare、/publish、/bind-course、/validate-test | 名单、考试发布、座位绑定与 TEST 门禁 |
| GET | /exams/{id}/monitor、/roster.csv | 原生答题卡状态与安全 CSV 导出 |
| POST | /grading/{id}/edit、/adopt | 人工修订并采用原答题卡候选分 |
| POST | /plagiarism/{id}/pairs/review、/writeback | 逐对复核并回写原生反作弊结论 |
| GET / POST | /interviews/* | 邀请、会话、媒体清单、回答、恢复、教师复核与采用 |

典型录题流程：

    POST /resources/create
    {"kind":"collection","data":{"title":"离散数学","text":"题面 Markdown","file_ids":[]}}

    POST /jobs/create
    {"task_type":"question.extract","resource_id":"上一步 id","operation_key":"本次操作稳定 UUID","payload":{}}

客户端重复请求须复用 operation_key，相同键不同输入返回 409；任务完成导致资源版本变化后，同一个请求重放仍返回原任务。后续主动重算应使用新的操作键。

资源更新必须提交读取到的 version；修改会使旧输入任务不能覆盖新内容。学生课程问答使用本人 conversation 资源绑定 course_id，不能写教师的共享 course 资源。学生不能读取其他人的 IDE/聊天，即使调用者是课程管理员。

验收 preflight/answer 都需要 operation_key；answer 还需当前 sequence。前置摄像头、麦克风、屏幕和单屏状态是客户端证据，不能当成不可伪造的作弊证明，最终结论仍需复核。媒体上传可带 resource_id 和 sequence 做分片幂等。

## 旧任务切换

1. 在开发/预生产验证迁移、提供者和沙箱，部署新 Web/Worker 制品但暂不开 Worker。
2. 用户在维护窗口冻结旧任务创建，停止旧 BLPOP 消费者，核对等待/运行任务与原人工成绩。
3. 为未完成的旧任务准备人工核实的 actor-map.json：键为旧 task ID，值为包含 userId/username 的实际发起教师。不能把 payload.username 中的被批阅学生当作付费/任务所有者。
4. 先检查，再显式导入：

       python -m auto_task.migrate --legacy-map actor-map.json
       python -m auto_task.migrate --legacy-map actor-map.json --apply --workers-stopped

5. 启动新 Worker，确认旧任务 ID、结果和日志可查；旧 Redis 队列留作受控归档，不再被消费。
6. 先验证候选批阅，再由教师通过 adopt 写入正式分数。前端本轮没有改动，所以旧页面不会凭空出现新的采用/复核按钮，需要后续按此契约接入。

原独立题栈 PostgreSQL 的历史资料不能直接复制进 MySQL。需要先导出、核实 OJ 身份/课程映射、转换资源/账本/文件后再导入；本轮没有迁移或删除原题栈的业务数据，也没有恢复它的本地账号体系。

## 验证与实际限制

本轮结果：快速全量套件 230 通过、11 跳过；独立 MySQL 8 代码快照 230 通过、9 跳过；核心真实容器 8/8，命题 generator/validator/标程真实容器 1/1；迁移/管理/身份/协作专项另有通过证据。此前本机 dev OJ 三类题目双向对接与程序题 Accepted/100 证据继续有效，最新程序题 SDUOJ-1813 也已 Accepted/100。当前服务契约由脚本生成并复核，为 91 条路径、13 张扩展表。详细复现步骤见 [AI_BACKEND_DEV_ACCEPTANCE.md](AI_BACKEND_DEV_ACCEPTANCE.md)。

- 新测试覆盖 SQLite 的快速行为测试以及独立本机 MySQL 8 的真实建表、全文索引、并发领取和事务。
- 模型结果与沙箱执行器在自动测试中使用确定性夹具；验证的是后端协议和业务状态，不是实际模型答案质量。
- 尚未进行生产迁移、付费真实模型 E2E、生产隔离执行器验收或摄像头/录屏硬件验收。不能据此声称已经通过全量生产替换验收。
- 快速全量的 10 个跳过项包含 1 个真实 MySQL 并发用例和 9 个真实 Docker 用例；独立 MySQL 与两组 Docker 专项已分别运行通过，不是业务失败。
- 签名回调/outbox 在独立题栈与 Autolab 跨库集成时有必要；本次候选成绩和采用操作已在同一 Autolab 数据库事务中完成，不再靠 HTTP 回调同步自己的成绩。
- 代码支持停止领取、租约失效和结果 fencing。外部模型已经开始计费的请求无法保证供应商立即停止；已返回的真实用量仍记录，未知用量标记 unsettled 并阻断同付款主体，管理员依据供应商证据幂等对账。
- 编程题正式评测、账号创建、课程/考试主数据和最终处分保留其原权威流程，不在 AI 任务内模拟网页或直接改 Java 数据库。
