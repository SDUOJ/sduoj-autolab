"""
课程时间管理模型
提供课程时间的创建、查询、更新等业务逻辑
"""
import json
from typing import List, Optional, Dict, Any
from datetime import datetime
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError

from db import dbSession, ojCourse, ojCourseSchedule, ojSign
from utils import Result


class CourseScheduleModel:
    """课程时间管理业务逻辑"""

    @staticmethod
    def add_schedule(
        course_id: int,
        sequence: int,
        start_time: datetime,
        end_time: datetime,
        course_content: Optional[str] = None,
        course_materials: Optional[List[str]] = None,
        course_homework: Optional[str] = None,
        sg_id: Optional[int] = None,
        auto_create_sign: bool = True
    ) -> Result:
        """
        添加课程时间
        
        Args:
            course_id: 课程ID
            sequence: 课程序号
            start_time: 开始时间
            end_time: 结束时间
            course_content: 课程内容
            course_materials: 课程资料（文件ID列表）
            course_homework: 课程作业
            sg_id: 座位组ID
            auto_create_sign: 是否自动创建考勤记录
            
        Returns:
            Result: 包含schedule_id的结果
        """
        # 参数验证
        if sequence <= 0:
            return Result(code=1, msg="课程序号必须为正整数")
        
        session = dbSession()
        try:
            # 验证课程是否存在
            course = session.session.query(ojCourse).filter(
                ojCourse.course_id == course_id
            ).first()
            
            if not course:
                return Result(code=1, msg="课程不存在")
            
            # 验证时间合法性
            if start_time >= end_time:
                return Result(code=1, msg="开始时间必须早于结束时间")
            
            # 可选：验证不是过去的时间（根据业务需求决定是否启用）
            # if start_time < datetime.now():
            #     return Result(code=1, msg="不能创建过去的课程时间")
            
            # 验证时间跨度合理性
            duration_hours = (end_time - start_time).total_seconds() / 3600
            if duration_hours > 12:
                return Result(code=1, msg="单次课程时间不能超过12小时")
            
            # JSON序列化，捕获可能的错误
            try:
                materials_json = json.dumps(course_materials) if course_materials else None
            except (TypeError, ValueError) as e:
                return Result(code=1, msg=f"课程资料JSON序列化失败: {str(e)}")
            
            # 创建课程时间记录
            schedule = ojCourseSchedule(
                course_id=course_id,
                sequence=sequence,
                start_time=start_time,
                end_time=end_time,
                course_content=course_content,
                course_materials=materials_json,
                course_homework=course_homework,
                sg_id=sg_id
            )
            
            session.session.add(schedule)
            session.session.flush()  # 获取schedule_id但不提交
            
            # 自动创建考勤记录
            if auto_create_sign:
                sign = ojSign(
                    course_id=course_id,
                    schedule_id=schedule.schedule_id,
                    title=f"第{sequence}次课考勤",
                    mode=0  # 默认模式0：手动记录
                )
                session.session.add(sign)
                session.session.flush()
                schedule.sg_id = sign.sg_id
            
            session.session.commit()
            session.session.refresh(schedule)
            
            return Result(
                code=0,
                msg="课程时间创建成功",
                data={"schedule_id": schedule.schedule_id}
            )
            
        except IntegrityError as e:
            session.session.rollback()
            return Result(code=1, msg=f"数据库错误（可能存在重复的sequence）: {str(e)}")
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"创建课程时间失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def get_schedule(schedule_id: int) -> Result:
        """
        获取课程时间详情
        
        Args:
            schedule_id: 课程时间ID
            
        Returns:
            Result: 包含课程时间信息的结果
        """
        session = dbSession()
        try:
            schedule = session.session.query(ojCourseSchedule).filter(
                ojCourseSchedule.schedule_id == schedule_id
            ).first()
            
            if not schedule:
                return Result(code=1, msg="课程时间不存在")
            
            # 处理数据
            schedule_data = session.dealData(schedule, timeKeys=['start_time', 'end_time'])
            
            # 解析JSON字段
            if schedule_data.get('course_materials'):
                schedule_data['course_materials'] = json.loads(schedule_data['course_materials'])
            
            return Result(code=0, msg="success", data=schedule_data)
            
        except Exception as e:
            return Result(code=1, msg=f"查询课程时间失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def list_schedules(
        course_id: Optional[int] = None,
        page_now: int = 1,
        page_size: int = 50
    ) -> Result:
        """
        查询课程时间列表
        
        Args:
            course_id: 课程ID过滤
            page_now: 当前页码
            page_size: 每页数量
            
        Returns:
            Result: 包含课程时间列表的结果
        """
        session = dbSession()
        try:
            query = session.session.query(ojCourseSchedule)
            
            # 应用过滤条件
            if course_id:
                query = query.filter(ojCourseSchedule.course_id == course_id)
            
            # 按序号排序
            query = query.order_by(ojCourseSchedule.sequence.asc())
            
            # 分页
            total = query.count()
            schedules = query.offset((page_now - 1) * page_size).limit(page_size).all()
            
            # 处理数据
            schedule_list = []
            for schedule in schedules:
                schedule_data = session.dealData(schedule, timeKeys=['start_time', 'end_time'])
                
                # 解析JSON字段
                if schedule_data.get('course_materials'):
                    schedule_data['course_materials'] = json.loads(schedule_data['course_materials'])
                
                schedule_list.append(schedule_data)
            
            return Result(
                code=0,
                msg="success",
                data={
                    "total": total,
                    "page_now": page_now,
                    "page_size": page_size,
                    "schedules": schedule_list
                }
            )
            
        except Exception as e:
            return Result(code=1, msg=f"查询课程时间列表失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def update_schedule(
        schedule_id: int,
        sequence: Optional[int] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        course_content: Optional[str] = None,
        course_materials: Optional[List[str]] = None,
        course_homework: Optional[str] = None,
        sg_id: Optional[int] = None
    ) -> Result:
        """
        更新课程时间信息
        
        Args:
            schedule_id: 课程时间ID
            sequence: 课程序号
            start_time: 开始时间
            end_time: 结束时间
            course_content: 课程内容
            course_materials: 课程资料（文件ID列表）
            course_homework: 课程作业
            sg_id: 座位组ID
            
        Returns:
            Result: 更新结果
        """
        session = dbSession()
        try:
            schedule = session.session.query(ojCourseSchedule).filter(
                ojCourseSchedule.schedule_id == schedule_id
            ).first()
            
            if not schedule:
                return Result(code=1, msg="课程时间不存在")
            
            # 更新字段
            if sequence is not None:
                schedule.sequence = sequence
            if start_time is not None:
                schedule.start_time = start_time
            if end_time is not None:
                schedule.end_time = end_time
            if course_content is not None:
                schedule.course_content = course_content
            if course_materials is not None:
                schedule.course_materials = json.dumps(course_materials)
            if course_homework is not None:
                schedule.course_homework = course_homework
            if sg_id is not None:
                schedule.sg_id = sg_id
            
            # 验证时间合法性
            if schedule.start_time >= schedule.end_time:
                return Result(code=1, msg="开始时间必须早于结束时间")
            
            session.session.commit()
            
            return Result(code=0, msg="课程时间更新成功")
            
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"更新课程时间失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def delete_schedule(schedule_id: int) -> Result:
        """
        删除课程时间（需要先删除考勤记录）
        
        Args:
            schedule_id: 课程时间ID
            
        Returns:
            Result: 删除结果
        """
        session = dbSession()
        try:
            schedule = session.session.query(ojCourseSchedule).filter(
                ojCourseSchedule.schedule_id == schedule_id
            ).first()
            
            if not schedule:
                return Result(code=1, msg="课程时间不存在")
            
            # 检查是否有考勤记录
            sign_count = session.session.query(ojSign).filter(
                ojSign.schedule_id == schedule_id
            ).count()
            
            if sign_count > 0:
                return Result(code=1, msg=f"课程时间有{sign_count}个考勤记录，请先删除考勤记录")
            
            # 删除课程时间
            session.session.delete(schedule)
            session.session.commit()
            
            return Result(code=0, msg="课程时间删除成功")
            
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"删除课程时间失败: {str(e)}")
        finally:
            del session
