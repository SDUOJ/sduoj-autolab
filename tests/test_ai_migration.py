import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_ai_backend import database
from auto_task import store
from auto_task.entities import AiFile, AiLedger, AiResource, TaskJob
from auto_task.migrate import import_problem_stack
from auto_task.problem_stack_migration import (
    ConversionError,
    convert_export,
    write_bundle,
)


ALL_MODELS = [
    "User", "Question", "Intake", "Tag", "QuestionTag", "UserQuestion",
    "UserQuestionTag", "AiSetting", "AiRun", "WorkflowJob", "AuthoringProject",
    "AuthoringMessage", "Exam", "ExamRoom", "ExamCandidate", "SubjectiveReviewTask",
    "PlagiarismScan", "PlagiarismSubmission", "PlagiarismPair", "IdeWorkspace",
    "IdeExecution", "IdeTutorMessage", "CourseKnowledgeBase", "CourseDocument",
    "CourseChunk", "CourseConversation", "CourseMessage", "InterviewTemplate",
    "InterviewSession", "InterviewTurn", "InterviewMediaChunk", "InterviewProctorEvent",
    "IntegrationCallbackAttempt", "QuestionFigure", "ProblemSet",
    "ProblemSetSubmission", "ProblemSetQuestion", "AiRunEvent", "ChatMessage",
    "SolveJob", "PointLedgerEntry", "ContentReview",
]


def asset_tree(root: Path):
    values = {
        "figures/source.png": b"source-image",
        "figures/crop.png": b"cropped-image",
        "documents/course.pdf": b"course-pdf",
        "interviews/answer.webm": b"answer-media",
    }
    for relative, content in values.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return values


