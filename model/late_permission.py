from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import and_

from db import dbSession, ProblemSetLatePermission
from utilsTime import getNowTime, getMsTime


class latePermissionModel(dbSession):
    def create(self, data: dict):
        self.session.add(ProblemSetLatePermission(**data))
        try:
            self.session.commit()
        except Exception as exc:
            self.session.rollback()
            raise exc

    def update_by_id(self, pid: int, data: dict):
        if "start_time" in data:
            # start_time 在此接口不允许直接修改
            data.pop("start_time")
        self.session.query(ProblemSetLatePermission).filter(
            ProblemSetLatePermission.id == pid
        ).update(data)
        self.session.commit()

    def get_obj_by_psid_username(self, psid: int, username: str):
        return self.session.query(ProblemSetLatePermission).filter(
            and_(
                ProblemSetLatePermission.psid == psid,
                ProblemSetLatePermission.username == username,
                ProblemSetLatePermission.is_active == 1
            )
        ).first()

    def list_by_psid(self, psid: int, offset: int, limit: int, username: str = None):
        query = self.session.query(ProblemSetLatePermission).filter(
            ProblemSetLatePermission.psid == psid
        )
        if username:
            query = query.filter(ProblemSetLatePermission.username == username)

        total = query.count()
        data = query.order_by(ProblemSetLatePermission.create_time.desc()) \
            .offset(max(0, offset)).limit(max(1, limit)).all()
        return total, self._format_list(data)

    def get_obj_by_id(self, pid: int):
        obj = self.session.query(ProblemSetLatePermission).filter(
            ProblemSetLatePermission.id == pid
        ).first()
        if obj is None:
            raise HTTPException(status_code=404, detail="Late permission not found")
        return obj

    def _format_list(self, data):
        return [self._format_obj(x) for x in data]

    def _format_obj(self, obj: ProblemSetLatePermission):
        data = self.dealData(
            obj,
            ["create_time", "update_time", "start_time"],
            []
        )
        # 统一补充截止时间信息
        start_ms = data.get("start_time")
        if start_ms is not None:
            try:
                start_dt = datetime.fromtimestamp(int(start_ms) / 1000)
            except Exception:
                start_dt = None
            if start_dt is not None:
                expire_dt = start_dt + timedelta(minutes=obj.duration_minute)
                data["expire_time"] = getMsTime(expire_dt)
            else:
                data["expire_time"] = None
        else:
            data["expire_time"] = None
        data["duration_minute"] = obj.duration_minute
        data["discount"] = obj.discount
        return data

    def ensure_activation(self, obj: ProblemSetLatePermission, now_ms: int = None):
        if obj is None:
            return None
        if obj.is_active != 1:
            return None
        if now_ms is None:
            now_ms = getNowTime()
        start_dt = obj.start_time
        if start_dt is None:
            start_dt = datetime.fromtimestamp(now_ms / 1000)
            self.session.query(ProblemSetLatePermission).filter(
                ProblemSetLatePermission.id == obj.id
            ).update({"start_time": start_dt})
            self.session.commit()
        else:
            start_ms = int(getMsTime(start_dt))
            if start_ms > now_ms:
                # 数据异常时，重置为当前时间
                start_dt = datetime.fromtimestamp(now_ms / 1000)
                self.session.query(ProblemSetLatePermission).filter(
                    ProblemSetLatePermission.id == obj.id
                ).update({"start_time": start_dt})
                self.session.commit()
        return self.get_obj_by_id(obj.id)

    def get_active_permission(self, psid: int, username: str, now_ms: int = None):
        if now_ms is None:
            now_ms = getNowTime()
        obj = self.get_obj_by_psid_username(psid, username)
        if obj is None:
            return None
        # 如果尚未启用，则立即启用
        obj = self.ensure_activation(obj, now_ms)
        start_dt = obj.start_time
        if start_dt is None:
            return None
        expire_dt = start_dt + timedelta(minutes=obj.duration_minute)
        if expire_dt.timestamp() * 1000 < now_ms:
            return None
        return self._format_obj(obj)

    def deactivate_if_expired(self, obj: ProblemSetLatePermission, now_ms: int = None):
        if now_ms is None:
            now_ms = getNowTime()
        if obj.start_time is None:
            return False
        expire_dt = obj.start_time + timedelta(minutes=obj.duration_minute)
        if expire_dt.timestamp() * 1000 < now_ms and obj.is_active == 1:
            self.session.query(ProblemSetLatePermission).filter(
                ProblemSetLatePermission.id == obj.id
            ).update({"is_active": 0})
            self.session.commit()
            return True
        return False

    def get_active_context(self, psid: int, username: str, now_ms: int = None):
        obj = self.get_active_permission(psid, username, now_ms)
        if obj is None:
            return None
        ctx = {
            "discount": obj["discount"],
            "start_time": obj.get("start_time"),
            "expire_time": obj.get("expire_time"),
            "duration_minute": obj.get("duration_minute"),
            "permission_id": obj.get("id"),
            "source": "late_permission",
        }
        return ctx
