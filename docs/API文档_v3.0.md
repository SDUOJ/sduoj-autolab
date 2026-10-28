# SDUOJ 实验课程与考勤管理系统 API 文档 v3.0

## 概述

本文档详细说明了实验课程与考勤管理系统的所有HTTP API接口。系统采用课程中心化架构，支持多教室分配、自动座位分配、六状态考勤管理等功能。

**基础信息：**
- 基础URL(网关): `http://127.0.0.1:5005/api/ps`
- 后端原始路径: `http://127.0.0.1:5005/{xxx}`（由网关统一转发）
- 认证方式: Token认证（请求头需携带token）
- 响应格式: JSON
- 字符编码: UTF-8
- 请求方法约束: 仅支持 `POST` 与 `GET`

**通用响应格式：**
```json
{
  "code": 0,              // 0-成功, 非0-失败
  "message": "success",   // 提示信息
  "data": {},             // 响应数据
  "timestamp": 1234567890 // 时间戳
}
```

---

## 一、课程管理模块

### 1.1 创建课程

**接口**: `POST /api/ps/course/create`

**描述**: 创建新课程

**请求头**:
```
Authorization: Bearer <token>
Content-Type: application/json
```

**请求体**:
```json
{
  "course_name": "数据结构实验课",
  "group_id": 123,
  "tag": "实验",
  "c_ids": [1, 2],
  "ext_config": {
    "description": "大二上学期"
  }
}
```

**字段说明**:
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| course_name | string | 是 | 课程名称 |
| group_id | int | 是 | 用户组ID |
| tag | string | 是 | 课程标签：授课/实验/考试/答疑 |
| c_ids | array[int] | 否 | 教室ID列表 |
| ext_config | object | 否 | 扩展配置（自定义JSON） |

**响应示例**:
```json
{
  "code": 0,
  "message": "课程创建成功",
  "data": {
    "course_id": 456
  }
}
```

---

### 1.2 获取课程详情

**接口**: `GET /api/ps/course/{course_id}`

**描述**: 获取指定课程的详细信息

**路径参数**:
- `course_id` (int): 课程ID

**响应示例**:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "course_id": 456,
    "course_name": "数据结构实验课",
    "group_id": 123,
    "tag": "实验",
    "c_ids": [1, 2],
    "ext_config": {
      "description": "大二上学期"
    },
    "create_time": "2024-01-15 10:30:00"
  }
}
```

---

### 1.3 查询课程列表

**接口**: `POST /api/ps/course/list`

**描述**: 查询课程列表（支持分页和过滤）

**请求体**:
```json
{
  "group_id": 123,
  "tag": "实验",
  "page_now": 1,
  "page_size": 20
}
```

**字段说明**:
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| group_id | int | 否 | 用户组ID过滤 |
| tag | string | 否 | 课程标签过滤 |
| page_now | int | 否 | 当前页码（默认1） |
| page_size | int | 否 | 每页数量（默认20，最大100） |

**响应示例**:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "total": 50,
    "page_now": 1,
    "page_size": 20,
    "courses": [
      {
        "course_id": 456,
        "course_name": "数据结构实验课",
        "group_id": 123,
        "tag": "实验",
        "c_ids": [1, 2],
        "ext_config": {},
        "create_time": "2024-01-15 10:30:00"
      }
    ]
  }
}
```

---

### 1.4 更新课程信息

**接口**: `POST /api/ps/course/{course_id}/update`

**描述**: 更新课程信息

**路径参数**:
- `course_id` (int): 课程ID

**请求体**:
```json
{
  "course_name": "数据结构与算法实验",
  "tag": "实验",
  "c_ids": [1, 2, 3]
}
```

**字段说明**: 所有字段可选，只更新提供的字段

**响应示例**:
```json
{
  "code": 0,
  "message": "课程更新成功"
}
```

---

### 1.5 删除课程

**接口**: `POST /api/ps/course/{course_id}/delete`

**描述**: 删除课程（级联删除所有关联数据：课程时间、座位绑定、考勤记录等）

**路径参数**:
- `course_id` (int): 课程ID

**响应示例**:
```json
{
  "code": 0,
  "message": "课程删除成功"
}
```

**注意**: 此操作不可逆，会删除所有关联数据

---

### 1.6 分配教室

