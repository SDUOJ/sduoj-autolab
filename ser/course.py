"""
课程管理序列化模型
定义课程相关API的请求和响应模型
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


# 课程创建请求
class CourseCreateRequest(BaseModel):
    course_name: str = Field(..., description="课程名称")
    group_id: int = Field(..., description="用户组ID")
    tag: str = Field(..., description="课程标签: 授课/实验/考试/答疑")
    c_ids: Optional[List[int]] = Field(None, description="教室ID列表")
    ext_config: Optional[Dict[str, Any]] = Field(None, description="扩展配置")


# 课程更新请求
class CourseUpdateRequest(BaseModel):
    course_name: Optional[str] = Field(None, description="课程名称")
    tag: Optional[str] = Field(None, description="课程标签")
    c_ids: Optional[List[int]] = Field(None, description="教室ID列表")
    ext_config: Optional[Dict[str, Any]] = Field(None, description="扩展配置")


# 课程响应
class CourseResponse(BaseModel):
    course_id: int = Field(..., description="课程ID")
    course_name: str = Field(..., description="课程名称")
    group_id: int = Field(..., description="用户组ID")
    tag: str = Field(..., description="课程标签")
    c_ids: Optional[List[int]] = Field(None, description="教室ID列表")
    ext_config: Optional[Dict[str, Any]] = Field(None, description="扩展配置")
    create_time: str = Field(..., description="创建时间")


# 课程列表查询请求
class CourseListRequest(BaseModel):
    group_id: Optional[int] = Field(None, description="用户组ID过滤")
    tag: Optional[str] = Field(None, description="课程标签过滤")
    page_now: int = Field(1, description="当前页码", ge=1)
    page_size: int = Field(20, description="每页数量", ge=1, le=100)


# 课程列表响应
class CourseListResponse(BaseModel):
    total: int = Field(..., description="总数")
    page_now: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页数量")
    courses: List[CourseResponse] = Field(..., description="课程列表")


# 教室分配请求
class ClassroomAssignRequest(BaseModel):
    c_ids: List[int] = Field(..., description="教室ID列表")


# 助教添加请求
class TAAddRequest(BaseModel):
    ta_name: str = Field(..., description="助教姓名")
    ext_info: Optional[Dict[str, Any]] = Field(None, description="扩展信息（联系方式等）")


# 助教响应
class TAResponse(BaseModel):
    TA_id: int = Field(..., description="助教ID")
    TA_name: str = Field(..., description="助教姓名")
    course_id: int = Field(..., description="课程ID")
    ext_info: Optional[Dict[str, Any]] = Field(None, description="扩展信息")
