from datetime import datetime
from typing import List

from auth import manager, group_manager
from ser.base_type import page, pageResult


def makePageResult(pg: page, tn: int, data: List):
    return obj2dict(pageResult(
        pageIndex=pg.pageNow,
        pageSize=pg.pageSize,
        totalNum=tn,
        rows=data
    ))


def base_add(data, SDUOJUserInfo):
    # 判定管理员权限
    manager(SDUOJUserInfo)
    data = obj2dict(data)
    data.update({"username": SDUOJUserInfo["username"]})
    return data


def group_base_edit(data, SDUOJUserInfo):
    # 判定编辑权限
    group_manager(data.gid, SDUOJUserInfo)
    data = obj2dict(data)
    # data.pop("gid")
    return data


def group_base_info(data, SDUOJUserInfo):
    group_manager(data.gid, SDUOJUserInfo)
    return obj2dict(data)


def base_page(data, SDUOJUserInfo):
    manager(SDUOJUserInfo)
    return {"page": data, "username": SDUOJUserInfo["username"],
            "groups": SDUOJUserInfo["groups"]}


def obj2dict(obj):
    m = obj.__dict__
    for k in m.keys():
        v = m[k]
        if hasattr(v, "__dict__"):
            m[k] = obj2dict(v)
        if type(v) == list:
            tp = []
            for it in v:
                if hasattr(it, "__dict__"):
                    tp.append(obj2dict(it))
                else:
                    tp.append(it)
            m[k] = tp
    return m