**接口**: `POST /api/ps/course/{course_id}/assign-classrooms`

**描述**: 为课程分配一个或多个教室

**路径参数**:
- `course_id` (int): 课程ID

**请求体**:
```json
{
  "c_ids": [1, 2, 3]
}
```

**响应示例**:
```json
{
  "code": 0,
  "message": "教室分配成功"
}
```

---

### 1.7 添加助教

**接口**: `POST /api/ps/course/{course_id}/add-ta`

**描述**: 为课程添加助教

**路径参数**:
- `course_id` (int): 课程ID

**请求体**:
```json
{
  "ta_name": "张三",
  "ext_info": {
    "phone": "13800138000",
    "email": "zhangsan@example.com",
    "office": "实验楼301"
  },
  "usernames": ["20220101", "20220102"]
}
```

**说明**:
- `usernames` 可选，若传入则在创建助教后立即为这些学生绑定该助教。

**响应示例**:
```json
{
  "code": 0,
  "message": "助教添加成功",
  "data": {
    "TA_id": 789
  }
}
```

---

### 1.8 查询助教列表

**接口**: `GET /api/ps/course/{course_id}/tas`

**描述**: 查询课程的所有助教

**路径参数**:
- `course_id` (int): 课程ID

**响应示例**:
```json
{
  "code": 0,
  "message": "success",
  "data": [
    {
      "TA_id": 789,
      "TA_name": "张三",      
      "course_id": 456,
      "bind_student_count": 2,
      "ext_info": {
        "phone": "13800138000",
        "email": "zhangsan@example.com"
      }
    }
  ]
}
```

---

### 1.9 删除助教

**接口**: `POST /api/ps/course/ta/{ta_id}/delete`

**描述**: 删除助教

**路径参数**:
- `ta_id` (int): 助教ID

**响应示例**:
```json
{
  "code": 0,
  "message": "助教删除成功"
}
```

---

### 1.10 绑定学生到助教

**接口**: `POST /api/ps/course/{course_id}/ta/bind-students`

**描述**: 为课程中的学生批量绑定指定助教（每个学生在该课程仅绑定一个助教）

**路径参数**:
- `course_id` (int): 课程ID

**请求体**:
```json
{
  "TA_id": 789,
  "usernames": ["20220101", "20220102", "20220103"]
}
```

**响应示例**:
```json
{
  "code": 0,
  "message": "学生助教绑定成功",
  "data": {
    "bound_students": 3
  }
}
```

---

### 1.11 用户端课程列表

**接口**: `POST /api/ps/course/my/list`

**描述**: 获取当前用户课程列表（含课程时间、课程地点、座位号、绑定助教）

**请求体**:
```json
{
  "group_id": 83,
  "target_username": "20220101",
  "tag": "实验",
  "page_now": 1,
  "page_size": 20
}
```

**字段说明**:
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| group_id | int | 否 | 按组过滤（建议在 group 页面传入） |
| target_username | string | 否 | 管理员代看学生（需课程管理权限） |
| tag | string | 否 | 课程标签过滤 |
| page_now | int | 否 | 页码，默认1 |
| page_size | int | 否 | 每页数量，1-100 |

