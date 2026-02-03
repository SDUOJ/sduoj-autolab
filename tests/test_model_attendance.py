"""
考勤管理模型测试
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

from model.sign_in_record import AttendanceModel


class TestAttendanceModel:
    """考勤管理模型测试类"""
    
    @patch('model.sign_in_record.dbSession')
    def test_get_or_create_sign_existing(self, mock_db_session_class, mock_sign):
        """测试获取已存在的考勤记录"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_sign
        mock_session.session.query.return_value = mock_query
        
        # 执行测试
        result = AttendanceModel.get_or_create_sign(1, 1)
        
        # 验证
        assert result.code == 0
        assert result.data['sg_id'] == 1
    
    @patch('model.sign_in_record.dbSession')
    def test_get_or_create_sign_new(self, mock_db_session_class, mock_schedule):
        """测试创建新的考勤记录"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_query = MagicMock()
        mock_query.filter.return_value.first = MagicMock(side_effect=[None, mock_schedule])
        mock_session.session.query.return_value = mock_query
        
        mock_new_sign = MagicMock()
        mock_new_sign.sg_id = 2
        mock_session.session.refresh = MagicMock(side_effect=lambda x: setattr(x, 'sg_id', 2))
        
        # 执行测试
        result = AttendanceModel.get_or_create_sign(1, 1)
        
        # 验证
        assert result.code == 0
        assert "考勤记录创建成功" in result.msg
    
    @patch('model.sign_in_record.getGroupMember')
    @patch('model.sign_in_record.dbSession')
    @pytest.mark.asyncio
    async def test_init_attendance_users_success(self, mock_db_session_class, mock_get_group_member, mock_sign):
        """测试初始化考勤学生名单成功"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        # Mock查询sign
        mock_query_sign = MagicMock()
        mock_query_sign.filter.return_value.first.return_value = mock_sign
        
        # Mock查询existing（两次，都不存在）
        mock_query_existing = MagicMock()
        mock_query_existing.filter.return_value.first.return_value = None
        
        # Mock查询seat_binding（两次）
        mock_seat_binding = MagicMock()
        mock_seat_binding.seat_number = 15
        mock_query_seat = MagicMock()
        mock_query_seat.filter.return_value.first.return_value = mock_seat_binding
        
        # 设置side_effect: sign查询, existing查询x2, seat查询x2
        mock_session.session.query.side_effect = [
            mock_query_sign,      # 查询sign
            mock_query_existing,  # 第一个学生existing查询
            mock_query_seat,      # 第一个学生seat查询
            mock_query_existing,  # 第二个学生existing查询
            mock_query_seat,      # 第二个学生seat查询
        ]
        
        # Mock getGroupMember
        mock_get_group_member.return_value = {
            "members": [{"username": "20220101"}, {"username": "20220102"}]
        }
        
        # 执行测试
        result = await AttendanceModel.init_attendance_users(1, 100)
        
        # 验证
        assert result.code == 0
        assert "初始化成功" in result.msg
    
    @patch('model.sign_in_record.dbSession')
    def test_student_sign_in_success(self, mock_db_session_class, mock_sign, mock_schedule, mock_sign_user):
        """测试学生签到成功"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        # 设置签到时间早于课程开始时间（正常签到）
        mock_schedule.start_time = datetime(2024, 9, 1, 8, 0, 0)
        
        with patch('model.sign_in_record.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2024, 9, 1, 7, 55, 0)
            
            mock_query = MagicMock()
            mock_query.filter.return_value.first = MagicMock(side_effect=[mock_sign, mock_schedule, mock_sign_user])
            mock_session.session.query.return_value = mock_query
            
            # 执行测试
            result = AttendanceModel.student_sign_in(1, "20220101", 0)
        
        # 验证
        assert result.code == 0
        assert mock_sign_user.status == 1  # 出勤
    
    @patch('model.sign_in_record.dbSession')
    def test_student_sign_in_late(self, mock_db_session_class, mock_sign, mock_schedule, mock_sign_user):
        """测试学生迟到"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        # 设置签到时间晚于课程开始时间（迟到）
        mock_schedule.start_time = datetime(2024, 9, 1, 8, 0, 0)
        
        with patch('model.sign_in_record.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2024, 9, 1, 8, 5, 0)
            
            mock_query = MagicMock()
            mock_query.filter.return_value.first = MagicMock(side_effect=[mock_sign, mock_schedule, mock_sign_user])
            mock_session.session.query.return_value = mock_query
            
            # 执行测试
            result = AttendanceModel.student_sign_in(1, "20220101", 0)
        
        # 验证
        assert result.code == 0
        assert mock_sign_user.status == 3  # 迟到
    
    @patch('model.sign_in_record.dbSession')
    def test_submit_leave_success(self, mock_db_session_class, mock_sign_user):
        """测试提交请假申请成功"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_sign_user.leave_status = None
        
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_sign_user
        mock_session.session.query.return_value = mock_query
        
        # 执行测试
        result = AttendanceModel.submit_leave(1, "20220101", "生病", ["file1.pdf"])
        
        # 验证
        assert result.code == 0
        assert "请假申请提交成功" in result.msg
    
    @patch('model.sign_in_record.dbSession')
    def test_review_leave_approve(self, mock_db_session_class, mock_sign_user):
        """测试批准请假"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_sign_user.leave_status = 0
        
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_sign_user
        mock_session.session.query.return_value = mock_query
        
        # 执行测试
        result = AttendanceModel.review_leave(1, "20220101", True)
        
        # 验证
        assert result.code == 0
        assert mock_sign_user.leave_status == 1
    
    @patch('model.sign_in_record.dbSession')
    def test_review_leave_reject(self, mock_db_session_class, mock_sign_user):
        """测试拒绝请假"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_sign_user.leave_status = 0
        
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_sign_user
        mock_session.session.query.return_value = mock_query
        
        # 执行测试
        result = AttendanceModel.review_leave(1, "20220101", False)
        
        # 验证
        assert result.code == 0
        assert mock_sign_user.leave_status == 2
    
    @patch('model.sign_in_record.dbSession')
    def test_get_attendance_list_success(self, mock_db_session_class, mock_sign, mock_sign_user):
        """测试获取考勤列表成功"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        # 设置mock_sign的属性
        mock_sign.sg_id = 1
        mock_sign.course_id = 1
        mock_sign.schedule_id = 1
        mock_sign.mode = 0
        
        # 准备mock_schedule
        mock_schedule = MagicMock()
        mock_schedule.start_time = MagicMock()
        mock_schedule.start_time.isoformat = MagicMock(return_value='2024-09-01T08:00:00')
        mock_schedule.end_time = MagicMock()
        mock_schedule.end_time.isoformat = MagicMock(return_value='2024-09-01T10:00:00')
        
        # 准备query chain - 按调用顺序
        mock_query1 = MagicMock()  # 查询ojSign
        mock_query1.filter.return_value.first.return_value = mock_sign
        
        mock_query2 = MagicMock()  # 查询ojCourseSchedule
        mock_query2.filter.return_value.first.return_value = mock_schedule
        
        mock_query3 = MagicMock()  # 查询ojSignUser
        mock_query3.filter.return_value.all.return_value = [mock_sign_user]
        
        # 使用side_effect按调用顺序返回不同的query对象
        mock_session.session.query.side_effect = [mock_query1, mock_query2, mock_query3]
        
        # mock dealData返回考勤用户数据
        mock_session.dealData.return_value = {
            'username': 'student001',
            'sg_time': '2024-09-01 08:00:00',
            'status': 1
        }
        
        # 执行测试
        result = AttendanceModel.get_attendance_list(1)
        
        # 验证
        assert result.code == 0
        assert len(result.data['students']) > 0
    
    @patch('model.sign_in_record.dbSession')
    def test_update_sign_mode_success(self, mock_db_session_class, mock_sign):
        """测试更新考勤模式成功"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_sign
        mock_session.session.query.return_value = mock_query
        
        # 执行测试
        result = AttendanceModel.update_sign_mode(1, 1)
        
        # 验证
        assert result.code == 0
        assert mock_sign.mode == 1  # 改为mode（数据库实际字段）
    
    @patch('model.sign_in_record.dbSession')
    def test_mark_absence_success(self, mock_db_session_class, mock_sign_user):
        """测试标记缺勤成功"""
        # 准备mock
        mock_session = MagicMock()
        mock_db_session_class.return_value = mock_session
        
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_sign_user
        mock_session.session.query.return_value = mock_query
        
        # 执行测试
        result = AttendanceModel.mark_absence(1, "20220101")
        
        # 验证
        assert result.code == 0
        assert mock_sign_user.status == 2  # 缺勤
