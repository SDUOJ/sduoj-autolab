"""Examination plans bound to native OJ courses, seats, and answer sheets."""

import csv
import io
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from db import ojClass, ojClassUser, ProblemSetAnswerSheet
from auto_task.resources import course_access, get_resource, principal, ps_access, resource_view
from auto_task.store import digest, dumps, transaction
from server.auto_task_ai import active_user
from sduojApi import getGroupMember
from utils import api_response

router = APIRouter()


def native_rooms(session, course):
    ids = json.loads(course.c_ids or "[]")
    result = []
    for room in session.query(ojClass).filter(ojClass.c_id.in_(ids)).order_by(ojClass.c_id):
        config = json.loads(room.ext_config or "{}")
        banned = {int(item["seat_number"]) for item in config.get("seat-ban", [])}
        banned.update(int(value) for value in config.get("disabled_seats", []))
        seats = [seat for seat in range(1, room.c_seat_num + 1) if seat not in banned]
        result.append({"c_id": room.c_id, "name": room.c_name, "capacity": len(seats), "available_seats": seats})
    return result


@router.get("/exams/rooms")
@api_response()
def rooms(course_id: int, user=Depends(active_user)):
    with transaction() as s:
        course = course_access(s, user, course_id, True)
        return {"rows": native_rooms(s, course)}


class BindCourse(BaseModel):
    version: int = Field(..., ge=1)
    course_id: int
    replace_existing: bool = False
    reason: str = Field(..., min_length=1, max_length=2000)


@router.post("/exams/{resource_id}/bind-course")
@api_response()
async def bind_course(resource_id: str, body: BindCourse, user=Depends(active_user)):
    with transaction() as s:
        row = get_resource(s, user, resource_id)
        course = course_access(s, user, body.course_id, True)
        group_id = course.group_id
    membership = await getGroupMember(group_id)
    if not isinstance(membership, dict):
        raise HTTPException(502, "Native course group unavailable")
    members = {str(item.get("username")): str(item.get("userId")) for item in membership.get("members", []) if item.get("status", 2) == 2}
    if isinstance(membership.get("owner"), dict):
        members[str(membership["owner"].get("username"))] = str(membership["owner"].get("userId"))
    with transaction() as s:
        row = get_resource(s, user, resource_id, True)
        course = course_access(s, user, body.course_id, True)
        s.refresh(course, with_for_update=True)
        data = json.loads(row.data)
        if row.kind != "exam" or not data.get("native_psid") or row.version != body.version:
            raise HTTPException(409, "Expected current published examination")
        ps = ps_access(s, user, data["native_psid"], True)
        if course.tag not in ("考试", "exam") or course.group_id != group_id or group_id != ps.groupId:
            raise HTTPException(409, "Use a dedicated examination course with the native participant group")
        allowed = {room["c_id"]: set(room["available_seats"]) for room in native_rooms(s, course)}
        assignments = []
        for room in data.get("candidate", {}).get("rooms", []):
            cid = room.get("c_id")
            for person in room.get("assigned", []):
                name, seat = person.get("username"), person.get("seat")
                if not name or members.get(name) != str(person.get("user_id")):
                    raise HTTPException(409, "Candidate is not a current OJ course member")
                if cid not in allowed or seat not in allowed[cid] or not 1 <= seat <= 127:
                    raise HTTPException(409, "Seat unavailable in native classroom")
                assignments.append({"username": name, "c_id": cid, "seat_number": seat})
        if not assignments or len({item["username"] for item in assignments}) != len(assignments) or len({(item["c_id"], item["seat_number"]) for item in assignments}) != len(assignments):
            raise HTTPException(409, "Empty or duplicate examination seat assignments")
        old = s.query(ojClassUser).filter_by(course_id=course.course_id).with_for_update().all()
        before = [{"username": item.username, "c_id": item.c_id, "seat_number": item.seat_number} for item in old]
        sort = lambda items: sorted(items, key=lambda item: item["username"])
        binding = data.get("course_binding")
        if binding and binding["course_id"] == body.course_id and sort(before) == sort(assignments):
            return {"course_id": body.course_id, "replay": True, "version": row.version}
        if old and not body.replace_existing:
            raise HTTPException(409, "Explicit confirmation required to replace existing examination seats")
        if binding:
            raise HTTPException(409, "Restore the previous course binding before rebinding")
        for item in old:
            s.delete(item)
        s.flush()
        for item in assignments:
            s.add(ojClassUser(course_id=course.course_id, **item))
        data["course_binding"] = {"course_id": course.course_id, "before": before, "after": assignments, "actor_id": principal(user), "reason": body.reason}
        row.data, row.version = dumps(data), row.version + 1
        return {"course_id": course.course_id, "assigned": len(assignments), "version": row.version, "replay": False}


