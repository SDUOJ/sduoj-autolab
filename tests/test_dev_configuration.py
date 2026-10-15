import asyncio
import importlib

import pytest


@pytest.fixture
def dev_api(monkeypatch):
    monkeypatch.setenv("SDUOJ_MODE", "debug")
    monkeypatch.setenv("SDUOJ_TOKEN", "test-local-token")
    monkeypatch.setenv("AUTOLAB_MYSQL_PASSWORD", "test-local-db")
    monkeypatch.setenv("AUTOLAB_REDIS_PASSWORD", "test-local-cache")
    import const
    import sduojApi

    importlib.reload(const)
    importlib.reload(sduojApi)
    return sduojApi


def test_debug_uses_only_local_infrastructure(dev_api):
    import const

    assert const.Mysql_addr == "127.0.0.1:7360"
    assert const.Redis_addr == "127.0.0.1:7361"
    assert const.NACOS_addr == "127.0.0.1:8848"
    assert const.NACOS_namespace == "dev"
    assert const.PUBLIC_OJ_BASE_URL == "http://api.test.sduoj.com:8080"
    assert const.LLM_DOUBAO_API_KEY == ""
    assert const.LLM_DOUBAO_BASE_URL == ""


def test_debug_discovery_does_not_contact_nacos(dev_api, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Debug service resolution must not issue a network request")

    monkeypatch.setattr(dev_api.requests, "get", unexpected)
    assert asyncio.run(dev_api.getService_ip_port("filesys-service")) == "127.0.0.1:18084"
    with pytest.raises(RuntimeError, match="No local development endpoint"):
        asyncio.run(dev_api.getService_ip_port("unconfigured-service"))


@pytest.mark.parametrize("file_id", ["123", "https://oj.qd.sdu.edu.cn/api/filesys/download/123/test.txt", "oj-file://123"])
def test_missing_local_files_never_fall_back_to_production(dev_api, monkeypatch, file_id):
    calls = []

    def unavailable(url, **kwargs):
        calls.append(url)
        raise ConnectionError("Local filesys unavailable")

    monkeypatch.setattr(dev_api.requests, "get", unavailable)
    with pytest.raises(RuntimeError, match="local development snapshot"):
        asyncio.run(dev_api.downloadFile(file_id))
    assert calls == ["http://127.0.0.1:18084/internal/filesys/download"]


def test_release_keeps_nacos_discovery(dev_api, monkeypatch):
    monkeypatch.setattr(dev_api, "MOD", "release")

    class Response:
        def json(self):
            return {"hosts": [{"ip": "192.0.2.10", "port": 8080}]}

    monkeypatch.setattr(dev_api.requests, "get", lambda *args, **kwargs: Response())
    assert asyncio.run(dev_api.getService_ip_port("filesys-service")) == "192.0.2.10:8080"
