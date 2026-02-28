"""
考勤管理API路由
提供考勤相关的HTTP接口
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional

from model.sign_in_record import AttendanceModel
from model.course import CourseModel
from ser.sign_in_record import (
    SignInRequest,
    LeaveRequest,
    LeaveReviewRequest,
    SignModeUpdateRequest,
    AttendanceListResponse,
    InitAttendanceRequest,
    AttendanceRecordRequest,
    TokenRequest,
    TokenVerifyRequest,
    StudentAttendanceRecordsResponse,
)
from auth import tokenTool, is_superadmin
from utils import api_response

router = APIRouter(prefix="/attendance", tags=["考勤管理"])


def _assert_course_manage_permission(course_id: int, token_data: dict):
    result = CourseModel.check_manage_permission(
        course_id=course_id,
        username=token_data.get("username", ""),
        user_groups=token_data.get("groups", []),
        is_superadmin_user=is_superadmin(token_data)
    )
    if result.code != 0:
        if result.msg == "课程不存在":
            raise HTTPException(status_code=404, detail=result.msg)
        raise HTTPException(status_code=403, detail=result.msg)


def _assert_sign_manage_permission(sg_id: int, token_data: dict):
    sign_result = AttendanceModel.get_sign_course_id(sg_id)
    if sign_result.code != 0:
        raise HTTPException(status_code=404, detail=sign_result.msg)
    _assert_course_manage_permission(sign_result.data["course_id"], token_data)


@router.post("/{course_id}/{schedule_id}/init", summary="初始化考勤")
@api_response()
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
    manage_permission_result = CourseModel.check_manage_permission(
        course_id=course_id,
        username=token_data.get("username", ""),
        user_groups=token_data.get("groups", []),
        is_superadmin_user=is_superadmin(token_data)
    )
    if manage_permission_result.code != 0:
        # 学生端兜底：允许课程组成员初始化（仅限本人所在组）
        course_result = CourseModel.get_course(course_id)
        if course_result.code != 0:
            raise HTTPException(status_code=404, detail=course_result.msg)
        course_group_id = course_result.data.get("group_id")
        if int(course_group_id) != int(data.group_id):
            raise HTTPException(status_code=403, detail="Permission Denial")
        if int(data.group_id) not in [int(g) for g in token_data.get("groups", [])]:
            raise HTTPException(status_code=403, detail="Permission Denial")

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
@api_response()
async def get_attendance_list(
    sg_id: int,
    token_data: dict = Depends(tokenTool)
):
    """
    获取考勤名单（包含学生签到状态、统计信息等）
    
    - **sg_id**: 考勤ID
    """
    _assert_sign_manage_permission(sg_id, token_data)

    result = AttendanceModel.get_attendance_list(sg_id)
    
    if result.code != 0:
        raise HTTPException(status_code=404, detail=result.msg)
    
    return result.data


@router.post("/{sg_id}/sign-in", summary="学生签到/签退")
@api_response()
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
@api_response()
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
    _assert_sign_manage_permission(sg_id, token_data)

    result = AttendanceModel.batch_record(sg_id, [r.dict() for r in data.records])

    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)

    return {"msg": result.msg}


@router.post("/{sg_id}/token", summary="生成签到token")
@api_response()
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
    _assert_sign_manage_permission(sg_id, token_data)

    result = AttendanceModel.generate_token(sg_id, data.username)

    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)

    return result.data


@router.post("/verify-token", summary="校验签到token")
@api_response()
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
@api_response()
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
    requester_username = token_data.get("username")
    if requester_username != username:
        _assert_sign_manage_permission(sg_id, token_data)

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
@api_response()
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
    _assert_sign_manage_permission(sg_id, token_data)

    result = AttendanceModel.review_leave(
        sg_id=sg_id,
        username=data.username,
        approved=data.approved
    )
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg}


@router.post("/{sg_id}/update-mode", summary="更新考勤模式")
@api_response()
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
    _assert_sign_manage_permission(sg_id, token_data)

    result = AttendanceModel.update_sign_mode(sg_id, data.sign_mode)
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg}


@router.post("/{sg_id}/mark-absence", summary="标记缺勤")
@api_response()
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
    _assert_sign_manage_permission(sg_id, token_data)

    result = AttendanceModel.mark_absence(sg_id, username)
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg}


@router.get("/student/{username}/records", summary="查询学生考勤记录", response_model=StudentAttendanceRecordsResponse)
@api_response(paged=True, rows_key="records")
async def get_student_records(
    username: str,
    course_id: Optional[int] = None,
    attendance_tag: Optional[str] = None,
    pageNow: int = 1,
    pageSize: int = 20,
    token_data: dict = Depends(tokenTool)
):
    """
    查询学生全部考勤记录（用户端）

    - **username**: 学生用户名
    - **course_id**: 课程ID过滤（可选）
    - **attendance_tag**: 标签过滤（future/signed/absent/leave_approved，可选）
    - **pageNow**: 页码（默认1）
    - **pageSize**: 每页数量（默认20）
    """
    requester_username = token_data.get("username")
    if requester_username != username:
        if course_id is None:
            if not is_superadmin(token_data):
                raise HTTPException(status_code=403, detail="Permission Denial")
        else:
            permission_result = CourseModel.check_manage_permission(
                course_id=course_id,
                username=requester_username or "",
                user_groups=token_data.get("groups", []),
                is_superadmin_user=is_superadmin(token_data)
            )
            if permission_result.code != 0:
                if permission_result.msg == "课程不存在":
                    raise HTTPException(status_code=404, detail=permission_result.msg)
                raise HTTPException(status_code=403, detail="Permission Denial")

    result = AttendanceModel.get_student_records(
        username=username,
        user_groups=token_data.get("groups", []) if requester_username == username else None,
        course_id=course_id,
        attendance_tag=attendance_tag,
        page_now=pageNow,
        page_size=pageSize
    )

    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)

    return result.data
