# 前端对接指南（管理端与客户端）+ 后端 TODO 清单

> 版本：v1.3  
> 日期：2026-02-28  
> 适用项目：SDUOJ Autolab（课程/教室/座位/签到）

---

## 1. 文档目标

本文件用于同步当前真实实现状态，避免前后端按旧契约联调：

1. 给出管理端与客户端的最新对接方式（路由、页面语义、接口）。
2. 说明课程中心化后的数据结构与权限规则。
3. 输出仍需推进的后端 TODO 清单（按优先级）。

统一约束：

- 前端网关前缀固定：`/api/ps/...`
- 后端路由仅使用 `GET/POST`
- 所有新课程相关接口返回统一通过 `@api_response` 封装（`makeResponse` + 分页兼容 `makePageResult`）

---

## 2. 当前实现快照（以代码为准）

## 2.1 课程中心化数据模型

课程主表：`oj_course`  
课程扩展字段：`ext_config`（JSON）

当前 `ext_config` 关键字段：

- `time_list`：课程时间列表（每项含 `time_id/start_time/end_time/schedule_id/...`）
- `sign_list`：签到记录 ID 列表（与 `time_list` 下标一一对应，无则 `null`）
- `ta_list`：助教列表
- `ta_student_map`：`{ TA_id: [username...] }`
- `manager_groups`：课程管理组（多选 group id）
- `creator_username`：课程创建人

说明：

- `oj_course_student_ta` 已从代码中停用，学生-助教绑定以 `ext_config.ta_student_map` 为准。
- 旧表数据通过兼容逻辑迁移到 `ext_config` 后继续使用。

## 2.2 权限规则（当前生效）

课程管理权限（M 端管理接口、C 端管理员视角）满足任一即通过：

- `superadmin`
- 课程创建人（`creator_username`）
- 当前用户 group 与 `manager_groups` 有交集

用户可见课程规则：

- 学生查看自己：可看自己所属组课程；若同时是课程管理者，也可看到其管理课程。
- 查看他人：仅课程管理者或 `superadmin` 可查看。

## 2.3 管理端路由与页面

课程一级菜单下二级页面：

- 课程管理：`/manage/course/course`
- 教室管理：`/manage/course/classroom`

管理端已落地：

- 课程 CRUD
- 教室 CRUD
- 课程时间管理（已合并到课程管理弹窗）
- 助教管理（新增/编辑/绑定学生，弹窗）
- 自动分配座位（弹窗，支持指定学生 + 指定教室）

UI 约束已落地：

- 操作列按钮使用 `link`
- 窄屏下操作列折叠为“更多”下拉
- 表格列宽以自适应为主，仅关键列定宽

## 2.4 客户端（C 端）课程栏目（Group -> course）

位置：Group 页面下 `course` 栏目。

当前行为：

- 按课程名称生成 Tab（不是按标签分组）
- 每门课展示：
  - 课程地点（绑定教室）
  - 当前学生座位分配
  - 助教信息
  - 课程时间表（表格）+ 对应签到状态
- 请假申请在 C 端完成（支持上传请假附件）
- 该栏目不再提供签到管理入口（仅查看与请假申请）

管理员学生视角切换：

- 管理员可看到“查看学生”选择框
- 非管理员不显示选择框，仅显示自己
- 管理员默认查看自己，可切换任意组内学生

## 2.5 客户端（C 端）课程管理栏目（Group -> courseManagement）

签到管理迁移到 `课程管理` 栏目（位于“外部成绩导入”后）：

- 统一页面展示多个课程卡片（不维护独立签到总表页面）
- 每个课程卡片展示该课程所有课次签到入口
- 点击某次签到后，使用弹窗进入签到详情（不是跳转新页面）
- 详情弹窗内提供两个 Tab：
  - `签到列表`
  - `请假审批`
- 批量操作入口：`批量考勤`（位于 Tab 标题右侧）
  - 仅对“无记录”学生生效
  - 右侧选中 -> 缺勤
  - 左侧未选中 -> 出勤
  - 已有记录学生状态保持不变

---

## 3. 管理端对接指南

## 3.1 教室管理

- `POST /api/ps/class/create`
- `GET /api/ps/class/{c_id}`
- `POST /api/ps/class/list`
- `POST /api/ps/class/{c_id}/update`
- `POST /api/ps/class/{c_id}/delete`

列表推荐字段：

- `c_id/c_name/address/c_seat_num/available_seats`

## 3.2 课程管理

- `POST /api/ps/course/create`
- `POST /api/ps/course/list`
- `GET /api/ps/course/{course_id}`
- `POST /api/ps/course/{course_id}/update`
- `POST /api/ps/course/{course_id}/delete`
- `POST /api/ps/course/{course_id}/assign-classrooms`

创建/编辑关键字段：

- `course_name`
- `group_id`（必填）
- `manager_groups`（多选）
- `tag`
- `c_ids`（多选）

