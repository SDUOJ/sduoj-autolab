from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import cover_header, problem_set_manager, group_manager
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
            ps_group_id = model.session.query(ProblemSet.groupId).filter(ProblemSet.psid == psid).scalar()
            payloads = [
                {
                    "psid": item.psid,
                    "gid": item.gid,
                    "pid": item.pid,
                    "username": item.username,
                    "programmingProblems": [pp.dict() for pp in item.programmingProblems],
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


@router.post("/list")
async def list_auto_tasks(
        data: AutoTaskListRequest,
        user=Depends(cover_header)):
    if data.psid is not None:
        problem_set_manager(data.psid, user)
    elif data.groupId is not None:
        group_manager(data.groupId, user)
    # 可以在此添加 contestId 或 其他维度的权限校验 logic

    from model.auto_task import autoTaskModel
    model = autoTaskModel()
    try:
        pg = page(pageNow=data.pageNow, pageSize=data.pageSize)
        total, rows = model.list_tasks_by_params(
            pg=pg,
            psid=data.psid,
            groupId=data.groupId,
            contestId=data.contestId,
            problemId=data.problemId,
            task_type=data.taskType,
            status=data.status,
            username=data.username
        )
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
    group_manager(data.groupId, userinfo)
    
    from model.auto_task import autoTaskModel
    model = autoTaskModel()
    
    payload = data.dict()
    payload["userId"] = userinfo["userId"]
    
    task_id = model.add_task(
        task_type="summary_report",
        psid=None,
        groupId=data.groupId,
        payload=payload
    )
    
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
    psid = detail.get("psid")
    groupId = detail.get("groupId")
    if psid is not None:
        problem_set_manager(psid, user)
    elif groupId is not None:
        group_manager(groupId, user)
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
        psid = detail.get("psid")
        groupId = detail.get("groupId")
        if psid is not None:
            problem_set_manager(psid, user)
        elif groupId is not None:
            group_manager(groupId, user)
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
        psid = detail.get("psid")
        groupId = detail.get("groupId")
        if psid is not None:
            problem_set_manager(psid, user)
        elif groupId is not None:
            group_manager(groupId, user)
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
