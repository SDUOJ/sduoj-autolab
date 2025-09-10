from fastapi import APIRouter, Depends, HTTPException

from model.answer_sheet import answerSheetModel
from sduojApi import programSubmit, getSubmissionList, getSubmissionInfo, \
    add_pbCheckPoint, get_pbCheckPoint, del_pbCheckPoint, upd_pbCheckPoint
from ser.answer_sheet import routerType, \
    ser_problem_set_routerType, routerTypeWithUsername, routerTypeWithData, \
    ser_problem_set_with_data, submissionListType, ser_submission_List, \
    ser_submission_info, submissionInfo, routerTypeBase, pbcp_deal, \
    ser_psid_only
from utils import makeResponse, get_random_list_by_str, get_group_hash_name, \
    deal_order_change

router = APIRouter(
    prefix="/answer_sheet"
)


# ############### 答题 ###################################

# 获取答题卡
@router.post("/info")
async def info(
        data: routerTypeWithUsername = Depends(ser_problem_set_routerType)
):
    db = answerSheetModel()
    await deal_order_change(db, data)
    return makeResponse(await db.get_info(data))


# 添加收藏
@router.post("/collect")
async def collect(
        data: routerTypeWithUsername = Depends(ser_problem_set_routerType)
):
    db = answerSheetModel()
    await deal_order_change(db, data)
    return makeResponse(await db.collect(data))


# 添加标记
@router.post("/mark")
async def mark(
        data: routerTypeWithData = Depends(ser_problem_set_with_data)):
    db = answerSheetModel()
    await deal_order_change(db, data, with_pro=True)
    return makeResponse(await db.mark(data))


# 添加答案（ 如果权重更高的分数段已有答案，
#          当前主观题与客观题的提交需要提醒，确认后才能修改）
@router.post("/answer")
async def answer(
        data: routerTypeWithData = Depends(ser_problem_set_with_data)):
    db = answerSheetModel()
    await deal_order_change(db, data, with_pro=True)
    return makeResponse(await db.update_answer(data))


# 交卷 （考试模式下，需要交卷才能离场）
@router.post("/finish")
async def finish(
        data: routerTypeWithUsername = Depends(ser_problem_set_routerType)):
    db = answerSheetModel()
    return makeResponse(
        db.finish_by_psid_username(
            data.router.psid, data.username
        )
    )


# 获取提交列表（分页数据）
@router.post("/submissionList")
async def submissionList(
        data: submissionListType = Depends(ser_submission_List)):
    db = answerSheetModel()
    psid = data.problemSetId
    router = data.router
    data = data.dict()
    data.pop("problemSetId")
    data.pop("router")
    if not (router.gid == -1 and router.pid == -1):
        gid, pid = await db.get_gid_pid_by_psid_gi_pi_cache(
            router.psid, router.gid, router.pid
        )
        data["problemId"] = pid
        res = await getSubmissionList(psid, ext=data)
        info = await db.ps_get_proInfo_cache(
            psid, router.gid, router.pid
        )
        config = await db.ps_get_config_by_psid_cache(psid)
        for x in res["rows"]:
            x["problemCode"] = info["problemCode"]
            x["problemTitle"] = info["problemTitle"]
            if config["showProgramScoreInRunning"] == 0:
                x["judgeScore"] = None
    else:
        res = await getSubmissionList(psid, ext=data, pop=False)
    return makeResponse(res)


# 查询
@router.post("/submissionInfo")
async def submissionInfo(data: submissionInfo = Depends(ser_submission_info)):
    db = answerSheetModel()
    res = await getSubmissionInfo(data.psid, data.submissionId)
    if not (data.pid == -1 and data.gid == -1):
        info = await db.ps_get_proInfo_cache(data.psid, data.gid, data.pid)
        config = await db.ps_get_config_by_psid_cache(data.psid)
        res["problemCode"] = info["problemCode"]
        res["problemTitle"] = info["problemTitle"]
        if config["showProgramScoreInRunning"] == 0:
            res["judgeScore"] = None
        # 此接口只能访问自己的代码
        if res["username"] != data.username:
            raise HTTPException(detail="Permission Denial", status_code=403)

    return makeResponse(res)


@router.post("/pbcp/add")
async def pbcp_add(data=Depends(ser_problem_set_with_data)):
    return makeResponse(await add_pbCheckPoint(await pbcp_deal(data)))


@router.post("/pbcp/get")
async def pbcp_get(data=Depends(ser_problem_set_routerType)):
    return makeResponse(await get_pbCheckPoint(await pbcp_deal(data)))


@router.post("/pbcp/del")
async def pbcp_del(data=Depends(ser_problem_set_with_data)):
    return makeResponse(await del_pbCheckPoint(await pbcp_deal(data)))


@router.post("/pbcp/upd")
async def pbcp_upd(data=Depends(ser_problem_set_with_data)):
    return makeResponse(await upd_pbCheckPoint(await pbcp_deal(data)))


@router.post("/acceptanceQueueList")
async def acceptance_queue_list(data=Depends(ser_psid_only)):
    db = answerSheetModel()
    res = await db.get_acceptance_queue_list(data["psid"])  # {'problems': [...], 'queueSet': [...]}
    return makeResponse(res.get("queueSet", []))
