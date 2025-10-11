from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

from model.late_permission import latePermissionModel
from ser.late_permission import (
    ser_late_permission_add,
    ser_late_permission_list,
    ser_late_permission_update,
)
from ser.base import makePageResult
from utils import makeResponse

router = APIRouter(prefix="/problem_set/late")


@router.post("/list")
def list_permissions(data: dict = Depends(ser_late_permission_list)):
    db = latePermissionModel()
    pg = data["page"]
    total, rows = db.list_by_psid(
        data["psid"],
        pg.offset(),
        pg.limit(),
        data.get("username")
    )
    return makeResponse(makePageResult(pg, total, rows))


@router.post("/add")
def add_permission(data: dict = Depends(ser_late_permission_add)):
    db = latePermissionModel()
    try:
        db.create(data)
    except IntegrityError:
        raise HTTPException(status_code=409, detail="late permission already exists")
    return makeResponse(None)


@router.post("/update")
def update_permission(data: dict = Depends(ser_late_permission_update)):
    db = latePermissionModel()
    db.update_by_id(data["id"], data["update"])
    return makeResponse(None)