def full_legacy_export(asset_values):
    tables = {name: [] for name in ALL_MODELS}
    tables.update({
        "User": [{
            "id": "u1", "username": "teacher", "displayName": "Teacher",
            "passwordHash": "DO_NOT_MIGRATE_PASSWORD", "role": "ADMIN", "status": "ACTIVE",
            "points": 90, "permanentlySuspended": False, "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-02-01T00:00:00Z",
        }, {
            "id": "u2", "username": "student", "displayName": "Student",
            "passwordHash": "DO_NOT_MIGRATE_STUDENT_PASSWORD", "role": "USER", "status": "ACTIVE",
            "points": 5, "permanentlySuspended": False, "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-02-01T00:00:00Z",
        }],
        "AiSetting": [{
            "id": "default", "providerName": "openai", "apiKeyCiphertext": "DO_NOT_MIGRATE_API_KEY",
        }],
        "Question": [{
            "id": "q1", "type": "SHORT_ANSWER", "status": "READY", "languageOriginal": "zh",
            "stemOriginal": "解释队列", "stemZh": "解释队列", "options": [], "answerZh": "先进先出",
            "solutionZh": "FIFO", "sourceImages": [], "canonicalHash": "canonical", "deduplicationKey": "queue",
            "searchText": "queue", "verificationStatus": "PASSED", "solutionStatus": "READY",
            "verificationSummary": "checked", "verificationEvidence": {"line": 1},
            "ownerId": "u1", "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-02-01T00:00:00Z",
            "futureColumn": "must be reported",
        }],
        "Tag": [{"id": "tag1", "name": "数据结构", "slug": "ds", "aliases": [], "createdAt": "2026-01-01T00:00:00Z"}],
        "QuestionTag": [{"questionId": "q1", "tagId": "tag1"}],
        "UserQuestion": [{"userId": "u2", "questionId": "q1", "createdAt": "2026-01-02T00:00:00Z"}],
        "UserQuestionTag": [{"userId": "u2", "questionId": "q1", "tagId": "tag1", "createdAt": "2026-01-02T00:00:00Z"}],
        "QuestionFigure": [{
            "id": "fig1", "questionId": "q1", "label": "图 1", "descriptionZh": "队列图",
            "sourceImagePath": "figures/source.png", "cropPath": "figures/crop.png", "svgPath": None,
            "sourceImageIndex": 0, "bbox": [0, 0, 1, 1], "createdAt": "2026-01-01T00:00:00Z",
        }],
        "ProblemSet": [{"id": "set1", "name": "数据结构题集", "ownerId": "u1", "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-02-01T00:00:00Z"}],
        "ProblemSetSubmission": [{
            "id": "submission1", "problemSetId": "set1", "status": "MERGED", "statusMessage": "done",
            "imagePaths": [], "holdForMore": False, "autoSolve": True, "createdAt": "2026-01-03T00:00:00Z",
            "updatedAt": "2026-01-03T01:00:00Z", "ownerId": "u1",
        }],
        "ProblemSetQuestion": [{
            "id": "link1", "problemSetId": "set1", "questionId": "q1",
            "sourceSubmissionId": "submission1", "problemNumber": "1", "createdAt": "2026-01-03T00:00:00Z",
        }],
        "AuthoringProject": [{
            "id": "author1", "title": "命题历史", "brief": "生成队列题", "currentDraft": {"title": "Queue"},
            "draftVersion": 2, "codexSessionId": "legacy-session", "workspacePath": "/old/workspace",
            "ownerId": "u1", "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-02-01T00:00:00Z",
        }],
        "AuthoringMessage": [{
            "id": "am1", "projectId": "author1", "role": "USER", "status": "COMPLETED",
            "content": "增加边界", "createdAt": "2026-01-02T00:00:00Z", "updatedAt": "2026-01-02T00:00:00Z",
        }],
        "IdeWorkspace": [{
            "id": "ide1", "title": "队列实现", "problemText": "实现队列", "language": "PYTHON",
            "code": "print('ok')", "stdin": "", "testCases": [{"input": "", "output": "ok"}],
            "examMode": False, "ownerId": "u2", "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-02-01T00:00:00Z",
        }],
        "IdeExecution": [{
            "id": "exec1", "workspaceId": "ide1", "kind": "RUN", "status": "COMPLETED",
            "sourceSnapshot": "print('ok')", "stdinSnapshot": "", "stdout": "ok\n", "stderr": "",
            "exitCode": 0, "durationMs": 4, "passedTests": 1, "totalTests": 1, "testResults": [],
            "jobId": "job1", "createdAt": "2026-01-02T00:00:00Z", "completedAt": "2026-01-02T00:00:01Z",
        }],
        "IdeTutorMessage": [{
            "id": "tutor1", "workspaceId": "ide1", "role": "ASSISTANT", "status": "COMPLETED",
            "content": "检查边界", "createdAt": "2026-01-03T00:00:00Z", "updatedAt": "2026-01-03T00:00:00Z",
        }],
        "CourseKnowledgeBase": [{
            "id": "kb1", "name": "课程资料", "description": "数据结构", "ownerId": "u1",
            "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-02-01T00:00:00Z",
        }],
        "CourseDocument": [{
            "id": "doc1", "knowledgeBaseId": "kb1", "title": "讲义", "sourceType": "PDF",
            "storagePath": "documents/course.pdf", "contentHash": hashlib.sha256(asset_values["documents/course.pdf"]).hexdigest(),
            "status": "COMPLETED", "extractedText": "队列是 FIFO", "jobId": "job-doc",
            "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-02-01T00:00:00Z",
        }],
        "CourseChunk": [{
            "id": "chunk1", "documentId": "doc1", "chunkIndex": 0, "heading": "队列", "locator": "page:1",
            "content": "队列是 FIFO", "searchText": "队列 FIFO", "tokenEstimate": 5,
            "createdAt": "2026-01-01T00:00:00Z",
        }],
        "CourseConversation": [{
            "id": "course-chat1", "knowledgeBaseId": "kb1", "title": "课程问答",
            "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-02-01T00:00:00Z",
        }],
        "CourseMessage": [{
            "id": "cm1", "conversationId": "course-chat1", "role": "ASSISTANT", "status": "COMPLETED",
            "content": "FIFO", "citations": ["chunk1"], "academicSearch": False,
            "createdAt": "2026-01-02T00:00:00Z", "updatedAt": "2026-01-02T00:00:00Z",
        }],
        "ChatMessage": [{
            "id": "qm1", "questionId": "q1", "role": "USER", "status": "COMPLETED", "content": "为什么？",
            "correctionApplied": False, "userId": "u2", "createdAt": "2026-01-04T00:00:00Z",
            "updatedAt": "2026-01-04T00:00:00Z",
        }],
        "InterviewTemplate": [{
            "id": "template1", "name": "实验验收", "acceptanceRequirements": "解释代码",
            "rubric": [{"name": "正确性", "max_score": 10}], "maxQuestions": 3, "durationMinutes": 20,
            "ownerId": "u1", "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-02-01T00:00:00Z",
        }],
        "InterviewSession": [{
            "id": "interview1", "templateId": "template1", "ownerId": "u1", "studentExternalId": "42",
            "studentName": "Student", "accessTokenHash": "DO_NOT_MIGRATE_ACCESS_TOKEN",
            "accessTokenExpiresAt": "2026-12-01T00:00:00Z", "status": "COMPLETED", "statusMessage": "done",
            "problemTitle": "队列实验", "problemDescription": "实现队列", "studentCode": "print('ok')",
            "codeLanguage": "python", "reportContent": "报告", "acceptanceRequirements": "解释代码",
            "rubric": [{"name": "正确性", "max_score": 10}], "maxQuestions": 3, "durationMinutes": 20,
            "rubricScores": [{"name": "正确性", "score": 9}], "totalScore": 90, "confidence": 0.9,
            "integrityConclusion": "pass", "finalSummary": "通过", "reviewNotes": "教师已复核",
            "callbackUrl": "https://legacy.invalid/callback?token=DO_NOT_MIGRATE_CALLBACK_TOKEN",
            "startedAt": "2026-02-01T00:00:00Z", "completedAt": "2026-02-01T00:10:00Z",
            "createdAt": "2026-02-01T00:00:00Z", "updatedAt": "2026-02-01T00:10:00Z",
        }],
        "InterviewTurn": [{
            "id": "turn1", "interviewSessionId": "interview1", "sequence": 0,
            "questionMarkdown": "为什么使用队列？", "focus": "理解", "sourceType": "CODE",
            "sourceStartLine": 1, "sourceEndLine": 1, "answerTranscript": "因为 FIFO",
            "transcriptFinal": True, "evaluation": {"correct": True},
            "askedAt": "2026-02-01T00:01:00Z", "answeredAt": "2026-02-01T00:02:00Z",
        }],
        "InterviewMediaChunk": [{
            "id": "media1", "interviewSessionId": "interview1", "turnId": "turn1", "sequence": 0,
            "kind": "ANSWER_AUDIO", "storagePath": "interviews/answer.webm", "mimeType": "audio/webm",
            "sizeBytes": len(asset_values["interviews/answer.webm"]),
            "sha256": hashlib.sha256(asset_values["interviews/answer.webm"]).hexdigest(), "durationMs": 2000,
            "createdAt": "2026-02-01T00:02:00Z",
        }],
        "InterviewProctorEvent": [{
            "id": "event1", "interviewSessionId": "interview1", "type": "focus_lost",
            "severity": "WARNING", "details": {"count": 1}, "occurredAt": "2026-02-01T00:03:00Z",
            "createdAt": "2026-02-01T00:03:00Z",
        }],
        "PointLedgerEntry": [{
            "id": "ledger1", "userId": "u2", "type": "AI_USAGE", "amountPoints": -3,
            "balanceAfter": 2, "costUsd": 0.003, "description": "legacy charge", "aiRunId": "run1",
            "billingKey": "legacy-billing-key", "createdAt": "2026-02-01T00:00:00Z",
        }],
        "AiRun": [{
            "id": "run1", "kind": "INTERVIEW", "status": "COMPLETED", "model": "legacy-model",
            "reasoningEffort": "high", "sessionId": "DO_NOT_MIGRATE_MODEL_SESSION", "inputTokens": 100,
            "cachedInputTokens": 10, "outputTokens": 20, "reasoningOutputTokens": 5,
            "estimatedCostUsd": 0.003, "userId": "u2", "startedAt": "2026-02-01T00:00:00Z",
            "completedAt": "2026-02-01T00:00:01Z",
        }],
        "AiRunEvent": [{
            "id": "run-event1", "runId": "run1", "sequence": 1, "type": "completed",
            "summary": "legacy completion", "payload": {"ok": True}, "createdAt": "2026-02-01T00:00:01Z",
        }],
        "WorkflowJob": [{
            "id": "job1", "kind": "IDE_RUN", "status": "COMPLETED", "attempts": 1,
            "ownerId": "u2",
            "createdAt": "2026-01-01T00:00:00Z", "completedAt": "2026-01-01T00:00:01Z",
        }],
        "ContentReview": [{
            "id": "review1", "userId": "u2", "decision": "NORMAL", "categories": [], "reason": "ok",
            "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z",
        }],
        "ExamCandidate": [{
            "id": "candidate1", "passwordCiphertext": "DO_NOT_MIGRATE_EXAM_PASSWORD", "examId": "exam1",
            "studentNo": "2026001", "name": "Student", "username": "student", "roomId": "room1", "seatNo": "3",
        }],
        "Exam": [{"id": "exam1", "ownerId": "u1", "name": "Legacy exam", "mode": "FORMAL", "status": "COMPLETED"}],
        "ExamRoom": [{"id": "room1", "examId": "exam1", "name": "Lab", "capacity": 30}],
        "SubjectiveReviewTask": [{"id": "grade1", "ownerId": "u1", "title": "Legacy grade", "questionMarkdown": "FIFO?",
            "studentAnswer": "First in", "rubric": [{"name": "correct", "max_score": 10}], "dimensionScores": [{"name": "correct", "score": 9}],
            "imagePaths": ["figures/source.png"], "totalScore": 9, "confidence": 0.9}],
        "PlagiarismScan": [{"id": "scan1", "ownerId": "u1", "name": "Legacy scan", "language": "PYTHON", "threshold": 0.7}],
        "PlagiarismSubmission": [{"id": "code1", "scanId": "scan1", "studentId": "legacy-student1", "code": "print(1)"},
                                  {"id": "code2", "scanId": "scan1", "studentId": "legacy-student2", "code": "print(1)"}],
        "PlagiarismPair": [{"id": "pair1", "scanId": "scan1", "firstSubmissionId": "code1", "secondSubmissionId": "code2",
                           "jplagScore": 1, "decision": "CLEAR", "reviewed": True}],
    })
    return {"format": "problem-stack-prisma-json-v1", "schema_sha256": "a" * 64, "tables": tables}


