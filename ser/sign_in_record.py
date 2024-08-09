from datetime import datetime
from http.client import HTTPException
from typing import List, Optional, Union

from pydantic import BaseModel
from sqlalchemy.orm import session
from db import ojClass, dbSession

import os
import uuid


# 学生签到信息
class signInType(BaseModel):
    sg_u_id: int
    username: str
    sg_id: int
    seat_id: int
    sg_user_message: str = None


class signType(BaseModel):
    #签到信息
    mode: int
    group_id: int
    m_group_id: int
    title: str
    start_time: datetime
    end_time: datetime
    seat_bind: int
    usl_id: int = None


class userSignIn(BaseModel):
    username: str


class signEditType(BaseModel):
    mode: int = None
    group_id: int = None
    m_group_id: int = None
    title: str = None
    start_time: datetime = None
    end_time: datetime = None
    seat_bind: int = None
    usl_id: int = None



class submitLeaveInfoType(BaseModel):
    # 用户提交的请假信息
    sg_u_id: int
    sg_user_message: str


class checkLeaveInfoType(BaseModel):
    # 审批用户提交的请假信息
    sg_u_id: int
    sg_absence_pass: int = None


def sign_create(data: signType):
    if data.mode is None:
        raise HTTPException(status_code=400, detail="签到模式未填写")
    elif data.mode not in range(0, 4):
        raise HTTPException(status_code=400, detail="签到模式的输入不合法")
    elif not data.m_group_id:
        raise HTTPException(status_code=400, detail="管理组未填写")
    elif not data.start_time or not data.end_time:
        raise HTTPException(status_code=400, detail="开始或结束时间未填写")
    elif not data.seat_bind:
        raise HTTPException(status_code=400, detail="绑定信息未填写")

    Now = datetime.now()
    data={
        "mode": data.mode,
        "group_id": data.m_group_id,
        "m_group_id": data.m_group_id,
        "u_gmt_create": Now,
        "u_gmt_modified": Now,
        "title": data.title,
        "start_time": data.start_time,
        "end_time": data.end_time,
        "seat_bind": data.seat_bind,
        "usl_id": 1
    }
    return data


def sign_edit(data: signEditType):
    if data.mode not in [0, 1, 2, 3]:
        raise HTTPException(status=400, detail="签到模式输入不合法")
    Now = datetime.now()
    data = {
        "mode": data.mode,
        "group_id": data.m_group_id,
        "m_group_id": data.m_group_id,
        "title": data.title,
        "start_time": data.start_time,
        "end_time": data.end_time,
        "u_gmt_modified":Now,
        "seat_bind": data.seat_bind,
        "usl_id": data.usl_id
    }
    return data


def checkIn(data: signInType):
    Now = datetime.now()
    Token = uuid.uuid4().hex
    sg_absence_pass = None
    #处理请假信息  # 1 通过  0 未通过  none 审批中  2 无请假
    if data.sg_user_message is None:
        sg_absence_pass = 2
    data = {
        "sg_u_id": data.sg_u_id,
        "username": data.username,
        "sg_id": data.sg_id,
        "sg_time": Now,
        "seat_id": data.seat_id,
        "token": Token,
        "sg_user_message": data.sg_user_message,
        "sg_absence_pass": sg_absence_pass
    }
    return data
