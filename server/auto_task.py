import json
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import cover_header, problem_set_manager
from sduojApi import getGroupMember
from utils import makeResponse
from ser.base_type import page
from model.answer_sheet import answerSheetModel
from db import (
    ProblemSubjective,
    ProblemSetAnswerSheet,
    ProblemSetAnswerSheetDetail,
    ProblemSet,
)

router = APIRouter(prefix="/auto-task", tags=["auto-task"])


def _model_to_dict(model: BaseModel) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _to_optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_group_info(group_info: Any) -> Optional[dict]:
    if not isinstance(group_info, dict):
        return None
    data = group_info.get("data")
    if isinstance(data, dict):
        return data
    return group_info


def _extract_payload_psid_from_logs(logs: List[dict]) -> Optional[int]:
    for log in logs or []:
        if log.get("tag") != "payload":
            continue
        content = log.get("content")
        if not content:
            continue
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        psid = _to_optional_int(payload.get("psid"))
        if psid is not None:
            return psid
    return None


async def _assert_group_creator(group_id: int, user: dict):
    """允许 group 创建者、superadmin 访问。"""
    if "superadmin" in (user.get("roles") or []):
        return
    group_info = _normalize_group_info(await getGroupMember(group_id))
    if group_info is None:
        raise HTTPException(status_code=403, detail="Permission Denial")
    owner = group_info.get("username")
    if str(owner or "") == str(user.get("username") or ""):
        return
    raise HTTPException(status_code=403, detail="Permission Denial")


async def _assert_task_access(detail: dict, user: dict):
    psid = _to_optional_int(detail.get("psid"))
    if psid is not None:
        problem_set_manager(psid, user)
        return

    group_id = _to_optional_int(detail.get("groupId"))
    if group_id is not None:
        await _assert_group_creator(group_id, user)
        return

    payload_psid = _extract_payload_psid_from_logs(detail.get("logs", []))
    if payload_psid is not None:
        problem_set_manager(payload_psid, user)
        return

    raise HTTPException(status_code=403, detail="Permission Denial")


def _has_problem_set_access(psid: int, user: dict, cache: Dict[int, bool]) -> bool:
    if psid not in cache:
        try:
            problem_set_manager(psid, user)
            cache[psid] = True
        except HTTPException:
            cache[psid] = False
    return cache[psid]


async def _has_group_creator_access(group_id: int, user: dict, cache: Dict[int, bool]) -> bool:
    if group_id not in cache:
        try:
            await _assert_group_creator(group_id, user)
            cache[group_id] = True
        except HTTPException:
            cache[group_id] = False
    return cache[group_id]


async def _filter_authorized_task_rows(model, rows: List[dict], user: dict) -> List[dict]:
    psid_cache: Dict[int, bool] = {}
    group_cache: Dict[int, bool] = {}
    authorized_rows: List[dict] = []

    for row in rows:
        psid = _to_optional_int(row.get("psid"))
        if psid is not None:
            if _has_problem_set_access(psid, user, psid_cache):
                authorized_rows.append(row)
            continue

        group_id = _to_optional_int(row.get("groupId"))
        if group_id is not None:
            if await _has_group_creator_access(group_id, user, group_cache):
                authorized_rows.append(row)
            continue

        task_id = row.get("id")
        if not task_id:
            continue

        try:
            detail = model.get_task_detail(str(task_id))
        except HTTPException:
            continue
        payload_psid = _extract_payload_psid_from_logs(detail.get("logs", []))
        if payload_psid is not None and _has_problem_set_access(payload_psid, user, psid_cache):
            authorized_rows.append(row)

    return authorized_rows


class ProgrammingProblemRef(BaseModel):
    gid: int
    pid: int


class SubjectiveTaskItem(BaseModel):
    psid: int
    gid: int
    pid: int
    username: str
    programmingProblems: List[ProgrammingProblemRef] = []


class SubjectiveTaskRequest(BaseModel):
    tasks: List[SubjectiveTaskItem]


