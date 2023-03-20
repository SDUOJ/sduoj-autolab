from fastapi import APIRouter, Depends

from model.answer_sheet import answerSheetModel
from model.problem_set import problemSetModel
from ser.answer_sheet import ser_problem_set_routerType, \
    routerTypeWithUsername
from ser.base import makePageResult
from ser.problem_set import ser_problem_set_add, ser_problem_set_edit, \
    ser_problem_set_info, ser_problem_set_list, ser_problem_set_search_list, \
    ser_problem_set_key_list, ser_problem_set_info_c, \
    ser_problem_set_info_public
from utils import makeResponse, deal_order_change

router = APIRouter(
    prefix="/problem_set"
)


@router.post("/add")
def add(data: dict = Depends(ser_problem_set_add)):
    db = problemSetModel()
    db.ps_create(data)
    return makeResponse(None)


@router.post("/edit")
def edit(data: dict = Depends(ser_problem_set_edit)):
    db = problemSetModel()
    gid = data["psid"]
    data.pop("psid")
    db.ps_update_by_id(gid, data)
    return makeResponse(None)


@router.post("/info")
async def info(data: dict = Depends(ser_problem_set_info)):
    db = problemSetModel()
    return makeResponse(await db.ps_get_info_by_id(data["psid"], True))


@router.post("/info_c")
async def info_c(data: dict = Depends(ser_problem_set_info_c)):
    db = answerSheetModel()
    res = await db.ps_get_info_c_by_id(data["psid"], data["username"])
    res.update({"isAdmin": data["isAdmin"]})
    return makeResponse(res)


@router.post("/list")
def list(data: dict = Depends(ser_problem_set_list)):
    db = problemSetModel()
    tn, res = db.ps_get_list_info_by_page(
        data["page"], data["username"], data["groups"]
    )
    return makeResponse(makePageResult(data["page"], tn, res))


@router.post("/search")
async def search_list(data: dict = Depends(ser_problem_set_search_list)):
    db = problemSetModel()
    tn, res = await db.ps_get_list_search_info_by_page_cache(
        data["page"], data["groupId"], data["tag"]
    )
    return makeResponse(makePageResult(data["page"], tn, res))


# 获取 group 中显示的标签
@router.post("/key")
async def search_list(data: int = Depends(ser_problem_set_key_list)):
    db = problemSetModel()
    res = await db.ps_get_key_list_cache(data)
    return makeResponse(res)


# 获取题单中的题目详情
@router.post("/pro_info")
async def pro_info(
        data: routerTypeWithUsername = Depends(ser_problem_set_routerType)):
    db = answerSheetModel()
    await deal_order_change(db, data)
    return makeResponse(await db.ps_get_proInfo(data))


# 获取题单中的题目详情
@router.post("/public")
async def public_info(
        data: dict = Depends(ser_problem_set_info_public)):
    db = answerSheetModel()
    res = await db.ps_get_public_info_by_psid(data["psid"])
    res["finish"] = db.get_user_finish(data["psid"], data["username"])
    res["isAdmin"] = data["isAdmin"]
    return makeResponse(res)
