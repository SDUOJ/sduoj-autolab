from typing import Optional

from fastapi import Depends
from pydantic import BaseModel, validator

from auth import cover_header, problem_set_manager
from model.problem_set import problemSetModel
from ser.base import obj2dict
from ser.base_type import page


class LatePermissionBase(BaseModel):
    psid: int
    username: str
    duration_minute: int
    discount: float
    note: Optional[str] = None

    @validator('username')
    def username_not_blank(cls, v):
        v = v.strip()
        if len(v) == 0:
            raise ValueError('username is empty')
        return v

    @validator('duration_minute')
    def duration_positive(cls, v):
        if v <= 0:
            raise ValueError('duration must be positive')
        return v

    @validator('discount')
    def discount_range(cls, v):
        if v <= 0 or v > 1:
            raise ValueError('discount must be within (0, 1]')
        return v

    @validator('note')
    def limit_note_length(cls, v):
        if v is None:
            return v
        v = v.strip()
        if len(v) > 200:
            raise ValueError('note is too long (max 200 chars)')
        return v


class LatePermissionUpdate(BaseModel):
    id: int
    psid: int
    duration_minute: Optional[int] = None
    discount: Optional[float] = None
    is_active: Optional[int] = None
    note: Optional[str] = None

    @validator('duration_minute')
    def duration_positive(cls, v):
        if v is not None and v <= 0:
            raise ValueError('duration must be positive')
        return v

    @validator('discount')
    def discount_range(cls, v):
        if v is not None and (v <= 0 or v > 1):
            raise ValueError('discount must be within (0, 1]')
        return v

    @validator('is_active')
    def active_flag(cls, v):
        if v is not None and v not in (0, 1):
            raise ValueError('is_active must be 0 or 1')
        return v

    @validator('note')
    def limit_note_length(cls, v):
        if v is None:
            return v
        v = v.strip()
        if len(v) > 200:
            raise ValueError('note is too long (max 200 chars)')
        return v


class LatePermissionList(BaseModel):
    psid: int
    page: page
    username: Optional[str] = None

    @validator('username')
    def normalize_username(cls, v):
        if v is None:
            return v
        v = v.strip()
        return v if len(v) > 0 else None


def _populate_base_payload(data: LatePermissionBase, SDUOJUserInfo: dict):
    ps_db = problemSetModel()
    ps_obj = ps_db.ps_get_obj_by_id(data.psid)
    payload = obj2dict(data)
    payload.update({
        "groupId": ps_obj.groupId,
        "created_by": SDUOJUserInfo["username"],
        "updated_by": SDUOJUserInfo["username"],
    })
    return payload


def ser_late_permission_add(
        data: LatePermissionBase, SDUOJUserInfo=Depends(cover_header)):
    problem_set_manager(data.psid, SDUOJUserInfo)
    payload = _populate_base_payload(data, SDUOJUserInfo)
    return payload


def ser_late_permission_update(
        data: LatePermissionUpdate, SDUOJUserInfo=Depends(cover_header)):
    problem_set_manager(data.psid, SDUOJUserInfo)
    item_id = data.id
    payload = obj2dict(data).copy()
    payload.pop("psid", None)
    payload.pop("id", None)
    payload = {k: v for k, v in payload.items() if v is not None}
    payload["updated_by"] = SDUOJUserInfo["username"]
    return {
        "id": item_id,
        "update": payload
    }


def ser_late_permission_list(
        data: LatePermissionList, SDUOJUserInfo=Depends(cover_header)):
    problem_set_manager(data.psid, SDUOJUserInfo)
    return {
        "psid": data.psid,
        "page": data.page,
        "username": data.username
    }
