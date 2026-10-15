import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from tests.test_ai_backend import database, client, USER, ADMIN, resource, model_config
from auto_task import store


def test_gateway_cidrs_fail_closed_for_release(monkeypatch):
    from auto_task.access import gateway_allowed
    monkeypatch.delenv("AUTOLAB_AI_TRUSTED_GATEWAY_CIDRS", raising=False)
    assert gateway_allowed("127.0.0.1", "debug")
    assert gateway_allowed("::1", "debug")
    assert not gateway_allowed("192.168.1.5", "debug")
    with pytest.raises(HTTPException) as missing:
        gateway_allowed("127.0.0.1", "release")
    assert missing.value.status_code == 503
    monkeypatch.setenv("AUTOLAB_AI_TRUSTED_GATEWAY_CIDRS", "172.18.0.0/24,127.0.0.1/32")
    assert gateway_allowed("172.18.0.5", "release")
    assert not gateway_allowed("10.0.0.5", "release")
    monkeypatch.setenv("AUTOLAB_AI_TRUSTED_GATEWAY_CIDRS", "0.0.0.0/0")
    with pytest.raises(HTTPException):
        gateway_allowed("10.0.0.5", "release")
    with pytest.raises(HTTPException):
        gateway_allowed("10.0.0.5", "debug")


@pytest.mark.parametrize("value", ["", "172.18.0.0/24,", "not-a-network"])
def test_gateway_rejects_ambiguous_configuration(monkeypatch, value):
    from auto_task.access import gateway_allowed
    monkeypatch.setenv("AUTOLAB_AI_TRUSTED_GATEWAY_CIDRS", value)
    with pytest.raises(HTTPException) as error:
        gateway_allowed("172.18.0.5", "release")
    assert error.value.status_code == 503


def test_gateway_rejects_proxy_rewritten_peer(monkeypatch):
    from auto_task.access import gateway_allowed
    monkeypatch.setenv("AUTOLAB_AI_TRUSTED_GATEWAY_CIDRS", "127.0.0.1/32")
    with pytest.raises(HTTPException, match="rewriting hides"):
        gateway_allowed("127.0.0.1", "release", 0)
    assert gateway_allowed("203.0.113.10", "debug", 0)


def identity_client():
    from auth import cover_header
    app = FastAPI()

    @app.get("/identity")
    def identity(user=Depends(cover_header)):
        return user

    return TestClient(app)


def identity_headers(user=USER, subject=None):
    from auth import parse_header
    return {
        "SDUOJUserInfo": parse_header(user),
        "SDUOJUserInfo-UserId": str(user["userId"] if subject is None else subject),
    }


def test_identity_header_uses_gateway_form_encoding():
    user = {**USER, "username": "space + plus"}
    response = identity_client().get("/identity", headers=identity_headers(user))
    assert response.status_code == 200
    assert response.json()["username"] == "space + plus"


def test_identity_header_rejects_duplicates_and_ambiguous_json():
    from starlette.requests import Request
    from auth import parse_header
    encoded = parse_header(USER)
    request = Request({
        "type": "http", "method": "GET", "path": "/identity",
        "headers": [(b"sduojuserinfo", encoded.encode()),
                    (b"sduojuserinfo", encoded.encode()),
                    (b"sduojuserinfo-userid", b"42")],
    })
    from auth import cover_header
    with pytest.raises(HTTPException) as error:
        cover_header(request, encoded, "42")
    assert error.value.status_code == 403
    duplicate_key = "%7B%22userId%22%3A42%2C%22userId%22%3A7%2C%22username%22%3A%22student%22%2C%22roles%22%3A%5B%22user%22%5D%2C%22groups%22%3A%5B%5D%7D"
    assert identity_client().get(
        "/identity", headers={"SDUOJUserInfo": duplicate_key,
                              "SDUOJUserInfo-UserId": "42"}).status_code == 403


def test_identity_header_requires_both_gateway_headers():
    headers = identity_headers()
    assert identity_client().get(
        "/identity", headers={"SDUOJUserInfo": headers["SDUOJUserInfo"]}).status_code == 403
    assert identity_client().get(
        "/identity", headers={"SDUOJUserInfo-UserId": "42"}).status_code == 403


