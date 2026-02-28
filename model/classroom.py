"""
教室管理模型
"""
import json
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from db import dbSession, ojClass, ojClassUser, ojCourse
from utils import Result


def _extract_banned_seats(ext_config: Optional[Dict[str, Any]]) -> Set[int]:
    """
    兼容 seat-ban 与 disabled_seats 两种格式，统一提取禁用座位号。
    """
    if not ext_config:
        return set()

    banned: Set[int] = set()

    seat_ban = ext_config.get("seat-ban")
    if isinstance(seat_ban, list):
        for item in seat_ban:
            if isinstance(item, dict):
                seat_number = item.get("seat_number")
                if isinstance(seat_number, int) and seat_number > 0:
                    banned.add(seat_number)

    disabled = ext_config.get("disabled_seats")
    if isinstance(disabled, list):
        for item in disabled:
            if isinstance(item, int) and item > 0:
                banned.add(item)

    return banned


class ClassroomModel:
    @staticmethod
    def create_classroom(
        c_name: str,
        address: str,
        c_seat_num: int,
        ext_config: Optional[Dict[str, Any]] = None
    ) -> Result:
        if not c_name or not c_name.strip():
            return Result(code=1, msg="教室名称不能为空")
        if not address or not address.strip():
            return Result(code=1, msg="教室地点不能为空")
        if c_seat_num <= 0:
            return Result(code=1, msg="座位数量必须为正整数")

        session = dbSession()
        try:
            ext_config_json = json.dumps(ext_config) if ext_config is not None else None
            obj = ojClass(
                c_name=c_name.strip(),
                address=address.strip(),
                c_seat_num=c_seat_num,
                ext_config=ext_config_json
            )
            session.session.add(obj)
            session.session.commit()
            session.session.refresh(obj)
            return Result(code=0, msg="教室创建成功", data={"c_id": obj.c_id})
        except IntegrityError:
            session.session.rollback()
            return Result(code=1, msg="教室名称已存在")
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"创建教室失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def get_classroom(c_id: int) -> Result:
        session = dbSession()
        try:
            obj = session.session.query(ojClass).filter(
                ojClass.c_id == c_id
            ).first()
            if not obj:
                return Result(code=1, msg="教室不存在")

            data = session.dealData(obj)
            ext_config = json.loads(data["ext_config"]) if data.get("ext_config") else {}
            banned = _extract_banned_seats(ext_config)
            data["ext_config"] = ext_config
            data["available_seats"] = max(0, int(data["c_seat_num"]) - len(banned))
            return Result(code=0, msg="success", data=data)
        except Exception as e:
            return Result(code=1, msg=f"查询教室失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def list_classrooms(
        page_now: int,
        page_size: int,
        keyword: Optional[str] = None
    ) -> Result:
        session = dbSession()
        try:
            query = session.session.query(ojClass)
            if keyword:
                kw = f"%{keyword.strip()}%"
                query = query.filter(
                    or_(ojClass.c_name.like(kw), ojClass.address.like(kw))
                )

            total = query.count()
            rows = query.order_by(ojClass.c_id.desc()).offset(
                (page_now - 1) * page_size
            ).limit(page_size).all()

            result_rows: List[Dict[str, Any]] = []
            for row in rows:
                item = session.dealData(row)
                ext_config = json.loads(item["ext_config"]) if item.get("ext_config") else {}
                banned = _extract_banned_seats(ext_config)
                item["ext_config"] = ext_config
                item["available_seats"] = max(0, int(item["c_seat_num"]) - len(banned))
                result_rows.append(item)

            return Result(code=0, msg="success", data={
                "pageIndex": page_now,
                "pageSize": page_size,
                "totalNum": total,
                "rows": result_rows
            })
        except Exception as e:
            return Result(code=1, msg=f"查询教室列表失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def update_classroom(
        c_id: int,
        c_name: Optional[str] = None,
        address: Optional[str] = None,
        c_seat_num: Optional[int] = None,
        ext_config: Optional[Dict[str, Any]] = None
    ) -> Result:
        session = dbSession()
        try:
            obj = session.session.query(ojClass).filter(
                ojClass.c_id == c_id
            ).first()
            if not obj:
                return Result(code=1, msg="教室不存在")

            if c_name is not None:
                if not c_name.strip():
                    return Result(code=1, msg="教室名称不能为空")
                obj.c_name = c_name.strip()
            if address is not None:
                if not address.strip():
                    return Result(code=1, msg="教室地点不能为空")
                obj.address = address.strip()
            if c_seat_num is not None:
                if c_seat_num <= 0:
                    return Result(code=1, msg="座位数量必须为正整数")
                obj.c_seat_num = c_seat_num
            if ext_config is not None:
                obj.ext_config = json.dumps(ext_config)

            session.session.commit()
            return Result(code=0, msg="教室更新成功")
        except IntegrityError:
            session.session.rollback()
            return Result(code=1, msg="教室名称已存在")
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"更新教室失败: {str(e)}")
        finally:
            del session

    @staticmethod
    def delete_classroom(c_id: int) -> Result:
        session = dbSession()
        try:
            obj = session.session.query(ojClass).filter(
                ojClass.c_id == c_id
            ).first()
            if not obj:
                return Result(code=1, msg="教室不存在")

            # 检查座位绑定引用
            bind_count = session.session.query(ojClassUser).filter(
                ojClassUser.c_id == c_id
            ).count()
            if bind_count > 0:
                return Result(code=1, msg=f"教室仍有{bind_count}条座位绑定，无法删除")

            # 检查课程引用（c_ids 为 JSON 数组，需在应用层判断）
            courses = session.session.query(ojCourse).all()
            used_by = 0
            for course in courses:
                if not course.c_ids:
                    continue
                try:
                    c_ids = json.loads(course.c_ids)
                except Exception:
                    c_ids = []
                if isinstance(c_ids, list) and c_id in c_ids:
                    used_by += 1
            if used_by > 0:
                return Result(code=1, msg=f"教室仍被{used_by}门课程引用，无法删除")

            session.session.delete(obj)
            session.session.commit()
            return Result(code=0, msg="教室删除成功")
        except Exception as e:
            session.session.rollback()
            return Result(code=1, msg=f"删除教室失败: {str(e)}")
        finally:
            del session

