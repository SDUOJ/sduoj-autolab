import json
from typing import List, Optional

from fastapi import Depends, HTTPException
from fastapi_cache.decorator import cache
from pydantic import BaseModel, validator, root_validator

from auth import cover_header, problem_set_manager, in_group, problem_set_user, group_manager
from fastapi import Header
from ser.base import base_add, base_page, obj2dict
from ser.base_type import page
from utilsTime import cover_to_dt


class timeSettingType(BaseModel):
    tm_start: int
    tm_end: int
    weight: float


class groupInfoItem(BaseModel):
    gid: int
    name: str
    score: float
    timeSetting: Optional[List[timeSettingType]]


class problem_set_subjectiveJudgeStrategy(BaseModel):
    mergerSubjectiveGroup: int
    dependencyPrograming: List[str]


class problem_set_config(BaseModel):
    useSameSE: int

    # 在时间统一设定的模式下（useSameSE=1），可以在此统一设定补题，并设定成绩计算公式
    usePractice: Optional[int]
    practiceScoreCalculate: Optional[str]
    practiceTimeSetting: Optional[List[timeSettingType]]

    # 在题组的作答时间结束之后，
    # 则显示当前题组的报告信息，分别显示在 overview 与 题目详情中展示
    # 答题时间结束之后，主观题与客观题将无法再次作答，编程题可以提交，但不算成绩
    showReport: int
    showObjectiveAnswer: Optional[int] = 0
    showSubjectiveAnswer: Optional[int] = 0
    showSubjectiveJudgeLog: Optional[int] = 0

    showScoreInRunning: int
    showProgramScoreInRunning: int

    mergerSubjectiveGroup: int

    @validator('practiceScoreCalculate', always=True)
    def validate_practiceScoreCalculate(cls, v, values, **kwargs):
        if "usePractice" in values and values["usePractice"] == 1:
            if v is None or len(v) == 0:
                raise ValueError('practiceScoreCalculate is missing')
        return v

    @validator('practiceScoreCalculate', always=True)
    def validate_practiceTimeSetting(cls, v, values, **kwargs):
        if "usePractice" in values and values["usePractice"] == 1:
            if v is None or len(v) == 0:
                raise ValueError('practiceTimeSetting is missing')
        return v


def check_value(values):
    if "groupInfo" not in values:
        raise ValueError('groupInfo is missing')
    if "config" not in values:
        raise ValueError('config is missing')
    if len(values["groupInfo"]) == 0:
        raise ValueError('groupInfo is empty')
    config = values["config"]
    if config.useSameSE != 1:
        for v0 in values["groupInfo"]:
            if v0.timeSetting is None:
                raise ValueError('timeSetting is missing')
    if config.useSameSE == 1:
        if values["tm_start"] is None:
            raise ValueError('tm_start is missing')
        if values["tm_end"] is None:
            raise ValueError('tm_end is missing')
    return values


class problem_set_base(BaseModel):
    name: str
    description: Optional[str]
    type: int
    groupInfo: List[groupInfoItem]
    config: problem_set_config
    global_score: Optional[float]

    tm_start: Optional[int]
    tm_end: Optional[int]

    manageGroupId: Optional[int]
    groupId: int
    tag: str

    @root_validator
    def validate_root(cls, values):
        return check_value(values)


class problem_set_psid(BaseModel):
    psid: int


class problem_set_summary(problem_set_psid):
    code: int


class personal_tag_summary(BaseModel):
    groupId: int
    username: Optional[str]
    force: bool = False


class problem_set_edit(problem_set_psid):
    name: Optional[str]
    description: Optional[str]
    type: Optional[int]
    config: Optional[problem_set_config]

    groupInfo: Optional[List[groupInfoItem]]
    global_score: Optional[float]

    tm_start: Optional[int]
    tm_end: Optional[int]

    manageGroupId: Optional[int]
    groupId: Optional[int]
    tag: Optional[str]

    @root_validator
    def validate_root(cls, values):
        return check_value(values)


class search_list_page(page):
    groupId: int
    tag: str


class userGroupId(BaseModel):
    groupId: int


class get_problem_info(problem_set_psid):
    gid: int
    pid: int


