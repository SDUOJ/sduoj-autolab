"""
课程时间管理模型测试
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
import json

from model.course_schedule import CourseScheduleModel


class TestCourseScheduleModel:
    """课程时间管理模型测试类"""
    
    @patch('model.course_schedule.dbSession')
    def test_add_schedule_success(self, mock_db_session_class, mock_course):
        """测试添加课程时间成功"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_course
        mock_session.session.query.return_value = mock_query
        
        mock_new_schedule = MagicMock()
        mock_new_schedule.schedule_id = 1
        mock_session.session.refresh = MagicMock(side_effect=lambda x: setattr(x, 'schedule_id', 1))
        
        # mock flush和commit
        mock_session.session.flush = MagicMock()
        mock_session.session.commit = MagicMock()
        
        # 执行测试
        result = CourseScheduleModel.add_schedule(
            course_id=1,
            sequence=1,
            start_time=datetime(2024, 9, 1, 8, 0, 0),
            end_time=datetime(2024, 9, 1, 10, 0, 0)
        )
        
        # 验证
        assert result.code == 0
    
    @patch('model.course_schedule.dbSession')
    def test_add_schedule_invalid_time(self, mock_db_session_class, mock_course):
        """测试添加课程时间-时间无效"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_course
        mock_session.session.query.return_value = mock_query
        
        # 执行测试（结束时间早于开始时间）
        result = CourseScheduleModel.add_schedule(
            course_id=1,
            sequence=1,
            start_time=datetime(2024, 9, 1, 10, 0, 0),
            end_time=datetime(2024, 9, 1, 8, 0, 0)
        )
        
        # 验证
        assert result.code == 1
        assert "开始时间必须早于结束时间" in result.msg
    
    @patch('model.course_schedule.dbSession')
    def test_get_schedule_success(self, mock_db_session_class, mock_schedule):
        """测试获取课程时间成功"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_query = MagicMock()
        mock_query.filter.return_value.all.return_value = [mock_schedule]
        mock_session.session.query.return_value = mock_query
        
        # mock dealData返回课程时间数据
        mock_session.dealData.return_value = {
            'schedule_id': 1,
            'course_id': 1,
            'sequence': 1
        }
        
        # 执行测试
        result = CourseScheduleModel.get_schedule(1)
        
        # 验证 - get_schedule返回单个对象，不是列表
        assert result.code == 0
        assert result.data['schedule_id'] == 1
    
    @patch('model.course_schedule.dbSession')
    def test_update_schedule_success(self, mock_db_session_class, mock_schedule):
        """测试更新课程时间成功"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_schedule
        mock_session.session.query.return_value = mock_query
        
        # 执行测试
        result = CourseScheduleModel.update_schedule(
            schedule_id=1,
            course_content="更新的内容"
        )
        
        # 验证
        assert result.code == 0
        assert mock_schedule.course_content == "更新的内容"
    
    @patch('model.course_schedule.dbSession')
    def test_delete_schedule_success(self, mock_db_session_class, mock_schedule):
        """测试删除课程时间成功"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_schedule
        # 模拟没有考勤记录
        mock_query.filter.return_value.count.return_value = 0
        mock_session.session.query.return_value = mock_query
        
        # 执行测试
        result = CourseScheduleModel.delete_schedule(1)
        
        # 验证
        assert result.code == 0
        assert "删除成功" in result.msg
