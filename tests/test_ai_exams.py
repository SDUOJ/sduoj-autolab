import json
import pytest
from db import Base, ojClass, ojClassUser, ojCourse, ProblemSet, ProblemSetAnswerSheet
from auto_task import store
from auto_task.entities import AiResource
from server import auto_task_ai as api, auto_task_exams as exams
from tests.test_ai_backend import database, client, ADMIN


@pytest.fixture
def exam_client(client, database, monkeypatch):
    tables = [ojClass.__table__, ojCourse.__table__, ojClassUser.__table__]
    Base.metadata.create_all(database, tables=tables)
    client.app.include_router(exams.router, prefix="/auto-task/ai")
    client.app.dependency_overrides[api.cover_header] = lambda: ADMIN
    async def membership(*_):
        return {"members": [{"username": "student", "userId": 42, "status": 2}]}
    monkeypatch.setattr(exams, "getGroupMember", membership)
    with store.transaction() as s:
        s.add(ojClass(c_id=1, c_name="Lab", c_seat_num=5, address="local", ext_config='{"seat-ban":[{"seat_number":2}]}'))
        s.add(ojCourse(course_id=1, course_name="Exam", group_id=10, tag="exam", c_ids="[1]", creator_username="teacher", manager_groups="[]", ext_config="{}"))
        s.add(ProblemSet(psid=1, name="Test", type=1, groupInfo='[{"gid":1}]', config="{}", groupId=10, username="teacher", tag="test"))
        s.add(AiResource(id="exam", owner_id="7", owner_username="teacher", kind="exam", psid=1, state="published",
            data=store.dumps({"mode": "TEST", "native_psid": 1, "publication_plan": {"groupInfo": [{"gid": 1}]},
                             "candidate": {"rooms": [{"c_id": 1, "name": "Lab", "assigned": [{"username": "student", "user_id": "42", "name": "Student", "seat": 3}]}]}})))
    yield client
    Base.metadata.drop_all(database, tables=tables)


def test_native_seats_bind_replay_and_safe_restore(exam_client):
    client = exam_client
    assert client.get("/auto-task/ai/exams/rooms?course_id=1").json()["data"]["rows"][0]["available_seats"] == [1, 3, 4, 5]
    path = "/auto-task/ai/exams/exam"
    body = {"version": 1, "course_id": 1, "reason": "checked"}
    bound = client.post(path + "/bind-course", json=body)
    assert bound.status_code == 200, bound.text
    assert bound.json()["data"]["assigned"] == 1
    assert client.post(path + "/bind-course", json={**body, "version": 2}).json()["data"]["replay"]
    with store.transaction() as s:
        assert s.query(ojClassUser).one().seat_number == 3
        s.query(ojClassUser).one().seat_number = 4
    assert client.post(path + "/unbind-course", json={"version": 2, "reason": "restore"}).status_code == 409
    with store.transaction() as s:
        s.query(ojClassUser).one().seat_number = 3
    assert client.post(path + "/unbind-course", json={"version": 2, "reason": "restore"}).status_code == 200
    with store.transaction() as s:
        assert not s.query(ojClassUser).count()


def test_test_certificate_requires_real_completed_answer_sheet(exam_client):
    path = "/auto-task/ai/exams/exam"
    body = {"version": 1, "reason": "witnessed test"}
    assert exam_client.post(path + "/validate-test", json=body).status_code == 409
    with store.transaction() as s:
        s.add(ProblemSetAnswerSheet(asid=1, psid=1, username="student", finish=1))
    response = exam_client.post(path + "/validate-test", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["certificate"]["completed_sheets"] == 1
    assert exam_client.get(path + "/monitor").json()["data"]["rows"][0]["submission"] == "SUBMITTED"
    assert exam_client.get(path + "/roster.csv").content.startswith(b"\xef\xbb\xbf")
