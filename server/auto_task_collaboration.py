"""Owner-managed, OJ-identity-bound collaboration for question collections."""

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from utils import api_response
from auto_task.collection_access import (accept_invitation, acl_data,
                                         create_assignment, get_acl,
                                         invite_member, permission,
                                         require_permission, revoke_member)
from auto_task.entities import AiResource
from auto_task.resources import course_access, get_resource, principal
from auto_task.store import transaction
from server.auto_task_ai import active_user

router = APIRouter(prefix="/collection-collaboration", tags=["auto-task-collaboration"])


class MemberInvite(BaseModel):
    subject_id: str = Field(..., min_length=1, max_length=64)
    username: str = Field(..., min_length=1, max_length=100)
    role: str = Field(..., regex=r"^(viewer|contributor|editor)$")
    course_id: Optional[int] = Field(None, ge=1)


class AssignmentCreate(BaseModel):
    assignee_id: str = Field(..., min_length=1, max_length=64)
    title: str = Field(..., min_length=1, max_length=160)
    description: str = Field("", max_length=2000)


def _collection(session, resource_id, write=False):
    query = session.query(AiResource).filter_by(id=resource_id, kind="collection", deleted=0)
    row = (query.with_for_update() if write else query).first()
    if not row:
        raise HTTPException(404, "Collection not found")
    return row


def _course_member(session, user, collection):
    if collection.course_id:
        course_access(session, user, collection.course_id, False)


def _view(data, subject, role):
    members = data["members"] if role == "owner" else [
        item for item in data["members"] if item.get("status") == "active"]
    reservations = data["batch_reservations"] if role == "owner" else [
        item for item in data["batch_reservations"] if item.get("subject_id") == subject]
    return {"role": role, "members": members, "assignments": data["assignments"],
            "batch_reservations": reservations}


@router.get("/list")
@api_response()
def collaboration_list(user=Depends(active_user)):
    subject = principal(user)
    with transaction() as s:
        rows = []
        for collection in s.query(AiResource).filter_by(kind="collection", deleted=0).order_by(AiResource.updated_at.desc()).all():
            role = permission(s, collection, subject)
            if not role:
                acl = get_acl(s, collection)
                pending = next((item for item in acl_data(acl)["members"]
                                if item.get("subject_id") == subject and item.get("status") == "pending"), None) if acl else None
                if not pending or pending.get("username") != user["username"]:
                    continue
                role = "pending"
            try:
                _course_member(s, user, collection)
            except HTTPException:
                continue
            rows.append({"id": collection.id, "title": json.loads(collection.data).get("title", collection.id),
                         "course_id": collection.course_id, "role": role, "version": collection.version})
        return rows[:200]


@router.get("/{resource_id}")
@api_response()
def collaboration_detail(resource_id: str, user=Depends(active_user)):
    subject = principal(user)
    with transaction() as s:
        collection = _collection(s, resource_id)
        _course_member(s, user, collection)
        role = require_permission(s, collection, subject, "view")
        acl = get_acl(s, collection, role == "owner")
        data = acl_data(acl) if acl else {"members": [], "assignments": [], "batch_reservations": []}
        return {"collection_id": collection.id, "course_id": collection.course_id,
                **_view(data, subject, role)}


@router.post("/{resource_id}/members")
@api_response()
def add_member(resource_id: str, body: MemberInvite, user=Depends(active_user)):
    subject = principal(user)
    with transaction() as s:
        collection = _collection(s, resource_id, True)
        _course_member(s, user, collection)
        item = invite_member(s, collection, body.subject_id, body.username, body.role,
                             subject, body.course_id)
        return {"collection_id": collection.id, "member": item}


@router.post("/{resource_id}/accept")
@api_response()
def accept_member(resource_id: str, user=Depends(active_user)):
    subject = principal(user)
    with transaction() as s:
        collection = _collection(s, resource_id, True)
        _course_member(s, user, collection)
        return {"collection_id": collection.id,
                "member": accept_invitation(s, collection, subject, user["username"])}


@router.post("/{resource_id}/members/{subject_id}/revoke")
@api_response()
def remove_member(resource_id: str, subject_id: str, user=Depends(active_user)):
    subject = principal(user)
    with transaction() as s:
        collection = _collection(s, resource_id, True)
        _course_member(s, user, collection)
        return {"collection_id": collection.id,
                "member": revoke_member(s, collection, subject_id, subject)}


@router.post("/{resource_id}/assignments")
@api_response()
def add_assignment(resource_id: str, body: AssignmentCreate, user=Depends(active_user)):
    subject = principal(user)
    with transaction() as s:
        collection = _collection(s, resource_id, True)
        _course_member(s, user, collection)
        assignment = create_assignment(s, collection, subject, body.assignee_id,
                                       body.title, body.description)
        return {"collection_id": collection.id, "assignment": assignment}
