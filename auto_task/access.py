"""Trusted Gateway boundary and execution-time OJ authorization."""

import ipaddress
import json
import os

import requests
from fastapi import HTTPException

from .resources import get_resource, ps_access
from .store import LeaseLost, principal, transaction


def gateway_allowed(peer, mode, peer_port=None):
    if mode not in ("debug", "release"):
        raise HTTPException(503, "Invalid SDUOJ runtime mode")
    # Uvicorn uses port 0 after replacing the TCP peer with X-Forwarded-For.
    # At that point this process can no longer prove which proxy connected.
    if peer_port == 0:
        if mode == "release":
            raise HTTPException(503, "Proxy header rewriting hides the Gateway peer")
        # Development Uvicorn only rewrites this field for its own trusted
        # proxy peers. Production must retain and check the direct TCP peer.
        return True

    raw = os.environ.get("AUTOLAB_AI_TRUSTED_GATEWAY_CIDRS", "")
    if not raw:
        if mode == "release":
            raise HTTPException(503, "Configure trusted Gateway networks for AI routes")
        try:
            return ipaddress.ip_address(peer).is_loopback
        except ValueError as error:
            raise HTTPException(503, "Invalid Gateway peer address") from error
    try:
        items = [item.strip() for item in raw.split(",")]
        if not items or any(not item for item in items):
            raise ValueError("Empty trusted network")
        networks = [ipaddress.ip_network(item) for item in items]
        if any(network.prefixlen == 0 for network in networks):
            raise ValueError("Unrestricted proxy range")
        address = ipaddress.ip_address(peer)
    except ValueError as error:
        raise HTTPException(503, "Invalid trusted Gateway network configuration") from error
    return any(address in network for network in networks)


def fetch_actor(subject, fresh=False):
    from auth import validate_user_info
    from sduojApi import get_service_address, requestHeaders
    address = get_service_address("user-service")
    path = "/internal/user/runSubject" if fresh else "/internal/user/userIdToUserSessionDTO"
    response = requests.get("http://" + address + path,
                            params={"userId": subject}, headers=requestHeaders,
                            timeout=(5, 15), allow_redirects=False)
    if response.status_code != 200:
        response.raise_for_status()
        raise requests.HTTPError("Unexpected identity response status", response=response)
    actor = response.json()
    # Java's internal HTTP serializer writes Long IDs as decimal strings.
    # Normalize only this trusted RPC response; Gateway header claims remain strict.
    def java_id(value):
        if isinstance(value, str):
            if (not value.isascii() or not value.isdecimal() or value.startswith("0")
                    or len(value) > 19):
                raise ValueError("Invalid internal ID")
            return int(value)
        return value

    if isinstance(actor, dict):
        actor = {**actor, "userId": java_id(actor.get("userId"))}
        if isinstance(actor.get("groups"), list):
            actor["groups"] = [java_id(value) for value in actor["groups"]]
    return validate_user_info(actor)


def authorize_execution(job):
    from const import MOD
    if job.get("task_type") == "management.turn":
        # Management never uses the development identity bypass.
        actor = fetch_actor(job["owner_id"], fresh=True)
        try:
            from .management_agent import owned
            if principal(actor) != job["owner_id"] or actor["username"] != job["owner_username"]:
                raise LeaseLost("OJ task identity changed")
            payload = json.loads(job.get("payload") or "{}")
            if payload.get("session_id") != job.get("resource_id"):
                raise LeaseLost("Management session binding changed")
            with transaction() as s:
                row = owned(s, actor, job["resource_id"])
                if json.loads(row.data).get("task_id") != job["id"]:
                    raise LeaseLost("Management task superseded")
        except HTTPException as error:
            raise LeaseLost("Management authorization revoked") from error
        return
    teaching_ide = job.get("task_type", "").startswith("ide.")
    if MOD == "debug" and os.environ.get("AUTOLAB_AI_VALIDATE_IDENTITY") == "0" and not teaching_ide:
        return
    actor = fetch_actor(job["owner_id"])
    if principal(actor) != job["owner_id"] or actor["username"] != job["owner_username"]:
        raise LeaseLost("OJ task identity changed")
    try:
        with transaction() as s:
            if job.get("task_type", "").startswith("ide."):
                from .run_context import authorize_teaching_ide
                from utilsTime import getNowTime
                actor = authorize_teaching_ide(s, job["owner_id"], getNowTime())
                if actor["username"] != job["owner_username"]:
                    raise LeaseLost("OJ task identity changed")
            if job.get("resource_id"):
                row = get_resource(s, actor, job["resource_id"])
                if row.kind != "interview":
                    payload = json.loads(job.get("payload") or "{}")
                    if job.get("task_type") == "question.extract" and payload.get("collaboration_grant"):
                        from .native_bank import extraction_grant
                        extraction_grant(s, actor, row, payload)
                    else:
                        get_resource(s, actor, row.id, True)
                    if job.get("task_type") == "question.extract":
                        from .native_bank import extraction_author
                        from types import SimpleNamespace
                        extraction_author(s, SimpleNamespace(**job), row, payload)
            elif job.get("psid"):
                ps_access(s, actor, job["psid"], True)
    except HTTPException as error:
        raise LeaseLost("OJ task authorization revoked") from error
