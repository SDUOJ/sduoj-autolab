from fastapi import APIRouter, Depends

from model.objective import objectiveModel
from ser.objective import ser_objective_add, ser_objective_edit, \
    ser_objective_info
from utils import makeResponse

router = APIRouter(
    prefix="/objective"
)


@router.post("/add")
async def add(data: dict = Depends(ser_objective_add)):
    db = objectiveModel()
    return makeResponse(db.get_info_by_id(db.create(data)))


@router.post("/edit")
async def edit(data: dict = Depends(ser_objective_edit)):
    db = objectiveModel()
    pid = data["pid"]
    data.pop("pid")
    data.pop("gid")
    db.update_by_id(pid, data)
    return makeResponse(db.get_info_by_id(pid))


@router.post("/info")
async def info(data: dict = Depends(ser_objective_info)):
    db = objectiveModel()
    return makeResponse(db.get_info_by_id(data["pid"]))