@pytest.fixture
def migration_fixture(tmp_path):
    source = tmp_path / "legacy-assets"
    values = asset_tree(source)
    identities = {
        "u1": {"userId": "7", "username": "teacher"},
        "u2": {"userId": "42", "username": "student"},
        "InterviewSession:interview1:student": {"userId": "42", "username": "student"},
        "ExamCandidate:candidate1:student": {"userId": "42", "username": "student"},
    }
    courses = {
        "ProblemSet:set1": {"psid": 8},
        "IdeWorkspace:ide1": {"psid": 8},
        "CourseKnowledgeBase:kb1": {"course_id": 7},
        "InterviewSession:interview1": {"course_id": 7},
    }
    return full_legacy_export(values), identities, courses, source


def test_full_schema_conversion_preserves_history_and_excludes_credentials(migration_fixture):
    raw, identities, courses, source = migration_fixture
    bundle = convert_export(raw, identities, courses, source)
    assert bundle["report"]["ready_to_import"] is True
    assert {item["kind"] for item in bundle["resources"]} >= {
        "question", "collection", "authoring", "ide", "course", "document", "conversation", "interview"
    }
    resources = {item["id"]: item for item in bundle["resources"]}
    assert resources["ProblemSet:set1"]["data"]["question_ids"] == ["Question:q1"]
    assert resources["Question:q1"]["data"]["legacy_library_owners"] == ["42"]
    assert resources["AuthoringProject:author1"]["data"]["messages"][0]["text"] == "增加边界"
    assert resources["IdeWorkspace:ide1"]["data"]["execution_history"][0]["stdout"] == "ok\n"
    assert resources["CourseDocument:doc1"]["data"]["chunks"][0]["locator"] == "page:1"
    assert resources["CourseConversation:course-chat1"]["data"]["messages"][0]["citations"] == ["chunk1"]
    assert resources["InterviewSession:interview1"]["data"]["turns"][0]["answer"] == "因为 FIFO"
    assert resources["InterviewSession:interview1"]["data"]["student_id"] == "42"
    assert resources["InterviewSession:interview1"]["data"]["media_segments"][0]["segment_count"] == 1
    assert bundle["audits"]["ledger"][0]["disposition"] == "audit_only_no_balance_mutation"
    assert bundle["audits"]["ai_runs"][0]["disposition"] == "audit_only_no_charge"
    assert bundle["audits"]["ai_runs"][0]["events"][0]["summary"] == "legacy completion"
    serialized = json.dumps(bundle, ensure_ascii=False)
    for secret in ("DO_NOT_MIGRATE_PASSWORD", "DO_NOT_MIGRATE_API_KEY", "DO_NOT_MIGRATE_ACCESS_TOKEN",
                   "DO_NOT_MIGRATE_CALLBACK_TOKEN", "DO_NOT_MIGRATE_EXAM_PASSWORD", "DO_NOT_MIGRATE_MODEL_SESSION"):
        assert secret not in serialized
    assert bundle["report"]["forbidden_fields"]
    assert any(item["path"].endswith("callbackUrl") for item in bundle["report"]["omitted_sensitive_fields"])
    assert bundle["report"]["unknown_fields"] == [
        {"table": "Question", "id": "q1", "fields": ["futureColumn"]}
    ]
    assert bundle["report"]["uncovered_tables"] == []
    assert resources["Exam:exam1"]["data"]["candidates"][0]["username"] == "student"
    assert "native_psid" not in resources["Exam:exam1"]["data"]
    assert resources["SubjectiveReviewTask:grade1"]["data"]["candidate"]["score"] == 9
    assert len(resources["PlagiarismScan:scan1"]["data"]["candidate"]["pairs"]) == 1


