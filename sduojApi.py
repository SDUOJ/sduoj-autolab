import json
from typing import Union

import requests
from fastapi_cache.decorator import cache

from cache import class_func_key_builder
from const import SDUOJ_TOKEN, NACOS_addr, NACOS_namespace

requestHeaders = {"sduoj-token": SDUOJ_TOKEN}


@cache(expire=60)
async def getService_ip_port(server_name):
    data = requests.get(
        "http://" + NACOS_addr + "/nacos/v1/ns/instance/list",
        params={
            "serviceName": "DEFAULT_GROUP@@{}".format(server_name),
            "namespaceId": NACOS_namespace
        },
    ).json()
    r = data["hosts"][0]
    return r["ip"] + ":" + str(r["port"])


async def make_get(service, url, params) -> Union[dict, str]:
    addr = await getService_ip_port(service)
    data = requests.get(
        "http://" + addr + url,
        params=params,
        headers=requestHeaders
    ).content.decode(encoding="utf-8")
    try:
        data = json.loads(data)
    except:
        pass
    return data


async def make_post(service, url, params, data) -> Union[dict, str]:
    addr = await getService_ip_port(service)
    # print(params, data)
    data = requests.post(
        "http://" + addr + url,
        params=params,
        json=data,
        headers=requestHeaders
    ).content.decode(encoding="utf-8")
    try:
        data = json.loads(data)
    except:
        pass
    return data


@cache(expire=60 * 10)
async def getGroupName(groupId):
    data = await make_get(
        "user-service",
        "/internal/group/groupIdToTitle",
        {"groupId": groupId}
    )
    return data


# 提交详情
async def getSubmissionInfo(psid, submissionId):
    data = await make_get(
        "problem-service",
        "/internal/submission/query",
        {
            "submissionId": int(submissionId, 16),
            "bizType": 2,
            "bizId": psid
        }
    )
    data.pop("problemId")
    data.pop("problemCode")
    data.pop("problemTitle")
    return data


@cache(expire=60)
async def getProblemInfo(pid, desId):
    data = await make_get(
        "problem-service",
        "/internal/problem/query",
        {
            "problemCode": await getProblemCode(pid),
            "problemDescriptionId": desId
        }
    )
    data.pop("problemId")
    data.pop("problemCode")
    data.pop("problemTitle")
    data.pop("submitNum")
    data.pop("acceptNum")
    data.pop("defaultDescriptionId")
    data.pop("source")
    data["problemDescriptionDTO"].pop("id")
    data["problemDescriptionDTO"].pop("problemId")
    data["problemDescriptionDTO"].pop("problemCode")
    data["problemDescriptionDTO"].pop("title")
    return data


# pid 查询 ProblemCode  [√] （固定的数据信息，不会改变）
@cache(expire=60 * 60)
async def getProblemCode(pid):
    data = await make_get(
        "problem-service",
        "/internal/problem/problemIdToProblemCode",
        {
            "problemId": pid
        }
    )
    return data


# ProblemCode 查询 pid  [√] （固定的数据信息，不会改变）
@cache(expire=60 * 60)
async def getProblemId(problemCode):
    data = await make_get(
        "problem-service",
        "/internal/problem/problemCodeToProblemId",
        {
            "problemCode": problemCode
        }
    )
    return data


# userId 查询 username （固定的数据信息，不会改变）
@cache(expire=60 * 60)
async def getUsername(uid):
    data = await make_get(
        "user-service",
        "/internal/user/userIdToUsername",
        {"userId": uid}
    )
    return data


# username 查询 userId （固定的数据信息，不会改变）
@cache(expire=60 * 60)
async def getUserId(username):
    data = await make_get(
        "user-service",
        "/internal/user/usernameToUserId",
        {"username": username}
    )
    return data


# username 查询 nickname （相对固定的数据信息，几乎不会改变）
@cache(expire=60 * 30)
async def getNickName(username):
    data = await make_get(
        "user-service",
        "/internal/user/userIdToNickname",
        {"userId": await getUserId(username)}
    )
    return data


# 查询组成员
async def getGroupMember(groupId):
    data = await make_get(
        "user-service",
        "/internal/group/query",
        {"groupId": groupId}
    )
    return data


# 提交代码
async def programSubmit(
        problemSetId, judgeTemplateId, code, zipFileId,
        pid, ipv4, username
):
    data = await make_post(
        "problem-service",
        "/internal/submission/create",
        data={
            "judgeTemplateId": judgeTemplateId,
            "code": code,
            "zipFileId": zipFileId,
            "problemCode": await getProblemCode(pid),
            "ipv4": ipv4,
            "userId": await getUserId(username)
        },
        params={
            "bizType": 2,
            "bizId": problemSetId
        }
    )
    return data


async def getSubmissionList(problemSetId, ext, pop=True):
    data = await make_post(
        "problem-service",
        "/internal/submission/page",
        params={
            "bizType": 2,
            "bizId": problemSetId
        },
        data=ext
    )
    if pop:
        for x in data["rows"]:
            x.pop("problemId")
            x.pop("problemCode")
            x.pop("problemTitle")
    return data


@cache(expire=60)
async def getSubmissionScore(
        problemSetId, problemId, username, exportCode=None):
    data: dict = await make_post(
        "problem-service",
        "/internal/submission/listResult",
        data={
            "bizType": 2,
            "bizId": problemSetId,
            "problemId": problemId,
            "userId": None if username is None else await getUserId(username),
            "exportCode": exportCode
        },
        params={}
    )
    user_pro2List = {}
    for x in data:
        # 特判，题目满分是 100
        if "fullScore" not in x:
            x["fullScore"] = 100
        id_ = str(x["userId"]) + "-" + str(x["problemId"])
        if id_ not in user_pro2List:
            user_pro2List[id_] = []
        user_pro2List[id_].append(x)

    # print(user_pro2List)

    return user_pro2List


@cache(expire=60)
async def getSubmissionScoreAll(
        problemSetId, problemId, username, exportCode=None):
    uid = await getUserId(username)
    id_ = str(uid) + "-" + str(problemId)
    try:
        data: dict = await getSubmissionScore(
            problemSetId, None, None, exportCode)
        return data[id_]
    except:
        data: dict = await getSubmissionScore(
            problemSetId, problemId, username, exportCode)
        # 特判没有查到数据的情况
        if id_ in data:
            return data[id_]
        return []


async def add_pbCheckPoint(req_data):
    data: dict = await make_post(
        "problem-service",
        "/internal/problem/appendCheckpoints",
        data=req_data,
        params={"userId": req_data["userId"]}
    )
    return data


async def get_pbCheckPoint(req_data):
    data: dict = await make_get(
        "problem-service",
        "/internal/problem/listCheckpoints",
        params=req_data,
    )
    return data


async def del_pbCheckPoint(req_data):
    data: dict = await make_post(
        "problem-service",
        "/internal/problem/updateCheckpoints",
        data=req_data,
        params={"userId": req_data["userId"]}
    )
    return data


async def upd_pbCheckPoint(req_data):
    data: dict = await make_post(
        "problem-service",
        "/internal/problem/appendCheckpoints",
        data=req_data,
        params={"userId": req_data["userId"]}
    )
    return data
