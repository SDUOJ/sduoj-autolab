"""Iterative authoring and verified native OJ programming publication."""

import hashlib
import json
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, validator

from auth import group_manager
from db import ProblemGroup
from sduojApi import downloadFile, make_get
from utils import api_response
from auto_task.authoring import verify_candidate
from auto_task.entities import AiResource, TaskJob
from auto_task.resources import get_resource, principal, require_admin
from auto_task.store import digest, dumps, submit, transaction
from server.auto_task_ai import active_user
from server.auto_task_native import authorized_group, program_info, publication_marker


router = APIRouter()


class GenerateRequest(BaseModel):
    version: int = Field(..., ge=1)
    operation_key: str = Field(..., min_length=1, max_length=96)
    instruction: str = Field(..., min_length=1, max_length=20000)


@router.post("/authoring/{resource_id}/generate")
@api_response()
def generate_revision(resource_id: str, body: GenerateRequest, user=Depends(active_user)):
    with transaction() as session:
        row = get_resource(session, user, resource_id, True)
        if row.kind != "authoring":
            raise HTTPException(400, "Expected an authoring resource")
        operation = "authoring-v2:" + body.operation_key
        previous = session.query(TaskJob).filter_by(owner_id=principal(user), operation_key=operation).first()
        if previous:
            saved = json.loads(previous.payload)
            if (previous.resource_id != row.id or previous.task_type != "authoring.generate"
                    or saved.get("resource_version") != body.version
                    or saved.get("authoring_protocol") != 2 or saved.get("instruction") != body.instruction):
                raise HTTPException(409, "Authoring operation conflicts")
            return {"taskId": previous.id, "status": previous.status, "replay": True}
        if row.version != body.version:
            raise HTTPException(409, "Authoring resource version conflict")
        payload = {"resource_version": body.version, "authoring_protocol": 2,
                   "instruction": body.instruction,
                   "parent_candidate_hash": (json.loads(row.data).get("candidate") or {}).get("package_hash", "")}
        job = submit(session, user, "authoring.generate", payload, operation, row)
        return {"taskId": job.id, "status": job.status, "replay": False}


class NativePlanRequest(BaseModel):
    version: int = Field(..., ge=1)
    judge_template_id: int = Field(..., ge=1)
    title: str = Field("", max_length=128)
    time_limit: int = Field(1000, ge=1, le=30000)
    memory_limit: int = Field(262144, ge=1024, le=1048576)
    output_limit: int = Field(65536, ge=1024, le=1048576)


def _candidate(session, user, resource_id, version, require_approved=True, write=False):
    row = get_resource(session, user, resource_id, write)
    if row.kind != "authoring" or row.version != version:
        raise HTTPException(409, "Authoring resource version conflict")
    if require_approved and row.state != "approved":
        raise HTTPException(409, "Teacher approval is required before native publication")
    try:
        candidate = verify_candidate((json.loads(row.data).get("candidate") or {}))
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(409, str(error)) from error
    return row, candidate


@router.post("/authoring/{resource_id}/native-plan")
@api_response()
def native_plan(resource_id: str, body: NativePlanRequest, user=Depends(active_user)):
    require_admin(user)
    with transaction() as session:
        row, candidate = _candidate(session, user, resource_id, body.version)
        marker = publication_marker(row.id, row.version)
        title = body.title.strip() or candidate["title"]
        return {"resource_id": row.id, "version": row.version, "revision": candidate["revision"],
                "package_hash": candidate["package_hash"], "marker": marker,
                "problem": {"problemTitle": title, "isPublic": 0, "source": marker,
                            "timeLimit": body.time_limit, "memoryLimit": body.memory_limit,
                            "outputLimit": body.output_limit, "managerGroups": [],
                            "judgeTemplates": [body.judge_template_id]},
                "description": {"title": "Autolab AI", "markdownDescription": candidate["description"],
                                "isPublic": 0},
                "checkpoints": [{"index": item["index"], "input": item["input"], "output": item["output"],
                                 "score": item["score"], "public": item["public"],
                                 "input_sha256": item["input_sha256"], "output_sha256": item["output_sha256"],
                                 "mode": None} for item in candidate["test_plan"]],
                "ready_for_judging": False}


class UploadedCheckpoint(BaseModel):
    index: int = Field(..., ge=1, le=30)
    checkpoint_id: int = Field(..., ge=1)


class NativeFinalizeRequest(BaseModel):
    version: int = Field(..., ge=1)
    group_id: int = Field(..., ge=1)
    problem_code: str = Field(..., min_length=1, max_length=128)
    judge_template_id: int = Field(..., ge=1)
    score: int = Field(100, ge=0, le=10000)
    checkpoints: List[UploadedCheckpoint] = Field(..., min_items=3, max_items=30)

    @validator("checkpoints")
    def distinct_receipts(cls, value):
        if len({item.index for item in value}) != len(value) or len({item.checkpoint_id for item in value}) != len(value):
            raise ValueError("Checkpoint indices and IDs must be unique")
        return value


def _template_ids(info):
    result = []
    for item in info.get("judgeTemplates") or []:
        value = item.get("id") if isinstance(item, dict) else item
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            raise HTTPException(502, "Native problem returned an invalid judge template")
    return result


def _request_hash(body, package_hash):
    return digest({"request": body.dict(), "package_hash": package_hash})


async def _download_hash(file_id):
    try:
        status, content, _ = await downloadFile(file_id)
    except Exception as error:
        raise HTTPException(502, "Native checkpoint file is unavailable") from error
    if status != 200 or not isinstance(content, bytes):
        raise HTTPException(502, "Native checkpoint file is unavailable")
    return hashlib.sha256(content).hexdigest()


