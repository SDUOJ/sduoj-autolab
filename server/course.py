"""
课程管理API路由
提供课程相关的HTTP接口
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import List

from model.course import CourseModel
from ser.course import (
    CourseCreateRequest,
    CourseUpdateRequest,
    CourseResponse,
    CourseListRequest,
    CourseListResponse,
    ClassroomAssignRequest,
    TAAddRequest,
    TAResponse
)
from utils import Result
from auth import tokenTool

router = APIRouter(prefix="/course", tags=["课程管理"])


@router.post("/create", summary="创建课程")
async def create_course(
    data: CourseCreateRequest,
    token_data: dict = Depends(tokenTool)
):
    """
    创建新课程
    
    - **course_name**: 课程名称
    - **group_id**: 用户组ID
    - **tag**: 课程标签（授课/实验/考试/答疑）
    - **c_ids**: 教室ID列表（可选）
    - **ext_config**: 扩展配置（可选）
    """
    result = CourseModel.create_course(
        course_name=data.course_name,
        group_id=data.group_id,
        tag=data.tag,
        c_ids=data.c_ids,
        ext_config=data.ext_config
    )
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return result.data


@router.get("/{course_id}", summary="获取课程详情", response_model=CourseResponse)
async def get_course(
    course_id: int,
    token_data: dict = Depends(tokenTool)
):
    """
    获取指定课程的详细信息
    
    - **course_id**: 课程ID
    """
    result = CourseModel.get_course(course_id)
    
    if result.code != 0:
        raise HTTPException(status_code=404, detail=result.msg)
    
    return result.data


@router.post("/list", summary="查询课程列表", response_model=CourseListResponse)
async def list_courses(
    data: CourseListRequest,
    token_data: dict = Depends(tokenTool)
):
    """
    查询课程列表（支持分页和过滤）
    
    - **group_id**: 用户组ID过滤（可选）
    - **tag**: 课程标签过滤（可选）
    - **page_now**: 当前页码（默认1）
    - **page_size**: 每页数量（默认20，最大100）
    """
    result = CourseModel.list_courses(
        group_id=data.group_id,
        tag=data.tag,
        page_now=data.page_now,
        page_size=data.page_size
    )
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return result.data


@router.post("/{course_id}/update", summary="更新课程信息")
async def update_course(
    course_id: int,
    data: CourseUpdateRequest,
    token_data: dict = Depends(tokenTool)
):
    """
    更新课程信息
    
    - **course_id**: 课程ID
    - **course_name**: 课程名称（可选）
    - **tag**: 课程标签（可选）
    - **c_ids**: 教室ID列表（可选）
    - **ext_config**: 扩展配置（可选）
    """
    result = CourseModel.update_course(
        course_id=course_id,
        course_name=data.course_name,
        tag=data.tag,
        c_ids=data.c_ids,
        ext_config=data.ext_config
    )
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg}


@router.post("/{course_id}/delete", summary="删除课程")
async def delete_course(
    course_id: int,
    token_data: dict = Depends(tokenTool)
):
    """
    删除课程（级联删除相关数据）
    
    - **course_id**: 课程ID
    """
    result = CourseModel.delete_course(course_id)
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg}


@router.post("/{course_id}/assign-classrooms", summary="分配教室")
async def assign_classrooms(
    course_id: int,
    data: ClassroomAssignRequest,
    token_data: dict = Depends(tokenTool)
):
    """
    为课程分配教室
    
    - **course_id**: 课程ID
    - **c_ids**: 教室ID列表
    """
    result = CourseModel.assign_classrooms(course_id, data.c_ids)
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg}


@router.post("/{course_id}/add-ta", summary="添加助教")
async def add_ta(
    course_id: int,
    data: TAAddRequest,
    token_data: dict = Depends(tokenTool)
):
    """
    为课程添加助教
    
    - **course_id**: 课程ID
    - **ta_name**: 助教姓名
    - **ext_info**: 扩展信息（联系方式等）
    """
    result = CourseModel.add_ta(
        course_id=course_id,
        ta_name=data.ta_name,
        ext_info=data.ext_info
    )
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return result.data


@router.get("/{course_id}/tas", summary="查询助教列表", response_model=List[TAResponse])
async def list_tas(
    course_id: int,
    token_data: dict = Depends(tokenTool)
):
    """
    查询课程助教列表
    
    - **course_id**: 课程ID
    """
    result = CourseModel.list_tas(course_id)
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return result.data


@router.post("/ta/{ta_id}/delete", summary="删除助教")
async def remove_ta(
    ta_id: int,
    token_data: dict = Depends(tokenTool)
):
    """
    删除助教
    
    - **ta_id**: 助教ID
    """
    result = CourseModel.remove_ta(ta_id)
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg}