def ser_problem_set_summary(data: problem_set_summary,
                            SDUOJUserInfo=Depends(cover_header)):
    problem_set_manager(data.psid, SDUOJUserInfo)
    return obj2dict(data)


def ser_personal_tag_summary(data: personal_tag_summary,
                             SDUOJUserInfo=Depends(cover_header)):
    from auth import group_manager, in_group, is_superadmin
    requester = SDUOJUserInfo["username"]
    
    # 检查是否为该 group 的管理员或超级管理员
    is_group_adm = False
    try:
        group_manager(data.groupId, SDUOJUserInfo)
        is_group_adm = True
    except HTTPException:
        is_group_adm = False

    # 只有当前 group 的管理员 或超级管理员可以查看 这个 group 内任意学生的信息
    if data.username and data.username != requester:
        if not is_group_adm:
            raise HTTPException(status_code=403, detail="权限不足，仅组管理员可查看他人报告")
    else:
        # 默认查询自己
        data.username = requester
        # 如果不是管理员，必须在组内才能查看自己的
        if not is_group_adm:
            in_group(data.groupId, SDUOJUserInfo)

    # force 字段仅管理员可用
    if data.force and not is_group_adm:
        data.force = False

    res = obj2dict(data)
    return res


def ser_problem_set_add(data: problem_set_base,
                        SDUOJUserInfo=Depends(cover_header)):
    data = base_add(data, SDUOJUserInfo)
    cover_to_dt(data, "tm_start")
    cover_to_dt(data, "tm_end")

    return data


def ser_problem_set_edit(data: problem_set_edit,
                         SDUOJUserInfo=Depends(cover_header)):
    problem_set_manager(data.psid, SDUOJUserInfo)
    data = obj2dict(data)
    cover_to_dt(data, "tm_start")
    cover_to_dt(data, "tm_end")
    return data


def ser_problem_set_info(data: problem_set_psid,
                         SDUOJUserInfo=Depends(cover_header)):
    problem_set_manager(data.psid, SDUOJUserInfo)
    return obj2dict(data)


# 获取当前用户是否为管理员
@cache(expire=60)
async def get_problem_set_manger(psid, SDUOJUserInfo):
    try:
        problem_set_manager(psid, SDUOJUserInfo)
        return True
    except:
        return False


async def ser_problem_set_info_c(data: problem_set_psid,
                                 SDUOJUserInfo=Depends(cover_header)):
    await problem_set_user(data.psid, SDUOJUserInfo)
    res = obj2dict(data)
    res.update({
        "username": SDUOJUserInfo["username"],
        "isAdmin": await get_problem_set_manger(data.psid, SDUOJUserInfo)
    })
    return res


async def ser_problem_set_info_public(data: problem_set_psid,
                                      SDUOJUserInfo=Depends(cover_header)):
    await problem_set_user(data.psid, SDUOJUserInfo, authOnly=True)
    res = obj2dict(data)
    res.update({
        "username": SDUOJUserInfo["username"],
        "isAdmin": await get_problem_set_manger(data.psid, SDUOJUserInfo)
    })
    return res


def ser_problem_set_list(data: page, SDUOJUserInfo=Depends(cover_header)):
    return base_page(data, SDUOJUserInfo)


def ser_problem_set_search_list(data: search_list_page,
                                SDUOJUserInfo=Depends(cover_header)):
    in_group(data.groupId, SDUOJUserInfo)
    return {"page": data, "groupId": data.groupId,
            "tag": data.tag}


def ser_problem_set_key_list(data: userGroupId,
                             SDUOJUserInfo=Depends(cover_header)):
    in_group(data.groupId, SDUOJUserInfo)
    return data.groupId


def ser_problem_set_list_by_group(data: userGroupId,
                                 SDUOJUserInfo=Depends(cover_header)):
    group_manager(data.groupId, SDUOJUserInfo)
    return data.groupId


# 可选鉴权：没有 SDUOJUserInfo 时，不抛错，返回 logged=False
def try_cover_header(SDUOJUserInfo: str = Header(None)):
    from urllib import parse
    import json
    if SDUOJUserInfo is None:
        return {"logged": False}
    try:
        info = json.loads(parse.unquote(SDUOJUserInfo))
        info["logged"] = True
        return info
    except Exception:
        # 解析失败也视为未登录
        return {"logged": False}
