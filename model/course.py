"""
课程管理模型
提供课程的创建、查询、更新等业务逻辑
"""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import and_, func
from sqlalchemy.exc import IntegrityError

from db import (
    dbSession,
    ojClass,
    ojClassManageUser,
    ojClassUser,
    ojCourse,
    ojCourseSchedule,
    ojSign,
    ojSignUser,
)
from sduojApi import getGroupMember
from utils import Result


VALID_COURSE_TAGS = ["授课", "实验", "考试", "答疑"]


class CourseModel:
    """课程管理业务逻辑"""

    EXT_TIME_LIST_KEY = "time_list"
    EXT_SIGN_LIST_KEY = "sign_list"
    EXT_TA_LIST_KEY = "ta_list"
    EXT_TA_STUDENT_MAP_KEY = "ta_student_map"
    EXT_TIME_NEXT_ID_KEY = "time_next_id"
    EXT_TA_NEXT_ID_KEY = "ta_next_id"
    # 兼容老数据：历史上这两个字段存放在 ext_config 中
    EXT_MANAGER_GROUPS_KEY = "manager_groups"
    EXT_CREATOR_USERNAME_KEY = "creator_username"

    @staticmethod
    def _parse_json(raw: Optional[str], default: Any) -> Any:
        if not raw:
            return default
        try:
            return json.loads(raw)
        except Exception:
            return default

    @staticmethod
    def _to_time_string(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(value, str):
            txt = value.strip()
            if not txt:
                return None
            try:
                parsed = datetime.fromisoformat(txt.replace("Z", "+00:00"))
                return parsed.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                return txt
        return str(value)

    @staticmethod
    def _normalize_group_ids(raw_groups: Any) -> List[int]:
        if not isinstance(raw_groups, list):
            return []
        groups: Set[int] = set()
        for item in raw_groups:
            if isinstance(item, int) and item > 0:
                groups.add(item)
            elif isinstance(item, str) and item.isdigit() and int(item) > 0:
                groups.add(int(item))
        return sorted(groups)

    @staticmethod
    def _normalize_username(raw_username: Any) -> Optional[str]:
        if not isinstance(raw_username, str):
            return None
        username = raw_username.strip()
        return username if username else None

    @staticmethod
    def _parse_manager_groups(raw_value: Any) -> List[int]:
        if isinstance(raw_value, list):
            return CourseModel._normalize_group_ids(raw_value)
        if isinstance(raw_value, str):
            parsed = CourseModel._parse_json(raw_value, None)
            if parsed is None:
                return []
            return CourseModel._normalize_group_ids(parsed)
        return []

    @staticmethod
    def _dump_manager_groups(group_ids: List[int]) -> Optional[str]:
        clean = CourseModel._normalize_group_ids(group_ids)
        return json.dumps(clean, ensure_ascii=False) if clean else None

    @staticmethod
    def _ensure_ext_shape(ext: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        result = ext.copy() if isinstance(ext, dict) else {}

        raw_time_list = result.get(CourseModel.EXT_TIME_LIST_KEY)
        time_list: List[Dict[str, Any]] = []
        if isinstance(raw_time_list, list):
            for item in raw_time_list:
                if not isinstance(item, dict):
                    continue
                time_item = dict(item)
                time_item["start_time"] = CourseModel._to_time_string(time_item.get("start_time"))
                time_item["end_time"] = CourseModel._to_time_string(time_item.get("end_time"))
                if time_item.get("time_id") is None:
                    continue
                time_list.append(time_item)

        raw_sign_list = result.get(CourseModel.EXT_SIGN_LIST_KEY)
        sign_list: List[Optional[int]] = []
        if isinstance(raw_sign_list, list):
            for sign_id in raw_sign_list:
                if sign_id is None:
                    sign_list.append(None)
                elif isinstance(sign_id, int):
                    sign_list.append(sign_id)
                elif isinstance(sign_id, str) and sign_id.isdigit():
                    sign_list.append(int(sign_id))
                else:
                    sign_list.append(None)

        if len(sign_list) < len(time_list):
            sign_list.extend([None] * (len(time_list) - len(sign_list)))
        elif len(sign_list) > len(time_list):
            sign_list = sign_list[: len(time_list)]

        raw_ta_list = result.get(CourseModel.EXT_TA_LIST_KEY)
        ta_list: List[Dict[str, Any]] = []
        if isinstance(raw_ta_list, list):
            for item in raw_ta_list:
                if not isinstance(item, dict):
                    continue
                ta_id = item.get("TA_id")
                if isinstance(ta_id, str) and ta_id.isdigit():
                    ta_id = int(ta_id)
                if not isinstance(ta_id, int):
                    continue
                ta_name = str(item.get("TA_name") or "").strip()
                if not ta_name:
                    continue
                ext_info = item.get("ext_info")
                ta_list.append(
                    {
                        "TA_id": ta_id,
                        "TA_name": ta_name,
                        "ext_info": ext_info if isinstance(ext_info, dict) else {},
                    }
                )

        raw_ta_student_map = result.get(CourseModel.EXT_TA_STUDENT_MAP_KEY)
        ta_student_map: Dict[str, List[str]] = {}
        if isinstance(raw_ta_student_map, dict):
            for ta_key, usernames in raw_ta_student_map.items():
                if not isinstance(usernames, list):
                    continue
                clean_usernames = []
                for username in usernames:
                    if not isinstance(username, str):
                        continue
                    val = username.strip()
                    if val:
                        clean_usernames.append(val)
                ta_student_map[str(ta_key)] = sorted(set(clean_usernames))

        time_ids = [int(item.get("time_id")) for item in time_list if str(item.get("time_id", "")).isdigit()]
        ta_ids = [item["TA_id"] for item in ta_list]

        # 权限字段迁移到课程独立列，不再保留在 ext_config 中
        result.pop(CourseModel.EXT_CREATOR_USERNAME_KEY, None)
        result.pop(CourseModel.EXT_MANAGER_GROUPS_KEY, None)

        result[CourseModel.EXT_TIME_LIST_KEY] = time_list
        result[CourseModel.EXT_SIGN_LIST_KEY] = sign_list
        result[CourseModel.EXT_TA_LIST_KEY] = ta_list
        result[CourseModel.EXT_TA_STUDENT_MAP_KEY] = ta_student_map
        result[CourseModel.EXT_TIME_NEXT_ID_KEY] = max(time_ids, default=0) + 1
        result[CourseModel.EXT_TA_NEXT_ID_KEY] = max(ta_ids, default=0) + 1

        return result

    @staticmethod
    def _dump_ext(ext: Dict[str, Any]) -> str:
        return json.dumps(ext, ensure_ascii=False)

    @staticmethod
    def _parse_course_row(session: dbSession, course: ojCourse) -> Dict[str, Any]:
        course_data = session.dealData(course, timeKeys=["create_time"])
        course_data["c_ids"] = CourseModel._parse_json(course_data.get("c_ids"), [])
        raw_ext = CourseModel._parse_json(course_data.get("ext_config"), {})
        ext = CourseModel._ensure_ext_shape(raw_ext)
        creator_username, manager_groups = CourseModel._get_course_permission_fields(course, raw_ext)
        course_data["ext_config"] = ext
        course_data["manager_groups"] = manager_groups
        course_data["creator_username"] = creator_username
        course_data["time_count"] = len(ext.get(CourseModel.EXT_TIME_LIST_KEY, []))
        course_data["ta_count"] = len(ext.get(CourseModel.EXT_TA_LIST_KEY, []))
        return course_data

    @staticmethod
    def _get_course_permission_fields(
        course: ojCourse,
        raw_ext: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[str], List[int]]:
        creator_username = CourseModel._normalize_username(getattr(course, "creator_username", None))
        manager_groups = CourseModel._parse_manager_groups(getattr(course, "manager_groups", None))

        if not creator_username and isinstance(raw_ext, dict):
            creator_username = CourseModel._normalize_username(raw_ext.get(CourseModel.EXT_CREATOR_USERNAME_KEY))
        if not manager_groups and isinstance(raw_ext, dict):
            manager_groups = CourseModel._normalize_group_ids(raw_ext.get(CourseModel.EXT_MANAGER_GROUPS_KEY))

        return creator_username, manager_groups

    @staticmethod
    def _sync_legacy_data(session: dbSession, course: ojCourse, ext: Dict[str, Any]) -> bool:
        changed = False

        creator_username, manager_groups = CourseModel._get_course_permission_fields(course, ext)
        if getattr(course, "creator_username", None) != creator_username:
            course.creator_username = creator_username
            changed = True

        manager_groups_json = CourseModel._dump_manager_groups(manager_groups)
        if getattr(course, "manager_groups", None) != manager_groups_json:
            course.manager_groups = manager_groups_json
            changed = True

        # 清理历史 ext_config 中的权限字段，避免脏数据继续扩散
        if CourseModel.EXT_CREATOR_USERNAME_KEY in ext:
            ext.pop(CourseModel.EXT_CREATOR_USERNAME_KEY, None)
            changed = True
        if CourseModel.EXT_MANAGER_GROUPS_KEY in ext:
            ext.pop(CourseModel.EXT_MANAGER_GROUPS_KEY, None)
            changed = True

        if not ext.get(CourseModel.EXT_TIME_LIST_KEY):
            schedules = (
                session.session.query(ojCourseSchedule)
                .filter(ojCourseSchedule.course_id == course.course_id)
                .order_by(ojCourseSchedule.sequence.asc())
                .all()
            )
            if schedules:
                time_list = []
                sign_list = []
                for idx, schedule in enumerate(schedules, start=1):
                    sign_id = schedule.sg_id
                    if sign_id is None:
                        sign = (
                            session.session.query(ojSign)
                            .filter(ojSign.schedule_id == schedule.schedule_id)
                            .first()
                        )
                        sign_id = sign.sg_id if sign else None
                    time_list.append(
                        {
                            "time_id": idx,
                            "schedule_id": schedule.schedule_id,
                            "start_time": CourseModel._to_time_string(schedule.start_time),
                            "end_time": CourseModel._to_time_string(schedule.end_time),
                            "course_content": schedule.course_content,
                            "course_homework": schedule.course_homework,
                            "course_materials": CourseModel._parse_json(schedule.course_materials, []),
                        }
                    )
                    sign_list.append(sign_id)
                ext[CourseModel.EXT_TIME_LIST_KEY] = time_list
                ext[CourseModel.EXT_SIGN_LIST_KEY] = sign_list
                ext[CourseModel.EXT_TIME_NEXT_ID_KEY] = len(time_list) + 1
                changed = True

        if not ext.get(CourseModel.EXT_TA_LIST_KEY):
            old_tas = (
                session.session.query(ojClassManageUser)
                .filter(ojClassManageUser.course_id == course.course_id)
                .all()
            )
            if old_tas:
                ta_list = []
                ta_student_map: Dict[str, List[str]] = {}
                for ta in old_tas:
                    ta_list.append(
                        {
                            "TA_id": ta.TA_id,
                            "TA_name": ta.TA_name,
                            "ext_info": CourseModel._parse_json(ta.ext_info, {}),
                        }
                    )
                    ta_student_map[str(ta.TA_id)] = []

                ext[CourseModel.EXT_TA_LIST_KEY] = ta_list
                ext[CourseModel.EXT_TA_STUDENT_MAP_KEY] = ta_student_map
                ext[CourseModel.EXT_TA_NEXT_ID_KEY] = max((item["TA_id"] for item in ta_list), default=0) + 1
                changed = True

        if changed:
            ext = CourseModel._ensure_ext_shape(ext)
            course.ext_config = CourseModel._dump_ext(ext)

        return changed

    @staticmethod
    def _get_course_and_ext(session: dbSession, course_id: int) -> Tuple[Optional[ojCourse], Optional[Dict[str, Any]], Optional[str]]:
        course = session.session.query(ojCourse).filter(ojCourse.course_id == course_id).first()
        if not course:
            return None, None, "课程不存在"

        ext = CourseModel._ensure_ext_shape(CourseModel._parse_json(course.ext_config, {}))
        if CourseModel._sync_legacy_data(session, course, ext):
            session.session.flush()

        ext = CourseModel._ensure_ext_shape(CourseModel._parse_json(course.ext_config, ext))
        return course, ext, None

    @staticmethod
    def _find_time_index(ext: Dict[str, Any], time_id: int) -> int:
        for idx, item in enumerate(ext.get(CourseModel.EXT_TIME_LIST_KEY, [])):
            if int(item.get("time_id", -1)) == time_id:
                return idx
        return -1

    @staticmethod
    def _find_ta(ext: Dict[str, Any], ta_id: int) -> Optional[Dict[str, Any]]:
        for item in ext.get(CourseModel.EXT_TA_LIST_KEY, []):
            if item.get("TA_id") == ta_id:
                return item
        return None

    @staticmethod
    def _apply_bind_usernames(ext: Dict[str, Any], ta_id: int, usernames: List[str], replace: bool) -> int:
        ta_key = str(ta_id)
        ta_student_map = ext.get(CourseModel.EXT_TA_STUDENT_MAP_KEY, {})
        clean_usernames = sorted({u.strip() for u in usernames if isinstance(u, str) and u.strip()})

        user_set = set(clean_usernames)
        for key, values in list(ta_student_map.items()):
            if not isinstance(values, list):
                ta_student_map[key] = []
                continue
            if key == ta_key and not replace:
                continue
            ta_student_map[key] = [name for name in values if name not in user_set]

        if replace:
            ta_student_map[ta_key] = clean_usernames
        else:
            existing = ta_student_map.get(ta_key, [])
            ta_student_map[ta_key] = sorted(set(existing + clean_usernames))

        ext[CourseModel.EXT_TA_STUDENT_MAP_KEY] = ta_student_map
        return len(clean_usernames)

    @staticmethod
    def _next_global_ta_id(session: dbSession) -> int:
        max_id = session.session.query(func.max(ojClassManageUser.TA_id)).scalar() or 0
        courses = session.session.query(ojCourse.ext_config).all()
        for raw_ext, in courses:
            ext = CourseModel._ensure_ext_shape(CourseModel._parse_json(raw_ext, {}))
            for item in ext.get(CourseModel.EXT_TA_LIST_KEY, []):
                ta_id = item.get("TA_id")
                if isinstance(ta_id, int):
                    max_id = max(max_id, ta_id)
        return int(max_id) + 1

    @staticmethod
    def _reorder_schedule_sequence(session: dbSession, course_id: int, ext: Dict[str, Any]):
        for idx, item in enumerate(ext.get(CourseModel.EXT_TIME_LIST_KEY, []), start=1):
            schedule_id = item.get("schedule_id")
            if not schedule_id:
                continue
            schedule = session.session.query(ojCourseSchedule).filter(
                ojCourseSchedule.schedule_id == schedule_id
            ).first()
            if schedule:
                schedule.sequence = idx

    @staticmethod
    def _has_manage_permission(
        creator_username: Optional[str],
        manager_groups: List[int],
        username: str,
        user_groups: List[int],
        is_superadmin_user: bool
    ) -> bool:
        if is_superadmin_user:
            return True

        if not username:
            return False

        if isinstance(creator_username, str) and creator_username == username:
            return True

        manager_group_set = set(CourseModel._normalize_group_ids(manager_groups))
        current_groups = set(CourseModel._normalize_group_ids(user_groups))
        return len(manager_group_set & current_groups) > 0

    @staticmethod
    def check_manage_permission(
        course_id: int,
        username: str,
        user_groups: List[int],
        is_superadmin_user: bool
    ) -> Result:
        session = dbSession()
        try:
            course = session.session.query(ojCourse).filter(ojCourse.course_id == course_id).first()
            if not course:
                return Result(code=1, msg="课程不存在")

            raw_ext = CourseModel._parse_json(course.ext_config, {})
            creator_username, manager_groups = CourseModel._get_course_permission_fields(course, raw_ext)
            if not CourseModel._has_manage_permission(
                creator_username=creator_username,
                manager_groups=manager_groups,
                username=username or "",
                user_groups=user_groups or [],
                is_superadmin_user=is_superadmin_user
            ):
                return Result(code=1, msg="Permission Denial")

            return Result(code=0, msg="success")
        except Exception as e:
            return Result(code=1, msg=f"权限校验失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def get_ta_course_id(ta_id: int) -> Result:
        session = dbSession()
        try:
            courses = session.session.query(ojCourse).all()
            for course in courses:
                ext = CourseModel._ensure_ext_shape(CourseModel._parse_json(course.ext_config, {}))
                ta_list = ext.get(CourseModel.EXT_TA_LIST_KEY, [])
                if any(item.get("TA_id") == ta_id for item in ta_list):
                    return Result(code=0, msg="success", data={"course_id": course.course_id})
            return Result(code=1, msg="助教不存在")
        except Exception as e:
            return Result(code=1, msg=f"查询助教所属课程失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def create_course(
        course_name: str,
        group_id: int,
        tag: str,
        c_ids: Optional[List[int]] = None,
        ext_config: Optional[Dict[str, Any]] = None,
        manager_groups: Optional[List[int]] = None,
        creator_username: Optional[str] = None,
    ) -> Result:
        if not course_name or not course_name.strip():
            return Result(code=1, msg="课程名称不能为空")
        if group_id <= 0:
            return Result(code=1, msg="用户组ID必须为正整数")
        if tag not in VALID_COURSE_TAGS:
            return Result(code=1, msg=f"无效的课程标签，允许的值: {', '.join(VALID_COURSE_TAGS)}")

        clean_c_ids = None
        if c_ids is not None:
            clean_c_ids = sorted({int(item) for item in c_ids if isinstance(item, int) and item > 0})

        session = dbSession()
        try:
            ext = CourseModel._ensure_ext_shape(ext_config or {})
            clean_manager_groups = CourseModel._normalize_group_ids(manager_groups)
            clean_creator_username = CourseModel._normalize_username(creator_username)
            course = ojCourse(
                course_name=course_name.strip(),
                group_id=group_id,
                tag=tag,
                c_ids=json.dumps(clean_c_ids) if clean_c_ids else None,
                creator_username=clean_creator_username,
                manager_groups=CourseModel._dump_manager_groups(clean_manager_groups),
                ext_config=CourseModel._dump_ext(ext),
            )
            session.session.add(course)
            session.session.commit()
            session.session.refresh(course)
            return Result(code=0, msg="课程创建成功", data={"course_id": course.course_id})
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
        session = dbSession()
        try:
            course, ext, error = CourseModel._get_course_and_ext(session, course_id)
            if error:
                return Result(code=1, msg=error)

            course.ext_config = CourseModel._dump_ext(ext)
            session.session.commit()

            data = CourseModel._parse_course_row(session, course)
            return Result(code=0, msg="success", data=data)
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"查询课程失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def list_courses(
        group_id: Optional[int] = None,
        tag: Optional[str] = None,
        page_now: int = 1,
        page_size: int = 20,
        viewer_username: Optional[str] = None,
        viewer_groups: Optional[List[int]] = None,
        viewer_is_superadmin: bool = False,
    ) -> Result:
        session = dbSession()
        try:
            query = session.session.query(ojCourse)
            if group_id:
                query = query.filter(ojCourse.group_id == group_id)
            if tag:
                query = query.filter(ojCourse.tag == tag)

            rows = query.order_by(ojCourse.create_time.desc()).all()
            enable_permission_filter = (
                viewer_is_superadmin
                or viewer_username is not None
                or viewer_groups is not None
            )

            courses = []
            changed = False
            for row in rows:
                raw_ext = CourseModel._parse_json(row.ext_config, {})
                ext = CourseModel._ensure_ext_shape(raw_ext)
                if CourseModel._sync_legacy_data(session, row, ext):
                    changed = True
                    raw_ext = CourseModel._parse_json(row.ext_config, ext)
                    ext = CourseModel._ensure_ext_shape(raw_ext)

                creator_username, manager_groups = CourseModel._get_course_permission_fields(row, raw_ext)

                if enable_permission_filter:
                    if not CourseModel._has_manage_permission(
                        creator_username=creator_username,
                        manager_groups=manager_groups,
                        username=viewer_username or "",
                        user_groups=viewer_groups or [],
                        is_superadmin_user=viewer_is_superadmin
                    ):
                        continue
                courses.append(CourseModel._parse_course_row(session, row))

            total = len(courses)
            page_start = (page_now - 1) * page_size
            page_end = page_start + page_size
            courses = courses[page_start:page_end]

            if changed:
                session.session.commit()

            return Result(
                code=0,
                msg="success",
                data={
                    "total": total,
                    "page_now": page_now,
                    "page_size": page_size,
                    "courses": courses,
                },
            )
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"查询课程列表失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def update_course(
        course_id: int,
        course_name: Optional[str] = None,
        group_id: Optional[int] = None,
        tag: Optional[str] = None,
        c_ids: Optional[List[int]] = None,
        ext_config: Optional[Dict[str, Any]] = None,
        manager_groups: Optional[List[int]] = None,
    ) -> Result:
        session = dbSession()
        try:
            course, ext, error = CourseModel._get_course_and_ext(session, course_id)
            if error:
                return Result(code=1, msg=error)

            if course_name is not None:
                name = course_name.strip()
                if not name:
                    return Result(code=1, msg="课程名称不能为空")
                course.course_name = name

            if group_id is not None:
                if group_id <= 0:
                    return Result(code=1, msg="用户组ID必须为正整数")
                course.group_id = group_id

            if tag is not None:
                if tag not in VALID_COURSE_TAGS:
                    return Result(code=1, msg=f"无效的课程标签，允许的值: {', '.join(VALID_COURSE_TAGS)}")
                course.tag = tag

            if c_ids is not None:
                clean_c_ids = sorted({int(item) for item in c_ids if isinstance(item, int) and item > 0})
                course.c_ids = json.dumps(clean_c_ids) if clean_c_ids else None

            if ext_config is not None:
                merged = dict(ext)
                merged.update(ext_config)
                ext = CourseModel._ensure_ext_shape(merged)

            if manager_groups is not None:
                course.manager_groups = CourseModel._dump_manager_groups(manager_groups)

            course.ext_config = CourseModel._dump_ext(ext)
            session.session.commit()
            return Result(code=0, msg="课程更新成功")
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"更新课程失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def delete_course(course_id: int) -> Result:
        session = dbSession()
        try:
            course = session.session.query(ojCourse).filter(ojCourse.course_id == course_id).first()
            if not course:
                return Result(code=1, msg="课程不存在")

            schedule_count = (
                session.session.query(ojCourseSchedule)
                .filter(ojCourseSchedule.course_id == course_id)
                .count()
            )
            if schedule_count > 0:
                return Result(code=1, msg=f"课程有{schedule_count}个课程时间，请先删除课程时间")

            sign_count = session.session.query(ojSign).filter(ojSign.course_id == course_id).count()
            if sign_count > 0:
                return Result(code=1, msg=f"课程有{sign_count}个考勤记录，无法删除")

            session.session.query(ojClassUser).filter(
                ojClassUser.course_id == course_id
            ).delete(synchronize_session=False)
            session.session.query(ojClassManageUser).filter(
                ojClassManageUser.course_id == course_id
            ).delete(synchronize_session=False)

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
        session = dbSession()
        try:
            course = session.session.query(ojCourse).filter(ojCourse.course_id == course_id).first()
            if not course:
                return Result(code=1, msg="课程不存在")

            clean_c_ids = sorted({int(item) for item in c_ids if isinstance(item, int) and item > 0})
            if not clean_c_ids:
                return Result(code=1, msg="请至少分配一个教室")

            classrooms = (
                session.session.query(ojClass.c_id)
                .filter(ojClass.c_id.in_(clean_c_ids))
                .all()
            )
            exists = {c_id for c_id, in classrooms}
            missing = [c_id for c_id in clean_c_ids if c_id not in exists]
            if missing:
                return Result(code=1, msg=f"教室ID不存在: {','.join(str(i) for i in missing)}")

            course.c_ids = json.dumps(clean_c_ids)
            session.session.commit()
            return Result(code=0, msg="教室分配成功")
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"分配教室失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def list_course_times(course_id: int) -> Result:
        session = dbSession()
        try:
            course, ext, error = CourseModel._get_course_and_ext(session, course_id)
            if error:
                return Result(code=1, msg=error)

            course.ext_config = CourseModel._dump_ext(ext)
            session.session.commit()
            return Result(
                code=0,
                msg="success",
                data={
                    "course_id": course_id,
                    "time_list": ext.get(CourseModel.EXT_TIME_LIST_KEY, []),
                    "sign_list": ext.get(CourseModel.EXT_SIGN_LIST_KEY, []),
                },
            )
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"查询课程时间失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def add_course_time(
        course_id: int,
        start_time: datetime,
        end_time: datetime,
        auto_create_sign: bool = True,
        course_content: Optional[str] = None,
        course_homework: Optional[str] = None,
        course_materials: Optional[List[str]] = None,
    ) -> Result:
        if start_time >= end_time:
            return Result(code=1, msg="开始时间必须早于结束时间")

        session = dbSession()
        try:
            course, ext, error = CourseModel._get_course_and_ext(session, course_id)
            if error:
                return Result(code=1, msg=error)

            max_sequence = (
                session.session.query(func.max(ojCourseSchedule.sequence))
                .filter(ojCourseSchedule.course_id == course_id)
                .scalar()
            )
            sequence = int(max_sequence or 0) + 1

            schedule = ojCourseSchedule(
                course_id=course_id,
                sequence=sequence,
                start_time=start_time,
                end_time=end_time,
                course_content=course_content,
                course_homework=course_homework,
                course_materials=json.dumps(course_materials) if course_materials is not None else None,
                sg_id=None,
            )
            session.session.add(schedule)
            session.session.flush()

            sign_id = None
            if auto_create_sign:
                sign = ojSign(
                    course_id=course_id,
                    schedule_id=schedule.schedule_id,
                    title=f"第{sequence}次课考勤",
                    mode=0,
                )
                session.session.add(sign)
                session.session.flush()
                sign_id = sign.sg_id
                schedule.sg_id = sign_id

            next_time_id = int(ext.get(CourseModel.EXT_TIME_NEXT_ID_KEY, 1))
            time_item = {
                "time_id": next_time_id,
                "schedule_id": schedule.schedule_id,
                "start_time": CourseModel._to_time_string(start_time),
                "end_time": CourseModel._to_time_string(end_time),
                "course_content": course_content,
                "course_homework": course_homework,
                "course_materials": course_materials if course_materials is not None else [],
            }

            ext[CourseModel.EXT_TIME_LIST_KEY].append(time_item)
            ext[CourseModel.EXT_SIGN_LIST_KEY].append(sign_id)
            ext[CourseModel.EXT_TIME_NEXT_ID_KEY] = next_time_id + 1
            CourseModel._reorder_schedule_sequence(session, course_id, ext)

            course.ext_config = CourseModel._dump_ext(ext)
            session.session.commit()

            return Result(
                code=0,
                msg="课程时间创建成功",
                data={"time_id": next_time_id, "schedule_id": schedule.schedule_id, "sg_id": sign_id},
            )
        except IntegrityError as e:
            session.session.rollback()
            return Result(code=1, msg=f"数据库错误: {str(e)}")
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"创建课程时间失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def update_course_time(
        course_id: int,
        time_id: int,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        auto_create_sign: Optional[bool] = None,
        course_content: Optional[str] = None,
        course_homework: Optional[str] = None,
        course_materials: Optional[List[str]] = None,
    ) -> Result:
        session = dbSession()
        try:
            course, ext, error = CourseModel._get_course_and_ext(session, course_id)
            if error:
                return Result(code=1, msg=error)

            index = CourseModel._find_time_index(ext, time_id)
            if index < 0:
                return Result(code=1, msg="课程时间不存在")

            time_item = ext[CourseModel.EXT_TIME_LIST_KEY][index]
            sign_list = ext[CourseModel.EXT_SIGN_LIST_KEY]
            schedule_id = time_item.get("schedule_id")
            schedule = None
            if schedule_id:
                schedule = (
                    session.session.query(ojCourseSchedule)
                    .filter(ojCourseSchedule.schedule_id == schedule_id)
                    .first()
                )

            old_start = time_item.get("start_time")
            old_end = time_item.get("end_time")
            new_start = CourseModel._to_time_string(start_time) if start_time is not None else old_start
            new_end = CourseModel._to_time_string(end_time) if end_time is not None else old_end

            if new_start and new_end:
                try:
                    if datetime.fromisoformat(new_start.replace(" ", "T")) >= datetime.fromisoformat(new_end.replace(" ", "T")):
                        return Result(code=1, msg="开始时间必须早于结束时间")
                except Exception:
                    pass

            if start_time is not None:
                time_item["start_time"] = new_start
                if schedule:
                    schedule.start_time = start_time
            if end_time is not None:
                time_item["end_time"] = new_end
                if schedule:
                    schedule.end_time = end_time
            if course_content is not None:
                time_item["course_content"] = course_content
                if schedule:
                    schedule.course_content = course_content
            if course_homework is not None:
                time_item["course_homework"] = course_homework
                if schedule:
                    schedule.course_homework = course_homework
            if course_materials is not None:
                time_item["course_materials"] = course_materials
                if schedule:
                    schedule.course_materials = json.dumps(course_materials)

            sign_id = sign_list[index]
            if auto_create_sign and sign_id is None and schedule:
                sign = ojSign(
                    course_id=course_id,
                    schedule_id=schedule.schedule_id,
                    title=f"第{index + 1}次课考勤",
                    mode=0,
                )
                session.session.add(sign)
                session.session.flush()
                sign_id = sign.sg_id
                sign_list[index] = sign_id
                schedule.sg_id = sign_id

            ext[CourseModel.EXT_TIME_LIST_KEY][index] = time_item
            ext[CourseModel.EXT_SIGN_LIST_KEY] = sign_list
            CourseModel._reorder_schedule_sequence(session, course_id, ext)

            course.ext_config = CourseModel._dump_ext(ext)
            session.session.commit()
            return Result(code=0, msg="课程时间更新成功", data={"sg_id": sign_id})
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"更新课程时间失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def remove_course_time(course_id: int, time_id: int) -> Result:
        session = dbSession()
        try:
            course, ext, error = CourseModel._get_course_and_ext(session, course_id)
            if error:
                return Result(code=1, msg=error)

            index = CourseModel._find_time_index(ext, time_id)
            if index < 0:
                return Result(code=1, msg="课程时间不存在")

            time_item = ext[CourseModel.EXT_TIME_LIST_KEY][index]
            sign_id = ext[CourseModel.EXT_SIGN_LIST_KEY][index]
            schedule_id = time_item.get("schedule_id")

            schedule = None
            if schedule_id:
                schedule = (
                    session.session.query(ojCourseSchedule)
                    .filter(ojCourseSchedule.schedule_id == schedule_id)
                    .first()
                )

            if sign_id is not None:
                sign_user_count = (
                    session.session.query(ojSignUser)
                    .filter(ojSignUser.sg_id == sign_id)
                    .count()
                )
                if sign_user_count > 0:
                    return Result(code=1, msg="该课程时间已有考勤记录，无法删除")

                sign = session.session.query(ojSign).filter(ojSign.sg_id == sign_id).first()
                if schedule:
                    schedule.sg_id = None
                if sign:
                    session.session.delete(sign)

            if schedule:
                session.session.delete(schedule)

            ext[CourseModel.EXT_TIME_LIST_KEY].pop(index)
            ext[CourseModel.EXT_SIGN_LIST_KEY].pop(index)
            CourseModel._reorder_schedule_sequence(session, course_id, ext)

            course.ext_config = CourseModel._dump_ext(ext)
            session.session.commit()
            return Result(code=0, msg="课程时间删除成功")
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"删除课程时间失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def add_ta(
        course_id: int,
        ta_name: str,
        ext_info: Optional[Dict[str, Any]] = None,
        usernames: Optional[List[str]] = None,
    ) -> Result:
        if not ta_name or not ta_name.strip():
            return Result(code=1, msg="助教姓名不能为空")

        session = dbSession()
        try:
            course, ext, error = CourseModel._get_course_and_ext(session, course_id)
            if error:
                return Result(code=1, msg=error)

            ta_id = CourseModel._next_global_ta_id(session)
            ext[CourseModel.EXT_TA_LIST_KEY].append(
                {
                    "TA_id": ta_id,
                    "TA_name": ta_name.strip(),
                    "ext_info": ext_info if isinstance(ext_info, dict) else {},
                }
            )
            ext[CourseModel.EXT_TA_NEXT_ID_KEY] = max(int(ext.get(CourseModel.EXT_TA_NEXT_ID_KEY, 1)), ta_id + 1)

            bound_count = 0
            if usernames:
                bound_count = CourseModel._apply_bind_usernames(ext, ta_id, usernames, replace=False)

            course.ext_config = CourseModel._dump_ext(ext)
            session.session.commit()
            return Result(code=0, msg="助教添加成功", data={"TA_id": ta_id, "bound_students": bound_count})
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"添加助教失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def list_tas(course_id: int) -> Result:
        session = dbSession()
        try:
            course, ext, error = CourseModel._get_course_and_ext(session, course_id)
            if error:
                return Result(code=1, msg=error)

            course.ext_config = CourseModel._dump_ext(ext)
            session.session.commit()

            ta_student_map = ext.get(CourseModel.EXT_TA_STUDENT_MAP_KEY, {})
            result = []
            for ta in sorted(ext.get(CourseModel.EXT_TA_LIST_KEY, []), key=lambda x: x.get("TA_id", 0)):
                ta_id = ta.get("TA_id")
                students = ta_student_map.get(str(ta_id), [])
                result.append(
                    {
                        "TA_id": ta_id,
                        "TA_name": ta.get("TA_name"),
                        "course_id": course_id,
                        "ext_info": ta.get("ext_info") if isinstance(ta.get("ext_info"), dict) else {},
                        "students": students,
                        "bind_student_count": len(students),
                    }
                )

            return Result(code=0, msg="success", data=result)
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"查询助教列表失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def update_ta(
        course_id: int,
        ta_id: int,
        ta_name: Optional[str] = None,
        ext_info: Optional[Dict[str, Any]] = None,
        usernames: Optional[List[str]] = None,
    ) -> Result:
        session = dbSession()
        try:
            course, ext, error = CourseModel._get_course_and_ext(session, course_id)
            if error:
                return Result(code=1, msg=error)

            ta = CourseModel._find_ta(ext, ta_id)
            if not ta:
                return Result(code=1, msg="助教不存在")

            if ta_name is not None:
                val = ta_name.strip()
                if not val:
                    return Result(code=1, msg="助教姓名不能为空")
                ta["TA_name"] = val

            if ext_info is not None:
                ta["ext_info"] = ext_info if isinstance(ext_info, dict) else {}

            bound_count = None
            if usernames is not None:
                bound_count = CourseModel._apply_bind_usernames(ext, ta_id, usernames, replace=True)

            course.ext_config = CourseModel._dump_ext(ext)
            session.session.commit()
            return Result(
                code=0,
                msg="助教更新成功",
                data={"TA_id": ta_id, "bound_students": bound_count},
            )
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"更新助教失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def remove_ta(ta_id: int) -> Result:
        session = dbSession()
        try:
            courses = session.session.query(ojCourse).all()
            for course in courses:
                ext = CourseModel._ensure_ext_shape(CourseModel._parse_json(course.ext_config, {}))
                ta_list = ext.get(CourseModel.EXT_TA_LIST_KEY, [])
                target = [item for item in ta_list if item.get("TA_id") == ta_id]
                if not target:
                    continue

                ext[CourseModel.EXT_TA_LIST_KEY] = [item for item in ta_list if item.get("TA_id") != ta_id]
                ext.get(CourseModel.EXT_TA_STUDENT_MAP_KEY, {}).pop(str(ta_id), None)
                course.ext_config = CourseModel._dump_ext(ext)
                session.session.commit()
                return Result(code=0, msg="助教删除成功")

            return Result(code=1, msg="助教不存在")
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"删除助教失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def bind_students_to_ta(course_id: int, ta_id: int, usernames: List[str]) -> Result:
        session = dbSession()
        try:
            course, ext, error = CourseModel._get_course_and_ext(session, course_id)
            if error:
                return Result(code=1, msg=error)

            if not CourseModel._find_ta(ext, ta_id):
                return Result(code=1, msg="助教不存在或不属于该课程")

            clean_usernames = [u for u in usernames if isinstance(u, str) and u.strip()]
            if not clean_usernames:
                return Result(code=1, msg="学生列表不能为空")

            bound_count = CourseModel._apply_bind_usernames(ext, ta_id, clean_usernames, replace=False)
            course.ext_config = CourseModel._dump_ext(ext)
            session.session.commit()
            return Result(code=0, msg="学生助教绑定成功", data={"bound_students": bound_count})
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"绑定学生助教失败: {str(e)}")
        finally:
            del session

    @staticmethod
    async def list_group_students(course_id: int) -> Result:
        session = dbSession()
        try:
            course = session.session.query(ojCourse).filter(ojCourse.course_id == course_id).first()
            if not course:
                return Result(code=1, msg="课程不存在")

            members_data = await getGroupMember(course.group_id)
            normalized = members_data
            if isinstance(normalized, str):
                text = normalized.strip()
                if not text:
                    return Result(code=1, msg="查询组成员失败: 用户服务返回空内容")
                try:
                    normalized = json.loads(text)
                except Exception:
                    return Result(code=1, msg="查询组成员失败: 用户服务返回非JSON内容")

            payload: Dict[str, Any]
            members: List[Any]
            if isinstance(normalized, dict):
                # 兼容两种结构:
                # 1) {"members":[...]}
                # 2) {"code":0,"data":{"members":[...]}}
                inner = normalized.get("data")
                payload = inner if isinstance(inner, dict) else normalized
                members = payload.get("members", [])
                if not isinstance(members, list):
                    members = []
            elif isinstance(normalized, list):
                members = normalized
                payload = {"members": members}
            else:
                return Result(code=1, msg="查询组成员失败: 用户服务返回格式异常")

            return Result(
                code=0,
                msg="success",
                data={
                    "course_id": course_id,
                    "group_id": course.group_id,
                    "students": members,
                    "members": members,
                    "group_info": payload,
                },
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            return Result(code=1, msg=f"查询课程学生失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def list_user_courses(
        requester_username: str,
        requester_groups: List[int],
        tag: Optional[str] = None,
        page_now: int = 1,
        page_size: int = 20,
        group_id: Optional[int] = None,
        target_username: Optional[str] = None,
        requester_is_superadmin: bool = False,
    ) -> Result:
        session = dbSession()
        try:
            if not requester_username:
                return Result(code=1, msg="用户名不能为空")

            viewer_username = requester_username.strip()
            effective_username = (
                target_username.strip()
                if isinstance(target_username, str) and target_username.strip()
                else viewer_username
            )
            viewer_groups = CourseModel._normalize_group_ids(requester_groups or [])
            viewer_group_set = set(viewer_groups)
            is_view_other_user = effective_username != viewer_username

            query = session.session.query(ojCourse)
            if group_id is not None:
                query = query.filter(ojCourse.group_id == group_id)
            if tag:
                query = query.filter(ojCourse.tag == tag)

            courses = query.order_by(ojCourse.create_time.desc()).all()

            result_courses = []
            changed = False
            for course in courses:
                raw_ext = CourseModel._parse_json(course.ext_config, {})
                ext = CourseModel._ensure_ext_shape(raw_ext)
                if CourseModel._sync_legacy_data(session, course, ext):
                    changed = True
                    raw_ext = CourseModel._parse_json(course.ext_config, ext)
                    ext = CourseModel._ensure_ext_shape(raw_ext)

                creator_username, manager_groups = CourseModel._get_course_permission_fields(course, raw_ext)

                can_manage = CourseModel._has_manage_permission(
                    creator_username=creator_username,
                    manager_groups=manager_groups,
                    username=viewer_username,
                    user_groups=viewer_groups,
                    is_superadmin_user=requester_is_superadmin
                )
                if is_view_other_user and not can_manage:
                    continue
                if not is_view_other_user:
                    in_group = requester_is_superadmin or int(course.group_id) in viewer_group_set
                    if not in_group and not can_manage:
                        continue

                c_ids = CourseModel._parse_json(course.c_ids, [])
                classrooms = []
                if c_ids:
                    cls_objs = session.session.query(ojClass).filter(ojClass.c_id.in_(c_ids)).all()
                    classrooms = [
                        {"c_id": c.c_id, "c_name": c.c_name, "address": c.address}
                        for c in cls_objs
                    ]

                seat_binding = (
                    session.session.query(ojClassUser)
                    .filter(
                        and_(
                            ojClassUser.course_id == course.course_id,
                            ojClassUser.username == effective_username,
                        )
                    )
                    .first()
                )

                seat_info = None
                if seat_binding:
                    classroom_name = None
                    classroom_address = None
                    if seat_binding.c_id:
                        c_obj = session.session.query(ojClass).filter(ojClass.c_id == seat_binding.c_id).first()
                        if c_obj:
                            classroom_name = c_obj.c_name
                            classroom_address = c_obj.address
                    seat_info = {
                        "c_id": seat_binding.c_id,
                        "classroom_name": classroom_name,
                        "address": classroom_address,
                        "seat_number": seat_binding.seat_number,
                    }

                assigned_ta = None
                ta_map = ext.get(CourseModel.EXT_TA_STUDENT_MAP_KEY, {})
                ta_id = None
                for key, values in ta_map.items():
                    if effective_username in (values or []):
                        ta_id = int(key) if str(key).isdigit() else None
                        break
                if ta_id is not None:
                    ta_item = CourseModel._find_ta(ext, ta_id)
                    if ta_item:
                        assigned_ta = {
                            "TA_id": ta_item.get("TA_id"),
                            "TA_name": ta_item.get("TA_name"),
                            "ext_info": ta_item.get("ext_info") if isinstance(ta_item.get("ext_info"), dict) else {},
                        }

                result_courses.append(
                    {
                        "course_id": course.course_id,
                        "course_name": course.course_name,
                        "group_id": course.group_id,
                        "tag": course.tag,
                        "course_times": ext.get(CourseModel.EXT_TIME_LIST_KEY, []),
                        "sign_list": ext.get(CourseModel.EXT_SIGN_LIST_KEY, []),
                        "classrooms": classrooms,
                        "seat_info": seat_info,
                        "assigned_ta": assigned_ta,
                        "viewer_username": viewer_username,
                        "target_username": effective_username,
                        "can_manage": can_manage,
                    }
                )

            if changed:
                session.session.commit()

            total = len(result_courses)
            start = (page_now - 1) * page_size
            end = start + page_size
            result_courses = result_courses[start:end]

            return Result(
                code=0,
                msg="success",
                data={
                    "total": total,
                    "page_now": page_now,
                    "page_size": page_size,
                    "courses": result_courses,
                },
            )
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"查询用户课程列表失败: {str(e)}")
        finally:
            del session
