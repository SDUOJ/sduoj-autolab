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
    TAResponse,
    TAStudentBindRequest,
    UserCourseListRequest,
    CourseTimeItemAddRequest,
    CourseTimeItemUpdateRequest,
    TAUpdateRequest,
)
from utils import api_response
from auth import tokenTool, is_superadmin

router = APIRouter(prefix="/course", tags=["课程管理"])


def _assert_manage_permission(course_id: int, token_data: dict):
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


@router.post("/create", summary="创建课程")
@api_response()
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
        ext_config=data.ext_config,
        manager_groups=data.manager_groups,
        creator_username=token_data.get("username")
    )
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return result.data


@router.get("/{course_id}", summary="获取课程详情", response_model=CourseResponse)
@api_response()
async def get_course(
    course_id: int,
    token_data: dict = Depends(tokenTool)
):
    """
    获取指定课程的详细信息
    
    - **course_id**: 课程ID
    """
    _assert_manage_permission(course_id, token_data)

    result = CourseModel.get_course(course_id)
    
    if result.code != 0:
        raise HTTPException(status_code=404, detail=result.msg)
    
    return result.data


@router.post("/list", summary="查询课程列表", response_model=CourseListResponse)
@api_response(paged=True, rows_key="courses")
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
        page_size=data.page_size,
        viewer_username=token_data.get("username"),
        viewer_groups=token_data.get("groups", []),
        viewer_is_superadmin=is_superadmin(token_data)
    )
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return result.data


@router.post("/{course_id}/update", summary="更新课程信息")
@api_response()
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
    _assert_manage_permission(course_id, token_data)

    result = CourseModel.update_course(
        course_id=course_id,
        course_name=data.course_name,
        group_id=data.group_id,
        tag=data.tag,
        c_ids=data.c_ids,
        ext_config=data.ext_config,
        manager_groups=data.manager_groups
    )
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg}


@router.post("/{course_id}/delete", summary="删除课程")
@api_response()
async def delete_course(
    course_id: int,
    token_data: dict = Depends(tokenTool)
):
    """
    删除课程（级联删除相关数据）
    
    - **course_id**: 课程ID
    """
    _assert_manage_permission(course_id, token_data)

    result = CourseModel.delete_course(course_id)
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg}


@router.post("/{course_id}/assign-classrooms", summary="分配教室")
@api_response()
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
    _assert_manage_permission(course_id, token_data)

    result = CourseModel.assign_classrooms(course_id, data.c_ids)
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg}


@router.post("/{course_id}/add-ta", summary="添加助教")
@api_response()
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
    _assert_manage_permission(course_id, token_data)

    result = CourseModel.add_ta(
        course_id=course_id,
        ta_name=data.ta_name,
        ext_info=data.ext_info,
        usernames=data.usernames
    )
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return result.data


@router.get("/{course_id}/tas", summary="查询助教列表", response_model=List[TAResponse])
@api_response()
async def list_tas(
    course_id: int,
    token_data: dict = Depends(tokenTool)
):
    """
    查询课程助教列表
    
    - **course_id**: 课程ID
    """
    _assert_manage_permission(course_id, token_data)

    result = CourseModel.list_tas(course_id)
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return result.data


@router.post("/ta/{ta_id}/delete", summary="删除助教")
@api_response()
async def remove_ta(
    ta_id: int,
    token_data: dict = Depends(tokenTool)
):
    """
    删除助教
    
    - **ta_id**: 助教ID
    """
    ta_course_result = CourseModel.get_ta_course_id(ta_id)
    if ta_course_result.code != 0:
        raise HTTPException(status_code=404, detail=ta_course_result.msg)
    _assert_manage_permission(ta_course_result.data["course_id"], token_data)

    result = CourseModel.remove_ta(ta_id)
    
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    
    return {"msg": result.msg}


