"""
考勤序列化模型
定义考勤相关API的请求和响应模型
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


# 签到/签退请求
class SignInRequest(BaseModel):
    username: str = Field(..., description="学生用户名")
    sign_type: int = Field(..., description="签到类型: 0-签到, 1-签退")


# 请假申请请求
class LeaveRequest(BaseModel):
    leave_message: str = Field(..., description="请假理由")
    leave_files: Optional[List[str]] = Field(None, description="请假附件（文件ID列表）")


# 请假审批请求
class LeaveReviewRequest(BaseModel):
    username: str = Field(..., description="学生用户名")
    approved: bool = Field(..., description="是否批准")


# 考勤模式更新请求
class SignModeUpdateRequest(BaseModel):
    sign_mode: int = Field(..., description="考勤模式: 0-签到+签退, 1-仅签到")


# 批量考勤记录项
class AttendanceRecordItem(BaseModel):
    username: str = Field(..., description="学生用户名")
    status: int = Field(..., description="状态: 0-无记录, 1-出勤, 2-缺勤, 3-迟到/早退, 4-请假已批准, 5-请假申请中")
    seat_number: Optional[int] = Field(None, description="座位号")


# 批量考勤记录请求
class AttendanceRecordRequest(BaseModel):
    records: List[AttendanceRecordItem] = Field(..., description="考勤记录列表")


# 二维码 token 请求
class TokenRequest(BaseModel):
    username: str = Field(..., description="学生用户名")


# 二维码 token 校验请求
class TokenVerifyRequest(BaseModel):
    token: str = Field(..., description="签到 token")
    seat_number: Optional[int] = Field(None, description="座位号")


# 考勤学生记录
class AttendanceStudentRecord(BaseModel):
    username: str = Field(..., description="学生用户名")
    status: int = Field(..., description="状态: 0-无记录, 1-出勤, 2-缺勤, 3-迟到/早退, 4-请假已批准, 5-请假申请中")
    seat_number: Optional[int] = Field(None, description="座位号")
    check_in_time: Optional[str] = Field(None, description="签到时间")
    check_out_time: Optional[str] = Field(None, description="签退时间")
    leave_message: Optional[str] = Field(None, description="请假理由")
    leave_files: Optional[List[str]] = Field(None, description="请假附件")
    leave_status: Optional[int] = Field(None, description="请假状态: NULL/0-申请中, 1-批准, 2-拒绝")


# 考勤列表响应
class AttendanceListResponse(BaseModel):
    sg_id: int = Field(..., description="考勤ID")
    course_id: int = Field(..., description="课程ID")
    schedule_id: int = Field(..., description="课程时间ID")
    sign_mode: int = Field(..., description="考勤模式")
    course_time: Dict[str, Optional[str]] = Field(..., description="课程时间")
    students: List[AttendanceStudentRecord] = Field(..., description="学生列表")
    statistics: Dict[str, int] = Field(..., description="统计信息")


# 初始化考勤请求
class InitAttendanceRequest(BaseModel):
    group_id: int = Field(..., description="用户组ID")
