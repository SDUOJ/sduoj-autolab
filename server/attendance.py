"""
考勤管理API路由
提供考勤相关的HTTP接口
"""
from fastapi import APIRouter, Depends, HTTPException

from model.sign_in_record import AttendanceModel
from ser.sign_in_record import (
    SignInRequest,
    LeaveRequest,
    LeaveReviewRequest,
    SignModeUpdateRequest,
    AttendanceListResponse,
    InitAttendanceRequest,
    AttendanceRecordRequest,
    TokenRequest,
    TokenVerifyRequest
)
from auth import tokenTool

router = APIRouter(prefix="/attendance", tags=["考勤管理"])


@router.post("/{course_id}/{schedule_id}/init", summary="初始化考勤")
async def init_attendance(
    course_id: int,
    schedule_id: int,
    data: InitAttendanceRequest,
    token_data: dict = Depends(tokenTool)
):
    """
    初始化考勤（获取或创建考勤记录并初始化学生名单）
    
    - **course_id**: 课程ID
    - **schedule_id**: 课程时间ID
    - **group_id**: 用户组ID
    """
    # 获取或创建考勤记录
    result = AttendanceModel.get_or_create_sign(course_id, schedule_id)
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    sg_id = result.data['sg_id']
    
    # 初始化学生名单
    result = await AttendanceModel.init_attendance_users(sg_id, data.group_id)
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"sg_id": sg_id, "msg": result.msg}


@router.get("/{sg_id}", summary="获取考勤名单", response_model=AttendanceListResponse)
async def get_attendance_list(
    sg_id: int,
    token_data: dict = Depends(tokenTool)
):
    """
    获取考勤名单（包含学生签到状态、统计信息等）
    
    - **sg_id**: 考勤ID
    """
    result = AttendanceModel.get_attendance_list(sg_id)
    
    if result.code != 0:
        raise HTTPException(status_code=404, detail=result.msg)
    
    return result.data


@router.post("/{sg_id}/sign-in", summary="学生签到/签退")
async def student_sign_in(
    sg_id: int,
    data: SignInRequest,
    token_data: dict = Depends(tokenTool)
):
    """
    学生签到或签退
    
    - **sg_id**: 考勤ID
    - **username**: 学生用户名
    - **sign_type**: 签到类型（0-签到, 1-签退）
    """
    result = AttendanceModel.student_sign_in(
        sg_id=sg_id,
        username=data.username,
        sign_type=data.sign_type
    )
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg}


@router.post("/{sg_id}/record", summary="批量记录考勤")
async def batch_record(
    sg_id: int,
    data: AttendanceRecordRequest,
    token_data: dict = Depends(tokenTool)
):
    """
    批量记录考勤状态

    - **sg_id**: 考勤ID
    - **records**: 记录列表
    """
    result = AttendanceModel.batch_record(sg_id, [r.dict() for r in data.records])

    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)

    return {"msg": result.msg}


@router.post("/{sg_id}/token", summary="生成签到token")
async def generate_token(
    sg_id: int,
    data: TokenRequest,
    token_data: dict = Depends(tokenTool)
):
    """
    生成签到 token

    - **sg_id**: 考勤ID
    - **username**: 学生用户名
    """
    result = AttendanceModel.generate_token(sg_id, data.username)

    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)

    return result.data


@router.post("/verify-token", summary="校验签到token")
async def verify_token(
    data: TokenVerifyRequest,
    token_data: dict = Depends(tokenTool)
):
    """
    校验签到 token

    - **token**: 签到token
    - **seat_number**: 座位号（可选）
    """
    result = AttendanceModel.verify_token(data.token, data.seat_number)

    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)

    return {"msg": result.msg}


@router.post("/{sg_id}/leave", summary="提交请假申请")
async def submit_leave(
    sg_id: int,
    username: str,
    data: LeaveRequest,
    token_data: dict = Depends(tokenTool)
):
    """
    学生提交请假申请
    
    - **sg_id**: 考勤ID
    - **username**: 学生用户名（通常从token获取）
    - **leave_message**: 请假理由
    - **leave_files**: 请假附件（文件ID列表，可选）
    """
    result = AttendanceModel.submit_leave(
        sg_id=sg_id,
        username=username,
        leave_message=data.leave_message,
        leave_files=data.leave_files
    )
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg}


@router.post("/{sg_id}/review-leave", summary="审批请假申请")
async def review_leave(
    sg_id: int,
    data: LeaveReviewRequest,
    token_data: dict = Depends(tokenTool)
):
    """
    教师审批请假申请
    
    - **sg_id**: 考勤ID
    - **username**: 学生用户名
    - **approved**: 是否批准（true-批准, false-拒绝）
    """
    result = AttendanceModel.review_leave(
        sg_id=sg_id,
        username=data.username,
        approved=data.approved
    )
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg}


@router.post("/{sg_id}/update-mode", summary="更新考勤模式")
async def update_sign_mode(
    sg_id: int,
    data: SignModeUpdateRequest,
    token_data: dict = Depends(tokenTool)
):
    """
    更新考勤模式
    
    - **sg_id**: 考勤ID
    - **sign_mode**: 考勤模式（0-签到+签退, 1-仅签到）
    """
    result = AttendanceModel.update_sign_mode(sg_id, data.sign_mode)
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg}


@router.post("/{sg_id}/mark-absence", summary="标记缺勤")
async def mark_absence(
    sg_id: int,
    username: str,
    token_data: dict = Depends(tokenTool)
):
    """
    标记学生为缺勤（用于批量设置未签到学生为缺勤）
    
    - **sg_id**: 考勤ID
    - **username**: 学生用户名
    """
    result = AttendanceModel.mark_absence(sg_id, username)
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg}
