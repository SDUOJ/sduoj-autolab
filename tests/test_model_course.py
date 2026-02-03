"""
课程管理模型测试
"""
import pytest
from unittest.mock import MagicMock, patch
import json

from model.course import CourseModel


class TestCourseModel:
    """课程管理模型测试类"""
    
    @patch('model.course.dbSession')
    def test_create_course_success(self, mock_db_session_class):
        """测试创建课程成功"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_new_course = MagicMock()
        mock_new_course.course_id = 1
        mock_session.session.refresh = MagicMock(side_effect=lambda x: setattr(x, 'course_id', 1))
        
        # 执行测试
        result = CourseModel.create_course(
            course_name="数据结构",
            group_id=100,
            tag="授课"
        )
        
        # 验证
        assert result.code == 0
        assert result.msg == "课程创建成功"
    
    @patch('model.course.dbSession')
    def test_create_course_error(self, mock_db_session_class):
        """测试创建课程失败"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        mock_session.session.add.side_effect = Exception("数据库错误")
        
        # 执行测试
        result = CourseModel.create_course(
            course_name="数据结构",
            group_id=100,
            tag="授课"
        )
        
        # 验证
        assert result.code == 1
        assert "创建课程失败" in result.msg
    
    @patch('model.course.dbSession')
    def test_get_course_success(self, mock_db_session_class, mock_course):
        """测试获取课程成功"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_course
        mock_session.session.query.return_value = mock_query
        
        # mock dealData返回课程数据
        mock_session.dealData.return_value = {
            'course_id': 1,
            'course_name': '测试课程',
            'group_id': 1,
            'tag': '授课',
            'c_ids': None,
            'ext_config': None
        }
        
        # 执行测试
        result = CourseModel.get_course(1)
        
        # 验证
        assert result.code == 0
        assert result.data['course_id'] == 1
    
    @patch('model.course.dbSession')
    def test_get_course_not_found(self, mock_db_session_class):
        """测试获取不存在的课程"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = None
        mock_session.session.query.return_value = mock_query
        
        # 执行测试
        result = CourseModel.get_course(999)
        
        # 验证
        assert result.code == 1
        assert "课程不存在" in result.msg
    
    @patch('model.course.dbSession')
    def test_list_courses(self, mock_db_session_class, mock_course):
        """测试获取课程列表"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_query = MagicMock()
        mock_query.offset.return_value.limit.return_value.all.return_value = [mock_course]
        mock_query.count.return_value = 1
        mock_session.session.query.return_value = mock_query
        
        # mock dealData返回课程数据
        mock_session.dealData.return_value = {
            'course_id': 1,
            'course_name': '测试课程',
            'group_id': 1,
            'tag': '授课'
        }
        
        # 执行测试
        result = CourseModel.list_courses(page_now=1, page_size=10)
        
        # 验证
        assert result.code == 0
        assert result.data['total'] == 1
        assert len(result.data['courses']) == 1
    
    @patch('model.course.dbSession')
    def test_update_course_success(self, mock_db_session_class, mock_course):
        """测试更新课程成功"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_course
        mock_session.session.query.return_value = mock_query
        
        # 执行测试
        result = CourseModel.update_course(
            course_id=1,
            course_name="新课程名"
        )
        
        # 验证
        assert result.code == 0
        assert mock_course.course_name == "新课程名"
    
    @patch('model.course.dbSession')
    def test_delete_course_success(self, mock_db_session_class, mock_course):
        """测试删除课程成功"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_course
        # 模拟没有课程时间和考勤记录
        mock_query.filter.return_value.count.return_value = 0
        mock_session.session.query.return_value = mock_query
        
        # 执行测试
        result = CourseModel.delete_course(1)
        
        # 验证
        assert result.code == 0
        assert "删除成功" in result.msg
    
    @patch('model.course.dbSession')
    def test_assign_classrooms_success(self, mock_db_session_class, mock_course):
        """测试分配教室成功"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_course
        mock_session.session.query.return_value = mock_query
        
        # 执行测试
        result = CourseModel.assign_classrooms(1, [1, 2, 3])
        
        # 验证
        assert result.code == 0
        assert mock_course.c_ids == json.dumps([1, 2, 3])
    
    @patch('model.course.dbSession')
    def test_add_ta_success(self, mock_db_session_class, mock_course):
        """测试添加助教成功"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_course
        mock_session.session.query.return_value = mock_query
        
        # 执行测试
        result = CourseModel.add_ta(1, "ta001")
        
        # 验证
        assert result.code == 0
    
    @patch('model.course.dbSession')
    def test_list_tas(self, mock_db_session_class, mock_course):
        """测试获取助教列表"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_ta = MagicMock()
        mock_ta.username = "ta001"
        
        mock_query = MagicMock()
        mock_query.filter.return_value.all.return_value = [mock_ta]
        
        mock_session.session.query.return_value = mock_query
        
        # mock dealData返回助教数据
        mock_session.dealData.return_value = {
            'username': 'ta001',
            'course_id': 1
        }
        
        # 执行测试
        result = CourseModel.list_tas(1)
        
        # 验证
        assert result.code == 0
        assert len(result.data) == 1