@router.post("/authoring/{resource_id}/native-finalize")
@api_response()
async def native_finalize(resource_id: str, body: NativeFinalizeRequest, user=Depends(active_user)):
    require_admin(user)
    group_manager(body.group_id, user)
    with transaction() as session:
        row = get_resource(session, user, resource_id)
        if row.kind != "authoring":
            raise HTTPException(400, "Expected an authoring resource")
        data = json.loads(row.data)
        current_candidate = data.get("candidate") or {}
        body_hash = digest(body.dict())
        previous = next((item for item in data.get("native_publications", [])
                         if item.get("request_body_hash") == body_hash), None)
        if previous:
            return {**previous, "version": row.version, "replay": True}
        package_hash = current_candidate.get("package_hash", "")
        requested_hash = _request_hash(body, package_hash)
        row, candidate = _candidate(session, user, resource_id, body.version)
        plan = candidate["test_plan"]
        receipts = sorted(body.checkpoints, key=lambda item: item.index)
        if [item.index for item in receipts] != list(range(1, len(plan) + 1)):
            raise HTTPException(400, "Checkpoint receipts must cover the complete test plan")
        expected_marker = publication_marker(row.id, row.version)

    info = await program_info(body.problem_code)
    description = info.get("problemDescriptionDTO") or {}
    if info.get("source") != expected_marker or description.get("markdownDescription") != candidate["description"]:
        raise HTTPException(409, "Native problem does not match the approved authoring revision")
    if str(description.get("userId")) != principal(user):
        raise HTTPException(403, "Native problem must belong to the authenticated publisher")
    if _template_ids(info) != [body.judge_template_id]:
        raise HTTPException(409, "Native judge template differs from the approved publication plan")

    problem_id = int(info["problemId"])
    relations = await make_get("problem-service", "/internal/problem/queryProblemCheckPoints",
                               {"problemCode": body.problem_code})
    judger = await make_get("problem-service", "/internal/problem/listJudgerCheckpoints",
                            {"problemId": problem_id})
    if not isinstance(relations, list) or not isinstance(judger, list):
        raise HTTPException(502, "Native checkpoint configuration is unavailable")
    relation_rows = [(int(item["checkpointId"]), int(item["checkpointScore"])) for item in relations]
    expected_rows = [(receipt.checkpoint_id, int(plan[receipt.index - 1]["score"])) for receipt in receipts]
    if relation_rows != expected_rows:
        raise HTTPException(409, "Native checkpoint order or scores differ from the approved test plan")
    judger_by_id = {int(item["checkpointId"]): item for item in judger}
    if set(judger_by_id) != {item[0] for item in expected_rows}:
        raise HTTPException(409, "Native judger checkpoint set differs from the approved test plan")
    verified = []
    for receipt in receipts:
        expected = plan[receipt.index - 1]
        native = judger_by_id[receipt.checkpoint_id]
        input_hash = await _download_hash(native.get("inputFileId"))
        output_hash = await _download_hash(native.get("outputFileId"))
        if input_hash != expected["input_sha256"] or output_hash != expected["output_sha256"]:
            raise HTTPException(409, "Native checkpoint content differs from the approved test plan")
        verified.append({"index": receipt.index, "checkpoint_id": receipt.checkpoint_id,
                         "score": expected["score"], "public": expected["public"],
                         "input_sha256": input_hash, "output_sha256": output_hash})
    expected_public = [{"input": item["input"][:64], "output": item["output"][:64]}
                       for item in plan if item["public"]]
    actual_public = info.get("problemCaseDTOList") or []
    if [{"input": item.get("input"), "output": item.get("output")} for item in actual_public] != expected_public:
        raise HTTPException(409, "Native public example cases differ from the approved test plan")

    with transaction() as session:
        row, current = _candidate(session, user, resource_id, body.version, write=True)
        if current["package_hash"] != candidate["package_hash"]:
            raise HTTPException(409, "Authoring revision changed during native verification")
        group = authorized_group(session, body.group_id, user)
        if group.type != 2:
            raise HTTPException(400, "Expected a native programming group")
        entries = json.loads(group.problemInfo)
        des_id = int(description["id"])
        existing = next((item for item in entries if str(item.get("pid")) == str(problem_id)), None)
        if existing and (str(existing.get("desId")) != str(des_id) or int(existing.get("score", 0)) != body.score):
            raise HTTPException(409, "Native programming group binding conflicts with this publication")
        if not existing:
            entries.append({"pid": problem_id, "desId": des_id, "name": info["problemTitle"],
                            "score": body.score, "submitLimit": -1, "antiCheatingRate": 0.85})
            group.problemInfo = dumps(entries)
        data = json.loads(row.data)
        publication = {"type": "programming", "problem_code": info["problemCode"], "pid": str(problem_id),
                       "description_id": str(des_id), "group_id": group.gid, "score": body.score,
                       "source_version": body.version, "package_hash": candidate["package_hash"],
                       "judge_template_id": body.judge_template_id, "checkpoints": verified,
                       "request_hash": requested_hash, "request_body_hash": body_hash,
                       "ready_for_judging": True,
                       "verified_at": datetime.utcnow().isoformat() + "Z"}
        data.setdefault("native_publications", []).append(publication)
        row.data, row.state, row.version = dumps(data), "published", row.version + 1
        return {**publication, "version": row.version, "replay": False}
