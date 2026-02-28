from datetime import datetime
from functools import wraps
from inspect import isawaitable
from types import SimpleNamespace
from typing import Any, Optional

from starlette.responses import JSONResponse
from starlette.responses import Response

from utilsTime import getMsTime


class Result:
    """统一返回结果类"""
    def __init__(self, code: int, msg: str, data=None):
        self.code = code
        self.msg = msg
        self.data = data


def removeNone(d: dict):
    ls = []
    for x in d:
        if d[x] is None:
            ls.append(x)
    for x in ls:
        d.pop(x)


def makeResponse(data):
    response = JSONResponse({
        "code": 0,
        "message": "OK",
        "data": data,
        "timestamp": getMsTime(datetime.now())
    }, status_code=200)
    return response


def _try_make_page_result(data: Any, rows_key: Optional[str] = None):
    """
    统一分页结构：
    1) 已是 pageIndex/pageSize/totalNum/rows，原样返回
    2) 兼容 total/page_now/page_size/courses|schedules|records 等，补齐 pageResult 字段
    3) 保留原字段，避免影响旧前端
    """
    if not isinstance(data, dict):
        return data

    # 已经是标准分页结构
    if {"pageIndex", "pageSize", "totalNum", "rows"}.issubset(data.keys()):
        return data

    page_now = data.get("page_now", data.get("pageNow", data.get("pageIndex")))
    page_size = data.get("page_size", data.get("pageSize"))
    total = data.get("total", data.get("totalNum"))

    resolved_rows_key = rows_key
    if resolved_rows_key is None:
        for key in ("rows", "courses", "schedules", "records", "items", "list"):
            if key in data and isinstance(data.get(key), list):
                resolved_rows_key = key
                break

    rows = data.get(resolved_rows_key) if resolved_rows_key else None
    if page_now is None or page_size is None or total is None or rows is None:
        return data

    from ser.base import makePageResult

    page_data = makePageResult(
        SimpleNamespace(pageNow=int(page_now), pageSize=int(page_size)),
        int(total),
        rows
    )

    merged = dict(data)
    merged.update(page_data)
    return merged


def api_response(paged: bool = False, rows_key: Optional[str] = None):
    """
    路由统一返回修饰器：
    - 自动包装 makeResponse
    - paged=True 时自动补齐 makePageResult 结构（同时保留原字段）
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            if isawaitable(result):
                result = await result

            if isinstance(result, Response):
                return result

            payload = _try_make_page_result(result, rows_key=rows_key) if paged else result
            return makeResponse(payload)

        return wrapper

    return decorator


def get_next(seed):
    return (25214903917 * seed + 11) % ((2 ** 31) - 1)


def get_random_list_by_str(n, s):
    import hashlib

    ha = hashlib.new("sha256")
    ha.update(s.encode("utf8"))
    seed = int(ha.hexdigest(), 16) % 998244353

    ls, ls2 = [], []
    for i in range(n):
        ls.append(i)
        ls2.append(i)

    for i in range(1, n):
        seed = get_next(seed)
        l = seed % (i + 1)
        ls[l], ls[i] = ls[i], ls[l]

    tp = {}
    for i in range(n):
        tp[str(i)] = ls[i]

    for x in tp:
        ls2[tp[x]] = int(x)

    return ls, ls2


def change_order(ls, order):
    res = []
    for i in range(len(order)):
        res.append(None)
    for i in range(len(order)):
        res[order[i]] = ls[i]
    return res


def change_choice_order(choice_list, order):
    if choice_list is None:
        return None
    res = []
    for x in choice_list:
        res.append(chr(order[ord(x) - 65] + 65))
    return res


def get_group_hash_name(psid, gi, username):
    return str(psid) + "-" + str(gi) + "-" + username + "-Group"


def get_pro_hash_name(psid, gi, pi, username):
    return str(psid) + "-" + str(gi) + "-" + str(pi) + "-" + username + "-Pro"


async def deal_order_change(db, data, with_pro=False):
    o1, o2, tp = await db.get_group_random_list_cache(
        data.router, data.username)
    if tp == 0:
        data.router.pid = o2[data.router.pid]
        if with_pro:
            o1, o2 = await db.get_pro_random_list_cache(
                data.router, data.username)
            data.data = chr(o2[ord(data.data) - 65] + 65)


def init_redis():
    from const import Redis_pass
    from const import Redis_addr
    from fastapi_cache import FastAPICache
    from fastapi_cache.backends.redis import RedisBackend
    import aioredis
    redis = aioredis.from_url(
        "redis://{}/0".format(Redis_addr),
        password=Redis_pass,
        encoding="utf8",
        decode_responses=True)
    FastAPICache.init(RedisBackend(redis), prefix="fastapi-cache")
