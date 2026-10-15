"""Persistent collection collaboration stored as an internal child resource."""

import json
from datetime import datetime

from fastapi import HTTPException

from .entities import AiResource, uid
from .store import digest, dumps

ROLES = {"view": 1, "viewer": 1, "contributor": 2, "editor": 3, "owner": 4}


def now():
    return datetime.utcnow().isoformat() + "Z"


def acl_data(row):
    data = json.loads(row.data)
    for key in ("members", "assignments", "batch_reservations"):
        if not isinstance(data.get(key, []), list):
            raise ValueError("Invalid collection collaboration state")
        data.setdefault(key, [])
    return data


def get_acl(session, collection, create=False):
    row = session.query(AiResource).filter_by(kind="collection_acl", parent_id=collection.id,
                                              deleted=0).with_for_update().first()
    if row or not create:
        return row
    row = AiResource(id=uid(), kind="collection_acl", owner_id=collection.owner_id,
                     owner_username=collection.owner_username, course_id=collection.course_id,
                     parent_id=collection.id, state="ready",
                     canonical_key=digest({"collection_acl": collection.id}),
                     data=dumps({"members": [], "assignments": [], "batch_reservations": []}))
    session.add(row)
    session.flush()
    return row


def member(data, subject):
    return next((item for item in data.get("members", [])
                 if item.get("subject_id") == str(subject) and item.get("status") == "active"), None)


def permission(session, collection, subject):
    if collection.owner_id == str(subject):
        return "owner"
    acl = get_acl(session, collection)
    if not acl:
        return None
    item = member(acl_data(acl), subject)
    return item.get("role") if item and item.get("role") in ROLES else None


def require_permission(session, collection, subject, required="view"):
    role = permission(session, collection, subject)
    if not role or ROLES[role] < ROLES[required]:
        raise HTTPException(403, "Collection collaboration permission denied")
    return role


def invite_member(session, collection, subject_id, username, role, actor_id, scope_course_id):
    if collection.owner_id != str(actor_id):
        raise HTTPException(403, "Only the collection owner may manage collaborators")
    if role not in ("viewer", "contributor", "editor"):
        raise HTTPException(400, "Invalid collection collaborator role")
    subject_id = str(subject_id)
    if not subject_id.isascii() or not subject_id.isdecimal() or int(subject_id) <= 0:
        raise HTTPException(400, "Expected a stable positive OJ user ID")
    if subject_id == collection.owner_id:
        raise HTTPException(409, "Collection owner is already authorized")
    if scope_course_id != collection.course_id:
        raise HTTPException(400, "Collaboration course scope mismatch")
    username = str(username).strip()
    if not username or len(username) > 100:
        raise HTTPException(400, "Expected the collaborator OJ username")
    acl = get_acl(session, collection, True)
    data = acl_data(acl)
    item = next((value for value in data["members"] if value.get("subject_id") == subject_id), None)
    if item:
        item.update(username=username, role=role, status="pending", invited_at=now(), accepted_at=None)
    else:
        item = {"subject_id": subject_id, "username": username, "role": role,
                "status": "pending", "invited_at": now(), "accepted_at": None}
        data["members"].append(item)
    acl.data, acl.version = dumps(data), acl.version + 1
    return item


def accept_invitation(session, collection, subject_id, username):
    acl = get_acl(session, collection)
    if not acl:
        raise HTTPException(404, "Collaboration invitation not found")
    data = acl_data(acl)
    item = next((value for value in data["members"] if value.get("subject_id") == str(subject_id)), None)
    if not item or item.get("status") != "pending":
        raise HTTPException(404, "Collaboration invitation not found")
    if item.get("username") != username:
        raise HTTPException(409, "Invitation OJ identity does not match the current session")
    item.update(status="active", accepted_at=now())
    acl.data, acl.version = dumps(data), acl.version + 1
    return item


