from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class newRecord(BaseModel):
    bs_id: int
    u_name: str
    u_id: int
    token: str


class ScreenRecord(BaseModel):
    bs_type: str
    bs_id: int
    v_path: str
    u_id: int
    u_name: str
    token: str
    start_time: datetime
    modify_time: datetime
    cnt_frame: int


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


class PSList(BaseModel):
    psid: int
    name: str
    description: Optional[str]
    tm_start: Optional[str]
    tm_end: Optional[str]
    groupId: int
    tag: Optional[str]


class VideoList(BaseModel):
    u_id: int
    u_name: str
    token: str
    start_time: Optional[str]
    modify_time: Optional[str]


class ResponseModel(BaseModel):
    code: int
    message: str
    data: List[BaseModel]


class NormalResponse(BaseModel):
    code: int
    message: str
    data: str
