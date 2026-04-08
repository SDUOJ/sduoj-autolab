import asyncio
import importlib
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


@pytest.fixture
def auto_task_module(monkeypatch):
    fake_auth = types.ModuleType("auth")
    fake_auth.cover_header = lambda *args, **kwargs: None
    fake_auth.problem_set_manager = MagicMock()

    fake_utils = types.ModuleType("utils")
    fake_utils.makeResponse = lambda data: data

    fake_ser_base_type = types.ModuleType("ser.base_type")

    class FakePage:
        def __init__(self, pageNow, pageSize):
            self.pageNow = pageNow
            self.pageSize = pageSize

        def offset(self):
            return (max(1, self.pageNow) - 1) * self.pageSize

        def limit(self):
            return self.pageSize

    fake_ser_base_type.page = FakePage

    fake_answer_sheet = types.ModuleType("model.answer_sheet")
    fake_answer_sheet.answerSheetModel = object

    fake_db = types.ModuleType("db")
    fake_db.ProblemSubjective = type("ProblemSubjective", (), {})
    fake_db.ProblemSetAnswerSheet = type("ProblemSetAnswerSheet", (), {})
    fake_db.ProblemSetAnswerSheetDetail = type("ProblemSetAnswerSheetDetail", (), {})
    fake_db.ProblemSet = type("ProblemSet", (), {})

    fake_sduoj_api = types.ModuleType("sduojApi")

    async def _get_group_member(group_id):
        return {"username": f"owner-{group_id}"}

    fake_sduoj_api.getGroupMember = _get_group_member

    monkeypatch.setitem(sys.modules, "auth", fake_auth)
    monkeypatch.setitem(sys.modules, "utils", fake_utils)
    monkeypatch.setitem(sys.modules, "ser.base_type", fake_ser_base_type)
    monkeypatch.setitem(sys.modules, "model.answer_sheet", fake_answer_sheet)
    monkeypatch.setitem(sys.modules, "db", fake_db)
    monkeypatch.setitem(sys.modules, "sduojApi", fake_sduoj_api)
    monkeypatch.delitem(sys.modules, "server.auto_task", raising=False)

    return importlib.import_module("server.auto_task")


def _install_auto_task_model(monkeypatch, model_cls):
    fake_model_module = types.ModuleType("model.auto_task")
    fake_model_module.autoTaskModel = model_cls
    monkeypatch.setitem(sys.modules, "model.auto_task", fake_model_module)


class _BaseFakeAutoTaskModel:
    def __init__(self):
        self.session = SimpleNamespace(close=MagicMock())


def test_list_auto_tasks_psid_prefers_problem_set_auth(auto_task_module, monkeypatch):
    class FakeAutoTaskModel(_BaseFakeAutoTaskModel):
        def list_tasks_by_params(self, **kwargs):
            return 1, [{"id": "task-1", "psid": 210}]

    _install_auto_task_model(monkeypatch, FakeAutoTaskModel)

    problem_set_manager = MagicMock()
    group_creator = AsyncMock()
    monkeypatch.setattr(auto_task_module, "problem_set_manager", problem_set_manager)
    monkeypatch.setattr(auto_task_module, "_assert_group_creator", group_creator)

    data = auto_task_module.AutoTaskListRequest(psid=210, pageNow=1, pageSize=20)
    result = asyncio.run(auto_task_module.list_auto_tasks(data, {"username": "teacher"}))

    assert result["total"] == 1
    assert result["rows"] == [{"id": "task-1", "psid": 210}]
    problem_set_manager.assert_called_once_with(210, {"username": "teacher"})
    group_creator.assert_not_awaited()


