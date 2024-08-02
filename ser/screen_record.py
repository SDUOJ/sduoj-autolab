from typing import List

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

class getVideoList(BaseModel):
    u_id: int
    bs_type: int
    bs_id: int

