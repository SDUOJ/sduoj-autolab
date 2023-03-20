from fastapi import APIRouter, Depends

from model.problem_group import groupModel
from ser.base import makePageResult
from ser.problem_group import ser_group_add, ser_group_edit, ser_group_list, \
    ser_group_info, ser_group_search
from utils import makeResponse

router = APIRouter(
    prefix="/group"
)


@router.post("/add")
async def add(data: dict = Depends(ser_group_add)):
    db = groupModel()
    db.group_create(data)
    return makeResponse(None)


@router.post("/edit")
async def edit(data: dict = Depends(ser_group_edit)):
    db = groupModel()
    gid = data["gid"]
    data.pop("gid")
    db.group_update_by_id(gid, data)
    return makeResponse(None)


@router.post("/list")
async def list(data: dict = Depends(ser_group_list)):
    db = groupModel()
    tn, res = db.group_get_list_info_by_page(
        data["page"], data["username"], data["groups"]
    )
    return makeResponse(makePageResult(data["page"], tn, res))


@router.post("/info")
async def info(data: dict = Depends(ser_group_info)):
    db = groupModel()
    return makeResponse(await db.group_get_info_by_id(data["gid"]))


@router.post("/search")
async def search(data: dict = Depends(ser_group_search)):
    db = groupModel()
    return makeResponse(db.group_get_idName_list_by_key(**data))
