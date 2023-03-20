from typing import List, Optional

from fastapi import Depends
from pydantic import BaseModel

from auth import cover_header, manager, group_manager
from ser.base import base_add, group_base_edit, group_base_info, \
    base_page
from ser.base_type import page


class problem_group_info(BaseModel):
    pid: int
    desId: Optional[int]
    name: Optional[str]
    score: int
    submitLimit: Optional[int]
    antiCheatingRate: Optional[float]


class problem_group(BaseModel):
    name: str
    type: int
    problemInfo: List[problem_group_info]
    manageGroupId: Optional[int]


class problem_group_id(BaseModel):
    gid: int


class problem_group_edit(problem_group_id):
    name: Optional[str]
    type: Optional[int]
    problemInfo: Optional[List[problem_group_info]]
    manageGroupId: Optional[int]


class searchKey(BaseModel):
    search: str


def ser_group_add(data: problem_group, SDUOJUserInfo=Depends(cover_header)):
    return base_add(data, SDUOJUserInfo)


def ser_group_edit(data: problem_group_edit,
                   SDUOJUserInfo=Depends(cover_header)):
    return group_base_edit(data, SDUOJUserInfo)


def ser_group_list(data: page, SDUOJUserInfo=Depends(cover_header)):
    return base_page(data, SDUOJUserInfo)


def ser_group_info(data: problem_group_id,
                   SDUOJUserInfo=Depends(cover_header)):
    return group_base_info(data, SDUOJUserInfo)


def ser_group_search(data: searchKey,
                     SDUOJUserInfo=Depends(cover_header)):
    manager(SDUOJUserInfo)
    return {
        "key": data.search,
        "username": SDUOJUserInfo["username"],
        "groups": SDUOJUserInfo["groups"]
    }