def test_converter_is_dry_run_by_default_and_copies_only_verified_assets(migration_fixture, tmp_path, capsys):
    from scripts.convert_problem_stack_export import main
    raw, identities, courses, source = migration_fixture
    input_path, identity_path, course_path = tmp_path / "input.json", tmp_path / "identities.json", tmp_path / "courses.json"
    input_path.write_text(json.dumps(raw), encoding="utf-8")
    identity_path.write_text(json.dumps(identities), encoding="utf-8")
    course_path.write_text(json.dumps(courses), encoding="utf-8")
    output, asset_output = tmp_path / "neutral" / "bundle.json", tmp_path / "neutral"
    arguments = ["--input", str(input_path), "--identity-map", str(identity_path), "--course-map", str(course_path),
                 "--asset-source", str(source), "--output", str(output), "--asset-output", str(asset_output)]
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert not output.exists()
    bundle_before_tamper = convert_export(raw, identities, courses, source)
    document = source / "documents/course.pdf"
    original_document = document.read_bytes()
    document.write_bytes(b"changed after audit")
    with pytest.raises(ConversionError, match="changed since conversion"):
        write_bundle(bundle_before_tamper, tmp_path / "bad" / "bundle.json", source, tmp_path / "bad")
    document.write_bytes(original_document)
    assert main([*arguments, "--write"]) == 0
    assert output.is_file()
    bundle = json.loads(output.read_text(encoding="utf-8"))
    for asset in bundle["assets"]:
        copied = asset_output.joinpath(*Path(asset["bundle_path"]).parts)
        assert hashlib.sha256(copied.read_bytes()).hexdigest() == asset["sha256"]


