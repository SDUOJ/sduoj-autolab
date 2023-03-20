from typing import List

from fastapi import Depends
from pydantic import BaseModel

from auth import cover_header
from ser.base import base_add, group_base_edit, group_base_info
from ser.base_type import problem_pid
from ser.problem_group import problem_group_id


class objective_content(BaseModel):
    description: str
    choice: List[str]


class objective_base(BaseModel):
    type: int
    content: objective_content
    answer: List[str]


class objective_edit(objective_base, problem_pid, problem_group_id):
    pass


class objective_info(problem_pid, problem_group_id):
    pass


def ser_objective_add(data: objective_base,
                      SDUOJUserInfo=Depends(cover_header)):
    return base_add(data, SDUOJUserInfo)


def ser_objective_edit(data: objective_edit,
                       SDUOJUserInfo=Depends(cover_header)):
    return group_base_edit(data, SDUOJUserInfo)


def ser_objective_info(data: objective_info,
                       SDUOJUserInfo=Depends(cover_header)):
    return group_base_info(data, SDUOJUserInfo)