**响应示例**:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "total": 2,
    "page_now": 1,
    "page_size": 20,
    "courses": [
      {
        "course_id": 456,
        "course_name": "Java实验",
        "group_id": 83,
        "tag": "实验",
        "course_times": [
          {"schedule_id": 1001, "sequence": 1, "start_time": "2026-03-01T08:00:00", "end_time": "2026-03-01T10:00:00"},
          {"schedule_id": 1002, "sequence": 2, "start_time": "2026-03-08T08:00:00", "end_time": "2026-03-08T10:00:00"}
        ],
        "sign_list": [5001, 5002],
        "classrooms": [
          {"c_id": 1, "c_name": "实验楼101", "address": "实验楼1层"}
        ],
        "seat_info": {"c_id": 1, "classroom_name": "实验楼101", "address": "实验楼1层", "seat_number": 10},
        "assigned_ta": {"TA_id": 789, "TA_name": "张三", "ext_info": {"phone": "13800138000"}},
        "viewer_username": "teacher01",
        "target_username": "20220101",
        "can_manage": true
      }
    ]
  }
}
```

---

## 二、课程时间管理模块

### 2.1 添加课程时间

**接口**: `POST /api/ps/schedule/add`

**描述**: 为课程添加上课时间（自动创建对应考勤记录）

**请求体**:
```json
{
  "course_id": 456,
  "sequence": 1,
  "start_time": "2024-09-01T08:00:00",
  "end_time": "2024-09-01T10:00:00",
  "course_content": "第一章：绪论",
  "course_materials": ["file_id_001", "file_id_002"],
  "course_homework": "完成课后习题1-5",
  "sg_id": 10,
  "auto_create_sign": true
}
```

**字段说明**:
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| course_id | int | 是 | 课程ID |
| sequence | int | 是 | 课程序号（第几次课） |
| start_time | datetime | 是 | 开始时间（ISO 8601格式） |
| end_time | datetime | 是 | 结束时间（ISO 8601格式） |
| course_content | string | 否 | 课程内容 |
| course_materials | array[string] | 否 | 课程资料文件ID列表 |
| course_homework | string | 否 | 课程作业 |
| sg_id | int | 否 | 座位组ID |
| auto_create_sign | bool | 否 | 是否自动创建考勤记录（默认true） |

**响应示例**:
```json
{
  "code": 0,
  "message": "课程时间创建成功",
  "data": {
    "schedule_id": 1001
  }
}
```

---

### 2.2 获取课程时间详情

**接口**: `GET /api/ps/schedule/{schedule_id}`

**描述**: 获取指定课程时间的详细信息

**路径参数**:
- `schedule_id` (int): 课程时间ID

**响应示例**:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "schedule_id": 1001,
    "course_id": 456,
    "sequence": 1,
    "start_time": "2024-09-01 08:00:00",
    "end_time": "2024-09-01 10:00:00",
    "course_content": "第一章：绪论",
    "course_materials": ["file_id_001", "file_id_002"],
    "course_homework": "完成课后习题1-5",
    "sg_id": 10
  }
}
```

---

### 2.3 查询课程时间列表

**接口**: `POST /api/ps/schedule/list`

**描述**: 查询课程时间列表（支持分页和过滤）

**请求体**:
```json
{
  "course_id": 456,
  "page_now": 1,
  "page_size": 50
}
```

**字段说明**:
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| course_id | int | 否 | 课程ID过滤 |
| page_now | int | 否 | 当前页码（默认1） |
| page_size | int | 否 | 每页数量（默认50，最大200） |

**响应示例**:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "total": 16,
    "page_now": 1,
    "page_size": 50,
    "schedules": [
      {
        "schedule_id": 1001,
        "course_id": 456,
        "sequence": 1,
        "start_time": "2024-09-01 08:00:00",
        "end_time": "2024-09-01 10:00:00",
        "course_content": "第一章：绪论",
        "course_materials": ["file_id_001"],
        "course_homework": "完成课后习题1-5",
        "sg_id": 10
      }
    ]
  }
}
```

---

### 2.4 更新课程时间

**接口**: `POST /api/ps/schedule/{schedule_id}/update`

**描述**: 更新课程时间信息

**路径参数**:
- `schedule_id` (int): 课程时间ID

**请求体**:
```json
{
  "course_content": "第一章：绪论（修订版）",
  "course_homework": "完成课后习题1-10"
}
```

**字段说明**: 所有字段可选，只更新提供的字段

**响应示例**:
```json
{
  "code": 0,
  "message": "课程时间更新成功"
}
```

---

### 2.5 删除课程时间

**接口**: `POST /api/ps/schedule/{schedule_id}/delete`

**描述**: 删除课程时间（级联删除相关考勤记录）

**路径参数**:
- `schedule_id` (int): 课程时间ID

**响应示例**:
```json
{
  "code": 0,
  "message": "课程时间删除成功"
}
```

---

## 三、教室管理模块

### 3.1 创建教室

**接口**: `POST /api/ps/class/create`

**描述**: 创建教室

**请求体**:
```json
{
  "c_name": "实验楼101",
  "address": "实验楼1层",
  "c_seat_num": 50,
  "ext_config": {
    "seat-ban": [
      {"seat_number": 5, "reason": "座位损坏"}
    ]
  }
}
```

**响应示例**:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "c_id": 1
  }
}
```

---

### 3.2 查询教室详情

