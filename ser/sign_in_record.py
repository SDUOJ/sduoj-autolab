from datetime import datetime
from http.client import HTTPException
from typing import List, Optional, Union

from pydantic import BaseModel
from sqlalchemy.orm import session
from db import ojClass, dbSession,ojSeat

import os
import uuid


# 学生签到信息
class signInType(BaseModel):
    username: str
    sg_id: int
    seat_id: int = None
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
    sg_id: int
    mode: int = None
    group_id: int = None
    m_group_id: int = None
    title: str = None
    start_time: datetime = None
    end_time: datetime = None
    seat_bind: int = None


class submitLeaveInfoType(BaseModel):
    # 用户提交的请假信息
    sg_u_id: int
    sg_user_message: str


class checkLeaveInfoType(BaseModel):
    # 审批用户提交的请假信息
    sg_u_id: int
    sg_absence_pass: int = None

class usermess(BaseModel):
    username: str
    sg_id: str

class SignInData(BaseModel):
    token: str
    c_id: int
    s_number: int

class pageType(BaseModel):
    pageNow: int
    pageSize: int


def sign_create(data: signType):
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
    Now = datetime.now()
    data = {
        "sg_id":data.sg_id,
        "mode": data.mode,
        "group_id": data.m_group_id,
        "m_group_id": data.m_group_id,
        "title": data.title,
        "start_time": data.start_time,
        "end_time": data.end_time,
        "u_gmt_modified": Now,
        "seat_bind": data.seat_bind,
    }
    return data


def checkIn(data: signInType):
    Now = datetime.now()
    Token = uuid.uuid4().hex
    sg_absence_pass = None
    #处理请假信息  # 1 通过  none 审批中  2 不通过
    if data.sg_user_message is None:
        return None
    data = {
        "username": data.username,
        "sg_id": data.sg_id,
        "sg_time": Now,
        "seat_id": data.seat_id,
        "token": Token,
        "sg_user_message": data.sg_user_message,
        "sg_absence_pass": sg_absence_pass
    }
    return data


def get_page(data: pageType):
    data = {
    "pageSize": data.pageSize,
    "pageNow": data.pageNow
    }
    return data


def checktoken(data: usermess):
    Token = uuid.uuid4().hex
    data = {
        "username": data.username,
        "sg_id": data.sg_id,
        "token": Token,
    }
    return data

def scanIn(data: SignInData):
    Now = datetime.now()
    data = {
        "sg_time": Now,
        "token": data.token,
        "c_id": data.c_id,
        "s_number": data.s_number
    }
    return data