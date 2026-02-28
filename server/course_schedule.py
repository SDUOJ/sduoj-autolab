"""
课程时间API路由
提供课程时间相关的HTTP接口
"""
from fastapi import APIRouter, Depends, HTTPException

from auth import tokenTool, is_superadmin
from model.course import CourseModel
from model.course_schedule import CourseScheduleModel
from ser.course_schedule import (
    ScheduleAddRequest,
    ScheduleUpdateRequest,
    ScheduleResponse,
    ScheduleListRequest,
    ScheduleListResponse
)
from utils import api_response

router = APIRouter(prefix="/schedule", tags=["课程时间管理"])


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


def _assert_schedule_manage_permission(schedule_id: int, token_data: dict):
    schedule_result = CourseScheduleModel.get_schedule(schedule_id)
    if schedule_result.code != 0:
        raise HTTPException(status_code=404, detail=schedule_result.msg)
    _assert_course_manage_permission(schedule_result.data["course_id"], token_data)


@router.post("/add", summary="添加课程时间")
@api_response()
async def add_schedule(
    data: ScheduleAddRequest,
    token_data: dict = Depends(tokenTool)
):
    """
    添加课程时间
    
    - **course_id**: 课程ID
    - **sequence**: 课程序号
    - **start_time**: 开始时间
    - **end_time**: 结束时间
    - **course_content**: 课程内容（可选）
    - **course_materials**: 课程资料文件ID列表（可选）
    - **course_homework**: 课程作业（可选）
    - **sg_id**: 座位组ID（可选）
    - **auto_create_sign**: 是否自动创建考勤记录（默认True）
    """
    _assert_course_manage_permission(data.course_id, token_data)

    result = CourseScheduleModel.add_schedule(
        course_id=data.course_id,
        sequence=data.sequence,
        start_time=data.start_time,
        end_time=data.end_time,
        course_content=data.course_content,
        course_materials=data.course_materials,
        course_homework=data.course_homework,
        sg_id=data.sg_id,
        auto_create_sign=data.auto_create_sign
    )
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return result.data


@router.get("/{schedule_id}", summary="获取课程时间详情", response_model=ScheduleResponse)
@api_response()
async def get_schedule(
    schedule_id: int,
    token_data: dict = Depends(tokenTool)
):
    """
    获取指定课程时间的详细信息
    
    - **schedule_id**: 课程时间ID
    """
    _assert_schedule_manage_permission(schedule_id, token_data)

    result = CourseScheduleModel.get_schedule(schedule_id)
    
    if result.code != 0:
        raise HTTPException(status_code=404, detail=result.msg)
    
    return result.data


@router.post("/list", summary="查询课程时间列表", response_model=ScheduleListResponse)
@api_response(paged=True, rows_key="schedules")
async def list_schedules(
    data: ScheduleListRequest,
    token_data: dict = Depends(tokenTool)
):
    """
    查询课程时间列表（支持分页和过滤）
    
    - **course_id**: 课程ID过滤（可选）
    - **page_now**: 当前页码（默认1）
    - **page_size**: 每页数量（默认50，最大200）
    """
    if data.course_id is not None:
        _assert_course_manage_permission(data.course_id, token_data)
    elif not is_superadmin(token_data):
        raise HTTPException(status_code=403, detail="Permission Denial")

    result = CourseScheduleModel.list_schedules(
        course_id=data.course_id,
        page_now=data.page_now,
        page_size=data.page_size
    )
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return result.data


@router.post("/{schedule_id}/update", summary="更新课程时间信息")
@api_response()
async def update_schedule(
    schedule_id: int,
    data: ScheduleUpdateRequest,
    token_data: dict = Depends(tokenTool)
):
    """
    更新课程时间信息
    
    - **schedule_id**: 课程时间ID
    - **sequence**: 课程序号（可选）
    - **start_time**: 开始时间（可选）
    - **end_time**: 结束时间（可选）
    - **course_content**: 课程内容（可选）
    - **course_materials**: 课程资料文件ID列表（可选）
    - **course_homework**: 课程作业（可选）
    - **sg_id**: 座位组ID（可选）
    """
    _assert_schedule_manage_permission(schedule_id, token_data)

    result = CourseScheduleModel.update_schedule(
        schedule_id=schedule_id,
        sequence=data.sequence,
        start_time=data.start_time,
        end_time=data.end_time,
        course_content=data.course_content,
        course_materials=data.course_materials,
        course_homework=data.course_homework,
        sg_id=data.sg_id
    )
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg}


@router.post("/{schedule_id}/delete", summary="删除课程时间")
@api_response()
async def delete_schedule(
    schedule_id: int,
    token_data: dict = Depends(tokenTool)
):
    """
    删除课程时间（级联删除相关考勤记录）
    
    - **schedule_id**: 课程时间ID
    """
    _assert_schedule_manage_permission(schedule_id, token_data)

    result = CourseScheduleModel.delete_schedule(schedule_id)
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg}
