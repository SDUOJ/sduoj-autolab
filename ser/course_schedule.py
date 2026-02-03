"""
课程时间序列化模型
定义课程时间相关API的请求和响应模型
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


# 课程时间添加请求
class ScheduleAddRequest(BaseModel):
    course_id: int = Field(..., description="课程ID")
    sequence: int = Field(..., description="课程序号")
    start_time: datetime = Field(..., description="开始时间")
    end_time: datetime = Field(..., description="结束时间")
    course_content: Optional[str] = Field(None, description="课程内容")
    course_materials: Optional[List[str]] = Field(None, description="课程资料（文件ID列表）")
    course_homework: Optional[str] = Field(None, description="课程作业")
    sg_id: Optional[int] = Field(None, description="座位组ID")
    auto_create_sign: bool = Field(True, description="是否自动创建考勤记录")


# 课程时间更新请求
class ScheduleUpdateRequest(BaseModel):
    sequence: Optional[int] = Field(None, description="课程序号")
    start_time: Optional[datetime] = Field(None, description="开始时间")
    end_time: Optional[datetime] = Field(None, description="结束时间")
    course_content: Optional[str] = Field(None, description="课程内容")
    course_materials: Optional[List[str]] = Field(None, description="课程资料（文件ID列表）")
    course_homework: Optional[str] = Field(None, description="课程作业")
    sg_id: Optional[int] = Field(None, description="座位组ID")


# 课程时间响应
class ScheduleResponse(BaseModel):
    schedule_id: int = Field(..., description="课程时间ID")
    course_id: int = Field(..., description="课程ID")
    sequence: int = Field(..., description="课程序号")
    start_time: str = Field(..., description="开始时间")
    end_time: str = Field(..., description="结束时间")
    course_content: Optional[str] = Field(None, description="课程内容")
    course_materials: Optional[List[str]] = Field(None, description="课程资料")
    course_homework: Optional[str] = Field(None, description="课程作业")
    sg_id: Optional[int] = Field(None, description="座位组ID")


# 课程时间列表查询请求
class ScheduleListRequest(BaseModel):
    course_id: Optional[int] = Field(None, description="课程ID过滤")
    page_now: int = Field(1, description="当前页码", ge=1)
    page_size: int = Field(50, description="每页数量", ge=1, le=200)


# 课程时间列表响应
class ScheduleListResponse(BaseModel):
    total: int = Field(..., description="总数")
    page_now: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页数量")
    schedules: List[ScheduleResponse] = Field(..., description="课程时间列表")
