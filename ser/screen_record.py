from auth import cover_header
from ser.base import base_add, group_base_edit, group_base_info
from fastapi import Depends
from pydantic import BaseModel, Field

class newRecord(BaseModel):
    bs_type: int
    bs_id: int
    u_name: str
    u_id: int
    token: str

class newFrame(BaseModel):
    token: str
    pic: bytes

class videoList(BaseModel):
    bs_type: int
    bs_id: int
    u_id: int

class videoInfo(BaseModel):
    token: str
    start_time: str
    modify_time: str