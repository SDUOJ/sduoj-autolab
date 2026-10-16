"""Human-approved writes to Autolab's native models, with version/hash checks."""

import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from auth import group_manager
from db import ProblemGroup, ProblemObjective, ProblemSubjective, ProblemSet, ProblemSetAnswerSheet, ProblemSetAnswerSheetDetail
from utils import api_response
from auto_task.entities import AiPlagiarismSnapshot, AiPlagiarismSubmission, AiResource, TaskJob, uid
from auto_task.legacy_bridge import answer_snapshot
from auto_task.resources import get_resource, principal, ps_access, require_admin
from auto_task.store import digest, dumps, log, transaction
from server.auto_task_ai import active_user

router = APIRouter()


class Approval(BaseModel):
    version: int = Field(..., ge=1)
    overwrite_manual: bool = False
    reason: str = Field(..., min_length=1, max_length=2000)


@router.post("/grading/{resource_id}/adopt")
@api_response()
def adopt_grade(resource_id: str, body: Approval, user=Depends(active_user)):
    with transaction() as s:
        row = get_resource(s, user, resource_id, True)
        if row.kind != "grading":
            raise HTTPException(400, "Expected grading candidate")
        data = json.loads(row.data)
        if row.state == "adopted":
            return {"adopted": True, "replay": True}
        if row.version != body.version:
            raise HTTPException(409, "Candidate version conflict")
        candidate = data.get("candidate", {})
        source = candidate.get("source")
        job = s.query(TaskJob).filter_by(id=data.get("candidate_job_id"), status="success").first()
        if not source or not job or job.resource_id != row.id:
            raise HTTPException(409, "Candidate has no verified native answer snapshot")
        ps_access(s, user, source["psid"], True)
        locked = s.query(ProblemSetAnswerSheetDetail).filter_by(asd_id=source["asd_id"]).with_for_update().one()
        detail, current = answer_snapshot(s, source["psid"], source["gid"], source["pid"], source["username"])
        if current["input_hash"] != source["input_hash"] or current["grade_hash"] != source["grade_hash"]:
            raise HTTPException(409, "Answer/rubric/grade changed; re-evaluate before adoption")
        if json.loads(detail.judgeLog or "[]") and not body.overwrite_manual:
            raise HTTPException(409, "Explicit confirmation required to replace an existing grade")
        before = {"judgeLog": detail.judgeLog, "judgeComment": detail.judgeComment}
        locked.judgeLog = dumps(candidate["judgeLog"])
        locked.judgeComment = candidate["judgeComment"]
        locked.judgeLock_username = user["username"]
        row.state, row.version = "adopted", row.version + 1
        log(s, job.id, "log", {"event": "candidate_adopted", "actor": principal(user), "reason": body.reason,
                               "before": before, "after": candidate["judgeLog"], "asd_id": locked.asd_id})
        return {"adopted": True, "asd_id": locked.asd_id, "version": row.version}


@router.post("/resources/{resource_id}/review")
@api_response()
def review_candidate(resource_id: str, body: Approval, accept: bool = True, user=Depends(active_user)):
    with transaction() as s:
        row = get_resource(s, user, resource_id, True)
        if row.version != body.version:
            raise HTTPException(409, "Resource version conflict")
        data = json.loads(row.data)
        if not data.get("candidate"):
            raise HTTPException(409, "No candidate result")
        data.setdefault("reviews", []).append({"actor": principal(user), "accepted": accept, "reason": body.reason, "at": datetime.utcnow().isoformat()})
        row.data, row.version, row.state = dumps(data), row.version + 1, "approved" if accept else "rejected"
        return {"state": row.state, "version": row.version, "native_grade_changed": False}


class PublishQuestion(BaseModel):
    version: int = Field(..., ge=1)
    group_id: int
    score: int = Field(..., ge=0, le=10000)
    correct_labels: List[str] = Field(default_factory=list)
    dry_run: bool = True


