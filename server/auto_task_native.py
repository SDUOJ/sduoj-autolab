"""Native OJ interoperability. Browser-authenticated Java writes stay in Gateway."""

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import group_manager
from db import ProblemGroup, ProblemObjective, ProblemSet, ProblemSetAnswerSheet, ProblemSetAnswerSheetDetail, ProblemSubjective
from sduojApi import make_get, make_post
from utils import api_response
from auto_task.entities import AiPlagiarismSnapshot, AiPlagiarismSubmission, AiResource, TaskJob, uid
from auto_task.resources import get_resource, principal, ps_access, require_admin, resource_view
from auto_task.store import account, digest, dumps, submit, transaction
from server.auto_task_ai import active_user

router = APIRouter()


class NativeGrade(BaseModel):
    psid: int
    gid: int
    pid: int
    username: str = Field(..., min_length=1, max_length=100)
    operation_key: str = Field(..., min_length=1, max_length=96)


@router.post("/native/grading/create")
@api_response()
def grade_native_answer(body: NativeGrade, user=Depends(active_user)):
    from auto_task.legacy_bridge import answer_snapshot
    with transaction() as s:
        account(s, principal(user))
        ps = ps_access(s, user, body.psid, True)
        if not any(str(item.get("gid")) == str(body.gid) for item in json.loads(ps.groupInfo)):
            raise HTTPException(403, "Subjective group is not in the authorized problem set")
        group = s.query(ProblemGroup).filter_by(gid=body.gid, type=1).first()
        if not group or not any(str(item.get("pid")) == str(body.pid) for item in json.loads(group.problemInfo)):
            raise HTTPException(403, "Subjective question is not in the authorized group")
        payload = body.dict(exclude={"operation_key"})
        previous = s.query(TaskJob).filter_by(owner_id=principal(user), operation_key="native-grade:" + body.operation_key).first()
        if previous:
            if previous.task_type != "subjective_review" or json.loads(previous.payload) != payload:
                raise HTTPException(409, "Native grade operation conflicts")
            get_resource(s, user, previous.resource_id)
            return {"taskId": previous.id, "resource_id": previous.resource_id, "replay": True}
        try:
            _, source = answer_snapshot(s, body.psid, body.gid, body.pid, body.username)
        except ValueError as error:
            raise HTTPException(404, "Native subjective answer unavailable") from error
        if not source["rubric"]:
            raise HTTPException(409, "Native subjective rubric is required")
        row = AiResource(id=uid(), kind="grading", owner_id=principal(user), owner_username=user["username"],
                         psid=body.psid, data=dumps({"native_source": source}))
        s.add(row)
        s.flush()
        job = submit(s, user, "subjective_review", payload, "native-grade:" + body.operation_key, row, psid=body.psid)
        return {"taskId": job.id, "resource_id": row.id, "replay": False}


class NativeImport(BaseModel):
    group_id: int
    pid: int
    description_id: Optional[str] = None
    refresh_resource_id: Optional[str] = Field(None, min_length=1, max_length=63)
    version: Optional[int] = Field(None, ge=1)


class NativePlagiarismCreate(BaseModel):
    """Create a plagiarism task from one native programming question."""
    psid: int
    group_id: int
    pid: int
    operation_key: str = Field(..., min_length=1, max_length=96)
    title: str = Field("OJ programming plagiarism", min_length=1, max_length=128)
    language: str = Field("cpp", regex="^(c|cpp|python|java)$")
    threshold: float = Field(0.7, ge=0, le=1)
    # A student contributes one immutable submission to a scan. "latest" is
    # the default and "best" picks the highest judged score, then latest time.
    selection_policy: str = Field("latest", regex="^(latest|best)$")


def _submission_ids(answer):
    """Read native answer JSON without assuming one historical shape."""
    try:
        value = json.loads(answer or "[]") if isinstance(answer, str) else answer
    except (TypeError, ValueError):
        return []
    if isinstance(value, dict):
        value = value.get("submission", value.get("submissions", []))
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _submission_id(value):
    return format(value, "x") if isinstance(value, int) else str(value or "").strip().lower()