**接口**: `GET /api/ps/class/{c_id}`

**描述**: 查询单个教室详情

**路径参数**:
- `c_id` (int): 教室ID

**响应示例**:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "c_id": 1,
    "c_name": "实验楼101",
    "address": "实验楼1层",
    "c_seat_num": 50,
    "ext_config": {
      "seat-ban": [
        {"seat_number": 5, "reason": "座位损坏"}
      ]
    },
    "available_seats": 49
  }
}
```

---

### 3.3 教室列表（分页）

**接口**: `POST /api/ps/class/list`

**描述**: 教室列表查询（支持分页与关键字搜索）

**请求体**:
```json
{
  "pageNow": 1,
  "pageSize": 20,
  "keyword": "实验楼"
}
```

**响应示例**:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "pageIndex": 1,
    "pageSize": 20,
    "totalNum": 2,
    "rows": [
      {
        "c_id": 2,
        "c_name": "实验楼102",
        "address": "实验楼1层",
        "c_seat_num": 50,
        "ext_config": {},
        "available_seats": 50
      }
    ]
  }
}
```

---

### 3.4 更新教室

**接口**: `POST /api/ps/class/{c_id}/update`

**描述**: 更新教室信息

**路径参数**:
- `c_id` (int): 教室ID

**请求体**:
```json
{
  "c_name": "实验楼101A",
  "address": "实验楼1层东区",
  "c_seat_num": 52
}
```

**响应示例**:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "msg": "教室更新成功"
  }
}
```

---

### 3.5 删除教室

**接口**: `POST /api/ps/class/{c_id}/delete`

**描述**: 删除教室（若存在课程引用或座位绑定会被阻止）

**路径参数**:
- `c_id` (int): 教室ID

**响应示例**:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "msg": "教室删除成功"
  }
}
```

---

## 四、座位管理模块

### 4.1 分配座位

**接口**: `POST /api/ps/seat/{course_id}/assign`

**描述**: 为指定学生分配座位

**路径参数**:
- `course_id` (int): 课程ID

**请求体**:
```json
{
  "username": "20220101",
  "seat_number": 15
}
```

**响应示例**:
```json
{
  "code": 0,
  "message": "座位分配成功"
}
```

---

### 4.2 自动分配座位

**接口**: `POST /api/ps/seat/auto-assign`

**描述**: 自动为用户组内所有学生分配座位

**请求体**:
```json
{
  "course_id": 456,
  "group_id": 123
}
```

**响应示例**:
```json
{
  "code": 0,
  "message": "自动分配成功，共分配45个座位"
}
```

**说明**: 
- 系统会自动跳过不可用座位
- 座位不足时返回错误
- 已有座位的学生会被跳过

---

### 4.3 获取座位分布图

**接口**: `GET /api/ps/seat/{course_id}/map`

**描述**: 获取课程的完整座位分布图

**路径参数**:
- `course_id` (int): 课程ID

**响应示例**:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "course_id": 456,
    "seat_bindings": {
      "1": "20220101",
      "2": "20220102",
      "15": "20220115"
    },
    "classrooms": [
      {
        "c_id": 1,
        "c_name": "实验楼101",
        "c_seat_num": 50,
        "address": "实验楼一层",
        "disabled_seats": [13, 14]
      }
    ]
  }
}
```

---

### 4.4 查询学生座位

**接口**: `GET /api/ps/seat/{course_id}/user/{username}`

**描述**: 查询指定学生的座位号

**路径参数**:
- `course_id` (int): 课程ID
- `username` (string): 学生用户名

**响应示例**:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "username": "20220101",
    "seat_number": 15
  }
}
```

---

### 4.5 删除座位绑定

**接口**: `POST /api/ps/seat/{course_id}/user/{username}/delete`

**描述**: 删除学生的座位绑定

**路径参数**:
- `course_id` (int): 课程ID
- `username` (string): 学生用户名

**响应示例**:
```json
{
  "code": 0,
  "message": "座位绑定删除成功"
}
```

---

## 五、考勤管理模块

### 5.1 初始化考勤

**接口**: `POST /api/ps/attendance/{course_id}/{schedule_id}/init`

**描述**: 为指定课程时间初始化考勤（创建考勤记录并初始化学生名单）

**路径参数**:
- `course_id` (int): 课程ID
- `schedule_id` (int): 课程时间ID

