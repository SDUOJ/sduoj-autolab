from typing import Optional, List

from fastapi import Depends
from pydantic import BaseModel

from auth import cover_header
from ser.base import group_base_info, group_base_edit, base_add
from ser.base_type import problem_pid
from ser.problem_group import problem_group_id


class fileInfo(BaseModel):
    name: str
    maxSizeMB: int
    fileType: str


class configInfo(BaseModel):
    name: str
    score: int
    answer: str


class subjective_config(BaseModel):
    maxCount: Optional[int]
    fileList: Optional[List[fileInfo]]
    judgeConfig: List[configInfo]


class subjective_base(BaseModel):
    type: int
    config: subjective_config
    description: str


class subjective_edit(problem_pid, subjective_base, problem_group_id):
    pass


class subjective_info(problem_pid, problem_group_id):
    pass


def ser_subjective_add(data: subjective_base,
                       SDUOJUserInfo=Depends(cover_header)):
    return base_add(data, SDUOJUserInfo)


def ser_subjective_edit(data: subjective_edit,
                        SDUOJUserInfo=Depends(cover_header)):
    return group_base_edit(data, SDUOJUserInfo)


def ser_subjective_info(data: subjective_info,
                        SDUOJUserInfo=Depends(cover_header)):
    return group_base_info(data, SDUOJUserInfo)
