from fastapi import APIRouter, Depends,Query
from model.sign_in_record import signInRecordModel
from ser.sign_in_record import (submitLeaveInfoType, checkLeaveInfoType,
    sign_create, signEditType, sign_edit, signInType, checkIn,
    checktoken, SignInData, scanIn, pageType, get_page)
from utils import makeResponse


router = APIRouter(
    #prefix="/sign"
)


# /server/sign_in_record.py------------------------------------------

# 创造签到信息 1
@router.post("/sign/create")
async def signCreate(data: dict = Depends(sign_create)):
    db = signInRecordModel()
    await db.createSign(data)
    return makeResponse(None)


# 修改签到信息 2
@router.post("/sign/{sg_id}/edit")
async def signEdit(sg_id: int, data: dict = Depends(sign_edit)):
    db = signInRecordModel()
    db.editSign(data, sg_id)
    return makeResponse(None)


# 删除签到信息 3
@router.post("/sign/{sg_id}/delete")
async def signDelete(sg_id: int):
    db=signInRecordModel()
    db.deleteSign(sg_id)
    return makeResponse(None)


#根据sg_id查询签到信息 4
@router.get("/sign/{sg_id}/info")
async def signInfo(sg_id: int):
    db = signInRecordModel()
    info = db.getSign(sg_id)
    return makeResponse(info)


# 查询所有的签到信息 5
@router.get("/sign/list")
async def signList(pageSize: int = Query(10, alias="pageSize"), pageNow: int = Query(1, alias="pageNow")):
    db=signInRecordModel()
    info = db.getSignList(pageNow, pageSize)
    return makeResponse(info)


# 查询一个sg_id中的用户签到信息 6
@router.get("/sign/userInfoList")
async def signList(pageSize: int = Query(10, alias="pageSize"), pageNow: int = Query(1, alias="pageNow"),
                   sg_id: int = Query(0, alias="sg_id"), mode: int = Query(1, alias="mode")):
    db = signInRecordModel()
    info = db.getUserSign(pageSize, pageNow, sg_id, mode)
    return makeResponse(info)


# 某一组中某一用户签到信息查询 7
@router.get("/sign/{sg_id}/{username}/userInfo")
async def userInfo(sg_id: int, username: str):
    db = signInRecordModel()
    info = db.getUserInfo(sg_id, username)
    return makeResponse(info)


# 用户签到 8
@router.post("/userSign/checkIn")
async def checkInUser(data: dict = Depends(checkIn)):
    db = signInRecordModel()
    db.signIn(data)
    return makeResponse(None)


# 用户单个签到信息查询 9
@router.get("/userSign/{group_id}/{username}/userInfo")
async def userInfo(group_id: int, username: str):
    db = signInRecordModel()
    info = db.getOneUserInfo(group_id, username)
    return makeResponse(info)


# 所有用户签到信息查询 10
@router.get("/userSign/List")
async def get_user_info_list(group_id: int = Query(0, alias="group_id"), username: str = Query("none", alias="username"),
                            pageSize: int = Query(10, alias="pageSize"), pageNow: int = Query(1, alias="pageNow")):
    db = signInRecordModel()
    res = db.getUserInfoList(group_id, username, pageNow, pageSize)
    return makeResponse(res)


# 用户提交请假信息 11
@router.post("/userSign/submit")
async def submit_leave_info(data: submitLeaveInfoType):
    db = signInRecordModel()
    # 组织字典data
    data = {
        "sg_u_id": data.sg_u_id,
        "sg_user_message": data.sg_user_message
    }
    db.submitLeaveInfo(data)
    return makeResponse(None)


# 后台审批请假信息 12
@router.post("/userSign/check")
async def check_leave_info(data: checkLeaveInfoType):
    db = signInRecordModel()
    data = {
        "sg_u_id": data.sg_u_id,
        "sg_absence_pass": data.sg_absence_pass
    }
    db.checkLeaveInfo(data)
    return makeResponse(None)


# 删除用户签到信息 13
@router.post("/userSign/{sg_u_id}/delete")
async def delete_leave_info(sg_u_id: int):
    db = signInRecordModel()
    db.deleteLeaveInfo(sg_u_id)
    return makeResponse(None)


# 返回token
@router.post("/sign/returnToken")
async def check_token(data: dict = Depends(checktoken)):
    db = signInRecordModel()
    info = db.committoken(data)

    return makeResponse(info)


# 传递二维码信息 14
@router.post("/sign/checkAdmin")
async def check_admin(data: dict = Depends(scanIn)):
    db = signInRecordModel()
    db.checktoken(data)

    return makeResponse(None)


# 小程序端得到所有签到的title 15
@router.get("/sign/weChatSignList")
async def weChatSignList():
    db = signInRecordModel()
    info = db.getSignListForWeChat()
    return makeResponse(info)