def _when(value):
    if isinstance(value, datetime):
        return value
    if not value:
        return datetime.min
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return datetime.min


def _native_submission_record(detail_id, username, submission_id, info):
    if not isinstance(info, dict) or not isinstance(info.get("code"), str) or not info["code"].strip():
        raise HTTPException(502, "Native submission has no source code")
    user_id = info.get("userId")
    if user_id is None or str(user_id) == "":
        raise HTTPException(502, "Native submission has no stable user ID")
    returned_id = info.get("submissionId")
    if returned_id is not None and str(returned_id).lower() != str(submission_id).lower():
        raise HTTPException(502, "Native submission identity mismatch")
    return {"answer_detail_id": detail_id, "username": username, "native_submission_id": submission_id,
            "user_id": str(user_id), "code": info["code"], "gmt_create": _when(info.get("gmtCreate")),
            "judge_result": info.get("judgeResult"), "judge_score": info.get("judgeScore")}


def _plagiarism_request(body):
    return {"psid": body.psid, "group_id": body.group_id, "pid": body.pid,
            "title": body.title, "language": body.language, "threshold": body.threshold,
            "selection_policy": body.selection_policy}


@router.post("/native/plagiarism/create")
@api_response()
async def create_native_plagiarism(body: NativePlagiarismCreate, user=Depends(active_user)):
    """Freeze OJ submissions before enqueueing JPlag, with replay semantics."""
    group_manager(body.group_id, user)
    owner_id = principal(user)
    operation = "native-plagiarism:" + body.operation_key
    with transaction() as s:
        account(s, owner_id, user["username"])
        previous = s.query(TaskJob).filter_by(owner_id=owner_id, operation_key=operation).first()
        if previous:
            requested = _plagiarism_request(body)
            if previous.task_type != "plagiarism.scan" or json.loads(previous.payload).get("native_request") != requested:
                raise HTTPException(409, "Native plagiarism operation conflicts")
            get_resource(s, user, previous.resource_id)
            return {"taskId": previous.id, "resource_id": previous.resource_id, "status": previous.status, "replay": True}
        ps = ps_access(s, user, body.psid, True)
        if not any(str(item.get("gid")) == str(body.group_id) for item in json.loads(ps.groupInfo)):
            raise HTTPException(403, "Programming group is not in the authorized problem set")
        group, member = verify_member(s, body.group_id, body.pid, user)
        if group.type != 2:
            raise HTTPException(400, "Plagiarism requires a programming group")
        details = s.query(ProblemSetAnswerSheetDetail, ProblemSetAnswerSheet.username).join(
            ProblemSetAnswerSheet, ProblemSetAnswerSheetDetail.asid == ProblemSetAnswerSheet.asid
        ).filter(ProblemSetAnswerSheet.psid == body.psid,
                 ProblemSetAnswerSheetDetail.gid == body.group_id,
                 ProblemSetAnswerSheetDetail.pid == body.pid,
                 ProblemSetAnswerSheetDetail.tm_answer_submit.isnot(None)).all()
        raw = [(detail.asd_id, username, sid) for detail, username in details for sid in _submission_ids(detail.answer)]
    if not raw:
        raise HTTPException(409, "No native programming submissions found")
    if len(raw) > 2000:
        raise HTTPException(413, "Too many native submissions; narrow the scan scope")

    references = {}
    for detail_id, username, submission_id in raw:
        references.setdefault(_submission_id(submission_id), (detail_id, username, submission_id))
    try:
        exported = await make_post("problem-service", "/internal/submission/listResult", {}, {
            "bizType": 2, "bizId": body.psid, "problemId": body.pid, "exportCode": True,
        })
    except Exception as error:
        raise HTTPException(502, "Native submission lookup failed") from error
    if not isinstance(exported, list):
        raise HTTPException(502, "Native submission response is invalid")
    records, found = [], set()
    for info in exported:
        if not isinstance(info, dict):
            continue
        key = _submission_id(info.get("submissionId"))
        if key not in references or key in found:
            continue
        detail_id, username, submission_id = references[key]
        if info.get("problemId") is not None and str(info["problemId"]) != str(body.pid):
            raise HTTPException(502, "Native submission belongs to a different problem")
        records.append(_native_submission_record(detail_id, username, submission_id, info))
        found.add(key)
    if found != set(references):
        raise HTTPException(502, "One or more native submissions are unavailable")
    if not records:
        raise HTTPException(502, "Native submissions are unavailable")
    by_user = {}
    usernames = {}
    for record in records:
        previous_user_id = usernames.setdefault(record["username"], record["user_id"])
        if previous_user_id != record["user_id"]:
            raise HTTPException(502, "Native submissions disagree on student identity")
        previous = by_user.get(record["user_id"])
        if previous is None:
            by_user[record["user_id"]] = record
            continue
        if previous["username"] != record["username"]:
            raise HTTPException(502, "Native user ID is shared by different answer sheets")
        score = int(record["judge_score"] or 0)
        old_score = int(previous["judge_score"] or 0)
        current_key = (score, record["gmt_create"], record["native_submission_id"])
        old_key = (old_score, previous["gmt_create"], previous["native_submission_id"])
        if (body.selection_policy == "best" and current_key > old_key) or (body.selection_policy == "latest" and (record["gmt_create"], record["native_submission_id"]) > (previous["gmt_create"], previous["native_submission_id"])):
            by_user[record["user_id"]] = record
    records = sorted(by_user.values(), key=lambda item: (item["user_id"], item["native_submission_id"]))
    if len(records) < 2:
        raise HTTPException(409, "At least two distinct student submissions are required")
    if len(records) > 1000:
        raise HTTPException(413, "At most 1000 student submissions may be scanned")
    if sum(len(item["code"].encode()) for item in records) > 32 * 1024 * 1024:
        raise HTTPException(413, "Native plagiarism source snapshot exceeds 32 MiB")

    import hashlib
    manifest_rows = [{"submission_id": item["native_submission_id"], "user_id": item["user_id"],
                      "username": item["username"], "code_sha256": hashlib.sha256(item["code"].encode()).hexdigest(),
                      "answer_detail_id": item["answer_detail_id"]} for item in records]
    snapshot_hash = digest(manifest_rows)
    requested = _plagiarism_request(body)
    with transaction() as s:
        previous = s.query(TaskJob).filter_by(owner_id=owner_id, operation_key=operation).first()
        if previous:
            if previous.task_type != "plagiarism.scan" or json.loads(previous.payload).get("native_request") != requested:
                raise HTTPException(409, "Native plagiarism operation conflicts")
            get_resource(s, user, previous.resource_id)
            return {"taskId": previous.id, "resource_id": previous.resource_id,
                    "status": previous.status, "replay": True}
        # Re-check all native authorization after the external OJ calls.
        ps_access(s, user, body.psid, True)
        current_group, current_member = verify_member(s, body.group_id, body.pid, user)
        if current_group.type != 2 or str(current_member.get("desId")) != str(member.get("desId")):
            raise HTTPException(409, "Native programming group changed during snapshot creation")
        row = AiResource(id=uid(), kind="plagiarism", owner_id=owner_id, owner_username=user["username"], psid=body.psid,
                         data=dumps({"title": body.title, "language": body.language, "threshold": body.threshold,
                                     "native_snapshot": {"id": uid(), "sha256": snapshot_hash, "count": len(records),
                                                         "psid": body.psid, "group_id": body.group_id, "pid": body.pid,
                                                         "selection_policy": body.selection_policy}}), state="draft")
        snapshot_id = json.loads(row.data)["native_snapshot"]["id"]
        s.add(row)
        s.flush()
        snapshot = AiPlagiarismSnapshot(id=snapshot_id, resource_id=row.id, resource_version=row.version,
                                         psid=body.psid, group_id=body.group_id, pid=body.pid,
                                         selection_policy=body.selection_policy, sha256=snapshot_hash, count=len(records))
        s.add(snapshot)
        for item in records:
            s.add(AiPlagiarismSubmission(snapshot_id=snapshot_id, answer_detail_id=item["answer_detail_id"],
                                         native_submission_id=item["native_submission_id"], user_id=item["user_id"],
                                         username=item["username"], code=item["code"],
                                         code_sha256=hashlib.sha256(item["code"].encode()).hexdigest(),
                                         gmt_create=None if item["gmt_create"] == datetime.min else item["gmt_create"],
                                         judge_result=item["judge_result"], judge_score=item["judge_score"]))
        payload = {"native_request": requested, "resource_version": row.version}
        job = submit(s, user, "plagiarism.scan", payload, operation, row, psid=body.psid)
        return {"taskId": job.id, "resource_id": row.id, "status": job.status, "snapshot_count": len(records), "replay": False}


