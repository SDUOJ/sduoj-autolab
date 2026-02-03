"""
课程管理模型
提供课程的创建、查询、更新等业务逻辑
"""
import json
from typing import List, Optional, Dict, Any
from datetime import datetime
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError

from db import dbSession, ojCourse, ojCourseSchedule, ojClassUser, ojClassManageUser, ojClass, ojSign
from utils import Result

# 有效的课程标签
VALID_COURSE_TAGS = ['授课', '实验', '考试', '答疑']


class CourseModel:
    """课程管理业务逻辑"""

    @staticmethod
    def create_course(
        course_name: str,
        group_id: int,
        tag: str,
        c_ids: Optional[List[int]] = None,
        ext_config: Optional[Dict[str, Any]] = None
    ) -> Result:
        """
        创建课程
        
        Args:
            course_name: 课程名称
            group_id: 用户组ID
            tag: 课程标签（授课/实验/考试/答疑）
            c_ids: 教室ID列表
            ext_config: 扩展配置
            
        Returns:
            Result: 包含course_id的结果
        """
        # 参数验证
        if not course_name or not course_name.strip():
            return Result(code=1, msg="课程名称不能为空")
        
        if group_id <= 0:
            return Result(code=1, msg="用户组ID必须为正整数")
        
        if tag not in VALID_COURSE_TAGS:
            return Result(code=1, msg=f"无效的课程标签，允许的值: {', '.join(VALID_COURSE_TAGS)}")
        
        session = dbSession()
        try:
            # JSON序列化，捕获可能的错误
            try:
                c_ids_json = json.dumps(c_ids) if c_ids else None
                ext_config_json = json.dumps(ext_config) if ext_config else None
            except (TypeError, ValueError) as e:
                return Result(code=1, msg=f"JSON序列化失败: {str(e)}")
            
            # 创建课程记录
            course = ojCourse(
                course_name=course_name,
                group_id=group_id,
                tag=tag,
                c_ids=c_ids_json,
                ext_config=ext_config_json
            )
            
            session.session.add(course)
            session.session.commit()
            session.session.refresh(course)
            
            return Result(
                code=0,
                msg="课程创建成功",
                data={"course_id": course.course_id}
            )
            
        except IntegrityError as e:
            session.session.rollback()
            return Result(code=1, msg=f"数据库错误: {str(e)}")
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"创建课程失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def get_course(course_id: int) -> Result:
        """
        获取课程详情
        
        Args:
            course_id: 课程ID
            
        Returns:
            Result: 包含课程信息的结果
        """
        session = dbSession()
        try:
            course = session.session.query(ojCourse).filter(
                ojCourse.course_id == course_id
            ).first()
            
            if not course:
                return Result(code=1, msg="课程不存在")
            
            # 处理数据
            course_data = session.dealData(course, timeKeys=['create_time'])
            
            # 解析JSON字段
            if course_data.get('c_ids'):
                course_data['c_ids'] = json.loads(course_data['c_ids'])
            if course_data.get('ext_config'):
                course_data['ext_config'] = json.loads(course_data['ext_config'])
            
            return Result(code=0, msg="success", data=course_data)
            
        except Exception as e:
            return Result(code=1, msg=f"查询课程失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def list_courses(
        group_id: Optional[int] = None,
        tag: Optional[str] = None,
        page_now: int = 1,
        page_size: int = 20
    ) -> Result:
        """
        查询课程列表
        
        Args:
            group_id: 用户组ID过滤
            tag: 课程标签过滤
            page_now: 当前页码
            page_size: 每页数量
            
        Returns:
            Result: 包含课程列表的结果
        """
        session = dbSession()
        try:
            query = session.session.query(ojCourse)
            
            # 应用过滤条件
            if group_id:
                query = query.filter(ojCourse.group_id == group_id)
            if tag:
                query = query.filter(ojCourse.tag == tag)
            
            # 分页
            total = query.count()
            courses = query.offset((page_now - 1) * page_size).limit(page_size).all()
            
            # 处理数据
            course_list = []
            for course in courses:
                course_data = session.dealData(course, timeKeys=['create_time'])
                
                # 解析JSON字段
                if course_data.get('c_ids'):
                    course_data['c_ids'] = json.loads(course_data['c_ids'])
                if course_data.get('ext_config'):
                    course_data['ext_config'] = json.loads(course_data['ext_config'])
                
                course_list.append(course_data)
            
            return Result(
                code=0,
                msg="success",
                data={
                    "total": total,
                    "page_now": page_now,
                    "page_size": page_size,
                    "courses": course_list
                }
            )
            
        except Exception as e:
            return Result(code=1, msg=f"查询课程列表失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def update_course(
        course_id: int,
        course_name: Optional[str] = None,
        tag: Optional[str] = None,
        c_ids: Optional[List[int]] = None,
        ext_config: Optional[Dict[str, Any]] = None
    ) -> Result:
        """
        更新课程信息
        
        Args:
            course_id: 课程ID
            course_name: 课程名称
            tag: 课程标签
            c_ids: 教室ID列表
            ext_config: 扩展配置
            
        Returns:
            Result: 更新结果
        """
        session = dbSession()
        try:
            course = session.session.query(ojCourse).filter(
                ojCourse.course_id == course_id
            ).first()
            
            if not course:
                return Result(code=1, msg="课程不存在")
            
            # 更新字段
            if course_name is not None:
                course.course_name = course_name
            if tag is not None:
                course.tag = tag
            if c_ids is not None:
                course.c_ids = json.dumps(c_ids)
            if ext_config is not None:
                course.ext_config = json.dumps(ext_config)
            
            session.session.commit()
            
            return Result(code=0, msg="课程更新成功")
            
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"更新课程失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def delete_course(course_id: int) -> Result:
        """
        删除课程（需要先删除相关数据）
        
        Args:
            course_id: 课程ID
            
        Returns:
            Result: 删除结果
        """
        session = dbSession()
        try:
            course = session.session.query(ojCourse).filter(
                ojCourse.course_id == course_id
            ).first()
            
            if not course:
                return Result(code=1, msg="课程不存在")
            
            # 检查是否有课程时间
            schedule_count = session.session.query(ojCourseSchedule).filter(
                ojCourseSchedule.course_id == course_id
            ).count()
            
            if schedule_count > 0:
                return Result(code=1, msg=f"课程有{schedule_count}个课程时间，请先删除课程时间")
            
            # 检查是否有考勤记录
            sign_count = session.session.query(ojSign).filter(
                ojSign.course_id == course_id
            ).count()
            
            if sign_count > 0:
                return Result(code=1, msg=f"课程有{sign_count}个考勤记录，无法删除")
            
            # 删除课程
            session.session.delete(course)
            session.session.commit()
            
            return Result(code=0, msg="课程删除成功")
            
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"删除课程失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def assign_classrooms(course_id: int, c_ids: List[int]) -> Result:
        """
        为课程分配教室
        
        Args:
            course_id: 课程ID
            c_ids: 教室ID列表
            
        Returns:
            Result: 分配结果
        """
        session = dbSession()
        try:
            course = session.session.query(ojCourse).filter(
                ojCourse.course_id == course_id
            ).first()
            
            if not course:
                return Result(code=1, msg="课程不存在")
            
            # 验证教室是否存在
            for c_id in c_ids:
                classroom = session.session.query(ojClass).filter(
                    ojClass.c_id == c_id
                ).first()
                if not classroom:
                    return Result(code=1, msg=f"教室ID {c_id} 不存在")
            
            # 更新教室列表
            course.c_ids = json.dumps(c_ids)
            session.session.commit()
            
            return Result(code=0, msg="教室分配成功")
            
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"分配教室失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def add_ta(course_id: int, ta_name: str, ext_info: Optional[Dict[str, Any]] = None) -> Result:
        """
        添加课程助教
        
        Args:
            course_id: 课程ID
            ta_name: 助教姓名
            ext_info: 扩展信息（联系方式等）
            
        Returns:
            Result: 添加结果
        """
        session = dbSession()
        try:
            # 验证课程是否存在
            course = session.session.query(ojCourse).filter(
                ojCourse.course_id == course_id
            ).first()
            
            if not course:
                return Result(code=1, msg="课程不存在")
            
            # 创建助教记录
            ta = ojClassManageUser(
                TA_name=ta_name,
                course_id=course_id,
                ext_info=json.dumps(ext_info) if ext_info else None
            )
            
            session.session.add(ta)
            session.session.commit()
            session.session.refresh(ta)
            
            return Result(
                code=0,
                msg="助教添加成功",
                data={"TA_id": ta.TA_id}
            )
            
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"添加助教失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def list_tas(course_id: int) -> Result:
        """
        查询课程助教列表
        
        Args:
            course_id: 课程ID
            
        Returns:
            Result: 包含助教列表的结果
        """
        session = dbSession()
        try:
            tas = session.session.query(ojClassManageUser).filter(
                ojClassManageUser.course_id == course_id
            ).all()
            
            ta_list = []
            for ta in tas:
                ta_data = session.dealData(ta)
                if ta_data.get('ext_info'):
                    ta_data['ext_info'] = json.loads(ta_data['ext_info'])
                ta_list.append(ta_data)
            
            return Result(code=0, msg="success", data=ta_list)
            
        except Exception as e:
            return Result(code=1, msg=f"查询助教列表失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def remove_ta(ta_id: int) -> Result:
        """
        删除课程助教
        
        Args:
            ta_id: 助教ID
            
        Returns:
            Result: 删除结果
        """
        session = dbSession()
        try:
            ta = session.session.query(ojClassManageUser).filter(
                ojClassManageUser.TA_id == ta_id
            ).first()
            
            if not ta:
                return Result(code=1, msg="助教不存在")
            
            session.session.delete(ta)
            session.session.commit()
            
            return Result(code=0, msg="助教删除成功")
            
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"删除助教失败: {str(e)}")
        finally:
            del session