@router.post("/questions/{resource_id}/publish")
@api_response()
def publish_question(resource_id: str, body: PublishQuestion, user=Depends(active_user)):
    group_manager(body.group_id, user)
    with transaction() as s:
        row = get_resource(s, user, resource_id, True)
        if row.kind != "question" or row.version != body.version:
            raise HTTPException(409, "Question version mismatch")
        data = json.loads(row.data)
        publications = data.get("publications", [])
        if data.get("question_type") == "programming":
            raise HTTPException(400, "Use the native OJ programming-problem publishing flow")
        previous = next((p for p in publications if p["group_id"] == body.group_id and p["version"] == body.version), None)
        if previous:
            if previous.get("request_hash") != digest(body.dict(exclude={"dry_run"})):
                raise HTTPException(409, "Publication request conflicts with the existing version")
            return {**previous, "replay": True}
        group = s.query(ProblemGroup).filter_by(gid=body.group_id).with_for_update().one()
        choice = data.get("question_type") in ("single_choice", "multiple_choice", "true_false")
        if group.type != (0 if choice else 1):
            raise HTTPException(400, "Target group type mismatch; programming questions must use native OJ publishing")
        labels = [option["label"] for option in data.get("options", [])]
        if choice and (len(labels) != len(set(labels)) or len(labels) > 26):
            raise HTTPException(400, "Invalid option labels")
        if choice and (not body.correct_labels or not set(body.correct_labels).issubset(labels)):
            raise HTTPException(400, "Explicit correct option labels required")
        if data.get("question_type") in ("single_choice", "true_false") and len(body.correct_labels) != 1:
            raise HTTPException(400, "Single-choice questions need one correct label")
        if body.dry_run:
            return {"dry_run": True, "group_id": group.gid, "native_type": "objective" if choice else "subjective"}
        if choice:
            native_type = 1 if data["question_type"] == "multiple_choice" else 0
            if native_type == 1 and data.get("native", {}).get("objective_type") == 2:
                native_type = 2
            item = ProblemObjective(type=native_type,
                                    content=dumps({"description": data["zh"], "choice": [o["zh"] for o in data["options"]]}),
                                    answer=dumps([chr(65 + labels.index(label)) for label in body.correct_labels]), username=user["username"])
        else:
            native = data.get("native", {})
            config = native.get("config") if native.get("type") == "subjective" else None
            item = ProblemSubjective(type=native.get("subjective_type", 1), description=data["zh"], username=user["username"],
                                     config=dumps(config or {"maxCount": 10000, "judgeConfig": [{"name": "Answer", "score": body.score, "answer": data.get("answer", "")}]}))
        s.add(item)
        s.flush()
        entries = json.loads(group.problemInfo)
        entries.append({"pid": item.pid, "score": body.score, "submitLimit": -1, "antiCheatingRate": 0.85})
        group.problemInfo = dumps(entries)
        publication = {"group_id": group.gid, "pid": item.pid, "version": row.version, "actor": principal(user), "request_hash": digest(body.dict(exclude={"dry_run"}))}
        data.setdefault("publications", []).append(publication)
        row.data = dumps(data)
        return publication


class PublishExam(BaseModel):
    version: int = Field(..., ge=1)
    problem_set: dict
    dry_run: bool = True
    formal_ack: bool = False
    validated_test_id: Optional[str] = None