def revoke_member(session, collection, subject_id, actor_id):
    if collection.owner_id != str(actor_id):
        raise HTTPException(403, "Only the collection owner may manage collaborators")
    acl = get_acl(session, collection)
    if not acl:
        raise HTTPException(404, "Collection collaborator not found")
    data = acl_data(acl)
    item = next((value for value in data["members"] if value.get("subject_id") == str(subject_id)), None)
    if not item or item.get("status") == "revoked":
        raise HTTPException(404, "Collection collaborator not found")
    item.update(status="revoked", revoked_at=now())
    for assignment in data["assignments"]:
        if assignment.get("assignee_id") == str(subject_id) and assignment.get("status") == "open":
            assignment.update(status="cancelled", updated_at=now())
    acl.data, acl.version = dumps(data), acl.version + 1
    return item


def create_assignment(session, collection, actor_id, assignee_id, title, description=""):
    if collection.owner_id != str(actor_id):
        raise HTTPException(403, "Only the collection owner may create assignments")
    acl = get_acl(session, collection, True)
    data = acl_data(acl)
    assignee = member(data, assignee_id)
    if not assignee or ROLES.get(assignee.get("role"), 0) < ROLES["contributor"]:
        raise HTTPException(409, "Assignment needs an active contributor")
    assignment = {"id": uid(), "title": str(title).strip()[:160],
                  "description": str(description).strip()[:2000], "assignee_id": str(assignee_id),
                  "status": "open", "created_at": now(), "updated_at": now()}
    if not assignment["title"]:
        raise HTTPException(400, "Assignment title is required")
    data["assignments"].append(assignment)
    acl.data, acl.version = dumps(data), acl.version + 1
    return assignment


def reserve_batch(session, collection, subject_id, batch_id, source_hash, assignment_id=None):
    role = require_permission(session, collection, subject_id, "contributor")
    acl = get_acl(session, collection, True)
    data = acl_data(acl)
    assignment = None
    if assignment_id:
        assignment = next((item for item in data["assignments"] if item.get("id") == assignment_id), None)
        if not assignment or assignment.get("status") != "open":
            raise HTTPException(409, "Collection assignment is unavailable")
        if role != "owner" and assignment.get("assignee_id") != str(subject_id):
            raise HTTPException(403, "Collection assignment belongs to another collaborator")
    existing = next((item for item in data["batch_reservations"] if item.get("batch_id") == batch_id), None)
    if existing:
        if (existing.get("subject_id"), existing.get("source_hash"), existing.get("assignment_id")) != (
                str(subject_id), source_hash, assignment_id):
            raise HTTPException(409, "Collection batch ID is already reserved")
        return existing, True
    reservation = {"batch_id": batch_id, "subject_id": str(subject_id), "source_hash": source_hash,
                   "assignment_id": assignment_id, "status": "queued", "created_at": now()}
    data["batch_reservations"].append(reservation)
    acl.data, acl.version = dumps(data), acl.version + 1
    return reservation, False


def assert_worker_grant(session, collection_id, subject_id, assignment_id=None):
    collection = session.query(AiResource).filter_by(id=collection_id, kind="collection", deleted=0).first()
    if not collection:
        raise ValueError("Collection collaboration source was removed")
    try:
        require_permission(session, collection, subject_id, "contributor")
    except HTTPException as error:
        raise ValueError("Collection collaboration access was revoked") from error
    if assignment_id:
        acl = get_acl(session, collection)
        assignment = next((item for item in acl_data(acl)["assignments"] if item.get("id") == assignment_id), None)
        if not assignment or assignment.get("status") != "open" or assignment.get("assignee_id") != str(subject_id):
            raise ValueError("Collection collaboration assignment was revoked")
    return collection


def complete_batch(session, collection, batch_id, subject_id, assignment_id=None):
    acl = get_acl(session, collection)
    if not acl:
        return
    data = acl_data(acl)
    reservation = next((item for item in data["batch_reservations"] if item.get("batch_id") == batch_id), None)
    if reservation and reservation.get("subject_id") == str(subject_id):
        reservation.update(status="applied", applied_at=now())
    if assignment_id:
        assignment = next((item for item in data["assignments"] if item.get("id") == assignment_id), None)
        if assignment and assignment.get("assignee_id") == str(subject_id):
            assignment.update(status="submitted", batch_id=batch_id, updated_at=now())
    acl.data, acl.version = dumps(data), acl.version + 1
