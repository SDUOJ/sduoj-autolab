from datetime import datetime

from starlette.responses import JSONResponse

from utilsTime import getMsTime


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