@router.post("/subjective/review")
async def create_subjective_review_tasks(
        data: SubjectiveTaskRequest,
        user=Depends(cover_header)):
    if not data.tasks:
        raise HTTPException(status_code=400, detail="tasks 不能为空")
    for task in data.tasks:
        problem_set_manager(task.psid, user)
    grouped = defaultdict(list)
    for task in data.tasks:
        grouped[task.psid].append(task)
    from model.auto_task import autoTaskModel
    model = autoTaskModel()
    task_ids: List[str] = []
    try:
        for psid, tasks in grouped.items():
            ps_obj = model.session.query(ProblemSet).filter(ProblemSet.psid == psid).first()
            ps_group_id = ps_obj.groupId if ps_obj else None
            ps_description = ps_obj.description if ps_obj else None
            payloads = [
                {
                    "psid": item.psid,
                    "gid": item.gid,
                    "pid": item.pid,
                    "username": item.username,
                    "ps_description": ps_description,
                    "programmingProblems": [_model_to_dict(pp) for pp in item.programmingProblems],
                }
                for item in tasks
            ]
            task_ids.extend(model.add_tasks("subjective_review", psid, payloads, groupId=ps_group_id))
    finally:
        model.session.close()
    return makeResponse({"taskIds": task_ids})


class AutoTaskListRequest(BaseModel):
    psid: Optional[int] = None
    groupId: Optional[int] = None
    contestId: Optional[int] = None
    problemId: Optional[int] = None
    pageNow: int = 1
    pageSize: int = 20
    status: Optional[str] = None
    taskType: Optional[str] = None
    username: Optional[str] = None
    scoreLe: Optional[float] = None


@router.post("/list")
async def list_auto_tasks(
        data: AutoTaskListRequest,
        user=Depends(cover_header)):
    if data.psid is not None:
        problem_set_manager(data.psid, user)

    from model.auto_task import autoTaskModel
    model = autoTaskModel()
    try:
        pg = page(pageNow=data.pageNow, pageSize=data.pageSize)
        query_kwargs = {
            "psid": data.psid,
            "groupId": data.groupId,
            "contestId": data.contestId,
            "problemId": data.problemId,
            "task_type": data.taskType,
            "status": data.status,
            "username": data.username,
            "score_le": data.scoreLe,
        }
        if data.psid is not None:
            total, rows = model.list_tasks_by_params(pg=pg, **query_kwargs)
        else:
            authorized_rows = await _filter_authorized_task_rows(
                model,
                model.list_tasks_all_by_params(**query_kwargs),
                user,
            )
            total = len(authorized_rows)
            rows = authorized_rows[pg.offset():pg.offset() + pg.limit()]
    finally:
        model.session.close()
    return makeResponse({
        "pageIndex": data.pageNow,
        "pageSize": data.pageSize,
        "total": total,
        "rows": rows,
    })


class SummaryReportRequest(BaseModel):
    """成绩报告生成请求参数"""
    groupId: int
    psids: Optional[List[int]] = Field(default=None, description="题单 ID 列表，留空则导出该小组所有题单")


@router.post("/summary/export")
async def create_summary_report_task(
    data: SummaryReportRequest,
    userinfo: dict = Depends(cover_header)
):
    """
    创建成绩报告导出任务 (小组维度)
    """
    await _assert_group_creator(data.groupId, userinfo)
    
    from model.auto_task import autoTaskModel
    model = autoTaskModel()
    try:
        payload = _model_to_dict(data)
        payload["userId"] = userinfo["userId"]
        task_id = model.add_task(
            task_type="summary_report",
            psid=None,
            groupId=data.groupId,
            payload=payload
        )
    finally:
        model.session.close()
    
    return makeResponse({
        "taskId": task_id,
        "message": "成绩报告生成任务已创建，请稍后查看任务状态"
    })


@router.get("/detail/{task_id}")
async def get_task_detail(task_id: str, user=Depends(cover_header)):
    from model.auto_task import autoTaskModel
    model = autoTaskModel()
    try:
        detail = model.get_task_detail(task_id)
    finally:
        model.session.close()
    await _assert_task_access(detail, user)
    logs = detail.pop("logs", [])
    detail["logs"] = logs
    return makeResponse(detail)


