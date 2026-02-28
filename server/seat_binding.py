"""
座位管理API路由
提供座位分配相关的HTTP接口
"""
from fastapi import APIRouter, Depends, HTTPException

from auth import tokenTool, is_superadmin
from model.course import CourseModel
from model.class_binding import SeatBindingModel
from ser.class_binding import (
    SeatAssignRequest,
    AutoAssignRequest,
    SeatResponse,
    SeatMapResponse
)
from utils import api_response

router = APIRouter(prefix="/seat", tags=["座位管理"])


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


@router.post("/{course_id}/assign", summary="分配座位")
@api_response()
async def assign_seat(
    course_id: int,
    data: SeatAssignRequest,
    token_data: dict = Depends(tokenTool)
):
    """
    为学生分配座位
    
    - **course_id**: 课程ID
    - **username**: 学生用户名
    - **seat_number**: 座位号
    """
    _assert_course_manage_permission(course_id, token_data)

    result = SeatBindingModel.assign_seats(
        course_id=course_id,
        username=data.username,
        seat_number=data.seat_number,
        c_id=data.c_id
    )
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg}


@router.post("/auto-assign", summary="自动分配座位")
@api_response()
async def auto_assign_seats(
    data: AutoAssignRequest,
    token_data: dict = Depends(tokenTool)
):
    """
    自动为课程学生分配座位
    
    - **course_id**: 课程ID
    - **usernames**: 需要分配座位的用户名列表（可选，不传则按课程组内全部未分配学生）
    - **c_ids**: 参与分配的教室ID列表（可选，不传则使用课程绑定教室）
    """
    _assert_course_manage_permission(data.course_id, token_data)

    result = await SeatBindingModel.auto_assign_seats(
        course_id=data.course_id,
        usernames=data.usernames,
        c_ids=data.c_ids
    )
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg, **(result.data or {})}


@router.get("/{course_id}/auto-assign-options", summary="获取自动分配弹窗数据")
@api_response()
async def get_auto_assign_options(
    course_id: int,
    token_data: dict = Depends(tokenTool)
):
    _assert_course_manage_permission(course_id, token_data)

    result = await SeatBindingModel.get_auto_assign_options(course_id)

    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)

    return result.data


@router.get("/{course_id}/map", summary="获取座位分布图", response_model=SeatMapResponse)
@api_response()
async def get_seat_map(
    course_id: int,
    token_data: dict = Depends(tokenTool)
):
    """
    获取课程座位分布图
    
    - **course_id**: 课程ID
    """
    _assert_course_manage_permission(course_id, token_data)

    result = SeatBindingModel.get_seat_map(course_id)
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return result.data


@router.get("/{course_id}/user/{username}", summary="查询学生座位", response_model=SeatResponse)
@api_response()
async def get_user_seat(
    course_id: int,
    username: str,
    token_data: dict = Depends(tokenTool)
):
    """
    查询学生在课程中的座位
    
    - **course_id**: 课程ID
    - **username**: 学生用户名
    """
    _assert_course_manage_permission(course_id, token_data)

    result = SeatBindingModel.get_user_seat(course_id, username)
    
    if result.code != 0:
        raise HTTPException(status_code=404, detail=result.msg)
    
    return result.data


@router.post("/{course_id}/user/{username}/delete", summary="删除座位绑定")
@api_response()
async def remove_seat(
    course_id: int,
    username: str,
    token_data: dict = Depends(tokenTool)
):
    """
    删除学生座位绑定
    
    - **course_id**: 课程ID
    - **username**: 学生用户名
    """
    _assert_course_manage_permission(course_id, token_data)

    result = SeatBindingModel.remove_seat(course_id, username)
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg}