def test_list_auto_tasks_group_id_requires_group_creator(auto_task_module, monkeypatch):
    class FakeAutoTaskModel(_BaseFakeAutoTaskModel):
        def list_tasks_by_params(self, **kwargs):
            return 1, [{"id": "task-1", "groupId": 94}]

    _install_auto_task_model(monkeypatch, FakeAutoTaskModel)

    problem_set_manager = MagicMock()
    group_creator = AsyncMock()
    monkeypatch.setattr(auto_task_module, "problem_set_manager", problem_set_manager)
    monkeypatch.setattr(auto_task_module, "_assert_group_creator", group_creator)

    data = auto_task_module.AutoTaskListRequest(groupId=94, pageNow=1, pageSize=20)
    result = asyncio.run(auto_task_module.list_auto_tasks(data, {"username": "owner-94"}))

    assert result["total"] == 1
    assert result["rows"] == [{"id": "task-1", "groupId": 94}]
    group_creator.assert_awaited_once_with(94, {"username": "owner-94"})
    problem_set_manager.assert_not_called()


def test_list_auto_tasks_without_filters_applies_row_level_permissions(auto_task_module, monkeypatch):
    class FakeAutoTaskModel(_BaseFakeAutoTaskModel):
        def list_tasks_all_by_params(self, **kwargs):
            return [
                {"id": "task-psid", "psid": 210, "groupId": 94},
                {"id": "task-group", "groupId": 94},
                {"id": "task-payload"},
                {"id": "task-deny-psid", "psid": 211},
                {"id": "task-deny-group", "groupId": 95},
            ]

        def get_task_detail(self, task_id):
            if task_id == "task-payload":
                return {
                    "logs": [
                        {"tag": "payload", "content": '{"psid": 310}'}
                    ]
                }
            raise HTTPException(status_code=404, detail="任务不存在")

    _install_auto_task_model(monkeypatch, FakeAutoTaskModel)

    def _problem_set_manager(psid, user):
        if psid not in {210, 310}:
            raise HTTPException(status_code=403, detail="Permission Denial")

    async def _group_creator(group_id, user):
        if group_id != 94:
            raise HTTPException(status_code=403, detail="Permission Denial")

    monkeypatch.setattr(auto_task_module, "problem_set_manager", _problem_set_manager)
    monkeypatch.setattr(auto_task_module, "_assert_group_creator", _group_creator)

    data = auto_task_module.AutoTaskListRequest(pageNow=1, pageSize=20)
    result = asyncio.run(auto_task_module.list_auto_tasks(data, {"username": "teacher"}))

    assert result["total"] == 3
    assert [row["id"] for row in result["rows"]] == [
        "task-psid",
        "task-group",
        "task-payload",
    ]


def test_task_detail_prefers_psid_over_group_id(auto_task_module, monkeypatch):
    class FakeAutoTaskModel(_BaseFakeAutoTaskModel):
        def get_task_detail(self, task_id):
            return {"id": task_id, "psid": 210, "groupId": 94, "logs": []}

    _install_auto_task_model(monkeypatch, FakeAutoTaskModel)

    problem_set_manager = MagicMock()
    group_creator = AsyncMock()
    monkeypatch.setattr(auto_task_module, "problem_set_manager", problem_set_manager)
    monkeypatch.setattr(auto_task_module, "_assert_group_creator", group_creator)

    result = asyncio.run(auto_task_module.get_task_detail("task-1", {"username": "teacher"}))

    assert result["id"] == "task-1"
    problem_set_manager.assert_called_once_with(210, {"username": "teacher"})
    group_creator.assert_not_awaited()


def test_group_task_creation_requires_group_creator(auto_task_module, monkeypatch):
    class FakeAutoTaskModel(_BaseFakeAutoTaskModel):
        def add_task(self, **kwargs):
            return "task-group-1"

    _install_auto_task_model(monkeypatch, FakeAutoTaskModel)

    group_creator = AsyncMock()
    monkeypatch.setattr(auto_task_module, "_assert_group_creator", group_creator)

    data = auto_task_module.SummaryReportRequest(groupId=94, psids=[210])
    result = asyncio.run(auto_task_module.create_summary_report_task(
        data,
        {"username": "owner-94", "userId": 1}
    ))

    assert result["taskId"] == "task-group-1"
    group_creator.assert_awaited_once_with(94, {"username": "owner-94", "userId": 1})