def authorized_group(session, group_id, user):
    group = session.query(ProblemGroup).filter_by(gid=group_id).with_for_update().one_or_none()
    if not group:
        raise HTTPException(404, "Problem group not found")
    if not ("superadmin" in (user.get("roles") or []) or group.username == user["username"] or group.manageGroupId in (user.get("groups") or [])):
        raise HTTPException(403, "Native source group access revoked")
    return group


def verify_member(session, group_id, pid, user):
    group = authorized_group(session, group_id, user)
    member = next((item for item in json.loads(group.problemInfo) if str(item.get("pid")) == str(pid)), None)
    if not member:
        raise HTTPException(403, "Problem is not a member of the authorized group")
    return group, member


async def program_info(code, description_id=None):
    params = {"problemCode": code, "problemDescriptionId": description_id or 0}
    value = await make_get("problem-service", "/internal/problem/query", params)
    if not isinstance(value, dict) or not value.get("problemId"):
        raise HTTPException(502, "Native programming problem unavailable")
    if not description_id and value.get("defaultDescriptionId"):
        value = await make_get("problem-service", "/internal/problem/query", {"problemCode": code, "problemDescriptionId": value["defaultDescriptionId"]})
    return value


@router.post("/native/import")
@api_response()
async def import_native(body: NativeImport, user=Depends(active_user)):
    if bool(body.refresh_resource_id) != bool(body.version):
        raise HTTPException(400, "Native refresh requires both resource ID and version")
    group_manager(body.group_id, user)
    with transaction() as s:
        group, member = verify_member(s, body.group_id, body.pid, user)
        native_type = group.type
        if native_type == 0:
            original = s.query(ProblemObjective).filter_by(pid=body.pid).one()
            content = json.loads(original.content)
            answers = json.loads(original.answer)
            value = {"question_type": "single_choice" if original.type == 0 else "multiple_choice",
                     "language": "zh", "original": content["description"], "zh": content["description"],
                     "options": [{"label": chr(65+i), "original": text, "zh": text} for i, text in enumerate(content["choice"])],
                     "answer": ",".join(answers), "explanation": "", "tags": []}
            native = {"type": "objective", "pid": str(body.pid), "objective_type": original.type,
                      "answer_labels": answers}
        elif native_type == 1:
            original = s.query(ProblemSubjective).filter_by(pid=body.pid).one()
            config = json.loads(original.config)
            value = {"question_type": "short_answer", "language": "zh", "original": original.description,
                     "zh": original.description, "options": [], "answer": "\n".join(x.get("answer", "") for x in config.get("judgeConfig", [])),
                     "explanation": "", "tags": [], "rubric": config.get("judgeConfig", [])}
            native = {"type": "subjective", "pid": str(body.pid), "subjective_type": original.type, "config": config}
        elif native_type != 2:
            raise HTTPException(400, "Unsupported native group type")
        description_id = member.get("desId")
        if body.description_id and str(body.description_id) != str(description_id):
            raise HTTPException(400, "Description must match the group reference")
    if native_type == 2:
        code = await make_get("problem-service", "/internal/problem/problemIdToProblemCode", {"problemId": body.pid})
        info = await program_info(code, description_id)
        description = info.get("problemDescriptionDTO") or {}
        markdown = description.get("markdownDescription")
        if not markdown:
            raise HTTPException(409, "Native description has no Markdown; convert it explicitly before importing")
        value = {"question_type": "programming", "language": "zh", "original": markdown, "zh": markdown,
                 "title": info["problemTitle"], "options": [], "answer": "", "explanation": "", "tags": [],
                 "limits": {key: info.get(key) for key in ("timeLimit", "memoryLimit", "outputLimit")}}
        native = {"type": "programming", "pid": str(info["problemId"]), "problem_code": info["problemCode"],
                  "description_id": str(description.get("id") or info["defaultDescriptionId"])}
    native["group_id"] = body.group_id
    value["native"] = native
    value["native_input_hash"] = digest(value)
    key = digest({"native": {key: native[key] for key in ("type", "pid", "group_id")}, "owner": principal(user)})
    with transaction() as s:
        from auto_task.native_bank import read_native
        source = read_native(s, native)
        if source:
            value["native_signature"] = source[2]
        current_group, current_member = verify_member(s, body.group_id, body.pid, user)
        if current_group.type != native_type or str(current_member.get("desId")) != str(description_id):
            raise HTTPException(409, "Native group reference changed during lookup")
        if body.refresh_resource_id:
            row = get_resource(s, user, body.refresh_resource_id, True)
            current = json.loads(row.data)
            source = current.get("native") or {}
            if (row.kind != "question" or str(source.get("group_id")) != str(body.group_id)
                    or str(source.get("pid")) != str(native.get("pid"))):
                raise HTTPException(409, "Resource is not an import of this native question")
            if row.version != body.version:
                raise HTTPException(409, "Imported resource version conflict")
            if current.get("native_input_hash") == value["native_input_hash"]:
                return {**resource_view(row), "replay": True, "refreshed": False}
            duplicate = s.query(AiResource.id).filter(AiResource.canonical_key == key,
                                                      AiResource.id != row.id).first()
            if duplicate:
                raise HTTPException(409, "Another AI resource already represents this native question")
            # Keep teacher-owned annotations while replacing the immutable OJ
            # source snapshot. Native fields (including answer/options and
            # source hash) always come from the fresh import; local tags and
            # explanations are explicitly preserved so a sync is not lossy.
            merged = {**value, **{field: current[field] for field in ("tags", "explanation")
                                  if field in current}}
            row.data, row.canonical_key = dumps(merged), key
            row.search_text, row.state, row.version = merged["zh"], "ready", row.version + 1
            return {**resource_view(row), "replay": False, "refreshed": True}
        existing = s.query(AiResource).filter_by(canonical_key=key).with_for_update().first()
        if not existing:
            # Reuse pointers created before stable native IDs replaced content-based keys.
            for candidate in s.query(AiResource).filter_by(kind="question", owner_id=principal(user)):
                binding = json.loads(candidate.data).get("native", {})
                if all(str(binding.get(field)) == str(native.get(field)) for field in ("type", "pid", "group_id")):
                    existing = candidate
                    existing.canonical_key = key
                    break
        if existing:
            if existing.deleted:
                raise HTTPException(409, "Imported resource was deleted; restoration must be explicit")
            if json.loads(existing.data).get("native_input_hash") != value["native_input_hash"]:
                current = json.loads(existing.data)
                value.update({field: current[field] for field in ("tags", "explanation") if field in current})
                existing.data, existing.version = dumps(value), existing.version + 1
            return {**resource_view(existing), "replay": True}
        row = AiResource(id=uid(), kind="question", owner_id=principal(user), owner_username=user["username"],
                         data=dumps(value), canonical_key=key, search_text=value["zh"], state="ready")
        s.add(row)
        s.flush()
        return resource_view(row)


