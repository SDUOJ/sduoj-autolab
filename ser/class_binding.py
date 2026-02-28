"""
座位绑定序列化模型
定义座位相关API的请求和响应模型
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


# 座位分配请求
class SeatAssignRequest(BaseModel):
    username: str = Field(..., description="学生用户名")
    seat_number: int = Field(..., description="座位号")
    c_id: Optional[int] = Field(None, description="教室ID")


# 自动分配座位请求
class AutoAssignRequest(BaseModel):
    course_id: int = Field(..., description="课程ID")
    usernames: Optional[List[str]] = Field(None, description="需要分配座位的用户名列表")
    c_ids: Optional[List[int]] = Field(None, description="参与分配的教室ID列表")


# 座位响应
class SeatResponse(BaseModel):
    username: str = Field(..., description="学生用户名")
    seat_number: int = Field(..., description="座位号")
    c_id: Optional[int] = Field(None, description="教室ID")


# 座位分布图响应
class SeatMapResponse(BaseModel):
    course_id: int = Field(..., description="课程ID")
    seat_bindings: Dict[int, Any] = Field(..., description="座位绑定映射 {座位号: {username, c_id}}")
    classrooms: List[Dict[str, Any]] = Field(..., description="教室信息列表")
