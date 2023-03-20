from fastapi import APIRouter, Depends

from model.subjective import subjectiveModel
from ser.subjective import ser_subjective_add, ser_subjective_edit, \
    ser_subjective_info
from utils import makeResponse

router = APIRouter(
    prefix="/subjective",
    dependencies=[]
)


@router.post("/add")
async def add(data: dict = Depends(ser_subjective_add)):
    db = subjectiveModel()
    return makeResponse(db.get_info_by_id(db.create(data)))


@router.post("/edit")
async def edit(data: dict = Depends(ser_subjective_edit)):
    db = subjectiveModel()
    pid = data["pid"]
    data.pop("pid")
    data.pop("gid")
    db.update_by_id(pid, data)
    return makeResponse(db.get_info_by_id(pid))


@router.post("/info")
async def info(data: dict = Depends(ser_subjective_info)):
    db = subjectiveModel()
    return makeResponse(db.get_info_by_id(data["pid"]))