@router.post("/{course_id}/ta/bind-students", summary="绑定学生到助教")
@api_response()
async def bind_students_to_ta(
    course_id: int,
    data: TAStudentBindRequest,
    token_data: dict = Depends(tokenTool)
):
    """
    为课程中的学生批量绑定助教（每个学生在课程内仅绑定一个助教）

    - **course_id**: 课程ID
    - **TA_id**: 助教ID
    - **usernames**: 学生用户名列表
    """
    _assert_manage_permission(course_id, token_data)

    result = CourseModel.bind_students_to_ta(
        course_id=course_id,
        ta_id=data.TA_id,
        usernames=data.usernames
    )

    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)

    return result.data


@router.post("/{course_id}/ta/{ta_id}/update", summary="更新助教")
@api_response()
async def update_ta(
    course_id: int,
    ta_id: int,
    data: TAUpdateRequest,
    token_data: dict = Depends(tokenTool)
):
    _assert_manage_permission(course_id, token_data)

    result = CourseModel.update_ta(
        course_id=course_id,
        ta_id=ta_id,
        ta_name=data.ta_name,
        ext_info=data.ext_info,
        usernames=data.usernames
    )

    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)

    return result.data


@router.get("/{course_id}/students", summary="查询课程学生")
@api_response()
async def list_course_students(
    course_id: int,
    token_data: dict = Depends(tokenTool)
):
    _assert_manage_permission(course_id, token_data)

    result = await CourseModel.list_group_students(course_id)

    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)

    return result.data


@router.get("/{course_id}/times", summary="查询课程时间列表")
@api_response()
async def list_course_times(
    course_id: int,
    token_data: dict = Depends(tokenTool)
):
    _assert_manage_permission(course_id, token_data)

    result = CourseModel.list_course_times(course_id)

    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)

    return result.data


@router.post("/{course_id}/times/add", summary="新增课程时间")
@api_response()
async def add_course_time(
    course_id: int,
    data: CourseTimeItemAddRequest,
    token_data: dict = Depends(tokenTool)
):
    _assert_manage_permission(course_id, token_data)

    result = CourseModel.add_course_time(
        course_id=course_id,
        start_time=data.start_time,
        end_time=data.end_time,
        auto_create_sign=data.auto_create_sign,
        course_content=data.course_content,
        course_homework=data.course_homework,
        course_materials=data.course_materials
    )

    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)

    return result.data


@router.post("/{course_id}/times/{time_id}/update", summary="更新课程时间")
@api_response()
async def update_course_time(
    course_id: int,
    time_id: int,
    data: CourseTimeItemUpdateRequest,
    token_data: dict = Depends(tokenTool)
):
    _assert_manage_permission(course_id, token_data)

    result = CourseModel.update_course_time(
        course_id=course_id,
        time_id=time_id,
        start_time=data.start_time,
        end_time=data.end_time,
        auto_create_sign=data.auto_create_sign,
        course_content=data.course_content,
        course_homework=data.course_homework,
        course_materials=data.course_materials
    )

    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)

    return result.data


@router.post("/{course_id}/times/{time_id}/delete", summary="删除课程时间")
@api_response()
async def delete_course_time(
    course_id: int,
    time_id: int,
    token_data: dict = Depends(tokenTool)
):
    _assert_manage_permission(course_id, token_data)

    result = CourseModel.remove_course_time(course_id=course_id, time_id=time_id)

    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)

    return {"msg": result.msg}


@router.post("/my/list", summary="用户端课程列表")
@api_response(paged=True, rows_key="courses")
async def list_my_courses(
    data: UserCourseListRequest,
    token_data: dict = Depends(tokenTool)
):
    """
    获取当前用户课程列表（含课程时间、课程地点、座位号、绑定助教）

    - **tag**: 课程标签过滤（可选）
    - **page_now**: 当前页码（默认1）
    - **page_size**: 每页数量（默认20）
    """
    username = token_data.get("username")
    groups = token_data.get("groups", [])
    result = CourseModel.list_user_courses(
        requester_username=username,
        requester_groups=groups,
        tag=data.tag,
        page_now=data.page_now,
        page_size=data.page_size,
        group_id=data.group_id,
        target_username=data.target_username,
        requester_is_superadmin=is_superadmin(token_data)
    )

    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)

    return result.data