## 3.3 课程时间（课程内弹窗）

- `GET /api/ps/course/{course_id}/times`
- `POST /api/ps/course/{course_id}/times/add`
- `POST /api/ps/course/{course_id}/times/{time_id}/update`
- `POST /api/ps/course/{course_id}/times/{time_id}/delete`

## 3.4 助教管理（课程内弹窗）

- `POST /api/ps/course/{course_id}/add-ta`
- `GET /api/ps/course/{course_id}/tas`
- `POST /api/ps/course/{course_id}/ta/{ta_id}/update`
- `POST /api/ps/course/ta/{ta_id}/delete`
- `POST /api/ps/course/{course_id}/ta/bind-students`
- `GET /api/ps/course/{course_id}/students`（组成员来源于 user-service）

## 3.5 自动分配座位（课程内弹窗）

- 选项查询：`GET /api/ps/seat/{course_id}/auto-assign-options`
- 执行分配：`POST /api/ps/seat/auto-assign`

请求体（已支持指定学生与指定教室）：

```json
{
  "course_id": 1,
  "usernames": ["202400130001", "202400130002"],
  "c_ids": [101, 102]
}
```

语义：

- 穿梭框左侧=待分配，右侧=本次要分配（仅右侧参与）
- 分配策略：优先剩余空位最多的教室
- 座位不足直接返回 400 与明确原因

---

## 4. 客户端对接指南（Group -> Course / CourseManagement）

## 4.1 课程列表

接口：`POST /api/ps/course/my/list`

新增入参：

- `group_id`：当前 group 页面 id
- `target_username`：管理员代看学生
- `tag/page_now/page_size`

返回关键字段：

- `courses[]`
- `can_manage`
- `viewer_username/target_username`
- `classrooms/seat_info/assigned_ta`
- `course_times/sign_list`

## 4.2 学生考勤记录

接口：`GET /api/ps/attendance/student/{username}/records`

支持：

- `course_id`
- `attendance_tag`
- `pageNow/pageSize`

权限：

- 查自己：正常
- 查他人：需要课程管理权限（或 superadmin）

## 4.3 请假申请（C 端）

流程：

1. `POST /api/ps/attendance/{course_id}/{schedule_id}/init`
2. `POST /api/ps/attendance/{sg_id}/leave?username={username}`

场景规则：

- 时间未到 / 未完成签到 / 已标缺勤：可申请请假
- 已签到 / 已批准请假：不可重复申请
- 上传请假附件使用文件服务返回的 file id

## 4.4 C 端签到管理（管理员，Group -> courseManagement）

管理入口位置：

- `课程管理` 栏目中的 `签到管理` Tab（在“外部成绩导入”后）

核心接口：

- `POST /api/ps/attendance/{course_id}/{schedule_id}/init`
- `GET /api/ps/attendance/{sg_id}`
- `POST /api/ps/attendance/{sg_id}/record`
- `POST /api/ps/attendance/{sg_id}/review-leave`

当前管理语义：

- 暂不使用扫码签到相关接口
- 暂不支持签退管理
- 管理员手动录入签到 + 请假审批
- 支持按学号检索学生

---

## 5. 错误处理约定（前端）

当前前端请求层约定：

- 遇到 `HTTP 400`，优先展示后端 `message/detail`，通过 antd `message.error` 弹出。
- 禁止将 400 直接当作后台崩溃处理。

---

## 6. 后端 TODO 清单（更新版）

| ID | 项目 | 优先级 | 当前状态 | 说明 |
|---|---|---|---|---|
| BE-01 | 课程级考勤统计接口 | P0 | 未完成 | 缺 `GET /api/ps/attendance/course/{course_id}/statistics` |
| BE-02 | 考勤导出能力 | P1 | 未完成 | 缺课程级/单次导出接口 |
| BE-03 | 签退语义与实现 | P1 | 未完成 | 现有 `sign_type=1` 仍未完整实现 |
| BE-04 | 禁用座位输出标准化 | P1 | 部分完成 | 已兼容读取 `seat-ban/disabled_seats`，建议统一对外输出结构 |
| BE-05 | 旧表物理清理迁移脚本 | P2 | 未完成 | `oj_course_student_ta` 已停用，尚未提供正式 DB migration |

---

## 7. 联调验收清单

1. 管理端可完成：建课 -> 配教室 -> 配时间 -> 配助教 -> 指定学生自动分座位。
2. 客户端可完成：按课程 tab 查看 -> 查看座位/助教 -> 申请请假（含附件）。
3. 管理员可在 C 端 `courseManagement -> 签到管理` 完成签到管理与请假审批。
4. 400 错误都能以可读 message 弹出，不出现静默失败。
5. 权限符合预期：仅创建者/管理组/superadmin 可操作 M 端课程接口与代看学生。