**请求体**:
```json
{
  "group_id": 123
}
```

**响应示例**:
```json
{
  "code": 0,
  "message": "初始化成功，共45个学生",
  "data": {
    "sg_id": 5001
  }
}
```

---

### 5.2 获取考勤名单

**接口**: `GET /api/ps/attendance/{sg_id}`

**描述**: 获取考勤的完整名单和统计信息

**路径参数**:
- `sg_id` (int): 考勤ID

**响应示例**:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "sg_id": 5001,
    "course_id": 456,
    "schedule_id": 1001,
    "sign_mode": 0,
    "course_time": {
      "start_time": "2024-09-01T08:00:00",
      "end_time": "2024-09-01T10:00:00"
    },
    "students": [
      {
        "username": "20220101",
        "status": 1,
        "seat_number": 15,
        "check_in_time": "2024-09-01 08:05:00",
        "check_out_time": "2024-09-01 09:55:00",
        "leave_message": null,
        "leave_files": null,
        "leave_status": null
      },
      {
        "username": "20220102",
        "status": 4,
        "seat_number": 16,
        "check_in_time": null,
        "check_out_time": null,
        "leave_message": "生病住院",
        "leave_files": ["file_id_003"],
        "leave_status": 1
      }
    ],
    "statistics": {
      "出勤": 40,
      "缺勤": 2,
      "迟到/早退": 1,
      "请假已批准": 2,
      "请假申请中": 0,
      "无记录": 0
    }
  }
}
```

**status字段说明**:
- 0: 无记录
- 1: 出勤
- 2: 缺勤
- 3: 迟到/早退
- 4: 请假已批准
- 5: 请假申请中

**leave_status字段说明**:
- null: 未提交请假
- 0: 申请中
- 1: 批准
- 2: 拒绝

---

### 5.3 学生签到（当前实现不支持签退）

**接口**: `POST /api/ps/attendance/{sg_id}/sign-in`

**描述**: 学生签到

**路径参数**:
- `sg_id` (int): 考勤ID

**请求体**:
```json
{
  "username": "20220101",
  "sign_type": 0
}
```

**字段说明**:
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| username | string | 是 | 学生用户名（通常从token获取） |
| sign_type | int | 是 | 签到类型：0-签到（当前仅支持0） |

**响应示例**:
```json
{
  "code": 0,
  "message": "签到成功"
}
```

**业务规则**:
- 签到时间晚于课程开始时间 → 状态为"迟到/早退"
- 签到时间早于或等于课程开始时间 → 状态为"出勤"
- 已批准请假的学生无法签到
- `sign_type=1` 当前返回“不支持签退”

---

### 5.4 提交请假申请

**接口**: `POST /api/ps/attendance/{sg_id}/leave`

**描述**: 学生提交请假申请

**路径参数**:
- `sg_id` (int): 考勤ID

**查询参数**:
- `username` (string): 学生用户名

**请求体**:
```json
{
  "leave_message": "因病请假，已在校医院就诊",
  "leave_files": ["file_id_004", "file_id_005"]
}
```

**字段说明**:
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| leave_message | string | 是 | 请假理由 |
| leave_files | array[string] | 否 | 请假附件（文件ID列表） |

**响应示例**:
```json
{
  "code": 0,
  "message": "请假申请提交成功"
}
```

**说明**: 
- 提交后状态自动变为"请假申请中"
- 支持重新申请（被拒绝后可再次提交）

---

### 5.5 审批请假申请

**接口**: `POST /api/ps/attendance/{sg_id}/review-leave`

**描述**: 教师审批学生的请假申请

**路径参数**:
- `sg_id` (int): 考勤ID

**请求体**:
```json
{
  "username": "20220102",
  "approved": true
}
```

**字段说明**:
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| username | string | 是 | 学生用户名 |
| approved | bool | 是 | 是否批准（true-批准, false-拒绝） |

**响应示例**:
```json
{
  "code": 0,
  "message": "请假审批完成"
}
```

**说明**:
- 批准后状态变为"请假已批准"
- 拒绝后状态恢复为"无记录"，学生可重新申请

---

### 5.6 更新考勤模式

**接口**: `POST /api/ps/attendance/{sg_id}/update-mode`

**描述**: 更新考勤模式（签到+签退 / 仅签到）

**路径参数**:
- `sg_id` (int): 考勤ID

**请求体**:
```json
{
  "sign_mode": 0
}
```

**sign_mode字段说明**:
- 0: 签到+签退（需要两次签到）
- 1: 仅签到（只需签到一次）

**响应示例**:
```json
{
  "code": 0,
  "message": "考勤模式更新成功"
}
```

---

### 5.7 标记缺勤

**接口**: `POST /api/ps/attendance/{sg_id}/mark-absence`

**描述**: 将学生标记为缺勤（用于批量处理未签到学生）

**路径参数**:
- `sg_id` (int): 考勤ID

**查询参数**:
- `username` (string): 学生用户名

**响应示例**:
```json
{
  "code": 0,
  "message": "标记缺勤成功"
}
```

**说明**: 只有状态为"无记录"的学生才能被标记为缺勤

---

### 5.8 查询学生考勤记录（用户端）

**接口**: `GET /api/ps/attendance/student/{username}/records`

**描述**: 查询当前学生的全部考勤记录，支持按课程和 `attendance_tag` 过滤

**路径参数**:
- `username` (string): 学生用户名

**查询参数**:
- `course_id` (int, 可选): 课程过滤
- `attendance_tag` (string, 可选): `future/signed/absent/leave_approved`
- `pageNow` (int, 可选): 默认1
- `pageSize` (int, 可选): 默认20

**响应示例**:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "username": "20220101",
    "total": 4,
    "page_now": 1,
    "page_size": 20,
    "statistics": {
      "future": 1,
      "signed": 1,
      "absent": 1,
      "leave_approved": 1
    },
    "records": [
      {
        "sg_u_id": 9001,
        "sg_id": 5001,
        "course_id": 456,
        "course_name": "Java实验",
        "course_tag": "实验",
        "schedule_id": 1001,
        "sequence": 1,
        "start_time": "2026-03-01T08:00:00",
        "end_time": "2026-03-01T10:00:00",
        "status": 1,
        "leave_status": null,
        "seat_number": 10,
        "check_in_time": "2026-03-01T07:58:00",
        "leave_message": null,
        "leave_files": null,
        "attendance_tag": "signed",
        "color_tag": "green"
      }
    ]
  }
}
```

