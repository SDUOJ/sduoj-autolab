import json
from types import SimpleNamespace
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from tests.test_ai_backend import database, ADMIN, USER
from auth import cover_header
from auto_task import store
from auto_task.entities import AiResource
from auto_task.native_bank import save_batch, refresh_reference, update_reference
from auto_task.handlers import apply_result
from db import ProblemGroup, ProblemObjective, ProblemSubjective
from server.question_bank import router


def question(kind="short_answer", text="解释栈", answer="后进先出"):
    return {"question_type": kind, "language": "zh", "original": text, "zh": text,
            "options": [{"label": "A", "original": "甲", "zh": "甲"}, {"label": "B", "original": "乙", "zh": "乙"}] if kind == "single_choice" else [],
            "answer": answer, "explanation": "", "tags": []}


def bank_client(user=ADMIN):
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[cover_header] = lambda: user
    return TestClient(app)


def test_mixed_batch_is_immediately_native_and_retry_does_not_duplicate(database):
    client = bank_client()
    payload = {"title": "期末录题", "operation_key": "batch-one", "questions": [question("single_choice", "选择甲", "A"), question()], "score": 8}
    first = client.post('/question-bank/batches', json=payload)
    assert first.status_code == 200, first.text
    assert {g['type'] for g in first.json()['data']['groups']} == {0, 1}
    replay = client.post('/question-bank/batches', json=payload)
    assert replay.json()['data']['replay']
    with store.transaction() as session:
        assert session.query(ProblemGroup).count() == 2
        assert session.query(ProblemObjective).count() == 1
        assert session.query(ProblemSubjective).count() == 1
        assert session.query(AiResource).filter_by(kind='question').count() == 0
        objective = session.query(ProblemObjective).one()
        assert json.loads(objective.answer) == ['A']
        groups = session.query(ProblemGroup).all()
        assert all(json.loads(g.problemInfo)[0]['score'] == 8 for g in groups)
    payload['title'] = '同键改名'
    assert client.post('/question-bank/batches', json=payload).status_code == 409


def test_invalid_question_rolls_back_whole_batch(database):
    with pytest.raises(HTTPException):
        with store.transaction() as s:
            save_batch(s, ADMIN, '失败批次', 'bad', [question(), question('single_choice', '甲', 'Z')])
    with store.transaction() as s:
        assert s.query(ProblemGroup).count() == s.query(ProblemSubjective).count() == 0


def test_same_question_different_batches_get_distinct_groups(database):
    with store.transaction() as s:
        a = save_batch(s, ADMIN, '同名批次', 'a', [question()])
        b = save_batch(s, ADMIN, '同名批次', 'b', [question()])
        assert a['groups'][0]['gid'] != b['groups'][0]['gid']


def test_students_cannot_create_native_groups(database):
    response = bank_client(USER).post('/question-bank/batches', json={'title': 'x', 'operation_key': 'x', 'questions': [question()]})
    assert response.status_code == 403


def test_ai_suspension_does_not_disable_native_manual_intake(database):
    with store.transaction() as s:
        account = store.account(s, str(ADMIN['userId']), ADMIN['username']); account.status = 'suspended'
    response = bank_client().post('/question-bank/batches', json={'title': 'x', 'operation_key': 'x', 'questions': [question()]})
    assert response.status_code == 200


def test_model_extraction_atomically_creates_native_group(database):
    with store.transaction() as s:
        collection = AiResource(id='native-extraction', kind='collection', owner_id='7', owner_username='teacher', data=store.dumps({'title':'教材录入','question_ids':[]}))
        s.add(collection); s.flush()
        job = SimpleNamespace(id='extract-one', resource_id=collection.id, owner_id='7', owner_username='teacher', task_type='question.extract', payload='{}')
        result = apply_result(s, job, {'questions':[question()]}, collection.version)
        native = s.query(ProblemSubjective).one()
        group = s.query(ProblemGroup).one()
        reference = s.query(AiResource).filter_by(kind='question').one()
        assert json.loads(group.problemInfo)[0]['pid'] == native.pid
        assert json.loads(reference.data)['native']['pid'] == str(native.pid)
        assert result['native_groups'][0]['gid'] == group.gid


def test_native_edit_is_seen_by_ai_and_stale_write_is_rejected(database):
    with store.transaction() as s:
        reference = AiResource(id='reference', kind='question', owner_id='7', owner_username='teacher', data=store.dumps(question()))
        s.add(reference);s.flush()
        save_batch(s, ADMIN, '录入', 'linked', [question()], resources=[reference])
        original = json.loads(reference.data)
        native = s.query(ProblemSubjective).one()
        native.description = '原题组编辑的新内容';s.flush()
        with pytest.raises(HTTPException) as exc:
            update_reference(s, reference, {**original, 'zh':'过期内容'})
        assert exc.value.status_code == 409
        refresh_reference(s, reference)
        assert json.loads(reference.data)['zh'] == '原题组编辑的新内容'
        updated = update_reference(s, reference, {**json.loads(reference.data), 'zh':'从题目工具修改'})
        reference.data = store.dumps(updated)
        assert native.description == '从题目工具修改'
        assert s.query(ProblemSubjective).count() == 1


def test_changed_native_source_prevents_stale_model_result(database):
    with store.transaction() as s:
        reference = AiResource(id='stale', kind='question', owner_id='7', owner_username='teacher', data=store.dumps(question()))
        s.add(reference);s.flush()
        save_batch(s, ADMIN, '录入', 'stale', [question()], resources=[reference])
        s.query(ProblemSubjective).one().description = '教师已修改';s.flush()
        job = SimpleNamespace(id='stale-task', resource_id=reference.id, owner_id='7', owner_username='teacher', task_type='question.solve', payload='{}')
        with pytest.raises(ValueError, match='Native question changed'):
            apply_result(s, job, {'answer':'旧答案','explanation':'','tags':[]}, reference.version)


def test_native_group_tools_enforce_the_original_group_permissions(database):
    with store.transaction() as session:
        saved = save_batch(session, ADMIN, '保密题组', 'permissions', [question()])
        gid = saved['groups'][0]['gid']
    assert bank_client(USER).get('/question-bank/groups/%s/questions' % gid).status_code == 403
    response = bank_client().get('/question-bank/groups/%s/questions' % gid)
    assert response.status_code == 200
    assert response.json()['data']['rows'][0]['question']['answer'] == '后进先出'
