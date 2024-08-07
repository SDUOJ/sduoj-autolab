from fastapi import APIRouter, Depends

from model.sign_in_record import signInRecordModel
from ser.sign_in_record import submitLeaveInfoType, checkLeaveInfoType
from utils import makeResponse

# 统一了路由的前缀
router = APIRouter(
    prefix="/sign"
)


# /server/sign_in_record.py------------------------------------------

# 所有用户签到信息查询8
@router.get("/{sg_id}/userInfoList")
async def get_user_info_list(sg_id: int):
    db = signInRecordModel()
    res = db.getUserInfoList(sg_id)
    return res


# 用户提交请假信息9
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


# 后台审批请假信息10
@router.post("/check")
async def check_leave_info(data: checkLeaveInfoType):
    db = signInRecordModel()
    data = {
        "sg_u_id": data.sg_u_id,
        "sg_absence_pass": data.sg_absence_pass
    }
    db.checkLeaveInfo(data)
    return makeResponse(None)


# 删除用户签到信息11
@router.post("/{sg_u_id}/delete")
async def delete_leave_info(sg_u_id: int):
    db = signInRecordModel()
    db.deleteLeaveInfo(sg_u_id)
    return makeResponse(None)