**颜色语义建议**:
- `future` -> 灰色
- `signed` -> 绿色
- `absent` -> 红色（含未请假与审批中）
- `leave_approved` -> 浅绿色

---

## 六、错误码说明

| 错误码 | 说明 |
|--------|------|
| 0 | 成功 |
| 400 | 请求参数错误 |
| 403 | 未登录或无权限 |
| 404 | 资源不存在 |
| 500 | 服务器内部错误 |

**常见错误示例**:

```json
{
  "code": 400,
  "message": "课程不存在",
  "data": null
}
```

```json
{
  "code": 403,
  "message": "未登录",
  "data": null
}
```

---

## 七、典型业务流程

### 7.1 创建课程并安排上课时间

1. 创建课程: `POST /api/ps/course/create`
2. 分配教室: `POST /api/ps/course/{course_id}/assign-classrooms`
3. 添加助教: `POST /api/ps/course/{course_id}/add-ta`
4. 添加课程时间: `POST /api/ps/schedule/add`（自动创建考勤）

### 7.2 学生座位分配

1. 自动分配: `POST /api/ps/seat/auto-assign`
2. 或手动分配: `POST /api/ps/seat/{course_id}/assign`
3. 查看分布: `GET /api/ps/seat/{course_id}/map`

### 7.3 考勤流程

1. 初始化考勤: `POST /api/ps/attendance/{course_id}/{schedule_id}/init`
2. 学生签到: `POST /api/ps/attendance/{sg_id}/sign-in` (sign_type=0)
3. 查看考勤: `GET /api/ps/attendance/{sg_id}`
4. （可选）签退请求目前不支持: `POST /api/ps/attendance/{sg_id}/sign-in` (sign_type=1)

### 7.4 请假流程

1. 学生提交: `POST /api/ps/attendance/{sg_id}/leave`
2. 教师审批: `POST /api/ps/attendance/{sg_id}/review-leave`
3. 查看结果: `GET /api/ps/attendance/{sg_id}`

---

## 八、文件上传说明

