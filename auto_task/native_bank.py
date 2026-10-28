"""Native OJ questions are authoritative; batch records contain only references."""
import json
import re
from fastapi import HTTPException
from db import ProblemGroup, ProblemObjective, ProblemSubjective
from .entities import AiResource
from .store import digest, dumps

CHOICE_TYPES = {"single_choice", "multiple_choice", "true_false"}


def extraction_target(session, actor, resource, payload):
    """Private extraction stays private; native persistence requires its owner manager."""
    from .access import fetch_actor
    from .resources import is_admin, course_access
    from .store import principal
    owner = actor if resource.owner_id == principal(actor) else fetch_actor(resource.owner_id, fresh=True)
    if principal(owner) != resource.owner_id or owner["username"] != resource.owner_username:
        raise HTTPException(403, "Native question owner identity changed")
    if not is_admin(owner):
        return None
    from auth import manager
    manager(owner)
    if resource.course_id:
        course_access(session, owner, resource.course_id, True)
    if principal(actor) != resource.owner_id:
        extraction_grant(session, actor, resource, payload)
    return resource.owner_id


def extraction_grant(session, actor, resource, payload):
    from .store import principal
    from .collection_access import assert_worker_grant
    grant = payload.get("collaboration_grant")
    if (resource.kind != "collection" or not isinstance(grant, dict)
            or grant.get("collection_id") != resource.id
            or str(grant.get("subject_id")) != principal(actor)):
        raise HTTPException(403, "A matching collection intake grant is required")
    try:
        assert_worker_grant(session, resource.id, principal(actor), grant.get("assignment_id"))
    except ValueError as error:
        raise HTTPException(403, str(error)) from error


def extraction_author(session, job, resource, payload):
    target = payload.get("native_bank_owner")
    if target is None:
        return None
    if target != resource.owner_id:
        raise HTTPException(403, "Native extraction target changed")
    from .access import fetch_actor
    from .store import principal
    from .resources import course_access
    from auth import manager
    owner = fetch_actor(target, fresh=True)
    if principal(owner) != target or owner["username"] != resource.owner_username:
        raise HTTPException(403, "Native question owner identity changed")
    manager(owner)
    if resource.course_id:
        course_access(session, owner, resource.course_id, True)
    if job.owner_id != target:
        actor = fetch_actor(job.owner_id, fresh=True)
        if principal(actor) != job.owner_id or actor["username"] != job.owner_username:
            raise HTTPException(403, "Intake contributor identity changed")
        from .resources import get_resource
        get_resource(session, actor, resource.id)
        extraction_grant(session, actor, resource, payload)
    return owner


def answer_labels(data):
    options = data.get("options", [])
    labels = [item["label"] for item in options]
    value = str(data.get("answer", "")).strip()
    if not value:
        return []
    if value in labels:
        return [chr(65 + labels.index(value))]
    for index, option in enumerate(options):
        if value == option.get("zh"):
            return [chr(65 + index)]
    parts = re.split(r"[,，、;；\s]+", value.strip("[]\"'"))
    if len(parts) == 1 and value.isascii() and value.isalpha():
        parts = list(value.upper())
    if not parts or any(label not in labels for label in parts):
        raise HTTPException(422, "选择题答案须为选项标号，例如 A 或 A,B")
    if data["question_type"] in {"single_choice", "true_false"} and len(parts) != 1:
        raise HTTPException(422, "单选题只能有一个答案")
    return [chr(65 + labels.index(label)) for label in dict.fromkeys(parts)]


def normalize(data):
    value = dict(data)
    if value.get("question_type") == "programming":
        raise HTTPException(422, "编程题请使用现有编程题命题流程")
    if value.get("question_type") == "true_false" and not value.get("options"):
        value["options"] = [{"label": "A", "zh": "正确"}, {"label": "B", "zh": "错误"}]
        raw = str(value.get("answer", "")).lower().strip()
        value["answer"] = {"true": "A", "false": "B", "正确": "A", "错误": "B", "对": "A", "错": "B"}.get(raw, raw.upper())
    if not str(value.get("zh") or value.get("original") or "").strip():
        raise HTTPException(422, "题目内容不能为空")
    if value.get("question_type") in CHOICE_TYPES:
        options = value.get("options", [])
        labels = [item.get("label") for item in options]
        if not 2 <= len(options) <= 26 or len(labels) != len(set(labels)):
            raise HTTPException(422, "选择题需要 2–26 个不同标号的选项")
    return value


def write_question(session, data, username, score=10, existing=None):
    data = normalize(data)
    choice = data.get("question_type") in CHOICE_TYPES
    description = data.get("zh") or data.get("original")
    if choice:
        item = existing or ProblemObjective(username=username)
        if not isinstance(item, ProblemObjective):
            raise HTTPException(409, "原生题目类型不可跨题组更改")
        item.type = 1 if data["question_type"] == "multiple_choice" else 0
        item.content = dumps({"description": description, "choice": [o.get("zh") or o.get("original", "") for o in data["options"]]})
        item.answer = dumps(answer_labels(data))
        if len(item.answer) > 63:
            raise HTTPException(422, "正确选项数量超过原生题目存储限制")
    else:
        item = existing or ProblemSubjective(username=username, type=1)
        if not isinstance(item, ProblemSubjective):
            raise HTTPException(409, "原生题目类型不可跨题组更改")
        config = json.loads(item.config) if existing else {"maxCount": 10000, "judgeConfig": [{"name": "参考答案", "score": score, "answer": ""}]}
        if len(config.get("judgeConfig", [])) > 1:
            # A generated answer must not flatten an existing multi-item rubric.
            raise HTTPException(409, "请在原题组中编辑多评分项题目的参考答案")
        config.setdefault("judgeConfig", [{"name": "参考答案", "score": score}])
        if not config["judgeConfig"]:
            config["judgeConfig"] = [{"name": "参考答案", "score": score}]
        config["judgeConfig"][0]["answer"] = data.get("answer", "")
        item.config, item.description = dumps(config), description
    if not existing:
        session.add(item)
    session.flush()
    return item, 0 if choice else 1


