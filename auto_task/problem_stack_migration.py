"""Offline conversion of a Prisma JSON export into the neutral AI import bundle."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


FORMAT = "problem-stack-neutral-v1"
SOURCE_FORMAT = "problem-stack-prisma-json-v1"
MAX_ASSET_SIZE = 512 * 1024 * 1024
SENSITIVE_OMISSIONS = {"callbackurl", "codexsessionid", "workspacepath", "sessionid"}
PATH_FIELDS = {
    "QuestionFigure": ("sourceImagePath", "cropPath", "svgPath"),
    "CourseDocument": ("storagePath",),
    "InterviewMediaChunk": ("storagePath",),
}
SUPPORTED_TABLES = {
    "User", "Question", "Tag", "QuestionTag", "UserQuestion", "UserQuestionTag",
    "QuestionFigure", "ProblemSet", "ProblemSetSubmission", "ProblemSetQuestion",
    "AuthoringProject", "AuthoringMessage", "IdeWorkspace", "IdeExecution",
    "IdeTutorMessage", "CourseKnowledgeBase", "CourseDocument", "CourseChunk",
    "CourseConversation", "CourseMessage", "InterviewTemplate", "InterviewSession",
    "InterviewTurn", "InterviewMediaChunk", "InterviewProctorEvent", "ChatMessage",
    "PointLedgerEntry", "AiRun", "AiRunEvent", "WorkflowJob", "ContentReview",
    "Exam", "ExamRoom", "ExamCandidate", "SubjectiveReviewTask", "PlagiarismScan",
    "PlagiarismSubmission", "PlagiarismPair", "Intake", "IntegrationCallbackAttempt", "SolveJob", "AiSetting",
}
KNOWN_FIELDS = {
    "User": {"id", "username", "displayName", "passwordHash", "role", "status", "points",
             "permanentlySuspended", "suspensionReason", "createdAt", "updatedAt"},
    "Question": {"id", "type", "status", "languageOriginal", "stemOriginal", "stemZh", "options",
                 "answerZh", "solutionZh", "sourceImages", "canonicalHash", "deduplicationKey",
                 "searchText", "errorMessage", "codexSessionId", "workspacePath", "verificationStatus",
                 "solutionStatus", "verificationSummary", "verificationEvidence", "verifiedAt", "createdAt",
                 "updatedAt", "ownerId"},
    "Tag": {"id", "name", "slug", "aliases", "createdAt"},
    "QuestionTag": {"questionId", "tagId"},
    "UserQuestion": {"userId", "questionId", "createdAt"},
    "UserQuestionTag": {"userId", "questionId", "tagId", "createdAt"},
    "QuestionFigure": {"id", "questionId", "label", "descriptionZh", "sourceImagePath", "cropPath",
                       "svgPath", "sourceImageIndex", "bbox", "calibrationNotes", "createdAt"},
    "ProblemSet": {"id", "name", "createdAt", "updatedAt", "ownerId"},
    "ProblemSetSubmission": {"id", "problemSetId", "textInput", "imagePaths", "requestedNumber",
                             "holdForMore", "autoSolve", "status", "statusMessage", "errorMessage",
                             "draftItems", "workspacePath", "claimedBySubmissionId", "createdAt", "updatedAt",
                             "ownerId"},
    "ProblemSetQuestion": {"id", "problemSetId", "questionId", "sourceSubmissionId", "problemNumber", "createdAt"},
    "AuthoringProject": {"id", "title", "brief", "currentDraft", "draftVersion", "codexSessionId",
                         "workspacePath", "ownerId", "createdAt", "updatedAt"},
    "AuthoringMessage": {"id", "projectId", "role", "status", "content", "jobId", "createdAt", "updatedAt"},
    "IdeWorkspace": {"id", "title", "problemText", "language", "code", "stdin", "testCases", "examMode",
                     "codexSessionId", "workspacePath", "ownerId", "createdAt", "updatedAt"},
    "IdeExecution": {"id", "workspaceId", "kind", "status", "sourceSnapshot", "stdinSnapshot", "stdout",
                     "stderr", "exitCode", "durationMs", "passedTests", "totalTests", "testResults", "jobId",
                     "createdAt", "completedAt"},
    "IdeTutorMessage": {"id", "workspaceId", "role", "status", "content", "jobId", "createdAt", "updatedAt"},
    "CourseKnowledgeBase": {"id", "name", "description", "ownerId", "createdAt", "updatedAt"},
    "CourseDocument": {"id", "knowledgeBaseId", "title", "sourceType", "storagePath", "contentHash", "status",
                       "extractedText", "errorMessage", "jobId", "createdAt", "updatedAt"},
    "CourseChunk": {"id", "documentId", "chunkIndex", "heading", "locator", "content", "searchText",
                    "tokenEstimate", "createdAt"},
    "CourseConversation": {"id", "knowledgeBaseId", "title", "createdAt", "updatedAt"},
    "CourseMessage": {"id", "conversationId", "role", "status", "content", "citations", "academicSearch",
                      "jobId", "createdAt", "updatedAt"},
    "InterviewTemplate": {"id", "name", "acceptanceRequirements", "rubric", "maxQuestions", "durationMinutes",
                          "createdAt", "updatedAt", "ownerId"},
    "InterviewSession": {"id", "templateId", "ownerId", "externalSystem", "externalId", "studentExternalId",
                         "studentName", "accessTokenHash", "accessTokenExpiresAt", "status", "statusMessage",
                         "problemTitle", "problemDescription", "studentCode", "codeLanguage", "reportContent",
                         "acceptanceRequirements", "rubric", "maxQuestions", "durationMinutes", "preflightSnapshot",
                         "codexSessionId", "workspacePath", "rubricScores", "totalScore", "confidence",
                         "integrityConclusion", "finalSummary", "reviewNotes", "callbackUrl", "callbackStatus",
                         "errorMessage", "startedAt", "completedAt", "createdAt", "updatedAt"},
    "InterviewTurn": {"id", "interviewSessionId", "sequence", "questionMarkdown", "focus", "sourceType",
                      "sourceStartLine", "sourceEndLine", "sourceQuote", "answerTranscript", "transcriptFinal",
                      "evaluation", "askedAt", "answeredAt"},
    "InterviewMediaChunk": {"id", "interviewSessionId", "turnId", "sequence", "kind", "storagePath", "mimeType",
                            "sizeBytes", "sha256", "durationMs", "createdAt"},
    "InterviewProctorEvent": {"id", "interviewSessionId", "type", "severity", "details", "occurredAt", "createdAt"},
    "ChatMessage": {"id", "questionId", "role", "status", "content", "correctionApplied", "correctionSummary",
                    "errorMessage", "createdAt", "updatedAt", "userId"},
    "PointLedgerEntry": {"id", "userId", "type", "amountPoints", "balanceAfter", "costUsd", "description",
                         "aiRunId", "billingKey", "createdAt"},
    "AiRun": {"id", "kind", "status", "model", "reasoningEffort", "sessionId", "inputTokens", "cachedInputTokens",
              "outputTokens", "reasoningOutputTokens", "estimatedCostUsd", "output", "errorMessage", "intakeId",
              "questionId", "chatMessageId", "problemSetSubmissionId", "interviewSessionId", "workflowJobId",
              "workflowAttempt", "userId", "billingScopeKey", "startedAt", "completedAt"},
    "AiRunEvent": {"id", "runId", "sequence", "type", "summary", "payload", "createdAt"},
    "WorkflowJob": {"id", "kind", "status", "queue", "resourceClass", "priority", "payload", "result", "progress",
                    "statusMessage", "errorMessage", "attempts", "maxAttempts", "availableAt", "leaseOwner",
                    "leaseExpiresAt", "idempotencyKey", "workspacePath", "ownerId", "createdAt", "updatedAt", "completedAt"},
    "ContentReview": {"id", "userId", "decision", "categories", "reason", "textSnapshot", "imagePaths", "intakeId",
                      "problemSetSubmissionId", "chatMessageId", "workflowJobId", "reviewerId", "reviewedAt",
                      "createdAt", "updatedAt"},
}


class ConversionError(ValueError):
    pass


def _sha256_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _rows(tables: Mapping[str, Any], name: str) -> List[dict]:
    value = tables.get(name, [])
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ConversionError("Expected {} to be a list of objects".format(name))
    return value


def _index(rows: Iterable[dict], table: str) -> Dict[str, dict]:
    result = {}
    for row in rows:
        identifier = str(row.get("id", ""))
        if not identifier:
            raise ConversionError("{} row has no stable id".format(table))
        if identifier in result:
            raise ConversionError("Duplicate {} id: {}".format(table, identifier))
        result[identifier] = row
    return result


def _source(table: str, identifier: Any) -> str:
    return "{}:{}".format(table, identifier)


def _sort_key(row: dict) -> Tuple[str, str]:
    return str(row.get("createdAt", "")), str(row.get("id", ""))


def _forbidden_field(key: str) -> bool:
    lowered = key.casefold().replace("-", "_")
    compact = lowered.replace("_", "")
    return ("password" in compact or "apikey" in compact or "secret" in compact
            or compact in {"token", "accesstoken", "accesstokenhash", "refreshtoken",
                           "sessiontoken", "bearertoken"})


def _find_forbidden(value: Any, location: str, result: List[dict], omitted: List[dict]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            path = location + "." + str(key)
            compact = str(key).casefold().replace("_", "").replace("-", "")
            if compact in SENSITIVE_OMISSIONS and item not in (None, "", [], {}):
                omitted.append({"path": path, "action": "excluded"})
            elif _forbidden_field(str(key)) and item not in (None, "", [], {}):
                result.append({"path": path, "action": "excluded"})
            else:
                _find_forbidden(item, path, result, omitted)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _find_forbidden(item, "{}[{}]".format(location, index), result, omitted)


def _strip_forbidden(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_forbidden(item) for key, item in value.items()
                if not _forbidden_field(str(key))
                and str(key).casefold().replace("_", "").replace("-", "") not in SENSITIVE_OMISSIONS}
    if isinstance(value, list):
        return [_strip_forbidden(item) for item in value]
    return value


def _safe_relative(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ConversionError("Unsafe asset path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ConversionError("Unsafe asset path: " + value)
    return path


def _asset_record(table: str, row: dict, field: str, source_root: Optional[Path], report: dict) -> Optional[dict]:
    raw = row.get(field)
    if not raw:
        return None
    try:
        relative = _safe_relative(str(raw))
    except ConversionError as error:
        report["missing_assets"].append({"table": table, "id": str(row.get("id", "")), "field": field,
                                         "path": str(raw), "reason": str(error)})
        return None
    if source_root is None:
        report["missing_assets"].append({"table": table, "id": str(row.get("id", "")), "field": field,
                                         "path": relative.as_posix(), "reason": "asset source root not provided"})
        return None
    target = source_root.joinpath(*relative.parts)
    try:
        resolved = target.resolve(strict=True)
    except FileNotFoundError:
        report["missing_assets"].append({"table": table, "id": str(row.get("id", "")), "field": field,
                                         "path": relative.as_posix(), "reason": "file not found"})
        return None
    if source_root not in resolved.parents or target.is_symlink() or not resolved.is_file():
        report["missing_assets"].append({"table": table, "id": str(row.get("id", "")), "field": field,
                                         "path": relative.as_posix(), "reason": "asset escaped source root or is unsafe"})
        return None
    size = resolved.stat().st_size
    if size > MAX_ASSET_SIZE:
        report["missing_assets"].append({"table": table, "id": str(row.get("id", "")), "field": field,
                                         "path": relative.as_posix(), "reason": "asset exceeds 512 MiB"})
        return None
    digest = _sha256_file(resolved)
    if table == "InterviewMediaChunk" and row.get("sha256") and row.get("sha256") != digest:
        report["missing_assets"].append({"table": table, "id": str(row.get("id", "")), "field": field,
                                         "path": relative.as_posix(), "reason": "declared SHA-256 does not match file"})
        return None
    if table == "InterviewMediaChunk" and row.get("sizeBytes") is not None and row.get("sizeBytes") != resolved.stat().st_size:
        report["missing_assets"].append({"table": table, "id": str(row.get("id", "")), "field": field,
                                         "path": relative.as_posix(), "reason": "declared size does not match file"})
        return None
    suffix = resolved.suffix.lower()
    asset_id = _source(table, row.get("id")) + ":" + field
    bundle_filename = hashlib.sha256(asset_id.encode("utf-8")).hexdigest() + suffix
    return {"id": asset_id, "source_path": relative.as_posix(),
            "bundle_path": "assets/{}/{}".format(digest, bundle_filename), "filename": resolved.name,
            "media_type": row.get("mimeType") or mimetypes.guess_type(resolved.name)[0] or "application/octet-stream",
            "sha256": digest, "size": size, "suffix": suffix}


def _identity(identity_map: Mapping[str, Any], owner_id: Any, source_id: str, report: dict) -> Optional[dict]:
    owner = identity_map.get(str(owner_id)) if owner_id is not None else None
    if not isinstance(owner, dict) or not str(owner.get("userId", "")).isdigit() or not owner.get("username"):
        report["unresolved_identities"].append({"source_id": source_id, "legacy_user_id": owner_id})
        return None
    return {"userId": str(int(str(owner["userId"]))), "username": str(owner["username"])}


def _scope(course_map: Mapping[str, Any], source_id: str, required: bool, report: dict) -> dict:
    value = course_map.get(source_id)
    if value is None:
        if required:
            report["unresolved_courses"].append({"source_id": source_id})
        return {}
    if not isinstance(value, dict) or bool(value.get("course_id")) == bool(value.get("psid")):
        report["unresolved_courses"].append({"source_id": source_id, "reason": "expected exactly one course_id or psid"})
        return {}
    key = "course_id" if value.get("course_id") else "psid"
    try:
        number = int(value[key])
    except (TypeError, ValueError):
        report["unresolved_courses"].append({"source_id": source_id, "reason": "mapping must be a positive integer"})
        return {}
    if number <= 0:
        report["unresolved_courses"].append({"source_id": source_id, "reason": "mapping must be a positive integer"})
        return {}
    return {key: number}


def convert_export(raw: dict, identity_map: Mapping[str, Any], course_map: Mapping[str, Any],
                   asset_source: Optional[Path] = None) -> dict:
    if not isinstance(raw, dict) or raw.get("format") != SOURCE_FORMAT or not isinstance(raw.get("tables"), dict):
        raise ConversionError("Expected a {} document".format(SOURCE_FORMAT))
    if not isinstance(identity_map, Mapping) or not isinstance(course_map, Mapping):
        raise ConversionError("Identity and course maps must be JSON objects")
    tables = raw["tables"]
    source_root = None
    if asset_source is not None:
        if asset_source.expanduser().is_symlink():
            raise ConversionError("Asset source root may not be a symbolic link")
        source_root = asset_source.expanduser().resolve(strict=True)
        if not source_root.is_dir():
            raise ConversionError("Asset source root must be a directory")
    report = {"unresolved_identities": [], "unresolved_courses": [], "unknown_fields": [],
              "missing_assets": [], "forbidden_fields": [], "dangling_relations": [],
              "omitted_sensitive_fields": [],
              "uncovered_tables": sorted(set(tables) - SUPPORTED_TABLES), "audit_only_tables": []}
    _find_forbidden(tables, "tables", report["forbidden_fields"], report["omitted_sensitive_fields"])
    for table in sorted(set(tables) & set(KNOWN_FIELDS)):
        for row in _rows(tables, table):
            unknown = sorted(set(row) - KNOWN_FIELDS[table])
            if unknown:
                report["unknown_fields"].append({"table": table, "id": str(row.get("id", "")), "fields": unknown})

    questions = _index(_rows(tables, "Question"), "Question")
    tags = _index(_rows(tables, "Tag"), "Tag")
    question_tags = {}
    for link in _rows(tables, "QuestionTag"):
        if str(link.get("questionId")) not in questions or str(link.get("tagId")) not in tags:
            report["dangling_relations"].append({"table": "QuestionTag", "row": link})
            continue
        question_tags.setdefault(str(link["questionId"]), []).append(tags[str(link["tagId"])]["name"])
    identities = {}
    library_owners, library_tags = {}, {}
    for link in _rows(tables, "UserQuestion"):
        question_id = str(link.get("questionId"))
        if question_id not in questions:
            report["dangling_relations"].append({"table": "UserQuestion", "questionId": question_id})
            continue
        actor = _identity(identity_map, link.get("userId"), _source("Question", question_id), report)
        if actor:
            identities_key = str(link.get("userId"))
            identities[identities_key] = actor
            library_owners.setdefault(question_id, {})[identities_key] = actor
    for link in _rows(tables, "UserQuestionTag"):
        question_id, tag_id = str(link.get("questionId")), str(link.get("tagId"))
        if question_id not in questions or tag_id not in tags:
            report["dangling_relations"].append({"table": "UserQuestionTag", "questionId": question_id, "tagId": tag_id})
            continue
        actor = _identity(identity_map, link.get("userId"), _source("Question", question_id), report)
        if actor:
            identities[str(link.get("userId"))] = actor
            library_tags.setdefault(question_id, []).append({"subject_id": actor["userId"],
                                                               "tag": tags[tag_id]["name"]})
    figures = {}
    asset_links = {}
    assets = []
    for figure in _rows(tables, "QuestionFigure"):
        question_id = str(figure.get("questionId"))
        if question_id not in questions:
            report["dangling_relations"].append({"table": "QuestionFigure", "id": figure.get("id")})
            continue
        saved = {key: figure.get(key) for key in ("label", "descriptionZh", "sourceImageIndex", "bbox", "calibrationNotes")}
        saved["asset_ids"] = []
        for field in PATH_FIELDS["QuestionFigure"]:
            asset = _asset_record("QuestionFigure", figure, field, source_root, report)
            if asset:
                assets.append(asset)
                saved["asset_ids"].append(asset["id"])
                asset_links.setdefault(asset["id"], []).append(_source("Question", question_id))
        figures.setdefault(question_id, []).append(saved)

    resources = []
    for question_id, row in sorted(questions.items()):
        source_id = _source("Question", question_id)
        actor = _identity(identity_map, row.get("ownerId"), source_id, report)
        if not actor:
            continue
        identities[str(row.get("ownerId"))] = actor
        data = {"question_type": str(row.get("type", "OTHER")).lower(),
                "language": row.get("languageOriginal") or "zh", "original": row.get("stemOriginal") or "",
                "zh": row.get("stemZh") or row.get("stemOriginal") or "", "options": row.get("options") or [],
                "answer": row.get("answerZh"), "explanation": row.get("solutionZh"),
                "tags": sorted(set(question_tags.get(question_id, []))), "figures": figures.get(question_id, []),
                "legacy_library_owners": sorted((actor["userId"] for actor in library_owners.get(question_id, {}).values())),
                "legacy_library_tags": sorted(library_tags.get(question_id, []), key=lambda item: (item["subject_id"], item["tag"])),
                "legacy_status": row.get("status"), "verification": {"status": row.get("verificationStatus"),
                    "summary": row.get("verificationSummary"), "evidence": row.get("verificationEvidence"),
                    "verified_at": row.get("verifiedAt")}}
        resources.append({"id": source_id, "owner_id": str(row.get("ownerId")), "kind": "question",
                          "state": "ready" if row.get("status") == "READY" else "draft", "data": data,
                          "updated_at": row.get("updatedAt")})

    problem_sets = _index(_rows(tables, "ProblemSet"), "ProblemSet")
    set_links = {}
    for link in sorted(_rows(tables, "ProblemSetQuestion"), key=lambda item: (str(item.get("problemSetId")), str(item.get("problemNumber")))):
        set_id, question_id = str(link.get("problemSetId")), str(link.get("questionId"))
        if set_id not in problem_sets or question_id not in questions:
            report["dangling_relations"].append({"table": "ProblemSetQuestion", "id": link.get("id")})
            continue
        set_links.setdefault(set_id, []).append((_source("Question", question_id), str(link.get("problemNumber", "")), link.get("sourceSubmissionId")))
    submissions = {}
    for item in _rows(tables, "ProblemSetSubmission"):
        submissions.setdefault(str(item.get("problemSetId")), []).append({
            "source_id": _source("ProblemSetSubmission", item.get("id")), "status": item.get("status"),
            "status_message": item.get("statusMessage"), "error": item.get("errorMessage"),
            "requested_number": item.get("requestedNumber"), "hold_for_more": item.get("holdForMore"),
            "created_at": item.get("createdAt"), "updated_at": item.get("updatedAt")})
    for set_id, row in sorted(problem_sets.items()):
        source_id = _source("ProblemSet", set_id)
        actor = _identity(identity_map, row.get("ownerId"), source_id, report)
        if not actor:
            continue
        identities[str(row.get("ownerId"))] = actor
        links = set_links.get(set_id, [])
        data = {"title": row.get("name") or source_id, "question_ids": [item[0] for item in links],
                "problem_numbers": [{"question_id": item[0], "number": item[1],
                                     "source_submission_id": item[2]} for item in links],
                "submission_history": sorted(submissions.get(set_id, []), key=_sort_key)}
        resource = {"id": source_id, "owner_id": str(row.get("ownerId")), "kind": "collection",
                    "state": "ready", "data": data, "updated_at": row.get("updatedAt")}
        resource.update(_scope(course_map, source_id, False, report))
        resources.append(resource)

    authoring_messages = {}
    for message in _rows(tables, "AuthoringMessage"):
        authoring_messages.setdefault(str(message.get("projectId")), []).append({
            "source_id": _source("AuthoringMessage", message.get("id")), "role": str(message.get("role", "USER")).lower(),
            "status": message.get("status"), "text": message.get("content") or "", "created_at": message.get("createdAt")})
    for row in sorted(_rows(tables, "AuthoringProject"), key=_sort_key):
        source_id = _source("AuthoringProject", row.get("id"))
        actor = _identity(identity_map, row.get("ownerId"), source_id, report)
        if not actor:
            continue
        identities[str(row.get("ownerId"))] = actor
        resources.append({"id": source_id, "owner_id": str(row.get("ownerId")), "kind": "authoring", "state": "review",
                          "data": {"title": row.get("title"), "requirements": row.get("brief"),
                                   "candidate": row.get("currentDraft"), "revision": row.get("draftVersion", 0),
                                   "messages": sorted(authoring_messages.get(str(row.get("id")), []), key=_sort_key)},
                          "updated_at": row.get("updatedAt")})

    executions, tutor = {}, {}
    for item in _rows(tables, "IdeExecution"):
        executions.setdefault(str(item.get("workspaceId")), []).append({
            "source_id": _source("IdeExecution", item.get("id")), "kind": item.get("kind"), "status": item.get("status"),
            "source_snapshot": item.get("sourceSnapshot"), "stdin": item.get("stdinSnapshot"), "stdout": item.get("stdout"),
            "stderr": item.get("stderr"), "exit_code": item.get("exitCode"), "duration_ms": item.get("durationMs"),
            "passed_tests": item.get("passedTests"), "total_tests": item.get("totalTests"),
            "test_results": item.get("testResults"), "created_at": item.get("createdAt"), "completed_at": item.get("completedAt")})
    for item in _rows(tables, "IdeTutorMessage"):
        tutor.setdefault(str(item.get("workspaceId")), []).append({"source_id": _source("IdeTutorMessage", item.get("id")),
            "role": str(item.get("role", "USER")).lower(), "status": item.get("status"), "text": item.get("content") or "",
            "created_at": item.get("createdAt")})
    for row in sorted(_rows(tables, "IdeWorkspace"), key=_sort_key):
        source_id = _source("IdeWorkspace", row.get("id"))
        actor = _identity(identity_map, row.get("ownerId"), source_id, report)
        if not actor:
            continue
        identities[str(row.get("ownerId"))] = actor
        resource = {"id": source_id, "owner_id": str(row.get("ownerId")), "kind": "ide", "state": "ready",
                    "data": {"title": row.get("title"), "question": row.get("problemText"),
                             "language": str(row.get("language", "PYTHON")).lower(), "code": row.get("code") or "",
                             "stdin": row.get("stdin") or "", "tests": row.get("testCases") or [],
                             "exam_mode": bool(row.get("examMode")),
                             "execution_history": sorted(executions.get(str(row.get("id")), []), key=_sort_key),
                             "messages": sorted(tutor.get(str(row.get("id")), []), key=_sort_key)},
                    "updated_at": row.get("updatedAt")}
        resource.update(_scope(course_map, source_id, False, report))
        resources.append(resource)

    document_chunks = {}
    for item in _rows(tables, "CourseChunk"):
        document_chunks.setdefault(str(item.get("documentId")), []).append({
            "source_id": _source("CourseChunk", item.get("id")), "chunk_index": item.get("chunkIndex"),
            "heading": item.get("heading"), "locator": item.get("locator"), "text": item.get("content") or "",
            "search_text": item.get("searchText"), "token_estimate": item.get("tokenEstimate")})
    knowledge_bases = _index(_rows(tables, "CourseKnowledgeBase"), "CourseKnowledgeBase")
    for kb_id, row in sorted(knowledge_bases.items()):
        source_id = _source("CourseKnowledgeBase", kb_id)
        actor = _identity(identity_map, row.get("ownerId"), source_id, report)
        scope = _scope(course_map, source_id, True, report)
        if not actor or not scope:
            continue
        identities[str(row.get("ownerId"))] = actor
        resources.append({"id": source_id, "owner_id": str(row.get("ownerId")), "kind": "course", "state": "ready",
                          "data": {"title": row.get("name"), "description": row.get("description") or "",
                                   "legacy_history": {"created_at": row.get("createdAt"), "updated_at": row.get("updatedAt")}},
                          "updated_at": row.get("updatedAt"), **scope})
    documents = _index(_rows(tables, "CourseDocument"), "CourseDocument")
    for document_id, row in sorted(documents.items()):
        kb_source = _source("CourseKnowledgeBase", row.get("knowledgeBaseId"))
        kb = knowledge_bases.get(str(row.get("knowledgeBaseId")))
        if not kb:
            report["dangling_relations"].append({"table": "CourseDocument", "id": document_id})
            continue
        actor = _identity(identity_map, kb.get("ownerId"), _source("CourseDocument", document_id), report)
        scope = _scope(course_map, kb_source, True, report)
        if not actor or not scope:
            continue
        asset = _asset_record("CourseDocument", row, "storagePath", source_root, report)
        file_ids = []
        if asset:
            assets.append(asset)
            file_ids.append(asset["id"])
            asset_links.setdefault(asset["id"], []).append(_source("CourseDocument", document_id))
        resources.append({"id": _source("CourseDocument", document_id), "owner_id": str(kb.get("ownerId")),
                          "kind": "document", "state": "ready" if row.get("status") == "COMPLETED" else "draft",
                          "parent_source_id": kb_source, "data": {"title": row.get("title"),
                              "source_type": row.get("sourceType"), "source_sha256": row.get("contentHash"),
                              "text": row.get("extractedText") or "", "file_ids": file_ids,
                              "chunks": sorted(document_chunks.get(document_id, []), key=lambda item: (item.get("chunk_index", 0), item["source_id"])),
                              "legacy_status": row.get("status"), "error": row.get("errorMessage")},
                          "updated_at": row.get("updatedAt"), **scope})

    course_messages = {}
    for item in _rows(tables, "CourseMessage"):
        course_messages.setdefault(str(item.get("conversationId")), []).append({
            "source_id": _source("CourseMessage", item.get("id")), "role": str(item.get("role", "USER")).lower(),
            "status": item.get("status"), "text": item.get("content") or "", "citations": item.get("citations") or [],
            "academic_search": bool(item.get("academicSearch")), "created_at": item.get("createdAt")})
    for row in sorted(_rows(tables, "CourseConversation"), key=_sort_key):
        kb_id = str(row.get("knowledgeBaseId"))
        kb = knowledge_bases.get(kb_id)
        if not kb:
            report["dangling_relations"].append({"table": "CourseConversation", "id": row.get("id")})
            continue
        source_id, kb_source = _source("CourseConversation", row.get("id")), _source("CourseKnowledgeBase", kb_id)
        actor = _identity(identity_map, kb.get("ownerId"), source_id, report)
        scope = _scope(course_map, kb_source, True, report)
        if actor and scope:
            resources.append({"id": source_id, "owner_id": str(kb.get("ownerId")), "kind": "conversation",
                              "state": "ready", "parent_source_id": kb_source,
                              "data": {"title": row.get("title"), "messages": sorted(course_messages.get(str(row.get("id")), []), key=_sort_key)},
                              "updated_at": row.get("updatedAt"), **scope})

    question_messages = {}
    for item in _rows(tables, "ChatMessage"):
        question_messages.setdefault(str(item.get("questionId")), []).append({
            "source_id": _source("ChatMessage", item.get("id")), "role": str(item.get("role", "USER")).lower(),
            "status": item.get("status"), "text": item.get("content") or "", "correction_applied": bool(item.get("correctionApplied")),
            "correction_summary": item.get("correctionSummary"), "error": item.get("errorMessage"), "created_at": item.get("createdAt")})
    for question_id, messages in sorted(question_messages.items()):
        question = questions.get(question_id)
        if not question:
            report["dangling_relations"].append({"table": "ChatMessage", "questionId": question_id})
            continue
        owner_id = next((item.get("userId") for item in _rows(tables, "ChatMessage") if str(item.get("questionId")) == question_id and item.get("userId")), question.get("ownerId"))
        source_id = _source("QuestionConversation", question_id)
        actor = _identity(identity_map, owner_id, source_id, report)
        if actor:
            identities[str(owner_id)] = actor
            resources.append({"id": source_id, "owner_id": str(owner_id), "kind": "conversation", "state": "ready",
                              "parent_source_id": _source("Question", question_id),
                              "data": {"title": "Legacy question conversation", "question_id": _source("Question", question_id),
                                       "messages": sorted(messages, key=_sort_key)}})

    turns, events, media = {}, {}, {}
    turn_index = _index(_rows(tables, "InterviewTurn"), "InterviewTurn")
    for item in turn_index.values():
        turns.setdefault(str(item.get("interviewSessionId")), []).append({
            "source_id": _source("InterviewTurn", item.get("id")), "sequence": item.get("sequence"),
            "question": item.get("questionMarkdown") or "", "focus": item.get("focus") or "",
            "source_type": str(item.get("sourceType", "NONE")).lower(), "source_start_line": item.get("sourceStartLine") or 0,
            "source_end_line": item.get("sourceEndLine") or 0, "source_quote": item.get("sourceQuote"),
            "answer": item.get("answerTranscript"), "transcript_final": bool(item.get("transcriptFinal")),
            "evaluation": item.get("evaluation"), "asked_at": item.get("askedAt"), "answered_at": item.get("answeredAt")})
    for item in _rows(tables, "InterviewProctorEvent"):
        events.setdefault(str(item.get("interviewSessionId")), []).append({
            "id": _source("InterviewProctorEvent", item.get("id")), "type": item.get("type"),
            "severity": str(item.get("severity", "INFO")).lower(), "details": item.get("details") or {},
            "occurred_at": item.get("occurredAt")})
    for item in _rows(tables, "InterviewMediaChunk"):
        session_id = str(item.get("interviewSessionId"))
        asset = _asset_record("InterviewMediaChunk", item, "storagePath", source_root, report)
        if not asset:
            continue
        assets.append(asset)
        interview_source = _source("InterviewSession", session_id)
        asset_links.setdefault(asset["id"], []).append(interview_source)
        turn = turn_index.get(str(item.get("turnId"))) if item.get("turnId") else None
        kind = item.get("kind")
        media.setdefault(session_id, []).append({"file_id": asset["id"],
            "turn_sequence": turn.get("sequence") if turn else 0,
            "track": "audio" if kind == "ANSWER_AUDIO" else "video", "segment_index": item.get("sequence", 0),
            "segment_count": None, "duration_ms": item.get("durationMs"), "sha256": item.get("sha256"),
            "created_at": item.get("createdAt")})
    templates = _index(_rows(tables, "InterviewTemplate"), "InterviewTemplate")
    for row in sorted(_rows(tables, "InterviewSession"), key=_sort_key):
        source_id = _source("InterviewSession", row.get("id"))
        actor = _identity(identity_map, row.get("ownerId"), source_id, report)
        student_mapping_key = source_id + ":student"
        student = _identity(identity_map, student_mapping_key, source_id, report)
        scope = _scope(course_map, source_id, False, report)
        if not actor or not student:
            continue
        identities[str(row.get("ownerId"))] = actor
        identities[student_mapping_key] = student
        template = templates.get(str(row.get("templateId"))) if row.get("templateId") else None
        session_media = sorted(media.get(str(row.get("id")), []), key=lambda item: (item["turn_sequence"], item["track"], item["segment_index"]))
        grouped = {}
        for item in session_media:
            grouped.setdefault((item["turn_sequence"], item["track"]), []).append(item)
        for group in grouped.values():
            total = max(int(item["segment_index"]) for item in group) + 1
            for item in group:
                item["segment_count"] = total
        data = {"title": row.get("problemTitle") or (template or {}).get("name") or source_id,
                "student_id": student["userId"], "student_username": student["username"],
                "student_name": row.get("studentName"), "legacy_student_external_id": row.get("studentExternalId"),
                "question": row.get("problemDescription") or "", "code": row.get("studentCode") or "",
                "language": row.get("codeLanguage") or "text", "report": row.get("reportContent") or "",
                "requirements": row.get("acceptanceRequirements") or (template or {}).get("acceptanceRequirements") or "",
                "rubric": row.get("rubric") or (template or {}).get("rubric") or [],
                "max_questions": row.get("maxQuestions") or (template or {}).get("maxQuestions") or 6,
                "duration_minutes": row.get("durationMinutes") or (template or {}).get("durationMinutes") or 20,
                "status": str(row.get("status", "FAILED")).lower(), "status_message": row.get("statusMessage"),
                "expires_at": row.get("accessTokenExpiresAt"), "preflight": row.get("preflightSnapshot"),
                "turns": sorted(turns.get(str(row.get("id")), []), key=lambda item: (item.get("sequence", 0), item["source_id"])),
                "media_segments": session_media, "events": sorted(events.get(str(row.get("id")), []), key=lambda item: str(item.get("occurred_at", ""))),
                "candidate": {"criteria": row.get("rubricScores") or [], "score": row.get("totalScore"),
                    "confidence": row.get("confidence"), "integrity_conclusion": row.get("integrityConclusion"),
                    "summary": row.get("finalSummary")} if row.get("completedAt") else None,
                "teacher_review": {"notes": row.get("reviewNotes")} if row.get("reviewNotes") else None,
                "legacy_timestamps": {"started_at": row.get("startedAt"), "completed_at": row.get("completedAt"),
                                      "created_at": row.get("createdAt"), "updated_at": row.get("updatedAt")}}
        resources.append({"id": source_id, "owner_id": str(row.get("ownerId")), "kind": "interview",
                          "state": "review" if row.get("status") in ("COMPLETED", "FLAGGED") else "draft",
                          "data": data, "updated_at": row.get("updatedAt"), **scope})

    # Historical assessments retain their evidence but gain no native-write
    # authority: teachers must create a fresh snapshot before adopting grades.
    exams = _index(_rows(tables, "Exam"), "Exam")
    exam_rooms = _index(_rows(tables, "ExamRoom"), "ExamRoom")
    candidates_by_exam = {}
    for candidate in _rows(tables, "ExamCandidate"):
        exam_id = str(candidate.get("examId", ""))
        if exam_id not in exams:
            report["dangling_relations"].append({"table": "ExamCandidate", "id": candidate.get("id"), "examId": exam_id})
            continue
        student_key = _source("ExamCandidate", candidate["id"]) + ":student"
        student = _identity(identity_map, student_key, student_key, report)
        if not student:
            continue
        identities[student_key] = student
        room_id = str(candidate.get("roomId")) if candidate.get("roomId") else None
        if room_id and (room_id not in exam_rooms or str(exam_rooms[room_id].get("examId")) != exam_id):
            report["dangling_relations"].append({"table": "ExamCandidate", "id": candidate.get("id"), "roomId": room_id})
            continue
        candidates_by_exam.setdefault(exam_id, []).append({"user_id": student["userId"], "username": student["username"],
            "name": candidate.get("name", ""), "student_no": candidate.get("studentNo"), "seat": candidate.get("seatNo"),
            "legacy_room_id": room_id, "attendance": candidate.get("attendance"), "submission": candidate.get("submission"), "score": candidate.get("score")})
    for room in exam_rooms.values():
        if str(room.get("examId")) not in exams:
            report["dangling_relations"].append({"table": "ExamRoom", "id": room["id"]})
    for exam_id, row in exams.items():
        source_id = _source("Exam", exam_id)
        actor = _identity(identity_map, row.get("ownerId"), source_id, report)
        if not actor:
            continue
        identities[str(row.get("ownerId"))] = actor
        people = candidates_by_exam.get(exam_id, [])
        rooms = [{"legacy_room_id": rid, "name": room.get("name"), "capacity": room.get("capacity"),
                  "assigned": [person for person in people if person["legacy_room_id"] == rid]}
                 for rid, room in exam_rooms.items() if str(room.get("examId")) == exam_id]
        resources.append({"id": source_id, "owner_id": str(row.get("ownerId")), "kind": "exam", "state": "draft",
            "updated_at": row.get("updatedAt"), **_scope(course_map, source_id, False, report),
            "data": {"title": row.get("name"), "mode": "FORMAL" if row.get("mode") == "FORMAL" else "TEST",
                     "candidates": people, "rooms": rooms, "legacy_state": row.get("status"),
                     "legacy_plan": {"rooms": rooms, "candidate_count": len(people)}, "legacy_record": row,
                     "migration_review_required": True}})

    for row in _rows(tables, "SubjectiveReviewTask"):
        source_id = _source("SubjectiveReviewTask", row.get("id"))
        actor = _identity(identity_map, row.get("ownerId"), source_id, report)
        if not actor:
            continue
        identities[str(row.get("ownerId"))] = actor
        file_ids = []
        for index, image_path in enumerate(row.get("imagePaths") or []):
            asset = _asset_record("SubjectiveReviewTask", {"id": str(row["id"]) + ":" + str(index), "storagePath": image_path}, "storagePath", source_root, report)
            if asset:
                assets.append(asset); file_ids.append(asset["id"])
                asset_links.setdefault(asset["id"], []).append(source_id)
        resources.append({"id": source_id, "owner_id": str(row.get("ownerId")), "kind": "grading", "state": "review",
            "updated_at": row.get("updatedAt"), **_scope(course_map, source_id, False, report),
            "data": {"title": row.get("title"), "question": row.get("questionMarkdown"), "answer": row.get("studentAnswer"),
                     "rubric": row.get("rubric"), "file_ids": file_ids, "legacy_record": row,
                     "candidate": {"criteria": row.get("dimensionScores") or [], "score": row.get("totalScore"),
                                   "confidence": row.get("confidence"), "summary": row.get("feedbackMarkdown"), "needs_review": True}}})

    scans = _index(_rows(tables, "PlagiarismScan"), "PlagiarismScan")
    submissions = _index(_rows(tables, "PlagiarismSubmission"), "PlagiarismSubmission")
    scan_pairs = {}
    for pair in _rows(tables, "PlagiarismPair"):
        scan_id = str(pair.get("scanId"))
        left, right = submissions.get(str(pair.get("firstSubmissionId"))), submissions.get(str(pair.get("secondSubmissionId")))
        if scan_id not in scans or not left or not right or str(left.get("scanId")) != scan_id or str(right.get("scanId")) != scan_id:
            report["dangling_relations"].append({"table": "PlagiarismPair", "id": pair.get("id")})
            continue
        scan_pairs.setdefault(scan_id, []).append({"left": left["studentId"], "right": right["studentId"],
            "similarity": pair.get("jplagScore"), "matches": pair.get("matchedEvidence"), "legacy_review": pair})
    for submission in submissions.values():
        if str(submission.get("scanId")) not in scans:
            report["dangling_relations"].append({"table": "PlagiarismSubmission", "id": submission["id"]})
    for scan_id, row in scans.items():
        source_id = _source("PlagiarismScan", scan_id)
        actor = _identity(identity_map, row.get("ownerId"), source_id, report)
        if not actor:
            continue
        identities[str(row.get("ownerId"))] = actor
        report_asset = _asset_record("PlagiarismScan", row, "reportPath", source_root, report)
        if report_asset:
            assets.append(report_asset); asset_links.setdefault(report_asset["id"], []).append(source_id)
        resources.append({"id": source_id, "owner_id": str(row.get("ownerId")), "kind": "plagiarism", "state": "review",
            "updated_at": row.get("updatedAt"), **_scope(course_map, source_id, False, report),
            "data": {"title": row.get("name"), "language": str(row.get("language", "PYTHON")).lower(),
                     "threshold": row.get("threshold", 0.7), "base_code": row.get("baseCode"), "legacy_record": row,
                     "submissions": [{"user_id": value["studentId"], "code": value.get("code", ""), "legacy_submission_id": sid}
                                     for sid, value in submissions.items() if str(value.get("scanId")) == scan_id],
                     "file_ids": [report_asset["id"]] if report_asset else [],
                     "candidate": {"pairs": scan_pairs.get(scan_id, []), "needs_review": True, "legacy_report_file_id": report_asset["id"] if report_asset else None}}})

    run_events = {}
    for row in _rows(tables, "AiRunEvent"):
        run_events.setdefault(str(row.get("runId")), []).append({
            "source_id": _source("AiRunEvent", row.get("id")), "sequence": row.get("sequence"),
            "type": row.get("type"), "summary": row.get("summary"), "payload": row.get("payload") or {},
            "created_at": row.get("createdAt")})
    ledger = []
    for row in sorted(_rows(tables, "PointLedgerEntry"), key=_sort_key):
        actor = _identity(identity_map, row.get("userId"), _source("PointLedgerEntry", row.get("id")), report)
        if actor:
            identities[str(row.get("userId"))] = actor
        ledger.append({"source_id": _source("PointLedgerEntry", row.get("id")), "legacy_user_id": row.get("userId"),
                       "subject_id": actor["userId"] if actor else None,
                       "type": row.get("type"), "amount_points": row.get("amountPoints"),
                       "balance_after": row.get("balanceAfter"), "cost_usd": row.get("costUsd"),
                       "description": row.get("description"), "ai_run_id": row.get("aiRunId"),
                       "billing_key_sha256": hashlib.sha256(str(row.get("billingKey", "")).encode()).hexdigest()
                       if row.get("billingKey") else None, "created_at": row.get("createdAt"),
                       "disposition": "audit_only_no_balance_mutation"})
    ai_runs = []
    for row in sorted(_rows(tables, "AiRun"), key=_sort_key):
        actor = _identity(identity_map, row.get("userId"), _source("AiRun", row.get("id")), report) if row.get("userId") else None
        if actor:
            identities[str(row.get("userId"))] = actor
        ai_runs.append({"source_id": _source("AiRun", row.get("id")), "kind": row.get("kind"),
                        "status": row.get("status"), "subject_id": actor["userId"] if actor else None,
                        "model": row.get("model"), "usage": {"input_tokens": row.get("inputTokens"),
                            "cached_input_tokens": row.get("cachedInputTokens"), "output_tokens": row.get("outputTokens")},
                        "estimated_cost_usd": row.get("estimatedCostUsd"), "started_at": row.get("startedAt"),
                        "completed_at": row.get("completedAt"),
                        "events": sorted(run_events.get(str(row.get("id")), []),
                                         key=lambda item: (item.get("sequence", 0), item["source_id"])),
                        "disposition": "audit_only_no_charge"})
    workflow = []
    for row in sorted(_rows(tables, "WorkflowJob"), key=_sort_key):
        actor = _identity(identity_map, row.get("ownerId"), _source("WorkflowJob", row.get("id")), report) if row.get("ownerId") else None
        if actor:
            identities[str(row.get("ownerId"))] = actor
        workflow.append({"source_id": _source("WorkflowJob", row.get("id")), "kind": row.get("kind"),
                         "status": row.get("status"), "subject_id": actor["userId"] if actor else None,
                         "attempts": row.get("attempts"), "created_at": row.get("createdAt"),
                         "completed_at": row.get("completedAt"), "disposition": "audit_only_not_requeued"})
    reviews = []
    for row in sorted(_rows(tables, "ContentReview"), key=_sort_key):
        actor = _identity(identity_map, row.get("userId"), _source("ContentReview", row.get("id")), report)
        if actor:
            identities[str(row.get("userId"))] = actor
        reviews.append({"source_id": _source("ContentReview", row.get("id")), "legacy_user_id": row.get("userId"),
                        "subject_id": actor["userId"] if actor else None, "decision": row.get("decision"),
                        "categories": row.get("categories") or [], "reason": row.get("reason"),
                        "reviewed_at": row.get("reviewedAt"), "disposition": "audit_only_no_account_action"})
    report["audit_only_tables"] = [name for name, values in (("PointLedgerEntry", ledger), ("AiRun", ai_runs),
                                         ("WorkflowJob", workflow), ("ContentReview", reviews)) if values]
    deduplicated_assets = {}
    for asset in assets:
        previous = deduplicated_assets.get(asset["id"])
        if previous and previous != asset:
            raise ConversionError("Asset identity conflict: " + asset["id"])
        asset["resource_source_ids"] = sorted(set(asset_links.get(asset["id"], [])))
        deduplicated_assets[asset["id"]] = asset
    resources.sort(key=lambda item: item["id"])
    report["counts"] = {"resources": len(resources), "assets": len(deduplicated_assets),
                        "ledger_audit_entries": len(ledger), "ai_run_audit_entries": len(ai_runs)}
    report["uncovered_nonempty_tables"] = [name for name in report["uncovered_tables"]
                                            if name != "AiSetting" and _rows(tables, name)]
    report["ready_to_import"] = not any(report[key] for key in ("unresolved_identities", "unresolved_courses",
                                                                  "missing_assets", "dangling_relations", "uncovered_nonempty_tables"))
    legacy_control = [{"table": name, "records": _rows(tables, name), "disposition": "archive_only_no_execution_or_credentials"}
                      for name in ("Intake", "IntegrationCallbackAttempt", "SolveJob", "AiSetting") if _rows(tables, name)]
    return _strip_forbidden({"format": FORMAT, "source_schema": raw.get("schema_sha256"), "resources": resources,
                             "identities": identities, "assets": [deduplicated_assets[key] for key in sorted(deduplicated_assets)],
                             "audits": {"ledger": ledger, "ai_runs": ai_runs, "workflow_jobs": workflow,
                                        "content_reviews": reviews, "legacy_control": legacy_control}, "report": report})


def write_bundle(bundle: dict, output: Path, asset_source: Optional[Path] = None,
                 asset_output: Optional[Path] = None) -> dict:
    output = output.expanduser().resolve(strict=False)
    if output.exists() or output.is_symlink():
        raise ConversionError("Output already exists")
    if bundle.get("assets"):
        if asset_source is None or asset_output is None:
            raise ConversionError("Asset source and output directories are required")
        source_root = asset_source.expanduser().resolve(strict=True)
        destination_root = asset_output.expanduser().resolve(strict=False)
        if asset_output.expanduser().is_symlink() or (destination_root.exists() and any(destination_root.iterdir())):
            raise ConversionError("Asset output must be a new or empty real directory")
        destination_root.mkdir(parents=True, mode=0o750, exist_ok=True)
        try:
            for asset in bundle["assets"]:
                source_relative = _safe_relative(asset["source_path"])
                bundled_relative = _safe_relative(asset["bundle_path"])
                source = source_root.joinpath(*source_relative.parts)
                source_resolved = source.resolve(strict=True)
                if source_root not in source_resolved.parents or source.is_symlink() or not source_resolved.is_file():
                    raise ConversionError("Asset escaped source root")
                if source_resolved.stat().st_size != asset["size"] or _sha256_file(source_resolved) != asset["sha256"]:
                    raise ConversionError("Asset changed since conversion: " + asset["id"])
                destination = destination_root.joinpath(*bundled_relative.parts)
                if destination_root not in destination.resolve(strict=False).parents:
                    raise ConversionError("Asset destination escaped output root")
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
                with source_resolved.open("rb") as input_stream, destination.open("xb") as output_stream:
                    shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
                destination.chmod(0o640)
                if destination.stat().st_size != asset["size"] or _sha256_file(destination) != asset["sha256"]:
                    raise ConversionError("Copied asset checksum mismatch: " + asset["id"])
        except BaseException:
            shutil.rmtree(destination_root)
            raise
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name("." + output.name + ".tmp")
    try:
        temporary.write_text(json.dumps(bundle, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return {"output": str(output), "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "assets": len(bundle.get("assets", []))}