@router.post("/exams/{resource_id}/prepare")
@api_response()
async def prepare_exam(resource_id: str, body: PublishExam, user=Depends(active_user)):
    from ser.problem_set import problem_set_base
    from server.auto_task import _assert_group_creator
    parsed = problem_set_base.parse_obj(body.problem_set)
    await _assert_group_creator(parsed.groupId, user)
    for group in parsed.groupInfo:
        group_manager(group.gid, user)
    if parsed.type != 1:
        raise HTTPException(400, "Exam publication must use ProblemSet.type=1")
    with transaction() as s:
        row = get_resource(s, user, resource_id, True)
        data = json.loads(row.data)
        if row.kind != "exam" or row.version != body.version or not data.get("candidate") or data.get("native_psid"):
            raise HTTPException(409, "Prepare a current unpublished examination plan first")
        data["publication_plan"] = parsed.dict()
        data["publication_hash"] = digest(parsed.dict())
        if str(data.get("mode", "TEST")).upper() == "FORMAL":
            if not body.validated_test_id or body.validated_test_id == row.id:
                raise HTTPException(409, "A separate validated TEST examination is required")
            tested = get_resource(s, user, body.validated_test_id)
            tested_data = json.loads(tested.data)
            certificate = tested_data.get("validation_certificate")
            if tested.kind != "exam" or tested_data.get("mode") != "TEST" or not certificate or certificate.get("version") != tested.version:
                raise HTTPException(409, "TEST examination has no current validation certificate")
            if certificate["groups_hash"] != digest([group.dict() for group in parsed.groupInfo]):
                raise HTTPException(409, "Formal question groups differ from validated TEST")
            data["validated_test"] = {"id": tested.id, "version": tested.version, "certificate": certificate}
        row.data, row.version, row.state = dumps(data), row.version + 1, "review"
        return {"version": row.version, "publication_hash": data["publication_hash"], "state": row.state}


@router.post("/exams/{resource_id}/publish")
@api_response()
async def publish_exam(resource_id: str, body: PublishExam, user=Depends(active_user)):
    from ser.problem_set import problem_set_base
    from server.auto_task import _assert_group_creator
    parsed = problem_set_base.parse_obj(body.problem_set)
    await _assert_group_creator(parsed.groupId, user)
    for group in parsed.groupInfo:
        group_manager(group.gid, user)
    with transaction() as s:
        row = get_resource(s, user, resource_id, True)
        if row.kind != "exam" or row.version != body.version:
            raise HTTPException(409, "Exam version mismatch")
        data = json.loads(row.data)
        if data.get("publication_hash") != digest(parsed.dict()):
            raise HTTPException(409, "Publication differs from the prepared examination; prepare and review again")
        if data.get("native_psid"):
            return {"psid": data["native_psid"], "replay": True}
        if row.state != "approved":
            raise HTTPException(409, "Teacher review required before publishing")
        if parsed.type != 1:
            raise HTTPException(400, "Exam publication must use ProblemSet.type=1")
        mode = str(data.get("mode", "TEST")).upper()
        if mode not in ("TEST", "FORMAL"):
            raise HTTPException(400, "Unsupported examination mode")
        if mode == "FORMAL" and not body.formal_ack:
            raise HTTPException(409, "Formal examination publication requires explicit acknowledgement")
        if mode == "FORMAL":
            reference = data.get("validated_test") or {}
            if not reference.get("id"):
                raise HTTPException(409, "Prepare using a validated TEST examination first")
            tested = get_resource(s, user, reference["id"])
            if tested.version != reference["version"] or json.loads(tested.data).get("validation_certificate") != reference["certificate"]:
                raise HTTPException(409, "TEST validation changed; prepare again")
        if body.dry_run:
            return {"dry_run": True, "group_count": len(parsed.groupInfo), "mode": mode, "formal_ack": body.formal_ack}
        values = parsed.dict()
        for key in ("tm_start", "tm_end"):
            if values.get(key) is not None:
                values[key] = datetime.fromtimestamp(values[key] / 1000)
        for key in ("config", "groupInfo"):
            values[key] = dumps(values[key])
        native = ProblemSet(username=user["username"], **values)
        s.add(native)
        s.flush()
        data["native_psid"] = native.psid
        row.data, row.state, row.psid = dumps(data), "published", native.psid
        return {"psid": native.psid, "mode": data.get("mode", "TEST")}


class PairReview(BaseModel):
    version: int = Field(..., ge=1)
    left: str
    right: str
    decision: str
    reason: str = Field(..., min_length=1, max_length=2000)


class PlagiarismWriteback(BaseModel):
    version: int = Field(..., ge=1)
    reason: str = Field(..., min_length=1, max_length=2000)
    require_all_reviewed: bool = True
    overwrite_existing: bool = False