def test_explicit_identity_course_and_asset_failures_are_reported(migration_fixture, tmp_path):
    raw, identities, courses, source = migration_fixture
    missing_identity = convert_export(raw, {"u1": identities["u1"]}, courses, source)
    assert not missing_identity["report"]["ready_to_import"]
    assert {item["legacy_user_id"] for item in missing_identity["report"]["unresolved_identities"]} == {
        "u2", "InterviewSession:interview1:student", "ExamCandidate:candidate1:student"
    }
    missing_course = convert_export(raw, identities, {}, source)
    assert not missing_course["report"]["ready_to_import"]
    assert missing_course["report"]["unresolved_courses"]
    (source / "documents/course.pdf").unlink()
    missing_asset = convert_export(raw, identities, courses, source)
    assert not missing_asset["report"]["ready_to_import"]
    assert any(item["path"] == "documents/course.pdf" for item in missing_asset["report"]["missing_assets"])
    raw["tables"]["CourseDocument"][0]["storagePath"] = "../outside.pdf"
    escaped = convert_export(raw, identities, courses, source)
    assert any("Unsafe asset path" in item["reason"] for item in escaped["report"]["missing_assets"])


def test_nonempty_unknown_table_and_dangling_exam_candidate_block_import(migration_fixture):
    raw, identities, courses, source = migration_fixture
    raw["tables"]["FutureAssessment"] = [{"id": "not-covered", "result": 42}]
    result = convert_export(raw, identities, courses, source)
    assert not result["report"]["ready_to_import"]
    assert result["report"]["uncovered_nonempty_tables"] == ["FutureAssessment"]
    raw["tables"].pop("FutureAssessment")
    raw["tables"]["ExamCandidate"][0]["examId"] = "missing"
    result = convert_export(raw, identities, courses, source)
    assert not result["report"]["ready_to_import"]
    assert any(item["table"] == "ExamCandidate" for item in result["report"]["dangling_relations"])


