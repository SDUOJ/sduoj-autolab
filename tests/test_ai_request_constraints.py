import pytest
from pydantic import ValidationError
from server.auto_task_questions import FollowupRequest
from server.auto_task_collaboration import MemberInvite
from server.auto_task_management import AccountAccessChange
from server.auto_task_native import NativePlagiarismCreate
from server.auto_task_interviews import InvitationCreate, MediaAttach


@pytest.mark.parametrize("model,payload,field,valid,invalid", [
    (FollowupRequest, {"operation_key": "follow"}, "task_type", "question.solve", "ide.execute"),
    (MemberInvite, {"subject_id": "42", "username": "student"}, "role", "contributor", "owner"),
    (AccountAccessChange, {"reason": "review", "suspended": False, "operation_key": "access"}, "subject_id", "42", "-1"),
    (NativePlagiarismCreate, {"psid": 1, "group_id": 1, "pid": 1, "operation_key": "scan"}, "language", "cpp", "ruby"),
    (NativePlagiarismCreate, {"psid": 1, "group_id": 1, "pid": 1, "operation_key": "scan"}, "selection_policy", "latest", "invalid"),
    (InvitationCreate, {"operation_key": "invite", "student_username": "student", "title": "Interview"}, "student_id", "42", "0"),
    (MediaAttach, {"file_id": "file", "turn_sequence": 0, "track": "video", "segment_index": 0,
                  "segment_count": 1, "duration_ms": 1000}, "sha256", "a" * 64, "g" * 64),
])
def test_declared_string_constraints_are_enforced(model, payload, field, valid, invalid):
    assert getattr(model(**{**payload, field: valid}), field) == valid
    with pytest.raises(ValidationError) as caught:
        model(**{**payload, field: invalid})
    assert any(error["loc"] == (field,) and error["type"] == "value_error.str.regex"
               for error in caught.value.errors())
