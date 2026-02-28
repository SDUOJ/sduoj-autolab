"""
教室管理序列化模型
"""
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ClassroomCreateRequest(BaseModel):
    c_name: str = Field(..., description="教室名称")
    address: str = Field(..., description="教室地点")
    c_seat_num: int = Field(..., description="座位数量", ge=1)
    ext_config: Optional[Dict[str, Any]] = Field(None, description="扩展配置")


class ClassroomUpdateRequest(BaseModel):
    c_name: Optional[str] = Field(None, description="教室名称")
    address: Optional[str] = Field(None, description="教室地点")
    c_seat_num: Optional[int] = Field(None, description="座位数量", ge=1)
    ext_config: Optional[Dict[str, Any]] = Field(None, description="扩展配置")


class ClassroomListRequest(BaseModel):
    pageNow: int = Field(1, description="当前页码", ge=1)
    pageSize: int = Field(20, description="每页数量", ge=1, le=200)
    keyword: Optional[str] = Field(None, description="按教室名/地点搜索")