class ProgramPlan(BaseModel):
    version: int = Field(..., ge=1)
    title: str = Field(..., min_length=1, max_length=128)
    time_limit: int = Field(1000, ge=1, le=30000)
    memory_limit: int = Field(262144, ge=1024, le=1048576)
    output_limit: int = Field(65536, ge=1024, le=1048576)


def publication_marker(resource_id, version):
    return "autolab-ai:{}:v{}".format(resource_id, version)


@router.post("/native/program/{resource_id}/plan")
@api_response()
def program_plan(resource_id: str, body: ProgramPlan, user=Depends(active_user)):
    require_admin(user)
    with transaction() as s:
        row = get_resource(s, user, resource_id)
        if row.version != body.version:
            raise HTTPException(409, "Resource version changed")
        data = json.loads(row.data)
        if row.kind == "authoring":
            data = data.get("candidate") or {}
            markdown = data.get("description")
        elif row.kind == "question" and data.get("question_type") == "programming":
            markdown = data.get("zh")
        else:
            raise HTTPException(400, "Expected programming question or authoring candidate")
        if not markdown:
            raise HTTPException(409, "No programming statement")
        marker = publication_marker(row.id, row.version)
        return {"resource_id": row.id, "version": row.version, "marker": marker,
                "create_path": "/manage/problem/create",
                "problem": {"problemTitle": body.title, "isPublic": 0, "source": marker,
                            "timeLimit": body.time_limit, "memoryLimit": body.memory_limit,
                            "outputLimit": body.output_limit, "managerGroups": [], "judgeTemplates": []},
                "description": {"title": "Autolab AI", "markdownDescription": markdown, "isPublic": 0},
                "ready_for_judging": False,
                "native_configuration_path": "/v2/manage/problem/program"}


