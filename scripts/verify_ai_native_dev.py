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
from dotenv import dotenv_values
from sqlalchemy.engine.url import make_url
from const import MOD
from db import link

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "sduoj-deploy/data/dev/ai-integration"
BASE = "http://127.0.0.1:8080/api"


def provision():
    assert MOD == "debug" and make_url(link).host in ("127.0.0.1", "localhost")
    env = dotenv_values(ROOT / "sduoj-deploy/env/mysql/mysql.env")
    connection = pymysql.connect(host="127.0.0.1", port=3306, user=env["MYSQL_USER"],
                                 password=env["MYSQL_PASSWORD"], database=env["MYSQL_DATABASE"])
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
    resource = call(ai + "/resources/create", {"kind": "question", "data": {"question_type": "programming", "zh": "# A+B\nRead a and b, print their sum.", "original": "# A+B\nRead a and b, print their sum.", "options": [], "tags": []}})
    plan = call(ai + "/native/program/" + resource["id"] + "/plan", {"version": resource["version"], "title": prefix + " A+B"})
    code = call("/manage/problem/create", plan["problem"])
    native = call("/manage/problem/query?problemCode=" + code)
    call("/manage/problem/updateDescription", {**plan["description"], "id": native["defaultDescriptionId"], "problemCode": code})
    bind = {"version": resource["version"], "group_id": report["groups"]["programming"], "problem_code": code, "score": 100}
    bound = call(ai + "/native/program/" + resource["id"] + "/bind", bind)
    assert call(ai + "/native/program/" + resource["id"] + "/bind", bind)["replay"]
    imported = call(ai + "/native/import", {"group_id": bind["group_id"], "pid": int(bound["pid"])})
    assert imported["data"]["native"]["problem_code"] == code
    report["checks"].append("program native create/description/bind/import/replay")
    # Use only the existing local A+B fixture's native checkpoint/template references.
    fixture = call("/manage/problem/query?problemCode=SDUOJ-1000")
    call("/manage/problem/update", {"problemCode": code, "checkpoints": fixture["checkpoints"],
                                    "judgeTemplates": ["6"], "checkerConfig": fixture["checkerConfig"]})
    verified = call("/manage/problem/query?problemCode=" + code)
    assert verified["checkpointNum"] == len(fixture["checkpoints"])
    submission = call("/submit/create", {"problemCode": code, "judgeTemplateId": "6",
                        "code": "#include <iostream>\nint main(){long long a,b;std::cin>>a>>b;std::cout<<a+b<<'\\n';}"})
    judged = None
    for _ in range(30):
        judged = call("/submit/query?submissionId=" + submission)
        status = judged.get("judgeResult", judged.get("status"))
        if judged.get("judgeScore") == 100:
            break
        time.sleep(2)
    report.update(program_code=code, program_id=bound["pid"], resource_id=resource["id"], status="PASSED",
                  submission_id=submission, judge={key: judged.get(key) for key in ("judgeResult", "judgeScore", "status", "score")},
                  checkpoint_count=verified["checkpointNum"],
                  note="Native three-type bank roundtrip; A+B uses existing local fixture checkpoints and C++14 template.")
    assert judged.get("judgeScore", judged.get("score")) == 100, report["judge"]
    report["checks"].append("new native programming problem judged Accepted/100 using local OJ")
    (STATE / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run()
