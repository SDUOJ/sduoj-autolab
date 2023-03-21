from typing import Optional, Union, List

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from auth import cover_header, problem_set_user, problem_set_manager
from sduojApi import getUserId
from ser.base_type import page, userSessionType


class routerTypeBase(BaseModel):
    psid: int
    gid: int
    pid: int


class programSubmitType(BaseModel):
    judgeTemplateId: int
    code: Optional[str]
    zipFileId: Optional[int]
    problemSetId: int
    ipv4: Optional[str]


class checkpointListType(BaseModel):
    checkpointId: str
    status: Optional[int]
    note: Optional[str]


class publicCheckPointType(BaseModel):
    checkpoints: List[checkpointListType]
    type: int


class routerType(BaseModel):
    router: routerTypeBase
    data: Optional[
        Union[str, programSubmitType, List[str], publicCheckPointType]]


class routerTypeWithUsername(routerType):
    username: str
    userSessionData: Optional[userSessionType]


class routerTypeWithData(routerTypeWithUsername):
    data: Union[str, programSubmitType, List[str], publicCheckPointType]


class submissionListType(BaseModel):
    pageNow: int
    pageSize: int
    problemSetId: int
    sortBy: Optional[str]
    ascending: Optional[bool] = False
    username: Optional[str]
    userId: Optional[int]
    problemCode: Optional[str]
    problemId: Optional[int]
    judgeTemplateId: Optional[int]
    judgeResult: Optional[int]
    problemCodeList: Optional[List[str]]
    router: routerTypeBase
    us: Optional[str]


class submissionInfo(BaseModel):
    submissionId: str
    psid: int
    gid: int
    pid: int
    username: Optional[str]


class judgeListType(page):
    psid: int
    proStr: Optional[str]
    gid: Optional[int]
    pid: Optional[int]
    username: Optional[str]
    judgeLock: Optional[str]
    hasJudge: Optional[int]


class routerTypeBaseWithUsername(routerTypeBase):
    username: str


class routerTypeBaseWithJudger(routerTypeBaseWithUsername):
    judger: str


class judgeLogType(BaseModel):
    name: str
    score: int
    jScore: int


class routerTypeBaseWithJudgeLog(routerTypeBaseWithUsername):
    judgeLog: List[judgeLogType]
    judgeComment: Optional[str]
    cancel: Optional[int]


async def ser_preview(
        data: routerTypeBaseWithUsername,
        SDUOJUserInfo=Depends(cover_header)):
    problem_set_manager(data.psid, SDUOJUserInfo)
    return data


async def ser_judge_add(
        data: routerTypeBaseWithJudgeLog, SDUOJUserInfo=Depends(cover_header)
):
    problem_set_manager(data.psid, SDUOJUserInfo)
    return data.dict()


async def ser_judge_info(
        data: routerTypeBaseWithUsername,
        SDUOJUserInfo=Depends(cover_header)):
    problem_set_manager(data.psid, SDUOJUserInfo)
    return routerTypeBaseWithJudger(
        **data.dict(), judger=SDUOJUserInfo["username"]
    )


async def ser_judge_list(
        data: judgeListType, SDUOJUserInfo=Depends(cover_header)):
    problem_set_manager(data.psid, SDUOJUserInfo)
    if data.proStr is not None:
        gi, pi = tuple(map(int, data.proStr.split("-")))
    else:
        gi, pi = None, None
    return {
        "pg": page(pageNow=data.pageNow, pageSize=data.pageSize),
        "username": data.username,
        "gi": gi,
        "pi": pi,
        "psid": data.psid,
        "judgeLock": data.judgeLock,
        "hasJudge": data.hasJudge
    }


async def ser_problem_set_routerType(data: routerType,
                                     SDUOJUserInfo=Depends(cover_header)):
    await problem_set_user(data.router.psid, SDUOJUserInfo, data.router.gid)
    res = routerTypeWithUsername(
        router=data.router, username=SDUOJUserInfo["username"],
    )
    save_Ipv4_info(res, SDUOJUserInfo["ipv4"])
    return res


async def ser_problem_set_with_data(data: routerType,
                                    SDUOJUserInfo=Depends(cover_header)):
    await problem_set_user(
        data.router.psid, SDUOJUserInfo, data.router.gid, True
    )
    if type(data.data) == programSubmitType:
        data.data.ipv4 = SDUOJUserInfo["ipv4"]
    res = routerTypeWithData(
        router=data.router, username=SDUOJUserInfo["username"], data=data.data
    )
    save_Ipv4_info(res, SDUOJUserInfo["ipv4"])
    return res


async def pbcp_deal(data):
    from model.answer_sheet import answerSheetModel
    db = answerSheetModel()
    para = {}
    if data.data is not None:
        para = data.data.dict()
    _, para["problemId"] = await db.get_gid_pid_by_psid_gi_pi_cache(
        data.router.psid, data.router.gid, data.router.pid
    )
    para["userId"] = await getUserId(data.username)
    return para


async def ser_submission_List(
        data: submissionListType, SDUOJUserInfo=Depends(cover_header)):
    if data.router.gid == -1 and data.router.pid == -1:
        problem_set_manager(data.router.psid, SDUOJUserInfo)
    else:
        await problem_set_user(data.router.psid, SDUOJUserInfo,
                               data.router.gid)
        # 用户只能访问自己的代码
        if data.username is None:
            raise HTTPException(detail="Permission Denial", status_code=403)

    data.us = SDUOJUserInfo["username"]
    return data


async def ser_submission_info(
        data: submissionInfo, SDUOJUserInfo=Depends(cover_header)):
    if data.gid == -1 and data.pid == -1:
        problem_set_manager(data.psid, SDUOJUserInfo)
    else:
        await problem_set_user(data.psid, SDUOJUserInfo, data.gid)
        data.username = SDUOJUserInfo["username"]
    return data


def save_Ipv4_info(data: routerTypeWithUsername, ip):
    from model.answer_sheet import answerSheetModel
    db = answerSheetModel()
    db.add_ipv4_by_psid_username(
        data.router.psid, data.username, ip
    )
