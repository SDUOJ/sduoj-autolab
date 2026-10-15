"""Authoritative native timed-problem-set policy for the independent OJ Run service."""

import json
from datetime import datetime

from fastapi import HTTPException
from db import ProblemGroup, ProblemSet


def _milliseconds(value):
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    if isinstance(value, bool):
        raise ValueError("Invalid time")
    return int(value)


def _active(start, end, now):
    start, end = _milliseconds(start), _milliseconds(end)
    if end < start:
        raise ValueError("Invalid time interval")
    # Native answer-sheet time policy includes the end boundary.
    return start <= now <= end


def check_native_context(session, actor, problem_id, now):
    """No writes, cached visibility, caller-provided roles, or answer-sheet creation."""
    groups = actor.get("groups")
    if not isinstance(groups, list) or not actor.get("username"):
        raise HTTPException(503, "Run subject is unavailable")
    if not groups:
        return {"allowed": True, "reason": None}
    candidates = session.query(ProblemSet).filter(ProblemSet.groupId.in_(groups)).all()
    for ps in candidates:
        # A native author/manager can inspect their own assignment; unrelated admin roles do not bypass it.
        if ps.username == actor["username"] or ps.manageGroupId in groups:
            continue
        try:
            config, items = json.loads(ps.config), json.loads(ps.groupInfo)
            if not isinstance(config, dict) or not isinstance(items, list):
                raise ValueError("Invalid problem set")
            for item in items:
                group = session.query(ProblemGroup).filter_by(gid=int(item["gid"])).one_or_none()
                if group is None:
                    raise ValueError("Missing native problem group")
                if group.type != 2:
                    continue
                problems = json.loads(group.problemInfo)
                if not isinstance(problems, list):
                    raise ValueError("Invalid native programming group")
                if not any(problem_id is None or int(problem["pid"]) == problem_id for problem in problems):
                    continue
                if config.get("useSameSE") == 1:
                    active = _active(ps.tm_start, ps.tm_end, now)
                elif config.get("useSameSE") == 0:
                    settings = item["timeSetting"]
                    if not isinstance(settings, list) or not settings:
                        raise ValueError("Missing native time settings")
                    active = any(_active(setting["tm_start"], setting["tm_end"], now)
                                 for setting in settings)
                else:
                    raise ValueError("Missing native time mode")
                # Finishing an answer sheet does not release the restriction before its timed window ends.
                if active:
                    return {"allowed": False, "reason": "ACTIVE_EXAM" if ps.type == 1 else "ACTIVE_TIMED_PROBLEM_SET"}
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise HTTPException(503, "Native run context is unavailable") from error
    return {"allowed": True, "reason": None}


def authorize_teaching_ide(session, subject, now):
    """Teaching IDE accepts arbitrary code, so active programming windows apply without a psid."""
    from .access import fetch_actor
    try:
        actor = fetch_actor(str(subject), fresh=True)
        if str(actor["userId"]) != str(subject):
            raise ValueError("Run subject mismatch")
        if not set(actor["roles"]).intersection({"user", "admin", "superadmin"}):
            raise HTTPException(403, "Teaching IDE permission revoked")
        if not check_native_context(session, actor, None, now)["allowed"]:
            raise HTTPException(403, "Teaching IDE disabled during a timed programming assignment")
        return actor
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(503, "Teaching IDE run context unavailable") from error
