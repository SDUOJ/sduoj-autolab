"""
Pytest配置文件 - 共享的fixtures
"""
import pytest
from unittest.mock import MagicMock
import json


@pytest.fixture
def mock_course():
    """模拟课程对象"""
    course = MagicMock()
    course.course_id = 1
    course.course_name = "数据结构"
    course.group_id = 100
    course.tag = "授课"
    course.c_ids = json.dumps([1, 2])
    course.ext_config = json.dumps({"key": "value"})
    return course


@pytest.fixture
def mock_schedule():
    """模拟课程时间对象"""
    from datetime import datetime
    schedule = MagicMock()
    schedule.schedule_id = 1
    schedule.course_id = 1
    schedule.start_time = datetime(2024, 9, 1, 8, 0, 0)
    schedule.end_time = datetime(2024, 9, 1, 10, 0, 0)
    schedule.week_day = 1
    schedule.week = json.dumps([1, 2, 3])
    return schedule


@pytest.fixture
def mock_classroom():
    """模拟教室对象"""
    classroom = MagicMock()
    classroom.c_id = 1
    classroom.c_name = "实验楼101"
    classroom.c_seat_num = 50
    classroom.address = "实验楼一层"
    classroom.ext_config = json.dumps({"disabled_seats": [13, 14]})
    return classroom


@pytest.fixture
def mock_sign():
    """模拟考勤对象"""
    sign = MagicMock()
    sign.sg_id = 1
    sign.course_id = 1
    sign.schedule_id = 1
    sign.mode = 0  # 数据库实际字段
    return sign


@pytest.fixture
def mock_sign_user():
    """模拟学生考勤记录"""
    sign_user = MagicMock()
    sign_user.username = "20220101"
    sign_user.status = 0
    sign_user.seat_number = 15
    sign_user.sg_time = None  # 数据库实际字段
    sign_user.leave_message = None
    sign_user.leave_files = None
    sign_user.leave_status = None
    return sign_user
