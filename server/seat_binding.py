"""
座位管理API路由
提供座位分配相关的HTTP接口
"""
from fastapi import APIRouter, Depends, HTTPException

from model.class_binding import SeatBindingModel
from ser.class_binding import (
    SeatAssignRequest,
    AutoAssignRequest,
    SeatResponse,
    SeatMapResponse
)
from auth import tokenTool

router = APIRouter(prefix="/seat", tags=["座位管理"])


@router.post("/{course_id}/assign", summary="分配座位")
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
async def auto_assign_seats(
    data: AutoAssignRequest,
    token_data: dict = Depends(tokenTool)
):
    """
    自动为课程学生分配座位
    
    - **course_id**: 课程ID
    - **group_id**: 用户组ID
    """
    result = await SeatBindingModel.auto_assign_seats(
        course_id=data.course_id,
        group_id=data.group_id
    )
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg}


@router.get("/{course_id}/map", summary="获取座位分布图", response_model=SeatMapResponse)
async def get_seat_map(
    course_id: int,
    token_data: dict = Depends(tokenTool)
):
    """
    获取课程座位分布图
    
    - **course_id**: 课程ID
    """
    result = SeatBindingModel.get_seat_map(course_id)
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return result.data


@router.get("/{course_id}/user/{username}", summary="查询学生座位", response_model=SeatResponse)
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
    result = SeatBindingModel.get_user_seat(course_id, username)
    
    if result.code != 0:
        raise HTTPException(status_code=404, detail=result.msg)
    
    return result.data


@router.post("/{course_id}/user/{username}/delete", summary="删除座位绑定")
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
    result = SeatBindingModel.remove_seat(course_id, username)
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg}
