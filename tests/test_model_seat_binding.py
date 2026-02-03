"""
座位绑定管理模型测试
"""
import pytest
from unittest.mock import MagicMock, patch
import json

from model.class_binding import SeatBindingModel


class TestSeatBindingModel:
    """座位绑定管理模型测试类"""
    
    @patch('model.class_binding.dbSession')
    def test_assign_seats_success(self, mock_db_session_class, mock_course):
        """测试分配座位成功"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_query = MagicMock()
        mock_query.filter.return_value.first = MagicMock(side_effect=[mock_course, None, None])
        mock_session.session.query.return_value = mock_query
        
        # 执行测试
        result = SeatBindingModel.assign_seats(1, "20220101", 15)
        
        # 验证
        assert result.code == 0
        assert "座位分配成功" in result.msg
    
    @patch('model.class_binding.dbSession')
    def test_assign_seats_occupied(self, mock_db_session_class, mock_course):
        """测试分配座位-座位已被占用"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_existing = MagicMock()
        mock_existing.username = "20220102"
        
        mock_query = MagicMock()
        mock_query.filter.return_value.first = MagicMock(side_effect=[mock_course, mock_existing])
        mock_session.session.query.return_value = mock_query
        
        # 执行测试
        result = SeatBindingModel.assign_seats(1, "20220101", 15)
        
        # 验证
        assert result.code == 1
        assert "已被" in result.msg
    
    @patch('model.class_binding.getGroupMember')
    @patch('model.class_binding.dbSession')
    @pytest.mark.asyncio
    async def test_auto_assign_seats_success(self, mock_db_session_class, mock_get_group_member, mock_course, mock_classroom):
        """测试自动分配座位成功"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_course.c_ids = json.dumps([1])
        
        # 需要多次查询，设置正确的side_effect
        mock_query_course = MagicMock()
        mock_query_course.filter.return_value.first.return_value = mock_course
        
        mock_query_classroom = MagicMock()
        mock_query_classroom.filter.return_value.all.return_value = [mock_classroom]
        
        mock_query_occupied = MagicMock()
        mock_query_occupied.filter.return_value.all.return_value = []  # 没有已占用座位
        
        mock_query_existing = MagicMock()
        mock_query_existing.filter.return_value.first.return_value = None  # 学生没有座位
        
        mock_session.session.query.side_effect = [
            mock_query_course,     # 查询课程
            mock_query_classroom,  # 查询教室
            mock_query_occupied,   # 查询已占用座位
            mock_query_existing,   # 第一个学生的existing查询
            mock_query_existing,   # 第二个学生的existing查询
        ]
        
        # Mock getGroupMember
        mock_get_group_member.return_value = {
            "members": [{"username": "20220101"}, {"username": "20220102"}]
        }
        
        # 执行测试
        result = await SeatBindingModel.auto_assign_seats(1, 100)
        
        # 验证
        assert result.code == 0
        assert "自动分配成功" in result.msg
    
    @patch('model.class_binding.dbSession')
    def test_get_seat_map_success(self, mock_db_session_class, mock_course, mock_classroom):
        """测试获取座位分布图成功"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_course.c_ids = json.dumps([1])
        
        mock_binding = MagicMock()
        mock_binding.seat_number = 15
        mock_binding.username = "20220101"
        
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_course
        mock_query.filter.return_value.all = MagicMock(side_effect=[[mock_binding], [mock_classroom]])
        mock_session.session.query = MagicMock(return_value=mock_query)
        
        # 执行测试
        result = SeatBindingModel.get_seat_map(1)
        
        # 验证
        assert result.code == 0
        assert 15 in result.data['seat_bindings']
    
    @patch('model.class_binding.dbSession')
    def test_remove_seat_success(self, mock_db_session_class):
        """测试删除座位绑定成功"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_binding = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_binding
        mock_session.session.query.return_value = mock_query
        
        # 执行测试
        result = SeatBindingModel.remove_seat(1, "20220101")
        
        # 验证
        assert result.code == 0
        assert "删除成功" in result.msg
