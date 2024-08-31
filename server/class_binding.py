from fastapi import APIRouter, Depends, UploadFile, File

from model.class_binding import classBindingModel
from ser.class_binding import createClassroom, classroomEditType, userSeatListType
from utils import makeResponse

router = APIRouter(
    prefix="/classroom"
)


# /server/class_binding.py------------------------------------------


# 新建教室
@router.post("/createClassroom")
async def create_classroom(data: dict = Depends(createClassroom)):
    db = classBindingModel()
    db.classroom_create(data)
    return makeResponse(None)


# 修改教室信息
@router.post("/editClassroom")
async def edit_classroom(data: dict = classroomEditType):
    db = classBindingModel()
    db.classroom_edit(data)
    return makeResponse(None)


# 获取当前教室信息（小程序）
@router.get("/classroomInfo")
async def get_classroom_info(c_name: str):
    db = classBindingModel()
    res = db.get_classroom_info({'c_name': c_name})
    return res


# 查找所有可用教室
@router.get("/classroomListInfo")
async def get_available_classroom(pageNow: int = None, pageSize: int = None):
    # pageNow: 当前页数  pageSize: 页数大小
    db = classBindingModel()
    res = db.get_available_classroom(pageNow, pageSize)
    return makeResponse(res)


# 新建用户座位名单
@router.post("/seatList/create")
async def create_seat_list(data: dict = userSeatListType):
    db = classBindingModel()
    db.create_seat_list(data)
    return makeResponse(None)


# 编辑用户座位名单和教室座位绑定表
@router.post("/seatList/edit")
async def edit_seat_list(data: dict = userSeatListType):
    db = classBindingModel()
    db.edit_seat_list(data)
    return makeResponse(None)


# 查询名单列表oj_user_seat_list
@router.get("/seatList/all/listInfo")
async def get_user_seat_list_info(pageNow: int = None, pageSize: int = None):
    db = classBindingModel()
    res = db.get_user_seat_list_info(pageNow, pageSize)
    return makeResponse(res)


# 查询整个名单，教室，座号，助教名称
@router.get("/seatList/{name}/allListInfo")
async def get_all_info(name: str, pageNow: int = None, pageSize: int = None):
    db = classBindingModel()
    res = db.get_all_info(name, pageNow, pageSize)
    return makeResponse(res)


# 查询单人信息
@router.get("/seatList/{groupId}/{username}/seatInfo")
async def get_single_user_info(groupId: int, username: int, pageNow: int = None, pageSize: int = None):
    db = classBindingModel()
    res = db.get_single_user_info(groupId, username, pageNow, pageSize)
    return makeResponse(res)


# 查找座位IP
@router.get("/searchIP")
async def search_s_ip(data: dict):
    db = classBindingModel()
    res = db.search_s_ip(data)
    return makeResponse(res)


# 查询教室名是否已存在
@router.get("/isExist")
async def c_name_is_exist(data: dict):
    db = classBindingModel()
    res = db.c_name_is_exist(data)
    return res


# 查询名单的助教
@router.get("/seatList/{usl_id}/listTAInfo")
async def check_TA_info(usl_id: int, pageNow: int = None, pageSize: int = None):
    db = classBindingModel()
    res = db.check_TA_info(usl_id, pageNow, pageSize)
    return makeResponse(res)


# 查询整个名单的用户，教室，座号
@router.get("/seatList/{usl_id}/checkAllListInfo")
async def check_list_info(usl_id: int, pageNow: int = None, pageSize: int = None):
    db = classBindingModel()
    res = db.check_list_info(usl_id, pageNow, pageSize)
    return makeResponse(res)


# 新建助教
@router.post("/seatList/createTA")
async def create_TA(data: dict):
    db = classBindingModel()
    db.create_TA(data)
    return makeResponse(None)


# 删除助教
@router.post("/seatList/deleteTA")
async def delete_TA(data: list):
    db = classBindingModel()
    db.delete_TA(data)
    return makeResponse(None)


# 编辑助教
@router.post("/seatList/editTA")
async def edit_TA(data: dict):
    db = classBindingModel()
    db.edit_TA(data)
    return makeResponse(None)


# 批量绑定IP
@router.post("/multiBindingIP")
async def multi_ip_binding(file: UploadFile = File(...)):
    db = classBindingModel()
    await db.multi_ip_binding(file)
    return makeResponse(None)


# 批量绑定用户座次
@router.post("/seatList/multiSeatsBinding")
async def multi_seats_binding(file: UploadFile = File(...)):
    db = classBindingModel()
    await db.multi_seats_binding(file)
    return makeResponse(None)
