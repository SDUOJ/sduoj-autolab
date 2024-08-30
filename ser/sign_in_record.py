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
    ip: str = None
    sg_user_message: str = None


class signType(BaseModel):
    #签到信息
    mode: int
    group_id: int
    m_group_id: int
    title: str
    gmtStart: float = 0
    gmtEnd: float = 0
    seat_bind: int
    usl_id: int = None


class userSignIn(BaseModel):
    username: str


class signEditType(BaseModel):
    mode: int = None
    group_id: int = None
    m_group_id: int = None
    title: str = None
    gmtStart: float = None
    gmtEnd: float = None
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
    gmtStart = convert_time(data.gmtStart / 1000.0)
    gmtStart = gmtStart["strdate"]
    gmtEnd = convert_time(data.gmtEnd / 1000.0)
    gmtEnd = gmtEnd["strdate"]
    data={
        "mode": data.mode,
        "group_id": data.group_id,
        "m_group_id": data.m_group_id,
        "u_gmt_create": Now,
        "u_gmt_modified": Now,
        "title": data.title,
        "gmtStart": gmtStart,
        "gmtEnd": gmtEnd,
        "seat_bind": data.seat_bind,
        "usl_id": data.usl_id
    }
    return data


def sign_edit(data: signEditType):
    Now = datetime.now()
    gmtStart = gmtEnd = None

    if data.gmtStart is not None:
        gmtStart = convert_time(data.gmtStart / 1000.0)
        gmtStart = gmtStart["date"]

    if data.gmtEnd is not None:
        gmtEnd = convert_time(data.gmtEnd / 1000.0)
        gmtEnd = gmtEnd["date"]
    data = {
        "mode": data.mode,
        "group_id": data.m_group_id,
        "m_group_id": data.m_group_id,
        "title": data.title,
        "gmtStart": gmtStart,
        "gmtEnd": gmtEnd,
        "u_gmt_modified": Now,
        "seat_bind": data.seat_bind,
    }
    return data


def checkIn(data: signInType):
    Now = datetime.now()
    Token = uuid.uuid4().hex
    sg_absence_pass = None
    #处理请假信息  # 1 通过  none 审批中  2 不通过
    #if data.sg_user_message is None:
    #    return None
    data = {
        "username": data.username,
        "sg_id": data.sg_id,
        "sg_time": Now,
        "ip": data.ip,
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
    data = {
        "username": data.username,
        "sg_id": data.sg_id,
        "token": ""
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


def convert_time(item: float):
    date_time = datetime.fromtimestamp(item)
    format_time = date_time.strftime('%Y-%m-%d %H:%M:%S')
    return {"date": date_time, "strdate": format_time}

