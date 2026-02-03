"""
座位绑定管理模型 - v3.0 课程中心化架构
提供学生座位分配、查询等业务逻辑
"""
import json
from typing import List, Optional, Dict, Any
from sqlalchemy import and_, or_, func
from sqlalchemy.exc import IntegrityError

from db import dbSession, ojClass, ojClassUser, ojCourse
from utils import Result
from sduojApi import getGroupMember


class SeatBindingModel:
    """座位绑定业务逻辑"""

    @staticmethod
    def assign_seats(
        course_id: int,
        username: str,
        seat_number: int,
        c_id: Optional[int] = None
    ) -> Result:
        """
        为学生分配座位
        
        Args:
            course_id: 课程ID
            username: 学生用户名
            seat_number: 座位号
            
        Returns:
            Result: 分配结果
        """
        session = dbSession()
        try:
            # 验证课程是否存在
            course = session.session.query(ojCourse).filter(
                ojCourse.course_id == course_id
            ).first()
            
            if not course:
                return Result(code=1, msg="课程不存在")
            
            # 校验教室是否属于该课程
            if c_id is not None and course.c_ids:
                c_ids = json.loads(course.c_ids)
                if c_id not in c_ids:
                    return Result(code=1, msg="教室不属于该课程")

            # 检查座位是否已被占用（课程内座位号唯一）
            existing = session.session.query(ojClassUser).filter(
                and_(
                    ojClassUser.course_id == course_id,
                    ojClassUser.seat_number == seat_number
                )
            ).first()
            
            if existing:
                return Result(code=1, msg=f"座位{seat_number}已被{existing.username}占用")
            
            # 检查学生是否已有座位
            user_seat = session.session.query(ojClassUser).filter(
                and_(
                    ojClassUser.course_id == course_id,
                    ojClassUser.username == username
                )
            ).first()
            
            if user_seat:
                # 更新座位
                user_seat.seat_number = seat_number
                user_seat.c_id = c_id
            else:
                # 新建座位绑定
                user_seat = ojClassUser(
                    course_id=course_id,
                    username=username,
                    seat_number=seat_number,
                    c_id=c_id
                )
                session.session.add(user_seat)
            
            session.session.commit()
            
            return Result(code=0, msg="座位分配成功")
            
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"分配座位失败: {str(e)}")
        finally:
            del session

    @staticmethod
    async def auto_assign_seats(
        course_id: int,
        group_id: int
    ) -> Result:
        """
        自动为课程学生分配座位
        
        Args:
            course_id: 课程ID
            group_id: 用户组ID
            
        Returns:
            Result: 分配结果
        """
        session = dbSession()
        try:
            # 验证课程是否存在并获取教室列表
            course = session.session.query(ojCourse).filter(
                ojCourse.course_id == course_id
            ).first()
            
            if not course:
                return Result(code=1, msg="课程不存在")
            
            if not course.c_ids:
                return Result(code=1, msg="课程未分配教室")
            
            c_ids = json.loads(course.c_ids)
            
            # 获取所有教室的可用座位
            classrooms = session.session.query(ojClass).filter(
                ojClass.c_id.in_(c_ids)
            ).all()
            
            if not classrooms:
                return Result(code=1, msg="未找到有效教室")
            
            # 收集所有可用座位
            available_seats = []
            for classroom in classrooms:
                # 解析ext_config获取不可用座位
                disabled_seats = set()
                if classroom.ext_config:
                    ext_config = json.loads(classroom.ext_config)
                    disabled_seats = set(ext_config.get('disabled_seats', []))
                
                # 获取已占用座位
                occupied_seats = session.session.query(ojClassUser.seat_number).filter(
                    ojClassUser.course_id == course_id
                ).all()
                occupied_set = set([s[0] for s in occupied_seats])
                
                # 收集可用座位
                for i in range(1, classroom.c_seat_num + 1):
                    if i not in disabled_seats and i not in occupied_set:
                        available_seats.append({
                            'c_id': classroom.c_id,
                            'seat_number': i
                        })
            
            if not available_seats:
                return Result(code=1, msg="没有可用座位")
            
            # 获取用户组成员
            members_data = await getGroupMember(group_id)
            members = members_data.get("members", [])
            
            if len(members) > len(available_seats):
                return Result(code=1, msg=f"座位不足: 需要{len(members)}个座位，可用{len(available_seats)}个")
            
            # 分配座位
            for idx, member in enumerate(members):
                username = member["username"]
                seat = available_seats[idx]
                
                # 检查是否已有座位
                existing = session.session.query(ojClassUser).filter(
                    and_(
                        ojClassUser.course_id == course_id,
                        ojClassUser.username == username
                    )
                ).first()
                
                if not existing:
                    user_seat = ojClassUser(
                        course_id=course_id,
                        username=username,
                        seat_number=seat['seat_number'],
                        c_id=seat['c_id']
                    )
                    session.session.add(user_seat)
            
            session.session.commit()
            
            return Result(
                code=0,
                msg=f"自动分配成功，共分配{len(members)}个座位"
            )
            
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"自动分配座位失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def get_seat_map(course_id: int) -> Result:
        """
        获取课程座位分布图
        
        Args:
            course_id: 课程ID
            
        Returns:
            Result: 包含座位分布信息
        """
        session = dbSession()
        try:
            # 获取课程信息
            course = session.session.query(ojCourse).filter(
                ojCourse.course_id == course_id
            ).first()
            
            if not course:
                return Result(code=1, msg="课程不存在")
            
            if not course.c_ids:
                return Result(code=1, msg="课程未分配教室")
            
            c_ids = json.loads(course.c_ids)
            
            # 获取座位绑定
            seat_bindings = session.session.query(ojClassUser).filter(
                ojClassUser.course_id == course_id
            ).all()
            
            # 构建座位映射
            seat_map = {}
            for binding in seat_bindings:
                seat_map[binding.seat_number] = {
                    'username': binding.username,
                    'c_id': binding.c_id
                }
            
            # 获取教室信息
            classrooms = session.session.query(ojClass).filter(
                ojClass.c_id.in_(c_ids)
            ).all()
            
            classroom_data = []
            for classroom in classrooms:
                disabled_seats = []
                if classroom.ext_config:
                    ext_config = json.loads(classroom.ext_config)
                    disabled_seats = ext_config.get('disabled_seats', [])
                
                classroom_data.append({
                    'c_id': classroom.c_id,
                    'c_name': classroom.c_name,
                    'c_seat_num': classroom.c_seat_num,
                    'address': classroom.address,
                    'disabled_seats': disabled_seats
                })
            
            return Result(
                code=0,
                msg="success",
                data={
                    'course_id': course_id,
                    'seat_bindings': seat_map,
                    'classrooms': classroom_data
                }
            )
            
        except Exception as e:
            return Result(code=1, msg=f"查询座位分布失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def get_user_seat(course_id: int, username: str) -> Result:
        """
        查询学生座位
        
        Args:
            course_id: 课程ID
            username: 学生用户名
            
        Returns:
            Result: 包含座位信息
        """
        session = dbSession()
        try:
            seat_binding = session.session.query(ojClassUser).filter(
                and_(
                    ojClassUser.course_id == course_id,
                    ojClassUser.username == username
                )
            ).first()
            
            if not seat_binding:
                return Result(code=1, msg="未分配座位")
            
            return Result(
                code=0,
                msg="success",
                data={
                    'username': username,
                    'seat_number': seat_binding.seat_number,
                    'c_id': seat_binding.c_id
                }
            )
            
        except Exception as e:
            return Result(code=1, msg=f"查询座位失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def remove_seat(course_id: int, username: str) -> Result:
        """
        删除学生座位绑定
        
        Args:
            course_id: 课程ID
            username: 学生用户名
            
        Returns:
            Result: 删除结果
        """
        session = dbSession()
        try:
            seat_binding = session.session.query(ojClassUser).filter(
                and_(
                    ojClassUser.course_id == course_id,
                    ojClassUser.username == username
                )
            ).first()
            
            if not seat_binding:
                return Result(code=1, msg="座位绑定不存在")
            
            session.session.delete(seat_binding)
            session.session.commit()
            
            return Result(code=0, msg="座位绑定删除成功")
            
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"删除座位绑定失败: {str(e)}")
        finally:
            del session
