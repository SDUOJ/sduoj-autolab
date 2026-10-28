"""Real local OJ integration fixture. Refuses non-loopback configuration."""

import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymysql
import requests
from sqlalchemy.engine.url import make_url
from urllib.parse import urlsplit
from const import MOD
from db import link

ROOT = Path(__file__).resolve().parents[2]
STATE = Path(os.environ.get("AUTOLAB_NATIVE_TEST_STATE_DIR", str(ROOT / "sduoj-deploy/data/dev/ai-integration")))
BASE = os.environ.get("AUTOLAB_NATIVE_TEST_API", "http://127.0.0.1:8080/api").rstrip("/")
endpoint = urlsplit(BASE)
if (endpoint.scheme != "http" or endpoint.hostname not in ("127.0.0.1", "localhost")
        or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or endpoint.path != "/api"):
    raise RuntimeError("Native integration requires an explicit loopback /api endpoint")


def provision():
    assert MOD == "debug" and make_url(link).host in ("127.0.0.1", "localhost")
    database_url = os.environ.get("AUTOLAB_NATIVE_TEST_OJ_DATABASE_URL")
    if not database_url:
        raise RuntimeError("Set AUTOLAB_NATIVE_TEST_OJ_DATABASE_URL to a task-owned local fixture database")
    target = make_url(database_url)
    if (target.host not in ("127.0.0.1", "localhost") or not target.database
            or not target.database.endswith(("_integration", "_test"))):
        raise RuntimeError("Native integration may only provision a local _integration/_test fixture database")
    connection = pymysql.connect(host=target.host, port=target.port or 3306,
                                 user=target.username, password=target.password, database=target.database)
    STATE.mkdir(parents=True, exist_ok=True)
    credentials = STATE / "test-user.json"
    if credentials.exists():
        user = json.loads(credentials.read_text())
    else:
        user = {"username": "ai_dev_" + secrets.token_hex(3), "password": secrets.token_urlsafe(18)}
    salt = secrets.token_hex(16)
    password_hash = hashlib.md5((salt + hashlib.md5(user["password"].encode()).hexdigest()).encode()).hexdigest()
    with connection.cursor() as cursor:
        cursor.execute("SELECT u_id FROM oj_user WHERE u_username=%s", (user["username"],))
        found = cursor.fetchone()
        if not found:
            cursor.execute("INSERT INTO oj_user (u_username,u_nickname,u_salt,u_password,u_email,u_roles) VALUES (%s,%s,%s,%s,%s,%s)",
                           (user["username"], "AI DEV ONLY", salt, password_hash, user["username"] + "@example.invalid", "superadmin"))
            user["userId"] = cursor.lastrowid
        else:
            user["userId"] = found[0]
    connection.commit()
    connection.close()
    fd = os.open(credentials, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(user, file)
    return user


def run():
    user = provision()
    client = requests.Session()
    client.trust_env = False

    def call(path, body=None):
        response = client.get(BASE + path, timeout=20) if body is None else client.post(BASE + path, json=body, timeout=30)
        if not response.ok:
            raise RuntimeError(path + " HTTP " + str(response.status_code) + " " + response.text[:1500])
        envelope = response.json()
        if envelope.get("code") != 0:
            raise RuntimeError(path + " failed: " + str(envelope.get("message")))
        return envelope.get("data")

    call("/user/login", {"username": user["username"], "password": user["password"]})
    prefix = "AI-DEV-" + uuid.uuid4().hex[:8]
    report = {"environment": "local-dev", "checks": [], "groups": {}}
    ai = "/ps/auto-task/ai"
    for kind, group_type in [("objective", 0), ("subjective", 1), ("programming", 2)]:
        call("/ps/group/add", {"name": prefix + "-" + kind, "type": group_type, "problemInfo": [], "manageGroupId": None})
        groups = call("/ps/group/list", {"pageNow": 1, "pageSize": 20, "searchKey": prefix + "-" + kind})
        gid = next(row["gid"] for row in groups["rows"] if row["name"] == prefix + "-" + kind)
        report["groups"][kind] = gid
    objective = call("/ps/objective/add", {"type": 0, "content": {"description": "2+2?", "choice": ["4", "5"]}, "answer": ["A"]})
    subjective = call("/ps/subjective/add", {"type": 1, "description": "Explain 2+2.",
                                          "config": {"maxCount": 1000, "judgeConfig": [{"name": "Correctness", "score": 10, "answer": "4"}]}})
    for kind, pid in [("objective", objective), ("subjective", subjective)]:
        gid = report["groups"][kind]
        pid = pid["pid"]
        call("/ps/group/edit", {"gid": gid, "name": prefix + "-" + kind, "problemInfo": [{"pid": pid, "score": 10, "submitLimit": -1}], "type": 0 if kind == "objective" else 1, "manageGroupId": None})
        imported = call(ai + "/native/import", {"group_id": gid, "pid": pid})
        replay = call(ai + "/native/import", {"group_id": gid, "pid": pid})
        assert imported["id"] == replay["id"] and replay["replay"]
        assert imported["data"]["native"]["type"] == kind
        report["checks"].append(kind + " native create/import/replay")
        publication_body = {"version": imported["version"], "group_id": gid, "score": 10,
                            "correct_labels": ["A"] if kind == "objective" else [], "dry_run": False}
        published = call(ai + "/questions/" + imported["id"] + "/publish", publication_body)
        assert call(ai + "/questions/" + imported["id"] + "/publish", publication_body)["replay"]
        info = call("/ps/" + kind + "/info", {"gid": gid, "pid": published["pid"]})
        assert info["type"] == (0 if kind == "objective" else 1)
        history = call(ai + "/resources/" + imported["id"] + "/history")
        assert history["totalNum"] >= 2
        report["checks"].append(kind + " AI to native publish/replay/readback/history")
    # Programming statements enter through the original native OJ flow. The
    # question resource creation endpoint intentionally rejects programming.
    title = prefix + " A+B"
    statement = "# A+B\nRead a and b, print their sum."
    code = call("/manage/problem/create", {"problemTitle": title, "isPublic": 0,
                "source": prefix, "timeLimit": 1000, "memoryLimit": 262144,
                "outputLimit": 65536, "managerGroups": [], "judgeTemplates": []})
    native = call("/manage/problem/query?problemCode=" + code)
    call("/manage/problem/updateDescription", {"title": "Native fixture", "markdownDescription": statement,
         "isPublic": 0, "id": native["defaultDescriptionId"], "problemCode": code})
    pid = native["problemId"]
    gid = report["groups"]["programming"]
    call("/ps/group/edit", {"gid": gid, "name": prefix + "-programming", "type": 2,
         "problemInfo": [{"pid": int(pid), "desId": int(native["defaultDescriptionId"]),
                          "name": title, "score": 100, "submitLimit": -1}], "manageGroupId": None})
    imported = call(ai + "/native/import", {"group_id": gid, "pid": int(pid)})
    assert call(ai + "/native/import", {"group_id": gid, "pid": int(pid)})["replay"]
    assert imported["data"]["native"]["problem_code"] == code
    report["checks"].append("program original native create/description/group/import/replay")
    # Use only the existing local A+B fixture's native checkpoint/template references.
    fixture_code = os.environ.get("AUTOLAB_NATIVE_TEST_PROBLEM_CODE", "SDUOJ-1000")
    fixture = call("/manage/problem/query?problemCode=" + fixture_code)
    templates = fixture["judgeTemplates"]
    if not templates:
        raise RuntimeError("Native fixture has no judging template")
    template_id = str(templates[0])
    call("/manage/problem/update", {"problemCode": code, "checkpoints": fixture["checkpoints"],
                                    "judgeTemplates": [template_id], "checkerConfig": fixture["checkerConfig"]})
    verified = call("/manage/problem/query?problemCode=" + code)
    assert verified["checkpointNum"] == len(fixture["checkpoints"])
    submission = call("/submit/create", {"problemCode": code, "judgeTemplateId": template_id,
                        "code": "#include <iostream>\nint main(){long long a,b;std::cin>>a>>b;std::cout<<a+b<<'\\n';}"})
    judged = None
    for _ in range(30):
        judged = call("/submit/query?submissionId=" + submission)
        status = judged.get("judgeResult", judged.get("status"))
        if judged.get("judgeScore") == 100:
            break
        time.sleep(2)
    report.update(program_code=code, program_id=pid, resource_id=imported["id"], status="PASSED",
                  submission_id=submission, judge={key: judged.get(key) for key in ("judgeResult", "judgeScore", "status", "score")},
                  checkpoint_count=verified["checkpointNum"],
                  note="Native three-type bank roundtrip; A+B uses the explicitly selected local fixture checkpoints and native template.")
    assert judged.get("judgeScore", judged.get("score")) == 100, report["judge"]
    report["checks"].append("new native programming problem judged Accepted/100 using local OJ")
    (STATE / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run()
