from typing import List, Optional, Union

from pydantic import BaseModel
from sqlalchemy.orm import session

from db import ojClass, dbSession


class submitLeaveInfoType(BaseModel):
    # 用户提交的请假信息
    sg_u_id: int
    sg_user_message: str


class checkLeaveInfoType(BaseModel):
    # 审批用户提交的请假信息
    sg_u_id: int
    sg_absence_pass: int = None
