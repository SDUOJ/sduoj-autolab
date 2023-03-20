from datetime import datetime

from pydantic import BaseModel


class timeSettingType(BaseModel):
    tm_start: int
    tm_end: int
    weight: float


def getNowTime():
    import time
    return round(time.time() * 1000)


def inTimeSetting(tm: int, ts):
    if type(ts) == dict:
        return ts["tm_start"] <= tm <= ts["tm_end"]
    return ts.tm_start <= tm <= ts.tm_end


def inTime(tm: int, st: int, ed: int):
    return st <= tm <= ed


def afterTime(st: int, ed: int):
    return st <= ed < getNowTime()


def inGroupInfoItemTime(tm, groupInfoItem):
    for y in groupInfoItem["timeSetting"]:
        if inTimeSetting(tm, y):
            return True
    return False


def getMsTime(dt):
    return str(int(dt.timestamp() * 1000))


def cover_to_dt(data, key):
    if key in data and data[key] is not None:
        data[key] = datetime.fromtimestamp(data[key] / 1000)