@router.post("/plagiarism/{resource_id}/pairs/review")
@api_response()
def review_pair(resource_id: str, body: PairReview, user=Depends(active_user)):
    if body.decision not in ("CLEAR", "SUSPICIOUS", "CONFIRMED"):
        raise HTTPException(400, "Invalid pair review decision")
    with transaction() as s:
        row = get_resource(s, user, resource_id, True)
        if row.kind != "plagiarism" or row.version != body.version:
            raise HTTPException(409, "Plagiarism candidate version conflict")
        if row.state == "adopted":
            raise HTTPException(409, "Published plagiarism decisions are immutable; create a new scan")
        data = json.loads(row.data)
        pairs = data.get("candidate", {}).get("pairs", [])
        pair = next((item for item in pairs if (
            (str(item.get("left")) == body.left and str(item.get("right")) == body.right) or
            (str(item.get("left")) == body.right and str(item.get("right")) == body.left)
        )), None)
        if pair is None:
            raise HTTPException(404, "Candidate pair not found")
        pair.setdefault("decisions", []).append({"decision": body.decision, "reason": body.reason,
            "actor_id": principal(user), "created_at": datetime.utcnow().isoformat() + "Z"})
        row.data, row.version = dumps(data), row.version + 1
        return {"version": row.version, "decision": body.decision, "native_grade_changed": False}


