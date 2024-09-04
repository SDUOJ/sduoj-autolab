from typing import List, Optional, Union

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import session

from db import ojClass, dbSession


# /ser/class_binding.py(序列化器部分，感觉像是定义信息;在这里面的函数把得到的传参汇总了再return)----------------------


class classroomType(BaseModel):
    # 记录所有的教室信息

    c_name: str  # 教室名
    c_seat_num: int  # 教室的座位数量
    c_description: Union[str, None]  # 教室描述
    c_is_available: int  # 教室是否可用
    address: str  # 教室在哪个楼上


class classroomEditType(BaseModel):
    # 修改教室信息
    c_id: int   # 教室id
    c_name: str  # 教室名
    c_seat_num: int  # 座位数量
    c_description: Union[str, None]  # 教室描述
    c_is_available: int  # 教室是否可用
    address: str  # 教室在哪个楼上
    s_number: list  # 不可用的座位号(列表)


class seatType(classroomType):
    # 记录所有的座位信息

    s_id: int  # 座位id,唯一标识
    s_number: int  # 座号
    c_id: int  # 外键，所属教室id
    s_tag: int  # 座位状态(1可用0不可用，未分配ip之前不可用)
    s_ip: str  # 座位ip


class userSeatListType(BaseModel):
    usl_id: int  # 学生座位名单id
    name: str  # 名单名称
    groupId: int  # 绑定组信息


class classUserType(userSeatListType, seatType):
    # 记录教室用户的绑定信息

    id: int
    usl_id: int
    username: int  # 用户名（学号）
    s_id: int  # 座位id


class classManageUserType(userSeatListType, classroomType):
    usl_id: int
    TA_name: str  # 助教名
    c_id: int  # 教室id


def createClassroom(data: dict):
    c_name = data.get("c_name")
    c_seat_num = data.get("c_seat_num")
    c_description = data.get("c_description")
    address = data.get("address")
    no_use_seat = data.get("s_number", [])

    if not c_name:
        raise HTTPException(status_code=400, detail="教室名称为空")
    elif not c_seat_num:
        raise HTTPException(status_code=400, detail="教室座位数量应为正整数")
    elif not c_description:
        raise HTTPException(status_code=400, detail="应写入教室描述")

    # c_available 默认为 True
    data = {
        "c_name": c_name,
        "c_seat_num": c_seat_num,
        "c_description": c_description,
        "c_is_available": 1,
        "address": address,
        "no_use_seat": no_use_seat
    }

    return data
