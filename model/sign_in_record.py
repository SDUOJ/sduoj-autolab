"""
考勤管理模型 - v3.0 课程中心化架构
提供考勤的创建、查询、签到、请假等业务逻辑
"""
import json
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid
from sqlalchemy import and_, or_, func
from sqlalchemy.exc import IntegrityError

from db import dbSession, ojSign, ojSignUser, ojCourse, ojCourseSchedule, ojClassUser
from utils import Result
from sduojApi import getGroupMember


class AttendanceModel:
    """考勤管理业务逻辑"""

    @staticmethod
    def get_or_create_sign(course_id: int, schedule_id: int) -> Result:
        """
        获取或创建考勤记录
        
        Args:
            course_id: 课程ID
            schedule_id: 课程时间ID
            
        Returns:
            Result: 包含sg_id的结果
        """
        session = dbSession()
        try:
            # 查找是否已存在
            sign = session.session.query(ojSign).filter(
                and_(
                    ojSign.course_id == course_id,
                    ojSign.schedule_id == schedule_id
                )
            ).first()
            
            if sign:
                return Result(code=0, msg="success", data={'sg_id': sign.sg_id})
            
            # 验证课程时间是否存在
            schedule = session.session.query(ojCourseSchedule).filter(
                ojCourseSchedule.schedule_id == schedule_id
            ).first()
            
            if not schedule:
                return Result(code=1, msg="课程时间不存在")
            
            # 创建考勤记录
            sign = ojSign(
                course_id=course_id,
                schedule_id=schedule_id,
                title=f"课程考勤",
                mode=0  # 默认模式：手动记录
            )
            
            session.session.add(sign)
            session.session.commit()
            session.session.refresh(sign)
            
            return Result(
                code=0,
                msg="考勤记录创建成功",
                data={'sg_id': sign.sg_id}
            )
            
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"获取/创建考勤记录失败: {str(e)}")
        finally:
            del session

    @staticmethod
    async def init_attendance_users(sg_id: int, group_id: int) -> Result:
        """
        初始化考勤学生名单
        
        Args:
            sg_id: 考勤ID
            group_id: 用户组ID
            
        Returns:
            Result: 初始化结果
        """
        session = dbSession()
        try:
            # 验证考勤是否存在
            sign = session.session.query(ojSign).filter(
                ojSign.sg_id == sg_id
            ).first()
            
            if not sign:
                return Result(code=1, msg="考勤不存在")
            
            # 获取用户组成员
            members_data = await getGroupMember(group_id)
            members = members_data.get("members", [])
            
            # 创建考勤用户记录
            for member in members:
                username = member["username"]
                
                # 检查是否已存在
                existing = session.session.query(ojSignUser).filter(
                    and_(
                        ojSignUser.sg_id == sg_id,
                        ojSignUser.username == username
                    )
                ).first()
                
                if not existing:
                    # 获取学生座位
                    seat_binding = session.session.query(ojClassUser).filter(
                        and_(
                            ojClassUser.course_id == sign.course_id,
                            ojClassUser.username == username
                        )
                    ).first()
                    
                    seat_number = seat_binding.seat_number if seat_binding else None
                    
                    sign_user = ojSignUser(
                        sg_id=sg_id,
                        username=username,
                        status=0,  # 无记录
                        seat_number=seat_number
                    )
                    session.session.add(sign_user)
            
            session.session.commit()
            
            return Result(code=0, msg=f"初始化成功，共{len(members)}个学生")
            
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"初始化考勤学生名单失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def student_sign_in(
        sg_id: int,
        username: str,
        sign_type: int  # 0-签到, 1-签退
    ) -> Result:
        """
        学生签到/签退
        
        Args:
            sg_id: 考勤ID
            username: 学生用户名
            sign_type: 签到类型（0-签到, 1-签退）
            
        Returns:
            Result: 签到结果
        """
        session = dbSession()
        try:
            now = datetime.now()
            
            # 获取考勤信息
            sign = session.session.query(ojSign).filter(
                ojSign.sg_id == sg_id
            ).first()
            
            if not sign:
                return Result(code=1, msg="考勤不存在")
            
            # 获取课程时间
            schedule = session.session.query(ojCourseSchedule).filter(
                ojCourseSchedule.schedule_id == sign.schedule_id
            ).first()
            
            if not schedule:
                return Result(code=1, msg="课程时间不存在")
            
            # 获取学生考勤记录
            sign_user = session.session.query(ojSignUser).filter(
                and_(
                    ojSignUser.sg_id == sg_id,
                    ojSignUser.username == username
                )
            ).first()
            
            if not sign_user:
                return Result(code=1, msg="学生未在考勤名单中")
            
            # 检查请假状态
            if sign_user.leave_status == 1:  # 请假已批准
                return Result(code=1, msg="已批准请假，无需签到")
            
            # 判断签到/签退时间
            if sign_type == 0:  # 签到
                if sign_user.sg_time:
                    return Result(code=1, msg="已签到，请勿重复签到")
                
                sign_user.sg_time = now
                
                # 判断是否迟到（晚于课程开始时间）
                if now > schedule.start_time:
                    sign_user.status = 3  # 迟到/早退
                else:
                    sign_user.status = 1  # 出勤
                
            else:  # 签退（暂不支持，使用sg_time作为签到时间）
                return Result(code=1, msg="当前系统仅支持签到，不支持单独签退")
            
            session.session.commit()
            
            return Result(code=0, msg="签到成功" if sign_type == 0 else "签退成功")
            
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"签到失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def submit_leave(
        sg_id: int,
        username: str,
        leave_message: str,
        leave_files: Optional[List[str]] = None
    ) -> Result:
        """
        提交请假申请
        
        Args:
            sg_id: 考勤ID
            username: 学生用户名
            leave_message: 请假理由
            leave_files: 请假附件（文件ID列表）
            
        Returns:
            Result: 提交结果
        """
        session = dbSession()
        try:
            sign_user = session.session.query(ojSignUser).filter(
                and_(
                    ojSignUser.sg_id == sg_id,
                    ojSignUser.username == username
                )
            ).first()
            
            if not sign_user:
                return Result(code=1, msg="学生未在考勤名单中")
            
            # 检查是否已有申请中的请假
            if sign_user.leave_status == 0:
                return Result(code=1, msg="已有请假申请在审批中")
            
            # 更新请假信息
            sign_user.leave_message = leave_message
            sign_user.leave_files = json.dumps(leave_files) if leave_files else None
            sign_user.leave_status = 0  # 申请中
            sign_user.status = 5  # 请假申请中
            
            session.session.commit()
            
            return Result(code=0, msg="请假申请提交成功")
            
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"提交请假申请失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def review_leave(
        sg_id: int,
        username: str,
        approved: bool  # True-批准, False-拒绝
    ) -> Result:
        """
        审批请假申请
        
        Args:
            sg_id: 考勤ID
            username: 学生用户名
            approved: 是否批准
            
        Returns:
            Result: 审批结果
        """
        session = dbSession()
        try:
            sign_user = session.session.query(ojSignUser).filter(
                and_(
                    ojSignUser.sg_id == sg_id,
                    ojSignUser.username == username
                )
            ).first()
            
            if not sign_user:
                return Result(code=1, msg="学生未在考勤名单中")
            
            if sign_user.leave_status != 0:
                return Result(code=1, msg="无待审批的请假申请")
            
            if approved:
                sign_user.leave_status = 1  # 批准
                sign_user.status = 4  # 请假已批准
            else:
                sign_user.leave_status = 2  # 拒绝
                sign_user.status = 0  # 恢复为无记录
            
            session.session.commit()
            
            return Result(code=0, msg="请假审批完成")
            
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"审批请假失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def get_attendance_list(sg_id: int) -> Result:
        """
        查询考勤名单
        
        Args:
            sg_id: 考勤ID
            
        Returns:
            Result: 包含考勤名单的结果
        """
        session = dbSession()
        try:
            # 获取考勤信息
            sign = session.session.query(ojSign).filter(
                ojSign.sg_id == sg_id
            ).first()
            
            if not sign:
                return Result(code=1, msg="考勤不存在")
            
            # 获取课程时间
            schedule = session.session.query(ojCourseSchedule).filter(
                ojCourseSchedule.schedule_id == sign.schedule_id
            ).first()
            
            # 获取考勤名单
            sign_users = session.session.query(ojSignUser).filter(
                ojSignUser.sg_id == sg_id
            ).all()
            
            user_list = []
            for sign_user in sign_users:
                user_data = session.dealData(
                    sign_user,
                    timeKeys=['sg_time']
                )
                
                # 解析JSON字段
                if user_data.get('leave_files'):
                    user_data['leave_files'] = json.loads(user_data['leave_files'])
                
                user_list.append(user_data)
            
            # 统计数据
            status_count = {
                '出勤': 0,
                '缺勤': 0,
                '迟到/早退': 0,
                '请假已批准': 0,
                '请假申请中': 0,
                '无记录': 0
            }
            
            for user in user_list:
                status = user.get('status', 0)
                if status == 1:
                    status_count['出勤'] += 1
                elif status == 2:
                    status_count['缺勤'] += 1
                elif status == 3:
                    status_count['迟到/早退'] += 1
                elif status == 4:
                    status_count['请假已批准'] += 1
                elif status == 5:
                    status_count['请假申请中'] += 1
                else:
                    status_count['无记录'] += 1
            
            return Result(
                code=0,
                msg="success",
                data={
                    'sg_id': sg_id,
                    'course_id': sign.course_id,
                    'schedule_id': sign.schedule_id,
                    'sign_mode': sign.mode,
                    'course_time': {
                        'start_time': schedule.start_time.isoformat() if schedule else None,
                        'end_time': schedule.end_time.isoformat() if schedule else None
                    },
                    'students': user_list,
                    'statistics': status_count
                }
            )
            
        except Exception as e:
            return Result(code=1, msg=f"查询考勤名单失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def update_sign_mode(sg_id: int, sign_mode: int) -> Result:
        """
        更新考勤模式
        
        Args:
            sg_id: 考勤ID
            sign_mode: 考勤模式（0-签到+签退, 1-仅签到）
            
        Returns:
            Result: 更新结果
        """
        session = dbSession()
        try:
            sign = session.session.query(ojSign).filter(
                ojSign.sg_id == sg_id
            ).first()
            
            if not sign:
                return Result(code=1, msg="考勤不存在")
            
            if sign_mode not in [0, 1]:
                return Result(code=1, msg="无效的考勤模式")
            
            sign.mode = sign_mode
            session.session.commit()
            
            return Result(code=0, msg="考勤模式更新成功")
            
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"更新考勤模式失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def batch_record(sg_id: int, records: List[Dict[str, Any]]) -> Result:
        """
        批量记录考勤状态

        Args:
            sg_id: 考勤ID
            records: [{username, status, seat_number?}]

        Returns:
            Result: 更新结果
        """
        session = dbSession()
        try:
            sign = session.session.query(ojSign).filter(
                ojSign.sg_id == sg_id
            ).first()
            if not sign:
                return Result(code=1, msg="考勤不存在")

            for record in records:
                username = record.get("username")
                status = record.get("status")
                seat_number = record.get("seat_number")
                if username is None or status is None:
                    return Result(code=1, msg="记录参数不完整")

                sign_user = session.session.query(ojSignUser).filter(
                    and_(
                        ojSignUser.sg_id == sg_id,
                        ojSignUser.username == username
                    )
                ).first()
                if not sign_user:
                    return Result(code=1, msg=f"学生未在考勤名单中: {username}")

                sign_user.status = status
                if seat_number is not None:
                    sign_user.seat_number = seat_number

            session.session.commit()
            return Result(code=0, msg="批量记录成功")

        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"批量记录失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def generate_token(sg_id: int, username: str) -> Result:
        """
        生成签到 token（一次有效）
        """
        session = dbSession()
        try:
            sign_user = session.session.query(ojSignUser).filter(
                and_(
                    ojSignUser.sg_id == sg_id,
                    ojSignUser.username == username
                )
            ).first()

            if not sign_user:
                return Result(code=1, msg="学生未在考勤名单中")

            token = uuid.uuid4().hex
            sign_user.token = token
            session.session.commit()
            return Result(code=0, msg="success", data={"token": token})
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"生成token失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def verify_token(token: str, seat_number: Optional[int] = None) -> Result:
        """
        校验 token 并完成签到
        """
        session = dbSession()
        try:
            sign_user = session.session.query(ojSignUser).filter(
                ojSignUser.token == token
            ).first()
            if not sign_user:
                return Result(code=1, msg="token无效或已使用")

            if seat_number is not None:
                sign_user.seat_number = seat_number

            # 使用既有签到逻辑
            result = AttendanceModel.student_sign_in(
                sg_id=sign_user.sg_id,
                username=sign_user.username,
                sign_type=0
            )
            if result.code != 0:
                session.session.rollback()
                return result

            sign_user.token = None
            session.session.commit()
            return Result(code=0, msg="签到成功")

        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"token校验失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def mark_absence(sg_id: int, username: str) -> Result:
        """
        标记缺勤（用于批量设置未签到学生为缺勤）
        
        Args:
            sg_id: 考勤ID
            username: 学生用户名
            
        Returns:
            Result: 标记结果
        """
        session = dbSession()
        try:
            sign_user = session.session.query(ojSignUser).filter(
                and_(
                    ojSignUser.sg_id == sg_id,
                    ojSignUser.username == username
                )
            ).first()
            
            if not sign_user:
                return Result(code=1, msg="学生未在考勤名单中")
            
            # 只有无记录状态才能标记为缺勤
            if sign_user.status != 0:
                return Result(code=1, msg=f"学生当前状态为{sign_user.status}，无法标记为缺勤")
            
            sign_user.status = 2  # 缺勤
            session.session.commit()
            
            return Result(code=0, msg="标记缺勤成功")
            
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"标记缺勤失败: {str(e)}")
        finally:
            del session