@router.post("/plagiarism/{resource_id}/writeback")
@api_response()
def writeback_plagiarism(resource_id: str, body: PlagiarismWriteback, user=Depends(active_user)):
    """Explicitly publish reviewed plagiarism evidence to native OJ answer sheets."""
    with transaction() as s:
        row = get_resource(s, user, resource_id, True)
        if row.kind != "plagiarism":
            raise HTTPException(400, "Expected plagiarism resource")
        data = json.loads(row.data)
        native = data.get("native_snapshot")
        candidate = data.get("candidate") or {}
        pairs = candidate.get("pairs") or []
        if not isinstance(native, dict) or not isinstance(candidate, dict):
            raise HTTPException(409, "Native plagiarism candidate is unavailable")
        generated = s.query(TaskJob).filter_by(resource_id=row.id, task_type="plagiarism.scan", status="success").order_by(TaskJob.finished_at.desc()).first()
        if not generated or not generated.result:
            raise HTTPException(409, "Native plagiarism candidate has no successful scan task")
        generated_result = json.loads(generated.result)
        candidate_without_decisions = dict(candidate)
        candidate_without_decisions["pairs"] = [
            {key: value for key, value in item.items() if key != "decisions"} for item in pairs
        ]
        if digest(candidate_without_decisions) != digest(generated_result):
            raise HTTPException(409, "Plagiarism evidence changed after the successful scan")
        snapshot = s.query(AiPlagiarismSnapshot).filter_by(id=native.get("id"), resource_id=row.id).one_or_none()
        if not snapshot or snapshot.sha256 != native.get("sha256"):
            raise HTTPException(409, "Native plagiarism snapshot is unavailable")

        def latest(item):
            decisions = item.get("decisions") or []
            return decisions[-1] if decisions else None

        decisions = [latest(item) for item in pairs]
        if body.require_all_reviewed and any(item is None for item in decisions):
            raise HTTPException(409, "Every candidate pair requires a teacher decision")
        request_hash = digest({"snapshot": snapshot.sha256,
                               "decisions": [{"left": item.get("left"), "right": item.get("right"),
                                               "decision": (latest(item) or {}).get("decision")}
                                              for item in pairs],
                               "reason": body.reason})
        previous = data.get("native_writeback")
        if previous and previous.get("request_hash") == request_hash:
            return {"version": row.version, "native_grade_changed": True, "replay": True,
                    "updated": previous.get("updated", 0)}
        if row.version != body.version:
            raise HTTPException(409, "Plagiarism candidate version conflict")
        submissions = s.query(AiPlagiarismSubmission).filter_by(snapshot_id=snapshot.id).order_by(
            AiPlagiarismSubmission.native_submission_id).all()
        if len(submissions) != snapshot.count:
            raise HTTPException(409, "Native plagiarism snapshot is incomplete")
        import hashlib
        if any(hashlib.sha256(item.code.encode()).hexdigest() != item.code_sha256 for item in submissions):
            raise HTTPException(409, "Native plagiarism source snapshot changed")
        # Re-check the native scope at the moment of the write. A removed
        # question/group must never receive a stale AI result.
        ps = ps_access(s, user, snapshot.psid, True)
        from server.auto_task_native import verify_member
        group, _ = verify_member(s, snapshot.group_id, snapshot.pid, user)
        if group.type != 2 or not any(str(item.get("gid")) == str(snapshot.group_id) for item in json.loads(ps.groupInfo)):
            raise HTTPException(403, "Native problem scope is no longer writable")
        rates = {item.native_submission_id: 0.0 for item in submissions}
        pair_evidence = []
        for pair, decision in zip(pairs, decisions):
            if decision is None:
                continue
            score = float(pair.get("similarity", 0) or 0)
            if decision.get("decision") == "CLEAR":
                score = 0.0
            for key in (pair.get("left_submission_id"), pair.get("right_submission_id")):
                if key in rates:
                    rates[key] = max(rates[key], score)
            pair_evidence.append({"left": pair.get("left"), "right": pair.get("right"),
                                  "left_submission_id": pair.get("left_submission_id"),
                                  "right_submission_id": pair.get("right_submission_id"),
                                  "similarity": float(pair.get("similarity", 0) or 0),
                                  "decision": decision.get("decision"), "reason": decision.get("reason", "")})
        details = s.query(ProblemSetAnswerSheetDetail).join(
            ProblemSetAnswerSheet, ProblemSetAnswerSheetDetail.asid == ProblemSetAnswerSheet.asid
        ).filter(ProblemSetAnswerSheet.psid == snapshot.psid,
                 ProblemSetAnswerSheetDetail.gid == snapshot.group_id,
                 ProblemSetAnswerSheetDetail.pid == snapshot.pid,
                 ProblemSetAnswerSheetDetail.asd_id.in_([item.answer_detail_id for item in submissions])
        ).with_for_update().all()
        by_id = {item.asd_id: item for item in details}
        if len(by_id) != len(submissions):
            raise HTTPException(409, "Native answer sheet changed or was removed")
        from server.auto_task_native import _submission_ids
        if any(item.native_submission_id not in _submission_ids(by_id[item.answer_detail_id].answer)
               for item in submissions):
            raise HTTPException(409, "Native answer submission changed after the snapshot")
        existing = [item for item in details if item.antiCheatingResult]
        if existing and not body.overwrite_existing:
            raise HTTPException(409, "Native anti-cheating result already exists; explicit overwrite required")
        for item in submissions:
            related = []
            for evidence in pair_evidence:
                if item.native_submission_id not in (evidence["left_submission_id"], evidence["right_submission_id"]):
                    continue
                other = evidence["right_submission_id"] if evidence["left_submission_id"] == item.native_submission_id else evidence["left_submission_id"]
                related.append({"id": other, "rate": 0.0 if evidence["decision"] == "CLEAR" else evidence["similarity"],
                                "decision": evidence["decision"]})
            native_result = {"summary": round(rates[item.native_submission_id], 6),
                             "detail": related, "resourceId": row.id, "resourceVersion": row.version,
                             "snapshotSha256": snapshot.sha256}
            by_id[item.answer_detail_id].antiCheatingResult = dumps(native_result)
        data["native_writeback"] = {"request_hash": request_hash, "updated": len(submissions),
                                    "at": datetime.utcnow().isoformat() + "Z", "actor_id": principal(user)}
        row.data, row.version, row.state = dumps(data), row.version + 1, "adopted"
        return {"version": row.version, "native_grade_changed": True, "updated": len(submissions), "replay": False}