def read_native(session, binding, lock=False):
    model = {"objective": ProblemObjective, "subjective": ProblemSubjective}.get(binding.get("type"))
    if model is None:
        return None
    query = session.query(model).filter_by(pid=int(binding["pid"]))
    item = (query.with_for_update() if lock else query).first()
    if not item:
        raise HTTPException(409, "原生题目已不存在")
    if isinstance(item, ProblemObjective):
        content = json.loads(item.content)
        result = {"question_type": "single_choice" if item.type == 0 else "multiple_choice",
                  "zh": content["description"], "original": content["description"],
                  "options": [{"label": chr(65+i), "original": text, "zh": text} for i, text in enumerate(content["choice"])],
                  "answer": ",".join(json.loads(item.answer))}
        signature = digest([item.type, item.content, item.answer])
    else:
        config = json.loads(item.config)
        result = {"question_type": "short_answer", "zh": item.description, "original": item.description,
                  "options": [], "answer": "\n".join(str(x.get("answer", "")) for x in config.get("judgeConfig", []))}
        signature = digest([item.type, item.description, item.config])
    return item, result, signature


def refresh_reference(session, resource):
    data = json.loads(resource.data)
    source = read_native(session, data.get("native", {}))
    if source:
        _, fields, signature = source
        if data.get("native_signature") != signature:
            data.update(fields, native_signature=signature)
            resource.data = dumps(data)
            resource.version += 1
    return resource


def update_reference(session, resource, data):
    source = read_native(session, data.get("native", {}), lock=True)
    if not source:
        return data
    item, fields, signature = source
    old = json.loads(resource.data)
    if old.get("native_signature") != signature:
        raise HTTPException(409, "题目已在原生题组中修改，请刷新后重试")
    # Metadata-only edits must preserve multi-item rubrics and native subtypes.
    if any(data.get(key, fields[key]) != fields[key] for key in ("zh", "answer", "options", "question_type")):
        write_question(session, data, resource.owner_username, existing=item)
    data["native_signature"] = read_native(session, data["native"])[2]
    return data


def save_batch(session, user, title, batch_key, questions, score=10, resources=None):
    from auth import manager
    manager(user)
    """One transaction, one group per native type per intake batch; retry is idempotent."""
    key = digest({"owner": str(user["userId"]), "batch": batch_key})
    record_id = "batch-" + key[:32]
    request_hash = digest({"title": title, "questions": questions, "score": score})
    record = session.query(AiResource).filter_by(id=record_id).with_for_update().first()
    if record:
        stored = json.loads(record.data)
        if stored["request_hash"] != request_hash:
            raise HTTPException(409, "同一批次请求内容不一致")
        return {**stored, "replay": True}
    groups, links = {}, []
    for index, value in enumerate(questions):
        value = normalize(value)
        kind = 0 if value["question_type"] in CHOICE_TYPES else 1
        if kind not in groups:
            suffix = " · 客观题" if kind == 0 else " · 主观题"
            group = ProblemGroup(name=title[:63-len(suffix)] + suffix, type=kind, problemInfo="[]", username=user["username"])
            session.add(group)
            session.flush()
            groups[kind] = group
        group = groups[kind]
        reference = resources[index] if resources else None
        binding = json.loads(reference.data).get("native", {}) if reference else {}
        existing = read_native(session, binding)
        if existing and (binding.get("type") == "objective") == (kind == 0):
            item = existing[0]
        else:
            item, _ = write_question(session, value, user["username"], score)
        entries = json.loads(group.problemInfo)
        if not any(entry["pid"] == item.pid for entry in entries):
            entries.append({"pid": item.pid, "score": score, "submitLimit": -1, "antiCheatingRate": 0.85})
        group.problemInfo = dumps(entries)
        native = {"type": "objective" if kind == 0 else "subjective", "pid": str(item.pid), "group_id": group.gid}
        if reference and not binding:
            metadata = json.loads(reference.data)
            metadata.update(native=native, native_signature=read_native(session, native)[2])
            reference.data = dumps(metadata)
        links.append({**native, "number": index + 1})
    result = {"request_hash": request_hash, "title": title, "batch_key": batch_key,
              "groups": [{"gid": g.gid, "name": g.name, "type": g.type, "count": len(json.loads(g.problemInfo))} for g in groups.values()],
              "questions": links}
    session.add(AiResource(id=record_id, kind="native_batch", owner_id=str(user["userId"]), owner_username=user["username"], data=dumps(result), state="ready"))
    session.flush()
    return {**result, "replay": False}
