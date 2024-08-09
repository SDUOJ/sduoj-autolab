from fastapi import APIRouter, Depends

from model.sign_in_record import signInRecordModel
from ser.sign_in_record import submitLeaveInfoType, checkLeaveInfoType, sign_create,signEditType,sign_edit, signInType, checkIn
from utils import makeResponse

# 统一了路由的前缀
router = APIRouter(
    prefix="/sign"
)


# /server/sign_in_record.py------------------------------------------

# 创造签到信息 1
@router.post("/create")
async def signCreate(data: dict = Depends(sign_create)):
    db=signInRecordModel()
    db.createSign(data)
    return makeResponse(None)


# 修改签到信息 2
@router.post("/{sg_id}/edit")
async def signEdit(sg_id: int, data: dict = Depends(sign_edit)):
    db=signInRecordModel()
    db.editSign(data, sg_id)
    return makeResponse(None)


# 删除签到信息 3
@router.post("/{sg_id}/delete")
async def signDelete(sg_id: int):
    db=signInRecordModel()
    db.deleteSign(sg_id)
    return makeResponse(None)


#根据sg_id查询签到信息 4
@router.get("/{sg_id}/info")
async def signInfo(sg_id: int):
    db = signInRecordModel()
    info = db.getSign(sg_id)
    return info


# 查询用户存在的签到 5
@router.get("/{username}/list")
async def signList(username: str):
    db = signInRecordModel()
    info = db.getUserSign(username)
    return info


# 用户签到 6
@router.post("/checkIn")
async def checkInUser(data: dict = Depends(checkIn)):
    db = signInRecordModel()
    db.signIn(data)
    return makeResponse(None)


# 用户签到信息查询 7
@router.get("/{sg_u_id}/userInfo")
async def userInfo(sg_u_id: int):
    db = signInRecordModel()
    info = db.getUserInfo(sg_u_id)
    return info


# 所有用户签到信息查询 8
@router.get("/{sg_id}/userInfoList")
async def get_user_info_list(sg_id: int):
    db = signInRecordModel()
    res = db.getUserInfoList(sg_id)
    return res


# 用户提交请假信息 9
@router.post("/submit")
async def submit_leave_info(data: submitLeaveInfoType):
    db = signInRecordModel()
    # 组织字典data
    data = {
        "sg_u_id": data.sg_u_id,
        "sg_user_message": data.sg_user_message
    }
    db.submitLeaveInfo(data)
    return makeResponse(None)


# 后台审批请假信息 10
@router.post("/check")
async def check_leave_info(data: checkLeaveInfoType):
    db = signInRecordModel()
    data = {
        "sg_u_id": data.sg_u_id,
        "sg_absence_pass": data.sg_absence_pass
    }
    db.checkLeaveInfo(data)
    return makeResponse(None)


# 删除用户签到信息 11
@router.post("/{sg_u_id}/delete")
async def delete_leave_info(sg_u_id: int):
    db = signInRecordModel()
    db.deleteLeaveInfo(sg_u_id)
    return makeResponse(None)
