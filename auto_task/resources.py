"""Resource ownership, OJ course scopes, and order-independent exact question identity."""

import json
import unicodedata
from datetime import datetime, timedelta

from fastapi import HTTPException

from db import ProblemGroup, ProblemSet, ojCourse
from .entities import AiResource
from .store import account, assert_active, digest, principal


def is_admin(user):
    return bool(set(user.get("roles") or []) & {"admin", "superadmin"})


def require_admin(user):
    principal(user)
    if not is_admin(user):
        raise HTTPException(403, "Administrator role required")


def course_access(session, user, course_id, write=False):
    from model.course import CourseModel
    course = session.query(ojCourse).filter_by(course_id=course_id).first()
    if not course:
        raise HTTPException(404, "Course not found")
    creator, managers = CourseModel._get_course_permission_fields(course, CourseModel._parse_json(course.ext_config, {}))
    manager = CourseModel._has_manage_permission(creator, managers, user["username"], user.get("groups", []), "superadmin" in user.get("roles", []))
    if manager or (not write and course.group_id in user.get("groups", [])):
        return course
    raise HTTPException(403, "Course permission denied")


def ps_access(session, user, psid, write=False):
    ps = session.query(ProblemSet).filter_by(psid=psid).first()
    if not ps:
        raise HTTPException(404, "Problem set not found")
    managed = "superadmin" in (user.get("roles") or []) or ps.username == user["username"] or ps.manageGroupId in (user.get("groups") or [])
    if not managed and (write or ps.groupId not in (user.get("groups") or [])):
        raise HTTPException(403, "Problem set permission denied")
    return ps


def get_resource(session, user, resource_id, write=False, include_deleted=False):
    subject = principal(user)
    session.info["ai_actor"] = subject
    assert_active(account(session, subject, user["username"]))
    query = session.query(AiResource).filter_by(id=resource_id)
    if not include_deleted:
        query = query.filter_by(deleted=0)
    row = (query.with_for_update() if write else query).first()
    if not row:
        raise HTTPException(404, "Resource not found")
    if row.kind == "management_session":
        raise HTTPException(403, "Use the owner-scoped management session API")
    native = json.loads(row.data).get("native")
    if isinstance(native, dict) and native.get("group_id"):
        group = session.query(ProblemGroup).filter_by(gid=native["group_id"]).first()
        managed = group and ("superadmin" in (user.get("roles") or []) or group.username == user["username"] or group.manageGroupId in (user.get("groups") or []))
        from .collection_access import native_question_permission
        if not group or not (managed or native_question_permission(session, row, user, native, group, write)):
            raise HTTPException(403, "Native source group access revoked")
        if not any(str(item.get("pid")) == str(native.get("pid")) for item in json.loads(group.problemInfo)):
            raise HTTPException(403, "Native source removed from authorized group")
    # Owner status does not override a revoked course or examination scope.
    if row.course_id:
        course_access(session, user, row.course_id, write and row.kind in ("course", "document", "exam", "grading", "authoring"))
    if row.psid:
        ps_access(session, user, row.psid, write and row.kind in ("exam", "grading", "authoring", "plagiarism"))
    if row.owner_id == subject:
        return row
    if row.kind == "collection":
        from .collection_access import permission, ROLES
        role = permission(session, row, subject)
        if role and (not write or ROLES[role] >= ROLES["editor"]):
            return row
    if row.kind == "question" and row.parent_id:
        parent = session.query(AiResource).filter_by(id=row.parent_id, kind="collection", deleted=0).first()
        if parent:
            from .collection_access import permission, ROLES
            role = permission(session, parent, subject)
            submitted_by = str(json.loads(row.data).get("submitted_by", ""))
            if role and (not write or ROLES[role] >= ROLES["editor"] or
                         (ROLES[role] >= ROLES["contributor"] and submitted_by == subject)):
                return row
    if not write and row.kind == "interview" and str(json.loads(row.data).get("student_id")) == subject:
        return row
    if row.kind in ("conversation", "ide"):
        raise HTTPException(403, "Private learning session")
    if row.course_id and row.kind in ("course", "document", "question", "collection") and not write:
        return row
    if row.course_id:
        course_access(session, user, row.course_id, True)
        return row
    if row.psid:
        ps_access(session, user, row.psid, True)
        return row
    raise HTTPException(403, "Resource permission denied")


def canonical_question(value):
    def normalize(text):
        return unicodedata.normalize("NFC", str(text)).replace("\r\n", "\n").strip()
    stem = normalize(value.get("original") or value.get("zh", ""))
    options = sorted(normalize(x.get("original") or x.get("zh", "")) for x in value.get("options", []))
    return digest({"type": value["question_type"], "stem": stem, "options": options})


def resource_view(row):
    return {"id": row.id, "kind": row.kind, "version": row.version, "state": row.state,
            "course_id": row.course_id, "psid": row.psid, "parent_id": row.parent_id,
            "data": json.loads(row.data), "created_at": row.created_at.isoformat() + "Z" if row.created_at else None}


def interview_expired(data):
    deadline = datetime.fromisoformat(data["expires_at"])
    if data.get("started_at"):
        deadline = min(deadline, datetime.fromisoformat(data["started_at"]) + timedelta(minutes=int(data.get("duration_minutes", 20))))
    return deadline <= datetime.utcnow()


def normalize_tags(tags, existing=()):
    from difflib import SequenceMatcher
    aliases = {}
    for item in existing:
        data = json.loads(item.data)
        for name in [data["name"], *data.get("aliases", [])]:
            aliases[unicodedata.normalize("NFKC", name).strip().casefold()] = data["name"]
    result = []
    for tag in tags[:6]:
        value = unicodedata.normalize("NFKC", tag).strip()
        if not value or len(value) > 64:
            continue
        canonical = aliases.get(value.casefold(), value)
        if canonical == value and aliases:
            best = max(aliases, key=lambda name: SequenceMatcher(None, name, value.casefold()).ratio())
            if SequenceMatcher(None, best, value.casefold()).ratio() >= 0.82:
                canonical = aliases[best]
        if canonical not in result:
            result.append(canonical)
    return result


def resolve_tags(session, tags, owner_id, username):
    from .entities import uid
    from .store import dumps
    existing = session.query(AiResource).filter_by(kind="tag", deleted=0).all()
    values = normalize_tags(tags, existing)
    known = {json.loads(row.data)["name"] for row in existing}
    for value in values:
        if value not in known:
            session.add(AiResource(id=uid(), kind="tag", owner_id=owner_id, owner_username=username,
                                   data=dumps({"name": value, "aliases": []}), search_text=value,
                                   canonical_key=digest({"tag": unicodedata.normalize("NFKC", value).strip().casefold()}), state="ready"))
            known.add(value)
    return values
