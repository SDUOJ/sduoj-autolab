from typing import Optional, Union, List

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, root_validator

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


class FileAnswerType(BaseModel):
    """主观题文件型提交单文件条目
    兼容以下可能的前端 key / 误写：
      fileId / fileid
      fileName / filename
    并防御错误解析导致 fileId 取到 'fileName' 的情况。
    """
    fileId: str = Field(..., alias='fileId')
    fileName: Optional[str] = Field(None, alias='fileName')

    @root_validator(pre=True)
    def _normalize_keys(cls, values):  # noqa
        if not isinstance(values, dict):
            return values
        # 兼容小写 key
        if 'fileid' in values and 'fileId' not in values:
            values['fileId'] = values['fileid']
        if 'filename' in values and 'fileName' not in values:
            values['fileName'] = values['filename']
        # 若出现 fileId = 'fileName' 且真实 id 在其他字段里，尝试修正
        if values.get('fileId') == 'fileName':
            # 常见误传格式: {fileId: 'fileName', fileName: 'xxx'} 真实 id 遗失，只能置空
            # 或者 {fileId: 'fileName', filename: 'xxx'}
            # 这里让它变成空字符串, 触发后续校验提示
            values['fileId'] = ''
        return values

    class Config:
        allow_population_by_field_name = True


class routerType(BaseModel):
    router: routerTypeBase
    data: Optional[Union[
        str,
        programSubmitType,
        List[str],
        publicCheckPointType,
    List[FileAnswerType],
    FileAnswerType
    ]]


class routerTypeWithUsername(routerType):
    username: str
    userSessionData: Optional[userSessionType]


class routerTypeWithData(routerTypeWithUsername):
    data: Union[
        str,
        programSubmitType,
        List[str],
        publicCheckPointType,
        List[FileAnswerType],
        FileAnswerType
    ]


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
    # 验收题队列过滤，可选；提供时仅返回加入该队列的验收题(type=2)
    review_queue: Optional[str]


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
        "hasJudge": data.hasJudge,
        "review_queue": data.review_queue
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


class psidOnlyType(BaseModel):
    psid: int


async def ser_psid_only(data: psidOnlyType, SDUOJUserInfo=Depends(cover_header)):
    await problem_set_user(data.psid, SDUOJUserInfo)
    return {"psid": data.psid}
