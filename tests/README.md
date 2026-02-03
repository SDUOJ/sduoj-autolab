# 测试使用指南

## 测试文件说明

项目包含两套测试方案：

### 方案1：完整单文件测试（推荐用于快速验证）
**文件**: `test_v3_complete.py`
- ✅ **31个测试全部通过**
- 包含完整的fixtures定义
- 适合快速运行和调试

### 方案2：模块化测试（推荐用于持续开发）
**目录**: `tests/`
- 测试按功能模块拆分
- 共享fixtures配置
- 易于扩展和维护
- 生成详细覆盖率报告

## 快速开始

### 1. 激活环境
```bash
conda activate oj_problem_set
```

### 2. 运行测试

#### 运行完整单文件测试
```bash
python -m pytest test_v3_complete.py -v
```

#### 运行模块化测试
```bash
python -m pytest tests/ -v
```

#### 运行特定模块测试
```bash
python -m pytest tests/test_model_course.py -v
python -m pytest tests/test_model_attendance.py -v
python -m pytest tests/test_model_seat_binding.py -v
python -m pytest tests/test_model_course_schedule.py -v
```

### 3. 生成覆盖率报告

#### 基础覆盖率报告
```bash
python -m pytest tests/ --cov=model --cov-report=term-missing
```

#### HTML覆盖率报告
```bash
python -m pytest tests/ --cov=model --cov=ser --cov=server --cov-report=html
# 在浏览器中打开 htmlcov/index.html 查看详细报告
```

## 测试覆盖范围

### ✅ 已覆盖模块
- **课程管理** (model/course.py) - 63%
- **座位绑定** (model/class_binding.py) - 76%
- **考勤管理** (model/sign_in_record.py) - 71%
- **课程时间** (model/course_schedule.py) - 32%

### 🎯 测试功能点

#### 课程管理 (10个测试)
- 创建课程及错误处理
- 获取课程详情
- 课程列表查询
- 更新课程信息
- 删除课程
- 分配教室
- 添加/查询助教

#### 座位绑定 (5个测试)
- 手动分配座位
- 自动分配座位
- 座位占用验证
- 获取座位分布图
- 删除座位绑定

#### 考勤管理 (11个测试)
- 获取/创建考勤记录
- 初始化学生名单
- 学生签到
- 迟到判断
- 请假申请
- 请假审批（批准/拒绝）
- 考勤列表查询
- 更新考勤模式
- 标记缺勤

#### 课程时间 (5个测试)
- 添加课程时间
- 时间有效性验证
- 获取课程时间
- 更新课程时间
- 删除课程时间

## 测试特性

### 虚拟数据库
- ✅ 所有测试使用Mock对象
- ✅ **不会修改实际数据库**
- ✅ 测试快速且隔离

### 异步支持
```python
@pytest.mark.asyncio
async def test_async_function(self):
    result = await SomeModel.async_method()
    assert result.code == 0
```

### 共享Fixtures
在 `tests/conftest.py` 中定义：
- `mock_course` - 课程对象
- `mock_schedule` - 课程时间对象
- `mock_classroom` - 教室对象
- `mock_sign` - 考勤对象
- `mock_sign_user` - 学生考勤记录

## 测试结果

### 方案1 (test_v3_complete.py)
```
31 passed in 0.28s ✅
```

### 方案2 (tests/)
```
23 passed, 8 failed
- 通过率: 74%
- Model层覆盖率: 9% (整体), 核心模块60-76%
```

## 常用命令

### 只运行失败的测试
```bash
pytest --lf
```

### 详细输出
```bash
pytest -vv
```

### 显示打印语句
```bash
pytest -s
```

### 停在第一个失败
```bash
pytest -x
```

### 并行运行测试（需要安装pytest-xdist）
```bash
pip install pytest-xdist
pytest -n auto
```

## 调试技巧

### 1. 查看详细错误
```bash
pytest --tb=long
```

### 2. 进入调试器
在测试代码中添加：
```python
import pdb; pdb.set_trace()
```

### 3. 只运行特定测试
```bash
pytest tests/test_model_course.py::TestCourseModel::test_create_course_success
```

## 持续集成

建议在CI/CD流程中添加：
```yaml
- name: Run tests
  run: |
    conda activate oj_problem_set
    pytest tests/ --cov=model --cov-report=xml
```

## 依赖包

测试相关依赖已安装在 `oj_problem_set` 环境：
- pytest==8.4.2
- pytest-asyncio==1.2.0
- pytest-cov==7.0.0
- httpx==0.28.1

## 常见问题

### Q: 测试失败怎么办？
A: 查看错误信息，通常是mock设置不正确或API参数不匹配。

### Q: 如何添加新测试？
A: 在相应的test_model_*.py文件中添加新的测试方法，使用`@patch`装饰器mock数据库。

### Q: 为什么有些测试失败？
A: 可能是测试中的API参数与实际实现不一致，需要调整测试代码以匹配实际API。

## 查看更多

详细测试报告请查看: [TEST_REPORT.md](TEST_REPORT.md)