@router.get("/subjective/options/{psid}")
async def get_subjective_options(psid: int, user=Depends(cover_header)):
    problem_set_manager(psid, user)
    model = answerSheetModel()
    try:
        ps_info = await model.ps_get_info_by_id_cache(psid)
        subj_pairs: List[Tuple[int, int]] = []
        program_list: List[dict] = []
        for group in ps_info["groupInfo"]:
            gid = group["gid"]
            group_detail = await model.group_get_info_by_id_cache(gid)
            gtype = group_detail.get("type")
            for problem in group_detail.get("problemInfo", []):
                pid = problem.get("pid")
                if pid is None:
                    continue
                if gtype == 1:
                    subj_pairs.append((gid, pid))
                elif gtype == 2:
                    program_list.append({
                        "gid": gid,
                        "pid": pid,
                        "name": problem.get("name") or f"编程题 {pid}",
                    })

        subjectives = _build_subjective_summary(model, psid, subj_pairs)
        students = _get_ps_students(model, psid)
    finally:
        model.session.close()
    return makeResponse({
        "subjectiveProblems": subjectives,
        "programmingProblems": program_list,
        "students": students,
    })


@router.post("/rerun/{task_id}")
async def rerun_task(task_id: str, user=Depends(cover_header)):
    from model.auto_task import autoTaskModel
    model = autoTaskModel()
    try:
        detail = model.get_task_detail(task_id)
        await _assert_task_access(detail, user)
        model.rerun_task(task_id)
    finally:
        model.session.close()
    return makeResponse({"taskId": task_id, "status": "pending"})


@router.post("/task/{task_id}/delete")
async def delete_task(task_id: str, user=Depends(cover_header)):
    from model.auto_task import autoTaskModel
    model = autoTaskModel()
    try:
        detail = model.get_task_detail(task_id)
        await _assert_task_access(detail, user)
        model.delete_task(task_id)
    finally:
        model.session.close()
    return makeResponse({"taskId": task_id, "deleted": True})


@router.post("/delete/{task_id}")
async def delete_task_legacy(task_id: str, user=Depends(cover_header)):
    return await delete_task(task_id, user)


def _build_subjective_summary(
        model: answerSheetModel,
        psid: int,
        subj_pairs: List[Tuple[int, int]]) -> List[dict]:
    if not subj_pairs:
        return []
    pids = [pid for _, pid in subj_pairs]
    records = model.session.query(ProblemSubjective).filter(
        ProblemSubjective.pid.in_(pids)
    ).all()
    record_map = {rec.pid: rec for rec in records}

    def preview(text: Optional[str]) -> str:
        if not text:
            return ""
        text = text.replace("\r", " ").replace("\n", " ")
        return text[:16]

    allowed_pairs: List[Tuple[int, int]] = []
    for gid, pid in subj_pairs:
        rec = record_map.get(pid)
        if rec is None:
            continue
        if rec.type not in (0, 1):
            continue
        allowed_pairs.append((gid, pid))
    if not allowed_pairs:
        return []

    pending_students = _get_pending_review_students(model, psid, allowed_pairs)
    result = []
    for gid, pid in allowed_pairs:
        rec = record_map[pid]
        result.append({
            "gid": gid,
            "pid": pid,
            "answerType": rec.type,
            "preview": preview(rec.description),
            "pendingStudents": pending_students.get((gid, pid), [])
        })
    return result


def _get_pending_review_students(
        model: answerSheetModel,
        psid: int,
        subj_pairs: List[Tuple[int, int]]) -> Dict[Tuple[int, int], List[str]]:
    if not subj_pairs:
        return {}
    gid_set = sorted({gid for gid, _ in subj_pairs})
    pid_set = sorted({pid for _, pid in subj_pairs})
    pending = defaultdict(set)
    rows = model.session.query(
        ProblemSetAnswerSheetDetail.gid,
        ProblemSetAnswerSheetDetail.pid,
        ProblemSetAnswerSheet.username,
    ).join(
        ProblemSetAnswerSheet,
        ProblemSetAnswerSheetDetail.asid == ProblemSetAnswerSheet.asid
    ).filter(
        ProblemSetAnswerSheet.psid == psid,
        ProblemSetAnswerSheetDetail.gid.in_(gid_set),
        ProblemSetAnswerSheetDetail.pid.in_(pid_set),
        ProblemSetAnswerSheetDetail.tm_answer_submit != None,
        ProblemSetAnswerSheetDetail.judgeLog == None,
    ).all()
    for row in rows:
        key = (row.gid, row.pid)
        if row.username:
            pending[key].add(row.username)
    return {key: sorted(list(usernames)) for key, usernames in pending.items()}


def _get_ps_students(model: answerSheetModel, psid: int) -> List[str]:
    rows = model.session.query(ProblemSetAnswerSheet.username).filter(
        ProblemSetAnswerSheet.psid == psid
    ).all()
    usernames = sorted({row.username for row in rows if row.username})
    return usernames
