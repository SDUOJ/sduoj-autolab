"""Token-only internal policy RPC. Resolve user identity from the OJ user service."""

import secrets
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, conint
from auto_task.access import fetch_actor
from auto_task.run_context import check_native_context
from auto_task.store import transaction
from const import SDUOJ_TOKEN
from utilsTime import getNowTime

router = APIRouter()


class RunSubject(BaseModel):
    userId: conint(gt=0)
    problemId: conint(gt=0)

    class Config:
        extra = "forbid"


@router.post("/internal/run/context")
def run_context(body: RunSubject, request: Request):
    tokens = request.headers.getlist("sduoj-token")
    if (not SDUOJ_TOKEN or len(tokens) != 1
            or not secrets.compare_digest(tokens[0].encode(), SDUOJ_TOKEN.encode())):
        raise HTTPException(403, "Internal credentials required")
    try:
        actor = fetch_actor(str(body.userId), fresh=True)
        if actor["userId"] != body.userId:
            raise ValueError("Run subject mismatch")
        if not set(actor["roles"]).intersection({"user", "admin", "superadmin"}):
            return {"allowed": False, "reason": "RUN_PERMISSION_REVOKED"}
        with transaction() as session:
            return check_native_context(session, actor, body.problemId, getNowTime())
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(503, "Run subject or native context unavailable") from error


class IdeAddress(BaseModel):
    type: conint(ge=2, le=2)
    bizId: conint(gt=0)
    groupIndex: conint(ge=0)
    problemIndex: conint(ge=0)
    problemCode: str = None

    class Config:
        extra = "forbid"


class IdeSubject(BaseModel):
    userId: conint(gt=0)
    context: IdeAddress
    writable: bool = False

    class Config:
        extra = "forbid"


@router.post("/internal/ide/context")
async def ide_context(body: IdeSubject, request: Request):
    """Resolve the same native route and answer window used by formal program submissions."""
    from auth import problem_set_user
    from model.answer_sheet import answerSheetModel
    from ser.answer_sheet import routerTypeBase, routerTypeWithUsername
    from sduojApi import getProblemCode
    from utils import deal_order_change

    tokens = request.headers.getlist("sduoj-token")
    if (not SDUOJ_TOKEN or len(tokens) != 1
            or not secrets.compare_digest(tokens[0].encode(), SDUOJ_TOKEN.encode())):
        raise HTTPException(403, "Internal credentials required")
    actor = fetch_actor(str(body.userId), fresh=True)
    if int(actor["userId"]) != body.userId or not set(actor["roles"]).intersection({"user", "admin", "superadmin"}):
        raise HTTPException(403, "IDE permission denied")
    address = body.context
    await problem_set_user(address.bizId, actor, address.groupIndex,
                           submit=True if body.writable else None)
    db = answerSheetModel()
    try:
        route = routerTypeWithUsername(username=actor["username"], router=routerTypeBase(
            psid=address.bizId, gid=address.groupIndex, pid=address.problemIndex))
        await deal_order_change(db, route)
        gi, pi = route.router.gid, route.router.pid
        gid, pid = await db.get_gid_pid_by_psid_gi_pi_cache(address.bizId, gi, pi)
        group = await db.group_get_info_by_id_cache(gid)
        if group["type"] != 2:
            raise HTTPException(400, "IDE requires a programming problem")
        return {"key": f"problemset:{address.bizId}:{gid}:{pid}:{pi}",
                "bizType": 2, "bizId": address.bizId,
                "problemCode": await getProblemCode(pid),
                "descriptionId": group["problemInfo"][pi]["desId"]}
    finally:
        db.session.close()
