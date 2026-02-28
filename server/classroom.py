"""
教室管理API路由
"""
from fastapi import APIRouter, Depends, HTTPException

from auth import tokenTool
from model.classroom import ClassroomModel
from ser.classroom import (
    ClassroomCreateRequest,
    ClassroomListRequest,
    ClassroomUpdateRequest,
)
from utils import api_response


router = APIRouter(prefix="/class", tags=["教室管理"])


@router.post("/create", summary="创建教室")
@api_response()
async def create_classroom(
    data: ClassroomCreateRequest,
    token_data: dict = Depends(tokenTool)
):
    result = ClassroomModel.create_classroom(
        c_name=data.c_name,
        address=data.address,
        c_seat_num=data.c_seat_num,
        ext_config=data.ext_config
    )
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    return result.data


@router.get("/{c_id}", summary="查询教室")
@api_response()
async def get_classroom(
    c_id: int,
    token_data: dict = Depends(tokenTool)
):
    result = ClassroomModel.get_classroom(c_id)
    if result.code != 0:
        raise HTTPException(status_code=404, detail=result.msg)
    return result.data


@router.post("/list", summary="教室列表（分页）")
@api_response(paged=True, rows_key="rows")
async def list_classrooms(
    data: ClassroomListRequest,
    token_data: dict = Depends(tokenTool)
):
    """
    统一分页结构：
    - pageIndex
    - pageSize
    - totalNum
    - rows
    """
    result = ClassroomModel.list_classrooms(
        page_now=data.pageNow,
        page_size=data.pageSize,
        keyword=data.keyword
    )
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    return result.data


@router.post("/{c_id}/update", summary="更新教室")
@api_response()
async def update_classroom(
    c_id: int,
    data: ClassroomUpdateRequest,
    token_data: dict = Depends(tokenTool)
):
    result = ClassroomModel.update_classroom(
        c_id=c_id,
        c_name=data.c_name,
        address=data.address,
        c_seat_num=data.c_seat_num,
        ext_config=data.ext_config
    )
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    return {"msg": result.msg}


@router.post("/{c_id}/delete", summary="删除教室")
@api_response()
async def delete_classroom(
    c_id: int,
    token_data: dict = Depends(tokenTool)
):
    result = ClassroomModel.delete_classroom(c_id)
    if result.code != 0:
        raise HTTPException(status_code=400, detail=result.msg)
    return {"msg": result.msg}
