import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import redis
from fastapi import HTTPException
from redis.exceptions import RedisError
from sqlalchemy import asc, desc

from auto_task.constants import TASK_QUEUE_NAME
from const import Redis_addr, Redis_pass
from db import AutoTaskRun, AutoTaskRunLog, dbSession
from ser.base_type import page

LOCAL_TZ = timezone(timedelta(hours=8))
def _now_local():
    return datetime.now(LOCAL_TZ).replace(tzinfo=None)


class autoTaskModel(dbSession):
    _redis_client: Optional[redis.Redis] = None

    def __init__(self):
        super().__init__()
        if autoTaskModel._redis_client is None:
            autoTaskModel._redis_client = redis.Redis.from_url(
                f"redis://{Redis_addr}/0",
                password=Redis_pass,
                decode_responses=True,
            )
        self.redis = autoTaskModel._redis_client

    # ------------------------ task creation ------------------------
    def add_task(self, task_type: str, psid: Optional[int], payload: Any, groupId: Optional[int] = None, contestId: Optional[int] = None, problemId: Optional[int] = None) -> str:
        ids = self.add_tasks(task_type, psid, [payload], groupId=groupId, contestId=contestId, problemId=problemId)
        return ids[0] if ids else ""

    def add_tasks(self, task_type: str, psid: Optional[int], payload_list: List[Any], groupId: Optional[int] = None, contestId: Optional[int] = None, problemId: Optional[int] = None) -> List[str]:
        if payload_list is None or len(payload_list) == 0:
            return []

        queue_payloads: List[Dict[str, Any]] = []
        task_ids: List[str] = []

        for payload in payload_list:
            task_id = uuid.uuid4().hex
            record = AutoTaskRun(
                id=task_id,
                task_type=task_type,
                psid=self._resolve_psid(psid, payload),
                groupId=self._resolve_groupId(groupId, payload),
                contestId=self._resolve_contestId(contestId, payload),
                problemId=self._resolve_problemId(problemId, payload),
                username=self._extract_username(payload),
                status="pending",
            )
            self.session.add(record)
            self._append_log(task_id, "payload", payload)
            queue_payloads.append({
                "task_id": task_id,
                "type": task_type,
                "payload": payload,
            })
            task_ids.append(task_id)

        self.session.commit()
        self._push_tasks(queue_payloads)
        return task_ids

    # ------------------------ task updates ------------------------
    def update_status(self, task_id: str, status: str) -> None:
        record = self.session.query(AutoTaskRun).filter(
            AutoTaskRun.id == task_id
        ).first()
        if record is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        record.status = status
        self.session.commit()

    def add_log(self, task_id: str, tag: str, content: Any) -> None:
        allowed_tags = {"result", "error", "warning", "log"}
        if tag not in allowed_tags:
            raise HTTPException(status_code=400, detail="不支持的日志类型")
        self._append_log(task_id, tag, content)
        self.session.commit()

    # ------------------------ worker helpers ------------------------
    def prepare_task_run(
            self, task_id: Optional[str], task_type: str,
            payload: Any) -> str:
        now = _now_local()
        record = None
        if task_id is not None:
            record = self.session.query(AutoTaskRun).filter(
                AutoTaskRun.id == task_id
            ).first()
        if record is None:
            task_id = task_id or uuid.uuid4().hex
            record = AutoTaskRun(
                id=task_id,
                task_type=task_type,
                psid=self._resolve_psid(None, payload),
                groupId=self._resolve_groupId(None, payload),
                contestId=self._resolve_contestId(None, payload),
                problemId=self._resolve_problemId(None, payload),
                username=self._extract_username(payload),
                status="running",
                start_time=now,
            )
            self.session.add(record)
            if payload is not None:
                self._append_log(task_id, "payload", payload)
        else:
            record.task_type = task_type
            if record.psid is None:
                record.psid = self._resolve_psid(None, payload)
            if record.groupId is None:
                record.groupId = self._resolve_groupId(None, payload)
            if record.contestId is None:
                record.contestId = self._resolve_contestId(None, payload)
            if record.problemId is None:
                record.problemId = self._resolve_problemId(None, payload)
            if record.username is None:
                record.username = self._extract_username(payload)
            record.status = "running"
            if record.start_time is None:
                record.start_time = now
        self.session.commit()
        return record.id

    def finish_task_success(self, task_id: str, result: Any = None) -> None:
        record = self._get_record(task_id)
        record.status = "success"
        record.end_time = _now_local()
        if result is not None:
            self._append_log(task_id, "result", result)
        self.session.commit()

    def finish_task_failure(self, task_id: str, error: Any) -> None:
        record = self._get_record(task_id)
        record.status = "failed"
        record.end_time = _now_local()
        self._append_log(task_id, "error", error)
        self.session.commit()

    def record_invalid_task(self, raw_task: str, error: str) -> None:
        now = _now_local()
        task_id = uuid.uuid4().hex
        record = AutoTaskRun(
            id=task_id,
            task_type="invalid",
            status="failed",
            start_time=now,
            end_time=now,
        )
        self.session.add(record)
        self._append_log(task_id, "payload", raw_task)
        self._append_log(task_id, "error", error)
        self.session.commit()

    # ------------------------ task queries ------------------------
    def get_task_detail(self, task_id: str) -> Dict[str, Any]:
        record = self.session.query(AutoTaskRun).filter(
            AutoTaskRun.id == task_id
        ).first()
        if record is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        data = self.dealData(
            record,
            ["start_time", "end_time", "create_time", "update_time"],
        )
        logs = self.session.query(AutoTaskRunLog).filter(
            AutoTaskRunLog.task_id == task_id
        ).order_by(asc(AutoTaskRunLog.create_time)).all()
        data["logs"] = self.dealDataList(
            logs,
            ["create_time"],
        )
        return data

    def _build_task_query(
            self,
            psid: Optional[int] = None,
            groupId: Optional[int] = None,
            contestId: Optional[int] = None,
            problemId: Optional[int] = None,
            task_type: Optional[str] = None,
            status: Optional[str] = None,
            username: Optional[str] = None,
    ):
        query = self.session.query(AutoTaskRun)
        if psid is not None:
            query = query.filter(AutoTaskRun.psid == psid)
        if groupId is not None:
            query = query.filter(AutoTaskRun.groupId == groupId)
        if contestId is not None:
            query = query.filter(AutoTaskRun.contestId == contestId)
        if problemId is not None:
            query = query.filter(AutoTaskRun.problemId == problemId)
        if task_type is not None:
            query = query.filter(AutoTaskRun.task_type == task_type)
        if status is not None:
            query = query.filter(AutoTaskRun.status == status)
        if username:
            query = query.filter(AutoTaskRun.username == username)
        return query.order_by(desc(AutoTaskRun.create_time))

    def _serialize_task_rows(self, rows: List[AutoTaskRun]) -> List[Dict[str, Any]]:
        self._mark_timeout_rows(rows)
        data = self.dealDataList(
            rows,
            ["start_time", "end_time", "create_time", "update_time"],
        )
        self._attach_result_scores(data)
        return data

    def list_tasks_all_by_params(
            self,
            psid: Optional[int] = None,
            groupId: Optional[int] = None,
            contestId: Optional[int] = None,
            problemId: Optional[int] = None,
            task_type: Optional[str] = None,
            status: Optional[str] = None,
            username: Optional[str] = None,
            score_le: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        query = self._build_task_query(
            psid=psid,
            groupId=groupId,
            contestId=contestId,
            problemId=problemId,
            task_type=task_type,
            status=status,
            username=username,
        )
        data = self._serialize_task_rows(query.all())
        if score_le is None:
            return data
        return [
            row for row in data
            if row.get("autoScore") is not None and float(row["autoScore"]) <= float(score_le)
        ]

    def list_tasks_by_params(
            self,
            pg: page,
            psid: Optional[int] = None,
            groupId: Optional[int] = None,
            contestId: Optional[int] = None,
            problemId: Optional[int] = None,
            task_type: Optional[str] = None,
            status: Optional[str] = None,
            username: Optional[str] = None,
            score_le: Optional[float] = None
    ) -> Tuple[int, List[Dict[str, Any]]]:
        query = self._build_task_query(
            psid=psid,
            groupId=groupId,
            contestId=contestId,
            problemId=problemId,
            task_type=task_type,
            status=status,
            username=username,
        )

        if score_le is None:
            total = query.count()
            data = self._serialize_task_rows(
                query.offset(pg.offset()).limit(pg.limit()).all()
            )
            return total, data

        filtered = self.list_tasks_all_by_params(
            psid=psid,
            groupId=groupId,
            contestId=contestId,
            problemId=problemId,
            task_type=task_type,
            status=status,
            username=username,
            score_le=score_le,
        )
        total = len(filtered)
        offset = pg.offset()
        limit = pg.limit()
        return total, filtered[offset:offset + limit]

    def rerun_task(self, task_id: str) -> str:
        record = self.session.query(AutoTaskRun).filter(
            AutoTaskRun.id == task_id
        ).first()
        if record is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        payload_data = self._get_payload_from_log(task_id)
        if payload_data is None:
            raise HTTPException(status_code=400, detail="任务缺少 payload 日志，无法重试")
        (self.session.query(AutoTaskRunLog)
         .filter(AutoTaskRunLog.task_id == task_id, AutoTaskRunLog.tag != "payload")
         .delete())
        record.status = "pending"
        record.start_time = None
        record.end_time = None
        if record.psid is None:
            record.psid = self._resolve_psid(record.psid, payload_data)
        if record.groupId is None:
            record.groupId = self._resolve_groupId(record.groupId, payload_data)
        if record.contestId is None:
            record.contestId = self._resolve_contestId(record.contestId, payload_data)
        if record.problemId is None:
            record.problemId = self._resolve_problemId(record.problemId, payload_data)
        if record.username is None:
            record.username = self._extract_username(payload_data)
        self.session.commit()
        self._push_tasks([
            {
                "task_id": task_id,
                "type": record.task_type,
                "payload": payload_data,
            }
        ])
        return task_id

    def delete_task(self, task_id: str) -> None:
        self.session.query(AutoTaskRunLog).filter(
            AutoTaskRunLog.task_id == task_id
        ).delete()
        deleted = self.session.query(AutoTaskRun).filter(
            AutoTaskRun.id == task_id
        ).delete()
        if deleted == 0:
            self.session.rollback()
            raise HTTPException(status_code=404, detail="任务不存在")
        self.session.commit()

    def task_exists(self, task_id: str) -> bool:
        return self.session.query(AutoTaskRun.id).filter(
            AutoTaskRun.id == task_id
        ).first() is not None

    # ------------------------ internal helpers ------------------------
    def _append_log(self, task_id: str, tag: str, content: Any) -> None:
        if content is None:
            content_str = ""
        else:
            content_str = self._stringify(content)
        log = AutoTaskRunLog(
            task_id=task_id,
            tag=tag,
            content=content_str,
        )
        self.session.add(log)

    def _get_record(self, task_id: str) -> AutoTaskRun:
        record = self.session.query(AutoTaskRun).filter(
            AutoTaskRun.id == task_id
        ).first()
        if record is None:
            record = AutoTaskRun(
                id=task_id,
                task_type="unknown",
                status="pending",
            )
            self.session.add(record)
        return record

    def _push_tasks(self, tasks: Iterable[Dict[str, Any]]) -> None:
        serialized = []
        for payload in tasks:
            try:
                serialized.append(json.dumps(payload, ensure_ascii=False))
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="任务参数无法序列化") from exc
        if not serialized:
            return
        try:
            self.redis.rpush(TASK_QUEUE_NAME, *serialized)
        except RedisError as exc:
            raise HTTPException(status_code=500, detail="推送任务到队列失败") from exc

    @staticmethod
    def _stringify(data: Any) -> str:
        if isinstance(data, str):
            return data
        try:
            return json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(data)

    @staticmethod
    def _resolve_psid(psid: Optional[int], payload: Any = None) -> Optional[int]:
        if psid is not None:
            try:
                return int(psid)
            except (TypeError, ValueError):
                return None
        return autoTaskModel._extract_psid(payload)

    @staticmethod
    def _resolve_groupId(groupId: Optional[int], payload: Any = None) -> Optional[int]:
        if groupId is not None:
            try:
                return int(groupId)
            except (TypeError, ValueError):
                return None
        return autoTaskModel._extract_groupId(payload)

    @staticmethod
    def _resolve_contestId(contestId: Optional[int], payload: Any = None) -> Optional[int]:
        if contestId is not None:
            try:
                return int(contestId)
            except (TypeError, ValueError):
                return None
        return autoTaskModel._extract_contestId(payload)

    @staticmethod
    def _resolve_problemId(problemId: Optional[int], payload: Any = None) -> Optional[int]:
        if problemId is not None:
            try:
                return int(problemId)
            except (TypeError, ValueError):
                return None
        return autoTaskModel._extract_problemId(payload)

    @staticmethod
    def _extract_psid(payload: Any) -> Optional[int]:
        if not isinstance(payload, dict):
            return None
        value = payload.get("psid")
        if value is None:
            return None
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_groupId(payload: Any) -> Optional[int]:
        if not isinstance(payload, dict):
            return None
        # payload might use 'groupId' or 'gid'
        value = payload.get("groupId") or payload.get("gid")
        if value is None:
            return None
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_contestId(payload: Any) -> Optional[int]:
        if not isinstance(payload, dict):
            return None
        value = payload.get("contestId")
        if value is None:
            return None
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_problemId(payload: Any) -> Optional[int]:
        if not isinstance(payload, dict):
            return None
        value = payload.get("problemId") or payload.get("pid")
        if value is None:
            return None
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_username(payload: Any) -> Optional[str]:
        if isinstance(payload, dict):
            username = payload.get("username")
            if username is not None:
                return str(username)
        return None

    def _get_payload_from_log(self, task_id: str) -> Optional[Dict[str, Any]]:
        payload_log = self.session.query(AutoTaskRunLog).filter(
            AutoTaskRunLog.task_id == task_id,
            AutoTaskRunLog.tag == "payload"
        ).order_by(asc(AutoTaskRunLog.log_id)).first()
        if payload_log is None or payload_log.content is None:
            return None
        try:
            return json.loads(payload_log.content)
        except (TypeError, ValueError):
            return None

    def _mark_timeout_rows(self, rows: List[AutoTaskRun]) -> None:
        if not rows:
            return
        now = _now_local()
        timeout_delta = timedelta(minutes=30)
        updated = False
        for row in rows:
            if row.status == "running":
                base_time = row.start_time
                if base_time and now - base_time > timeout_delta:
                    row.status = "failed"
                    row.end_time = now
                    self._append_log(
                        row.id, "warning", "任务超过30分钟未完成，已自动失败"
                    )
                    updated = True
        if updated:
            self.session.commit()

    def _attach_result_scores(self, rows: List[Dict[str, Any]]) -> None:
        if not rows:
            return
        task_ids = [str(item.get("id")) for item in rows if item.get("id")]
        if not task_ids:
            return
        score_map = self._build_result_score_map(task_ids)
        for row in rows:
            score_info = score_map.get(str(row.get("id")))
            if score_info is None:
                row["autoScore"] = None
                row["autoFullScore"] = None
                continue
            row["autoScore"] = score_info[0]
            row["autoFullScore"] = score_info[1]

    def _build_result_score_map(self, task_ids: List[str]) -> Dict[str, Tuple[Optional[float], Optional[float]]]:
        if not task_ids:
            return {}
        logs = (self.session.query(AutoTaskRunLog)
                .filter(
                    AutoTaskRunLog.task_id.in_(task_ids),
                    AutoTaskRunLog.tag == "result"
                )
                .order_by(desc(AutoTaskRunLog.log_id))
                .all())
        result: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
        for log in logs:
            task_id = str(log.task_id)
            if task_id in result:
                continue
            result[task_id] = self._parse_score_from_result(log.content)
        return result

    @staticmethod
    def _parse_score_from_result(content: str) -> Tuple[Optional[float], Optional[float]]:
        if not content:
            return None, None
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            return None, None
        if not isinstance(payload, dict):
            return None, None
        judge_log = payload.get("judgeLog")
        if not isinstance(judge_log, list):
            return None, None
        total_score = 0.0
        full_score = 0.0
        has_value = False
        for item in judge_log:
            if not isinstance(item, dict):
                continue
            jscore = item.get("jScore")
            score = item.get("score")
            try:
                if jscore is not None:
                    total_score += float(jscore)
                    has_value = True
            except (TypeError, ValueError):
                pass
            try:
                if score is not None:
                    full_score += float(score)
            except (TypeError, ValueError):
                pass
        if not has_value:
            return None, None
        return round(total_score, 2), round(full_score, 2)
