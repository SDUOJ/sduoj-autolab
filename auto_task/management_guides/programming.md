# 程序题命题
1. 与管理员明确知识点、难度、输入输出格式、数据范围及测试要求；缺少条件先提问。
2. 先规划题面、正确解法、复杂度和边界测试。已有题目先查询原数据。
3. 逐项提出 OJ 操作：创建编程题基本信息、保存题面、配置测试点、读回验证。
4. 每次调用 oj_request 都会暂停等待管理员在 UI 确认。必须展示真实参数，不得用口头同意绕过审批。
5. 只有工具结果成功才可报告已保存；区分草稿、已保存、测试完成。禁止编造题号或测试结果。

## 已核对的接口字段
- 基本信息来自 ProblemManageDTO：problemTitle、source、isPublic、timeLimit、memoryLimit、outputLimit、judgeTemplates（ID 数组）、managerGroups（ID 数组）。更新需 problemCode。先向管理员确认限制与评测模板，模板可通过 /manage/judgetemplate/page 查询，不能编造 ID。
- 题面来自 ProblemDescriptionDTO：problemCode、title、markdownDescription、isPublic；更新使用 id。Markdown 包含完整题意、输入输出、样例和提示，不能使用不存在的 description/input/output 字段代替。
- 文本测试点 /checkpoint/upload：input、output，mode 可选 dos2unix/unix2dos。将返回的 checkpointId 作为引用；基本信息的 checkpoints 为 [{checkpointId,checkpointScore}]。查询原配置后保留已有测试点，并让管理员审阅完整修改。
- 不允许执行本地代码或外部命令。无法通过现有允许接口验证的测试结果必须标注未验证，不能宣称完成评测。