def test_identity_header_rejects_duplicate_subject_header():
    from auth import cover_header, parse_header
    from starlette.requests import Request
    encoded = parse_header(USER)
    request = Request({
        "type": "http", "method": "GET", "path": "/identity",
        "headers": [(b"sduojuserinfo", encoded.encode()),
                    (b"sduojuserinfo-userid", b"42"),
                    (b"sduojuserinfo-userid", b"42")],
    })
    with pytest.raises(HTTPException) as error:
        cover_header(request, encoded, "42")
    assert error.value.status_code == 403


@pytest.mark.parametrize("subject", ["7", "042", "+42"])
def test_identity_header_rejects_mismatched_or_noncanonical_subject(subject):
    assert identity_client().get(
        "/identity", headers=identity_headers(subject=subject)).status_code == 403


def test_identity_header_rejects_non_ascii_subject():
    from auth import cover_header, parse_header
    from starlette.requests import Request
    encoded = parse_header(USER)
    request = Request({
        "type": "http", "method": "GET", "path": "/identity",
        "headers": [(b"sduojuserinfo", encoded.encode()),
                    (b"sduojuserinfo-userid", b"\xff")],
    })
    with pytest.raises(HTTPException) as error:
        cover_header(request, encoded, "\xff")
    assert error.value.status_code == 403


@pytest.mark.parametrize("change", [
    {"userId": "42"}, {"userId": True}, {"username": ""},
    {"roles": "admin"}, {"groups": [True]},
])
def test_identity_header_rejects_noncanonical_claim_types(change):
    import json
    from urllib.parse import quote_plus
    value = {**USER, **change}
    encoded = quote_plus(json.dumps(value, separators=(",", ":")))
    assert identity_client().get(
        "/identity", headers={"SDUOJUserInfo": encoded,
                              "SDUOJUserInfo-UserId": "42"}).status_code == 403


@pytest.mark.parametrize("change", [
    {"roles": ["user", "user"]}, {"roles": ["x" * 129]}, {"groups": [1, 1]},
])
def test_identity_header_rejects_duplicate_or_oversized_claims(change):
    import json
    from urllib.parse import quote_plus
    value = {**USER, **change}
    encoded = quote_plus(json.dumps(value, separators=(",", ":")))
    assert identity_client().get(
        "/identity", headers={"SDUOJUserInfo": encoded,
                              "SDUOJUserInfo-UserId": "42"}).status_code == 403


def test_worker_refreshes_oj_identity_and_rechecks_resource(database, monkeypatch):
    from auto_task.access import authorize_execution
    monkeypatch.setenv("AUTOLAB_AI_VALIDATE_IDENTITY", "1")
    with store.transaction() as s:
        row = resource(s)
    job = {"owner_id": "42", "owner_username": "student", "resource_id": row.id}
    monkeypatch.setattr("auto_task.access.fetch_actor", lambda *_: USER)
    authorize_execution(job)
    monkeypatch.setattr("auto_task.access.fetch_actor", lambda *_: {**USER, "username": "renamed"})
    with pytest.raises(store.LeaseLost):
        authorize_execution(job)
    monkeypatch.setattr("auto_task.access.fetch_actor", lambda *_: USER)
    from auto_task.entities import AiResource
    with store.transaction() as s:
        s.query(AiResource).get(row.id).deleted = 1
    with pytest.raises(store.LeaseLost):
        authorize_execution(job)


def test_release_worker_identity_refresh_cannot_be_disabled(monkeypatch):
    from auto_task.access import authorize_execution
    import const
    monkeypatch.setattr(const, "MOD", "release")
    monkeypatch.setenv("AUTOLAB_AI_VALIDATE_IDENTITY", "0")
    calls = []
    monkeypatch.setattr("auto_task.access.fetch_actor", lambda subject: calls.append(subject) or USER)
    authorize_execution({"owner_id": "42", "owner_username": "student"})
    assert calls == ["42"]


