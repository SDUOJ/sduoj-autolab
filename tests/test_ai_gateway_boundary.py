"""The shared middleware checks the actual peer before trusting forwarded identities."""
import json
from urllib.parse import quote_plus, unquote_plus

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from auth import cover_header
from auto_task.resources import require_admin
from main import ai_gateway_boundary

PREFIXES = ("/auto-task", "/management-agent", "/question-bank")
HEADERS = {
    "SDUOJUserInfo": quote_plus(json.dumps({"userId": 7, "username": "teacher",
        "roles": ["admin"], "groups": []})),
    "SDUOJUserInfo-UserId": "7",
}


def client(peer, duplicate_identity=False):
    app = FastAPI()
    app.middleware("http")(ai_gateway_boundary)

    def probe(user=Depends(cover_header)):
        require_admin(user)
        return {"ok": True}

    for prefix in (*PREFIXES, "/ordinary", "/management-agents"):
        app.add_api_route(prefix + "/probe", probe, methods=["GET"])

    async def transport(scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "client": peer}
            if duplicate_identity:
                identity = next(value for name, value in scope['headers'] if name == b'sduojuserinfo')
                scope['headers'] = [*scope['headers'], (b'sduojuserinfo', identity)]
        await app(scope, receive, send)

    return TestClient(transport)


@pytest.fixture(autouse=True)
def production_boundary(monkeypatch):
    import const
    monkeypatch.setattr(const, "MOD", "release")
    monkeypatch.setenv("AUTOLAB_AI_TRUSTED_GATEWAY_CIDRS", "10.202.0.0/24")


@pytest.mark.parametrize("prefix", PREFIXES)
def test_untrusted_peer_is_rejected_before_identity_headers(prefix):
    assert client(("10.203.0.9", 50000)).get(prefix + "/probe", headers=HEADERS).status_code == 403


@pytest.mark.parametrize("prefix", PREFIXES)
def test_missing_configuration_and_rewritten_peer_fail_closed(prefix, monkeypatch):
    monkeypatch.delenv("AUTOLAB_AI_TRUSTED_GATEWAY_CIDRS")
    assert client(("10.202.0.9", 50000)).get(prefix + "/probe", headers=HEADERS).status_code == 503
    monkeypatch.setenv("AUTOLAB_AI_TRUSTED_GATEWAY_CIDRS", "10.202.0.0/24")
    assert client(("10.202.0.9", 0)).get(prefix + "/probe", headers=HEADERS).status_code == 503


@pytest.mark.parametrize("prefix", PREFIXES)
def test_trusted_peer_still_requires_unique_identity_and_original_admin_role(prefix):
    c = client(("10.202.0.9", 50000))
    assert c.get(prefix + "/probe", headers=HEADERS).status_code == 200
    assert c.get(prefix + "/probe").status_code == 403
    assert client(("10.202.0.9", 50000), duplicate_identity=True).get(
        prefix + "/probe", headers=HEADERS).status_code == 403
    user = json.loads(unquote_plus(HEADERS['SDUOJUserInfo']))
    user['roles'] = ['user']
    assert c.get(prefix + "/probe", headers={**HEADERS,
        'SDUOJUserInfo': quote_plus(json.dumps(user))}).status_code == 403


def test_ordinary_and_prefix_neighbour_routes_keep_existing_boundary():
    c = client(("10.203.0.9", 50000))
    for path in ("/ordinary/probe", "/management-agents/probe"):
        assert c.get(path, headers=HEADERS).status_code == 200