课程资料（course_materials）和请假附件（leave_files）使用SDUOJ文件接口上传，上传后获得文件ID，将ID填入相应字段。

**文件ID格式**: 字符串数组，例如 `["file_001", "file_002"]`

---

## 九、数据库状态说明

### 9.1 考勤状态（status）

| 值 | 状态 | 说明 |
|----|------|------|
| 0 | 无记录 | 学生未签到也未请假 |
| 1 | 出勤 | 正常签到签退 |
| 2 | 缺勤 | 教师手动标记缺勤 |
| 3 | 迟到/早退 | 签到/签退时间不符合要求 |
| 4 | 请假已批准 | 请假申请已通过 |
| 5 | 请假申请中 | 等待教师审批 |

### 9.2 请假状态（leave_status）

| 值 | 状态 | 说明 |
|----|------|------|
| NULL | 未提交 | 未提交请假申请 |
| 0 | 申请中 | 等待审批 |
| 1 | 批准 | 请假已批准 |
| 2 | 拒绝 | 请假被拒绝 |

---

## 十、补充说明

1. **所有接口都需要Token认证**，请在请求头中添加：`Authorization: Bearer <token>`

2. **时间格式**: ISO 8601格式，例如 `2024-09-01T08:00:00` 或 `2024-09-01 08:00:00`

3. **分页参数**:
   - 课程/排课等接口使用 `page_now`、`page_size`
   - 教室列表使用 `pageNow`、`pageSize`，响应为 `pageIndex/pageSize/totalNum/rows`

4. **级联删除**: 删除课程会级联删除所有相关数据（课程时间、座位绑定、考勤记录等）

5. **JSON字段**: `ext_config`、`ext_info`等字段支持自定义JSON结构，用于扩展功能

6. **自动创建**: 添加课程时间时会自动创建对应的考勤记录（可通过`auto_create_sign`控制）

7. **多教室支持**: `c_ids`字段为数组，支持一个课程分配多个教室

8. **座位冲突检测**: 分配座位时会自动检查是否已被占用

9. **删除语义**: 教室删除为“依赖阻止删除”（被课程引用或存在座位绑定时返回错误）

---

## 附录：完整接口列表

### 课程管理 (11个接口)
- POST /api/ps/course/create
- GET /api/ps/course/{course_id}
- POST /api/ps/course/list
- POST /api/ps/course/{course_id}/update
- POST /api/ps/course/{course_id}/delete
- POST /api/ps/course/{course_id}/assign-classrooms
- POST /api/ps/course/{course_id}/add-ta
- GET /api/ps/course/{course_id}/tas
- POST /api/ps/course/ta/{ta_id}/delete
- POST /api/ps/course/{course_id}/ta/bind-students
- POST /api/ps/course/my/list

### 课程时间管理 (5个接口)
- POST /api/ps/schedule/add
- GET /api/ps/schedule/{schedule_id}
- POST /api/ps/schedule/list
- POST /api/ps/schedule/{schedule_id}/update
- POST /api/ps/schedule/{schedule_id}/delete

### 教室管理 (5个接口)
- POST /api/ps/class/create
- GET /api/ps/class/{c_id}
- POST /api/ps/class/list
- POST /api/ps/class/{c_id}/update
- POST /api/ps/class/{c_id}/delete

### 座位管理 (5个接口)
- POST /api/ps/seat/{course_id}/assign
- POST /api/ps/seat/auto-assign
- GET /api/ps/seat/{course_id}/map
- GET /api/ps/seat/{course_id}/user/{username}
- POST /api/ps/seat/{course_id}/user/{username}/delete

### 考勤管理 (11个接口)
- POST /api/ps/attendance/{course_id}/{schedule_id}/init
- GET /api/ps/attendance/{sg_id}
- POST /api/ps/attendance/{sg_id}/sign-in
- POST /api/ps/attendance/{sg_id}/record
- POST /api/ps/attendance/{sg_id}/token
- POST /api/ps/attendance/verify-token
- POST /api/ps/attendance/{sg_id}/leave
- POST /api/ps/attendance/{sg_id}/review-leave
- POST /api/ps/attendance/{sg_id}/update-mode
- POST /api/ps/attendance/{sg_id}/mark-absence
- GET /api/ps/attendance/student/{username}/records

**总计：37个接口**