class ExamAction(BaseModel):
    version: int = Field(..., ge=1)
    reason: str = Field(..., min_length=1, max_length=2000)


@router.post("/exams/{resource_id}/validate-test")
@api_response()
def validate_test(resource_id: str, body: ExamAction, user=Depends(active_user)):
    with transaction() as s:
        row = get_resource(s, user, resource_id, True)
        data = json.loads(row.data)
        if row.kind != "exam" or row.version != body.version or data.get("mode") != "TEST" or not data.get("native_psid"):
            raise HTTPException(409, "Expected a published TEST examination")
        ps_access(s, user, data["native_psid"], True)
        count = s.query(ProblemSetAnswerSheet).filter_by(psid=data["native_psid"], finish=1).count()
        if count < 1:
            raise HTTPException(409, "At least one native TEST submission must be completed")
        certificate = {"version": row.version + 1, "completed_sheets": count, "groups_hash": digest(data["publication_plan"]["groupInfo"]),
                       "actor_id": principal(user), "reason": body.reason, "at": datetime.utcnow().isoformat() + "Z"}
        data["validation_certificate"] = certificate
        row.version, row.data = row.version + 1, dumps(data)
        return {"version": row.version, "certificate": certificate}


@router.post("/exams/{resource_id}/unbind-course")
@api_response()
def unbind_course(resource_id: str, body: ExamAction, user=Depends(active_user)):
    with transaction() as s:
        row = get_resource(s, user, resource_id, True)
        data = json.loads(row.data)
        binding = data.get("course_binding")
        if row.kind != "exam" or row.version != body.version or not binding:
            raise HTTPException(409, "No current course binding")
        course = course_access(s, user, binding["course_id"], True)
        s.refresh(course, with_for_update=True)
        rows = s.query(ojClassUser).filter_by(course_id=course.course_id).with_for_update().all()
        actual = [{"username": item.username, "c_id": item.c_id, "seat_number": item.seat_number} for item in rows]
        if sorted(actual, key=lambda item: item["username"]) != sorted(binding["after"], key=lambda item: item["username"]):
            raise HTTPException(409, "Native seat assignments changed after binding")
        for item in rows:
            s.delete(item)
        s.flush()
        for item in binding["before"]:
            s.add(ojClassUser(course_id=course.course_id, **item))
        data.setdefault("binding_history", []).append({**data.pop("course_binding"), "restored_by": principal(user), "restore_reason": body.reason})
        row.data, row.version = dumps(data), row.version + 1
        return {"version": row.version, "restored": True}


def monitor_data(session, user, resource_id):
    row = get_resource(session, user, resource_id, True)
    data = json.loads(row.data)
    if row.kind != "exam" or not data.get("native_psid"):
        raise HTTPException(409, "Examination is not published")
    ps_access(session, user, data["native_psid"], True)
    sheets = {item.username: item for item in session.query(ProblemSetAnswerSheet).filter_by(psid=data["native_psid"]).all()}
    result = []
    for room in data.get("candidate", {}).get("rooms", []):
        for person in room.get("assigned", []):
            sheet = sheets.get(person.get("username"))
            result.append({"username": person.get("username"), "name": person.get("name", ""), "room": room.get("name"), "seat": person.get("seat"),
                           "submission": "SUBMITTED" if sheet and sheet.finish == 1 else "WORKING" if sheet else "NOT_STARTED",
                           "finish_time": sheet.finish_time.isoformat() if sheet and sheet.finish_time else None})
    return {"rows": result, "psid": data["native_psid"], "mode": data.get("mode"), "generated_at": datetime.utcnow().isoformat() + "Z"}


@router.get("/exams/{resource_id}/monitor")
@api_response()
def monitor(resource_id: str, user=Depends(active_user)):
    with transaction() as s:
        return monitor_data(s, user, resource_id)


@router.get("/exams/{resource_id}/roster.csv")
def export_roster(resource_id: str, user=Depends(active_user)):
    with transaction() as s:
        data = monitor_data(s, user, resource_id)
    stream = io.StringIO()
    columns = ["username", "name", "room", "seat", "submission", "finish_time"]
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for row in data["rows"]:
        writer.writerow({key: "'" + value if isinstance(value, str) and value[:1] in "=+-@" else value for key, value in row.items()})
    return Response("\ufeff" + stream.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=exam-roster.csv", "Cache-Control": "no-store"})
