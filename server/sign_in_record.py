from fastapi import APIRouter, Depends

from model.sign_in_record import signInRecordModel
from model.problem_group import groupModel
from ser.base import makePageResult
from ser.sign_in_record import submitLeaveInfoType
from utils import makeResponse

router = APIRouter(
    prefix="/sign"
)


# /server/sign_in_record.py------------------------------------------

# 用户提交请假信息
@router.post("/edit")
async def submit_leave_info(data: submitLeaveInfoType):
    db = signInRecordModel()
    # 组织字典data
    data = {
        "sg_u_id": data.sg_u_id,
        "sg_user_message": data.sg_user_message
    }
    db.submitLeaveInfo(data)
    return makeResponse(None)