def test_identity_refresh_rejects_redirect_response(monkeypatch):
    import requests
    import sys
    from types import SimpleNamespace
    from auto_task.access import fetch_actor

    def service_address(_):
        return "127.0.0.1:1234"

    class RedirectResponse:
        status_code = 302

        def raise_for_status(self):
            return None

        def json(self):
            return USER

    options = {}
    monkeypatch.setitem(sys.modules, "sduojApi", SimpleNamespace(
        get_service_address=service_address, requestHeaders={}))
    monkeypatch.setattr(
        "auto_task.access.requests.get",
        lambda *args, **kwargs: options.update(kwargs) or RedirectResponse(),
    )
    with pytest.raises(requests.HTTPError, match="Unexpected identity response status"):
        fetch_actor("42")
    assert options["allow_redirects"] is False


def test_worker_authorizes_before_handling_and_before_completion(database, monkeypatch):
    from auto_task.entities import TaskJob
    from auto_task.worker import run_once
    with store.transaction() as s:
        row = resource(s)
        job_id = store.submit(s, USER, "question.extract", {}, "reauthorize", row).id
    monkeypatch.setenv("AUTOLAB_AI_VALIDATE_IDENTITY", "1")
    calls = []
    monkeypatch.setattr("auto_task.access.fetch_actor", lambda subject: calls.append(subject) or USER)
    monkeypatch.setattr("auto_task.handlers.handle", lambda context: ({"ok": True}, None))
    assert run_once()
    assert calls == ["42", "42"]
    with store.transaction() as s:
        assert s.query(TaskJob).get(job_id).status == "success"


def test_budget_reservations_prevent_concurrent_scope_overspend(database):
    from auto_task.accounting import begin_run
    config = model_config().copy(update={"monthly_course_budget_usd": 0.025})
    with store.transaction() as s:
        first = resource(s, "42")
        second = resource(s, "7")
        first.course_id = second.course_id = 123
        store.submit(s, USER, "question.extract", {}, "first", first)
        store.submit(s, ADMIN, "question.extract", {}, "second", second)
    a, b = store.claim(), store.claim()
    assert a and b
    begin_run(a, config)
    with pytest.raises(ValueError, match="Monthly course budget"):
        begin_run(b, config)


@pytest.mark.parametrize("user_id", [42, "42", "9223372036854775807"])
def test_identity_refresh_accepts_java_long_serialization(monkeypatch, user_id):
    from types import SimpleNamespace
    import sys
    from auto_task.access import fetch_actor

    def address(_):
        return "127.0.0.1:1234"

    monkeypatch.setitem(sys.modules, "sduojApi", SimpleNamespace(
        get_service_address=address, requestHeaders={}))
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: SimpleNamespace(
        status_code=200, json=lambda: {**USER, "userId": user_id, "groups": ["7", 8]}))
    actor = fetch_actor(str(user_id))
    assert actor["userId"] == int(user_id)
    assert actor["groups"] == [7, 8]


def test_fresh_identity_rpc_is_safe_inside_async_api_decorator(monkeypatch):
    import asyncio
    import sys
    from types import SimpleNamespace
    from auto_task.access import fetch_actor
    monkeypatch.setitem(sys.modules, "sduojApi", SimpleNamespace(
        get_service_address=lambda _: "127.0.0.1:1234", requestHeaders={}))
    calls = []
    monkeypatch.setattr("requests.get", lambda url, **kwargs: calls.append(url) or SimpleNamespace(
        status_code=200, json=lambda: USER))
    async def api_route():
        return fetch_actor("42", fresh=True)
    assert asyncio.run(api_route())["userId"] == 42
    assert calls == ["http://127.0.0.1:1234/internal/user/runSubject"]


@pytest.mark.parametrize("user_id", ["042", "+42", " 42", "４２", "0", "-1",
                                    "9223372036854775808", True, None])
def test_identity_refresh_rejects_invalid_java_long(monkeypatch, user_id):
    from types import SimpleNamespace
    import sys
    from auto_task.access import fetch_actor

    def address(_):
        return "127.0.0.1:1234"

    monkeypatch.setitem(sys.modules, "sduojApi", SimpleNamespace(
        get_service_address=address, requestHeaders={}))
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: SimpleNamespace(
        status_code=200, json=lambda: {**USER, "userId": user_id}))
    with pytest.raises(ValueError):
        fetch_actor("42")
