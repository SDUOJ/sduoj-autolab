from fastapi import APIRouter, Depends

from model.answer_sheet import answerSheetModel
from ser.answer_sheet import ser_judge_list, \
    ser_judge_info, ser_judge_add, routerTypeBaseWithJudger
from ser.base import makePageResult
from utils import makeResponse

router = APIRouter(
    prefix="/judge"
)


# ############### 主观题批阅 ###################################

# 更新评测
@router.post("/add")
async def judge(data: dict = Depends(ser_judge_add)):
    db = answerSheetModel()
    await db.update_judgeLog_by_psid_gi_pi_username(
        data["psid"], data["gid"], data["pid"], data["username"],
        data["judgeLog"], data["cancel"], data["judgeComment"]
    )
    return makeResponse(None)


# 批阅列表
@router.post("/list")
async def J_list(data: dict = Depends(ser_judge_list)):
    db = answerSheetModel()
    tn, res = await db.get_judge_list_by_psid_page(**data)
    return makeResponse(makePageResult(data["pg"], tn, res))


# 返回一个批阅页面的具体信息，如果已批阅，返回批阅详情
@router.post("/info")
async def info(data: routerTypeBaseWithJudger = Depends(ser_judge_info)):
    db = answerSheetModel()
    data = await db.get_judge_info_by_psid_gi_pi_username(
        data.psid, data.gid, data.pid, data.username, data.judger
    )
    return makeResponse(data)
