"""
座位绑定管理模型 - v3.0 课程中心化架构
提供学生座位分配、查询等业务逻辑
"""
import json
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import and_

from db import dbSession, ojClass, ojClassUser, ojCourse
from sduojApi import getGroupMember
from utils import Result


class SeatBindingModel:
    """座位绑定业务逻辑"""

    @staticmethod
    def _normalize_positive_ints(raw_values: Optional[List[Any]]) -> List[int]:
        if not isinstance(raw_values, list):
            return []
        result: Set[int] = set()
        for item in raw_values:
            if isinstance(item, int) and item > 0:
                result.add(item)
            elif isinstance(item, str) and item.isdigit() and int(item) > 0:
                result.add(int(item))
        return sorted(result)

    @staticmethod
    def _extract_disabled_seats(ext_config: Optional[Dict[str, Any]]) -> Set[int]:
        if not isinstance(ext_config, dict):
            return set()

        disabled: Set[int] = set()

        seat_ban = ext_config.get("seat-ban")
        if isinstance(seat_ban, list):
            for item in seat_ban:
                if isinstance(item, dict):
                    seat_number = item.get("seat_number")
                    if isinstance(seat_number, int) and seat_number > 0:
                        disabled.add(seat_number)

        disabled_seats = ext_config.get("disabled_seats")
        if isinstance(disabled_seats, list):
            for seat in disabled_seats:
                if isinstance(seat, int) and seat > 0:
                    disabled.add(seat)

        return disabled

    @staticmethod
    def _build_course_classroom_slots(
        session: dbSession,
        course_id: int,
        classroom_ids: List[int],
    ) -> Dict[int, List[int]]:
        classrooms = (
            session.session.query(ojClass)
            .filter(ojClass.c_id.in_(classroom_ids))
            .all()
        )

        classroom_map = {item.c_id: item for item in classrooms}
        slots: Dict[int, List[int]] = {}

        for c_id in classroom_ids:
            classroom = classroom_map.get(c_id)
            if classroom is None:
                continue

            ext_config = json.loads(classroom.ext_config) if classroom.ext_config else {}
            disabled = SeatBindingModel._extract_disabled_seats(ext_config)

            occupied_rows = (
                session.session.query(ojClassUser.seat_number)
                .filter(
                    and_(
                        ojClassUser.course_id == course_id,
                        ojClassUser.c_id == c_id,
                        ojClassUser.seat_number.isnot(None),
                    )
                )
                .all()
            )
            occupied = {seat for seat, in occupied_rows if seat is not None}

            available = []
            for seat_number in range(1, classroom.c_seat_num + 1):
                if seat_number in disabled or seat_number in occupied:
                    continue
                available.append(seat_number)

            slots[c_id] = available

        return slots

    @staticmethod
    def assign_seats(
        course_id: int,
        username: str,
        seat_number: int,
        c_id: Optional[int] = None
    ) -> Result:
        session = dbSession()
        try:
            course = session.session.query(ojCourse).filter(
                ojCourse.course_id == course_id
            ).first()

            if not course:
                return Result(code=1, msg="课程不存在")

            if c_id is not None and course.c_ids:
                c_ids = json.loads(course.c_ids)
                if c_id not in c_ids:
                    return Result(code=1, msg="教室不属于该课程")

            existing = session.session.query(ojClassUser).filter(
                and_(
                    ojClassUser.course_id == course_id,
                    ojClassUser.seat_number == seat_number,
                    ojClassUser.c_id == c_id,
                )
            ).first()

            if existing and existing.username != username:
                return Result(code=1, msg=f"座位{seat_number}已被{existing.username}占用")

            user_seat = session.session.query(ojClassUser).filter(
                and_(
                    ojClassUser.course_id == course_id,
                    ojClassUser.username == username
                )
            ).first()

            if user_seat:
                user_seat.seat_number = seat_number
                user_seat.c_id = c_id
            else:
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
    async def get_auto_assign_options(course_id: int) -> Result:
        session = dbSession()
        try:
            course = session.session.query(ojCourse).filter(ojCourse.course_id == course_id).first()
            if not course:
                return Result(code=1, msg="课程不存在")

            c_ids = json.loads(course.c_ids) if course.c_ids else []
            if not c_ids:
                return Result(code=1, msg="课程未分配教室")

            members_data = await getGroupMember(course.group_id)
            members = members_data.get("members", []) if isinstance(members_data, dict) else []

            existing_rows = session.session.query(ojClassUser).filter(
                ojClassUser.course_id == course_id
            ).all()
            existing_map = {row.username: row for row in existing_rows}

            classroom_slots = SeatBindingModel._build_course_classroom_slots(session, course_id, c_ids)
            classrooms = (
                session.session.query(ojClass)
                .filter(ojClass.c_id.in_(c_ids))
                .all()
            )
            classroom_map = {item.c_id: item for item in classrooms}

            classroom_list = []
            for c_id in c_ids:
                classroom = classroom_map.get(c_id)
                if classroom is None:
                    continue
                classroom_list.append(
                    {
                        "c_id": c_id,
                        "c_name": classroom.c_name,
                        "address": classroom.address,
                        "remaining_seats": len(classroom_slots.get(c_id, [])),
                    }
                )

            students = []
            for item in members:
                if not isinstance(item, dict):
                    continue
                username = str(item.get("username") or "").strip()
                if not username:
                    continue
                seat_row = existing_map.get(username)
                students.append(
                    {
                        "username": username,
                        "nickname": item.get("nickname"),
                        "has_seat": seat_row is not None,
                        "c_id": seat_row.c_id if seat_row else None,
                        "seat_number": seat_row.seat_number if seat_row else None,
                    }
                )

            return Result(
                code=0,
                msg="success",
                data={
                    "course_id": course_id,
                    "group_id": course.group_id,
                    "students": students,
                    "classrooms": classroom_list,
                },
            )
        except Exception as e:
            return Result(code=1, msg=f"查询自动分配信息失败: {str(e)}")
        finally:
            del session

    @staticmethod
    async def auto_assign_seats(
        course_id: int,
        usernames: Optional[List[str]] = None,
        c_ids: Optional[List[int]] = None,
    ) -> Result:
        session = dbSession()
        try:
            course = session.session.query(ojCourse).filter(
                ojCourse.course_id == course_id
            ).first()

            if not course:
                return Result(code=1, msg="课程不存在")

            if course.group_id is None:
                return Result(code=1, msg="课程未绑定用户组")

            course_c_ids = json.loads(course.c_ids) if course.c_ids else []
            if not course_c_ids:
                return Result(code=1, msg="课程未分配教室")

            selected_c_ids = SeatBindingModel._normalize_positive_ints(c_ids) if c_ids else list(course_c_ids)
            if not selected_c_ids:
                return Result(code=1, msg="未选择可分配教室")

            invalid_c_ids = [item for item in selected_c_ids if item not in course_c_ids]
            if invalid_c_ids:
                return Result(code=1, msg=f"存在不属于课程的教室: {','.join(str(x) for x in invalid_c_ids)}")

            existing_classrooms = (
                session.session.query(ojClass.c_id)
                .filter(ojClass.c_id.in_(selected_c_ids))
                .all()
            )
            existing_classroom_ids = {c_id for c_id, in existing_classrooms}
            missing_classrooms = [c_id for c_id in selected_c_ids if c_id not in existing_classroom_ids]
            if missing_classrooms:
                return Result(code=1, msg=f"教室不存在: {','.join(str(x) for x in missing_classrooms)}")

            members_data = await getGroupMember(course.group_id)
            members = members_data.get("members", []) if isinstance(members_data, dict) else []
            group_usernames = {
                str(item.get("username")).strip()
                for item in members
                if isinstance(item, dict) and str(item.get("username") or "").strip()
            }

            if not group_usernames:
                return Result(code=1, msg="课程绑定组中没有学生")

            if usernames is not None:
                target_usernames = {
                    str(item).strip() for item in usernames if isinstance(item, str) and str(item).strip()
                }
                if not target_usernames:
                    return Result(code=1, msg="指定学生列表为空")
                not_in_group = sorted(target_usernames - group_usernames)
                if not_in_group:
                    return Result(
                        code=1,
                        msg=f"以下学生不在课程组内: {','.join(not_in_group)}"
                    )
            else:
                target_usernames = set(group_usernames)

            if not target_usernames:
                return Result(code=1, msg="没有可分配的学生")

            existing_rows = session.session.query(ojClassUser).filter(
                ojClassUser.course_id == course_id
            ).all()
            existing_map = {row.username: row for row in existing_rows}

            unassigned_usernames = [
                username for username in sorted(target_usernames)
                if username not in existing_map
            ]
            assigned_already = sorted(target_usernames & set(existing_map.keys()))
            if not unassigned_usernames:
                return Result(
                    code=0,
                    msg="选中学生均已分配座位",
                    data={"assigned": [], "already_assigned": assigned_already}
                )

            classroom_slots = SeatBindingModel._build_course_classroom_slots(session, course_id, selected_c_ids)

            # 按剩余空位从高到低排序，优先填充空位最多的教室。
            ranked_classrooms = sorted(
                selected_c_ids,
                key=lambda cid: len(classroom_slots.get(cid, [])),
                reverse=True,
            )

            total_available = sum(len(classroom_slots.get(cid, [])) for cid in ranked_classrooms)
            if total_available < len(unassigned_usernames):
                return Result(
                    code=1,
                    msg=f"座位不足: 需要{len(unassigned_usernames)}个座位，可用{total_available}个",
                )

            assignments = []
            user_index = 0
            for c_id in ranked_classrooms:
                seats = classroom_slots.get(c_id, [])
                for seat_number in seats:
                    if user_index >= len(unassigned_usernames):
                        break
                    username = unassigned_usernames[user_index]
                    assignments.append({"username": username, "c_id": c_id, "seat_number": seat_number})
                    user_index += 1
                if user_index >= len(unassigned_usernames):
                    break

            if len(assignments) != len(unassigned_usernames):
                return Result(code=1, msg="自动分配失败：未能完成全部学生分配")

            for item in assignments:
                session.session.add(
                    ojClassUser(
                        course_id=course_id,
                        username=item["username"],
                        c_id=item["c_id"],
                        seat_number=item["seat_number"],
                    )
                )

            session.session.commit()
            return Result(
                code=0,
                msg=f"自动分配成功，共分配{len(assignments)}个座位",
                data={"assigned": assignments, "already_assigned": assigned_already},
            )

        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"自动分配座位失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def get_seat_map(course_id: int) -> Result:
        session = dbSession()
        try:
            course = session.session.query(ojCourse).filter(
                ojCourse.course_id == course_id
            ).first()

            if not course:
                return Result(code=1, msg="课程不存在")

            if not course.c_ids:
                return Result(code=1, msg="课程未分配教室")

            c_ids = json.loads(course.c_ids)

            seat_bindings = session.session.query(ojClassUser).filter(
                ojClassUser.course_id == course_id
            ).all()

            seat_map = {}
            for binding in seat_bindings:
                seat_map[binding.seat_number] = {
                    "username": binding.username,
                    "c_id": binding.c_id
                }

            classrooms = session.session.query(ojClass).filter(
                ojClass.c_id.in_(c_ids)
            ).all()

            classroom_data = []
            for classroom in classrooms:
                ext_config = json.loads(classroom.ext_config) if classroom.ext_config else {}
                disabled_seats = sorted(SeatBindingModel._extract_disabled_seats(ext_config))

                classroom_data.append(
                    {
                        "c_id": classroom.c_id,
                        "c_name": classroom.c_name,
                        "c_seat_num": classroom.c_seat_num,
                        "address": classroom.address,
                        "disabled_seats": disabled_seats,
                    }
                )

            return Result(
                code=0,
                msg="success",
                data={
                    "course_id": course_id,
                    "seat_bindings": seat_map,
                    "classrooms": classroom_data,
                },
            )

        except Exception as e:
            return Result(code=1, msg=f"查询座位分布失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def get_user_seat(course_id: int, username: str) -> Result:
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
                    "username": username,
                    "seat_number": seat_binding.seat_number,
                    "c_id": seat_binding.c_id,
                },
            )

        except Exception as e:
            return Result(code=1, msg=f"查询座位失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def remove_seat(course_id: int, username: str) -> Result:
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