def test_neutral_import_is_stable_idempotent_and_never_recharges(database, migration_fixture, tmp_path):
    raw, identities, courses, source = migration_fixture
    bundle = convert_export(raw, identities, courses, source)
    bundle_root = tmp_path / "bundle"
    output = bundle_root / "bundle.json"
    write_bundle(bundle, output, source, bundle_root)
    assert not import_problem_stack(bundle, identities, asset_root=bundle_root)["applied"]
    with store.transaction() as session:
        assert session.query(AiResource).count() == 0
        assert session.query(AiLedger).count() == 0
    applied = import_problem_stack(bundle, identities, True, True, bundle_root)
    assert applied["applied"] and applied["ledger_mutations"] == 0 and applied["task_requeues"] == 0
    assert all(len(item["resource_id"]) == 32 for item in applied["resources"])
    replay = import_problem_stack(bundle, identities, True, True, bundle_root)
    assert all(item["replay"] for item in replay["resources"])
    with store.transaction() as session:
        resources = {json.loads(row.data)["migration"]["source"]: row for row in session.query(AiResource).all()}
        collection = json.loads(resources["problem-stack:ProblemSet:set1"].data)
        question = resources["problem-stack:Question:q1"]
        assert collection["question_ids"] == [question.id]
        assert session.query(AiFile).count() == len(bundle["assets"])
        assert session.query(AiLedger).count() == 0
        assert session.query(TaskJob).count() == 0
    changed = json.loads(json.dumps(bundle))
    changed["resources"][0]["data"]["title"] = "changed"
    with pytest.raises(ValueError, match="Previously imported source changed"):
        import_problem_stack(changed, identities, True, True, bundle_root)
    changed_identity = dict(identities)
    changed_identity["u1"] = {"userId": "99", "username": "other-teacher"}
    with pytest.raises(ValueError, match="identity or scope mapping changed"):
        import_problem_stack(bundle, changed_identity, True, True, bundle_root)


def test_asset_tamper_rolls_back_database_and_remote_apply_is_rejected(database, migration_fixture, tmp_path, monkeypatch):
    raw, identities, courses, source = migration_fixture
    bundle = convert_export(raw, identities, courses, source)
    bundle_root = tmp_path / "tampered-bundle"
    write_bundle(bundle, bundle_root / "bundle.json", source, bundle_root)
    asset = bundle["assets"][0]
    bundle_root.joinpath(*Path(asset["bundle_path"]).parts).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        import_problem_stack(bundle, identities, True, True, bundle_root)
    with store.transaction() as session:
        assert session.query(AiResource).count() == 0
        assert session.query(AiFile).count() == 0

    import auto_task.migrate as migration
    remote_engine = SimpleNamespace(dialect=SimpleNamespace(name="mysql"),
                                    url=SimpleNamespace(host="production.example.invalid"))
    monkeypatch.setattr(migration, "factory", lambda: SimpleNamespace(kw={"bind": remote_engine}))
    monkeypatch.setenv("SDUOJ_MODE", "debug")
    with pytest.raises(ValueError, match="loopback"):
        import_problem_stack({"format": "problem-stack-neutral-v1", "resources": [], "assets": [],
                              "audits": {}, "report": {"ready_to_import": True}}, {}, True, True)
