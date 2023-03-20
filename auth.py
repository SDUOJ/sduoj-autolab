import json
from urllib import parse

from fastapi import Header, HTTPException

from utilsTime import afterTime


def is_superadmin(SDUOJUserInfo):
    return "superadmin" in SDUOJUserInfo["roles"]


def is_admin(SDUOJUserInfo):
    return is_superadmin(SDUOJUserInfo) or "admin" in SDUOJUserInfo["roles"]


def is_manager(obj, SDUOJUserInfo):
    # 超级管理员
    if is_superadmin(SDUOJUserInfo):
        return

    # 创建者 或 管理组成员
    if obj.username == SDUOJUserInfo["username"] or \
            obj.manageGroupId in SDUOJUserInfo["groups"]:
        return

    raise HTTPException(detail="Permission Denial", status_code=403)


def in_group(groupId, SDUOJUserInfo):
    if is_superadmin(SDUOJUserInfo):
        return
    if groupId not in SDUOJUserInfo["groups"]:
        raise HTTPException(detail="Permission Denial", status_code=403)


def group_manager(gid: int, SDUOJUserInfo):
    from model.problem_group import groupModel
    db = groupModel()
    pg = db.group_get_obj_by_id(gid)
    is_manager(pg, SDUOJUserInfo)


def problem_set_manager(psid: int, SDUOJUserInfo):
    from model.problem_set import problemSetModel
    db = problemSetModel()
    ps = db.ps_get_obj_by_id(psid)
    is_manager(ps, SDUOJUserInfo)


# 用户是否可以查看题单信息
# 开启报告模式，则题单始终可查
# 关闭报告模式，题单在任意题组的作答时间内可查

# 判断题组的提交权限
# 题组只有在【作答时间内】&&【未交卷】才可以提交，
async def problem_set_user(  # 提交相关交卷后不能提交，authOnly 表示只认证
        psid: int, SDUOJUserInfo, gi=None, submit=None, authOnly=None):
    from model.answer_sheet import answerSheetModel
    from utilsTime import getNowTime, inTime, inTimeSetting, \
        inGroupInfoItemTime

    db = answerSheetModel()
    obj = await db.ps_get_info_by_id_cache(psid)

    # 管理员可以随时查看
    if is_superadmin(SDUOJUserInfo):
        return

    if obj["username"] == SDUOJUserInfo["username"] or \
            obj["manageGroupId"] in SDUOJUserInfo["groups"]:
        return

    # 拒绝不在作答组内的查看
    if obj["groupId"] not in SDUOJUserInfo["groups"]:
        raise HTTPException(detail="Permission Denial", status_code=403)

    if authOnly:
        return

    if submit is True:
        as_info = db.get_info_by_psid_username(
            psid, SDUOJUserInfo["username"])
        tm_start, tm_end = await db.ps_get_tm_by_psid_cache(psid)

        # 交卷只在考试的时间内进行限制
        if as_info["finish"] == 1 and not afterTime(int(tm_start),
                                                    int(tm_end)):
            raise HTTPException(detail="Permission Denial After Submitted",
                                status_code=403)
    else:
        useReport = obj["config"]["showReport"]
        if useReport:
            return

    # 用户只能在限时内或补题时查看或提交
    mode = obj["config"]["useSameSE"]
    now = getNowTime()

    if mode == 1:
        tm_start = int(obj["tm_start"])
        tm_end = int(obj["tm_end"])

        # 特判，考试模式中，交卷后不再显示题单
        if obj["type"] == 1:
            if inTime(now, tm_start, tm_end):
                as_info = db.get_info_by_psid_username(
                    psid, SDUOJUserInfo["username"])
                if as_info["finish"] == 1:
                    raise HTTPException(
                        detail="Permission Denial After Submitted",
                        status_code=403)
        # 在规定时间内
        if inTime(now, tm_start, tm_end):
            return

        usePractice = obj["config"]["usePractice"]

        if usePractice == 1:
            practiceTimeSetting = obj["config"]["practiceTimeSetting"]

            # 在练习模式的某个时间内
            for x in practiceTimeSetting:
                if inTimeSetting(now, x):
                    return
    else:
        # 在题组自定义时间的模式中，只要在任意一个题组的规定时间内即可查看题单信息
        # 如果指定了题组，要在题组的作答时间中合法
        groupInfo = obj["groupInfo"]
        if gi is None:
            for x in groupInfo:
                if inGroupInfoItemTime(now, x):
                    return
        else:
            if inGroupInfoItemTime(now, groupInfo[gi]):
                return

    raise HTTPException(detail="Permission Denial", status_code=403)


def manager(SDUOJUserInfo):
    if not is_admin(SDUOJUserInfo):
        raise HTTPException(detail="Permission Denial", status_code=403)


def cover_header(SDUOJUserInfo=Header(None)):
    try:
        return json.loads(parse.unquote(SDUOJUserInfo))
    except:
        raise HTTPException(detail="Permission Denial", status_code=403)


def parse_header(SDUOJUserInfo):
    try:
        return parse.quote(json.dumps(SDUOJUserInfo))
    except:
        raise HTTPException(detail="Gateway Message Error", status_code=500)