class ProgramBind(BaseModel):
    version: int = Field(..., ge=1)
    group_id: int
    problem_code: str = Field(..., min_length=1, max_length=128)
    score: int = Field(100, ge=0, le=10000)


@router.post("/native/program/{resource_id}/bind")
@api_response()
async def bind_program(resource_id: str, body: ProgramBind, user=Depends(active_user)):
    require_admin(user)
    group_manager(body.group_id, user)
    with transaction() as s:
        row = get_resource(s, user, resource_id)
        if row.kind not in ("question", "authoring"):
            raise HTTPException(400, "Expected programming publication")
        if row.version != body.version:
            raise HTTPException(409, "Resource version changed")
        data = json.loads(row.data)
        expected = data.get("candidate", {}).get("description") if row.kind == "authoring" else data.get("zh")
    info = await program_info(body.problem_code)
    if info.get("source") != publication_marker(resource_id, body.version):
        raise HTTPException(409, "Programming problem is not the prepared native publication")
    description = info.get("problemDescriptionDTO") or {}
    if description.get("markdownDescription") != expected:
        raise HTTPException(409, "Native statement differs from the prepared version")
    if str(description.get("userId")) != principal(user):
        raise HTTPException(403, "Native publication must belong to the authenticated publisher")
    with transaction() as s:
        row = get_resource(s, user, resource_id, True)
        if row.version != body.version:
            raise HTTPException(409, "Resource changed during native lookup")
        group = authorized_group(s, body.group_id, user)
        if group.type != 2:
            raise HTTPException(400, "Expected native programming group")
        entries = json.loads(group.problemInfo)
        pid, des_id = int(info["problemId"]), int(description["id"])
        previous = next((x for x in entries if str(x["pid"]) == str(pid)), None)
        if previous and (str(previous.get("desId")) != str(des_id) or previous["score"] != body.score):
            raise HTTPException(409, "Native group binding conflict")
        if not previous:
            entries.append({"pid": pid, "desId": des_id, "name": info["problemTitle"], "score": body.score,
                            "submitLimit": -1, "antiCheatingRate": 0.85})
            group.problemInfo = dumps(entries)
        data = json.loads(row.data)
        publication = {"type": "programming", "problem_code": info["problemCode"], "pid": str(pid),
                       "description_id": str(des_id), "group_id": group.gid, "score": body.score, "version": row.version}
        publications = data.setdefault("native_publications", [])
        if publication not in publications:
            publications.append(publication)
            row.data = dumps(data)
        return {**publication, "replay": bool(previous), "ready_for_judging": False,
                "readiness": "requires_native_checkpoint_and_judge_configuration_verification"}