@router.get("/plagiarism/{resource_id}/report")
def plagiarism_report(resource_id: str, user=Depends(active_user)):
    from auto_task.configuration import safe_path
    with transaction() as s:
        row = get_resource(s, user, resource_id, True)
        relative = json.loads(row.data).get("candidate", {}).get("report")
        if row.kind != "plagiarism" or not relative:
            raise HTTPException(404, "Plagiarism report unavailable")
        root = safe_path("workspaces/" + row.id)
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(404, "Plagiarism report unavailable")
        return FileResponse(path, filename="plagiarism.jplag", media_type="application/zip",
                            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


class TagInput(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    aliases: List[str] = Field(default_factory=list, max_items=20)


@router.get("/tags")
@api_response()
def tags(user=Depends(active_user)):
    with transaction() as s:
        return [{"id": row.id, **json.loads(row.data)} for row in s.query(AiResource).filter_by(kind="tag", deleted=0).order_by(AiResource.search_text)]


@router.post("/tags/upsert")
@api_response()
def upsert_tag(body: TagInput, user=Depends(active_user)):
    require_admin(user)
    import unicodedata
    normalize = lambda text: unicodedata.normalize("NFKC", text).strip().casefold()
    if not normalize(body.name):
        raise HTTPException(400, "Tag name cannot be blank")
    aliases = [str(alias).strip() for alias in body.aliases]
    if any(not alias or len(alias) > 64 for alias in aliases):
        raise HTTPException(400, "Tag aliases must be non-empty and at most 64 characters")
    if len({normalize(alias) for alias in aliases}) != len(aliases):
        raise HTTPException(400, "Tag aliases must be unique")
    if normalize(body.name) in {normalize(alias) for alias in aliases}:
        raise HTTPException(400, "Tag alias cannot equal the canonical name")
    body = body.copy(update={"name": body.name.strip(), "aliases": aliases})
    requested = {normalize(x) for x in [body.name, *body.aliases]}
    with transaction() as s:
        key = digest({"tag": normalize(body.name)})
        row = s.query(AiResource).filter_by(canonical_key=key).first()
        for other in s.query(AiResource).filter_by(kind="tag", deleted=0):
            value = json.loads(other.data)
            names = {normalize(x) for x in [value["name"], *value.get("aliases", [])]}
            if other is not row and names & requested:
                raise HTTPException(409, "Tag name or alias is already assigned")
        if not row:
            row = AiResource(id=uid(), kind="tag", owner_id=principal(user), owner_username=user["username"],
                             canonical_key=key, state="ready")
            s.add(row)
        row.data, row.search_text = dumps(body.dict()), body.name
        row.deleted = 0
        return {"id": row.id, **body.dict()}


@router.get("/authoring/{resource_id}/bundle")
def authoring_bundle(resource_id: str, user=Depends(active_user)):
    import io
    import zipfile
    with transaction() as s:
        row = get_resource(s, user, resource_id)
        data = json.loads(row.data).get("candidate", {})
        if row.kind != "authoring" or not data.get("reference_code"):
            raise HTTPException(409, "Generate a complete authoring draft first")
        stream = io.BytesIO()
        suffix = {"c": ".c", "cpp": ".cpp", "python": ".py", "java": ".java"}[data["language"]]
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("problem.md", data["description"])
            archive.writestr("Main.java" if suffix == ".java" else "reference" + suffix, data["reference_code"])
            archive.writestr("solution.md", data.get("explanation", ""))
            archive.writestr("validation.json", dumps({"validated": data.get("validated", False), "runs": data.get("validation", [])}))
            for index, case in enumerate(data["tests"]):
                archive.writestr("tests/{:03}.in".format(index + 1), case["input"])
                archive.writestr("tests/{:03}.out".format(index + 1), case["output"])
        stream.seek(0)
        return StreamingResponse(stream, media_type="application/zip", headers={"Content-Disposition": "attachment; filename=authoring.zip", "Cache-Control": "no-store"})


class RosterInput(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000000)
    course_id: Optional[int] = None
    resolve_users: bool = False


@router.post("/exams/roster/parse")
@api_response()
async def roster(body: RosterInput, user=Depends(active_user)):
    from auto_task.handlers import parse_roster
    from auto_task.resources import course_access
    with transaction() as s:
        if body.course_id:
            course_access(s, user, body.course_id, True)
        else:
            require_admin(user)
    try:
        rows = parse_roster(body.text)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if body.resolve_users:
        from sduojApi import getUserId
        for row in rows:
            value = await getUserId(row["username"])
            row["user_id"] = str(value) if value else None
    return {"rows": rows, "creates_accounts": False}
