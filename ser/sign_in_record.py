from typing import List, Optional, Union

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import session

from model.class_binding import classBindingModel, IDGenerator
from ser.base_type import page
from db import ojClass, dbSession


class submitLeaveInfoType(BaseModel):
    # 用户提交的请假信息
    sg_u_id: int
    sg_user_message: str
