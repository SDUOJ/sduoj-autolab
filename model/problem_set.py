import json

from fastapi import HTTPException
from fastapi_cache.decorator import cache
from sqlalchemy import or_, and_

from cache import class_func_key_builder
from db import dbSession, ProblemSet
from sduojApi import getGroupName
from ser.base_type import page


class problemSetModel(dbSession):
    def ps_create(self, data):
        data = self.jsonDumps(data, ["groupInfo", "config"])
        self.session.add(ProblemSet(**data))
        self.session.commit()

    async def ps_get_info_by_id(self, id_, groupName=False):
        data = self.ps_get_obj_by_id(id_)
        data = self.dealData(data, ["create_time", "tm_start", "tm_end"])
        data = self.jsonLoads(data, ["groupInfo", "config"])
        if groupName:
            if data["manageGroupId"] is not None:
                data["managerGroupDTO"] = {
                    "groupId": data["manageGroupId"],
                    "title": await getGroupName(data["manageGroupId"])
                }
            if data["groupId"] is not None:
                data["participatingGroupDTOList"] = [{
                    "groupId": data["groupId"],
                    "title": await getGroupName(data["groupId"])
                }]
        return data

    @cache(expire=60, key_builder=class_func_key_builder)
    async def ps_get_info_by_id_cache(self, id_):
        return await self.ps_get_info_by_id(id_)

    def ps_get_obj_by_id(self, id_):
        ps = self.session.query(ProblemSet).filter(
            ProblemSet.psid == id_
        ).first()
        if ps is None:
            raise HTTPException(detail="Problem group not found",
                                status_code=404)
        return ps

    def ps_get_info_list_by_ids(self, ids):
        res = []
        for id_ in ids:
            data = self.ps_get_obj_by_id(id_)
            data = self.dealData(data, ["create_time", "tm_start", "tm_end"],
                                 ["groupInfo", "config"])
            res.append(data)
        return res

    def ps_update_by_id(self, id_, data):
        data = self.jsonDumps(data, ["groupInfo", "config"])
        self.session.query(ProblemSet).filter(
            ProblemSet.psid == id_
        ).update(data)
        self.session.commit()

    def ps_get_list_info_by_page(self, pg: page, username, groups):
        if username != "superadmin":
            cmd = self.session.query(ProblemSet).filter(
                or_(ProblemSet.manageGroupId.in_(groups),
                    ProblemSet.username == username)
            )
        else:
            cmd = self.session.query(ProblemSet)
        tn = cmd.count()
        data = cmd.offset(pg.offset()).limit(pg.limit())
        return tn, self.dealDataList(
            data,
            ["create_time", "tm_start", "tm_end"],
            ["groupInfo", "config"])

    # 按 groupId 列表查询所有题单，返回基础信息（包含 psid/name/tm_start/tm_end/groupId/tag/config/groupInfo）
    def ps_list_by_groups(self, groups: list):
        if groups is None or len(groups) == 0:
            return []
        data = self.session.query(ProblemSet).filter(
            ProblemSet.groupId.in_(groups)
        ).all()
        res = self.dealDataList(
            data,
            ["create_time", "tm_start", "tm_end"],
            []
        )
        # 反序列化 JSON 字段
        for i in range(len(res)):
            if isinstance(res[i].get("config"), str):
                try:
                    res[i]["config"] = json.loads(res[i]["config"])
                except Exception:
                    pass
            if isinstance(res[i].get("groupInfo"), str):
                try:
                    res[i]["groupInfo"] = json.loads(res[i]["groupInfo"])
                except Exception:
                    pass
        return res

    @cache(expire=60, key_builder=class_func_key_builder)
    async def ps_get_list_search_info_by_page_cache(
            self, pg: page, groupId, key):
        cmd = self.session.query(ProblemSet).filter(
            and_(ProblemSet.groupId == groupId,
                 ProblemSet.tag == key)
        )
        tn = cmd.count()
        data = cmd.offset(pg.offset()).limit(pg.limit())
        return tn, self.dealDataList(
            data,
            ["create_time", "tm_start", "tm_end"],
            ["groupInfo", "config"])

    @cache(expire=60, key_builder=class_func_key_builder)
    async def ps_get_key_list_cache(self, groupId):
        data = self.session.query(ProblemSet).filter(
            ProblemSet.groupId == groupId
        ).all()
        res = {"label": [], "score": {}, "sum": 0}
        for x in data:
            if x.tag not in res["label"]:
                res["label"].append(x.tag)
            if x.tag not in res["score"]:
                res["score"][x.tag] = 0
            # 兼容 global_score 可能为 None 的情况
            gscore = x.global_score if x.global_score is not None else 0
            res["score"][x.tag] += gscore
            res["sum"] += gscore

        # sum 四舍五入保留一位小数
        res["sum"] = round(res["sum"], 1)

        return res

    @cache(expire=60, key_builder=class_func_key_builder)
    async def ps_get_config_by_psid_cache(self, psid):
        obj = self.ps_get_obj_by_id(psid)
        return json.loads(obj.config)

    @cache(expire=60, key_builder=class_func_key_builder)
    async def ps_get_tm_by_psid_cache(self, psid):
        obj = self.ps_get_obj_by_id(psid)
        data = self.dealDataToy(
            obj, ["tm_start", "tm_end"], ["tm_start", "tm_end"]
        )
        return data["tm_start"], data["tm_end"]

    @cache(expire=60, key_builder=class_func_key_builder)
    async def ps_get_groupInfo_by_psid_cache(self, psid):
        obj = self.ps_get_obj_by_id(psid)
        return json.loads(obj.groupInfo)

    # 带缓存：根据 group 列表获取“正在运行/即将开始”的题单列表，按组聚合并排序
    @cache(expire=60, key_builder=class_func_key_builder)
    async def ps_get_upcoming_running_by_groups_cache(self, groups: list):
        from utilsTime import getNowTime, inTime

        now = getNowTime()
        one_day_ms = 24 * 60 * 60 * 1000

        ps_list = self.ps_list_by_groups(groups)

        running_grouped = {}
        upcoming_grouped = {}

        def add_item(target, ps, st, ed):
            gid = ps.get("groupId")
            if gid not in target:
                target[gid] = {
                    "groupId": gid,
                    "groupTitle": None,
                    "problemSets": []
                }
            target[gid]["problemSets"].append({
                "psid": ps.get("psid"),
                "name": ps.get("name"),
                "startTime": str(st) if st is not None else None,
                "endTime": str(ed) if ed is not None else None,
                "tag": ps.get("tag")
            })

        for ps in ps_list:
            cfg = ps.get("config") or {}
            useSame = cfg.get("useSameSE", 0) == 1

            if useSame:
                st = ps.get("tm_start")
                ed = ps.get("tm_end")
                if st is None or ed is None:
                    continue
                try:
                    st = int(st)
                    ed = int(ed)
                except Exception:
                    continue
                if inTime(now, st, ed):
                    add_item(running_grouped, ps, st, ed)
                elif now < st <= now + one_day_ms:
                    add_item(upcoming_grouped, ps, st, ed)
            else:
                groupInfo = ps.get("groupInfo") or []
                placed = False
                for gi in groupInfo:
                    ts_list = gi.get("timeSetting") or []
                    for ts in ts_list:
                        st = ts.get("tm_start")
                        ed = ts.get("tm_end")
                        if st is None or ed is None:
                            continue
                        try:
                            st = int(st)
                            ed = int(ed)
                        except Exception:
                            continue
                        if inTime(now, st, ed):
                            add_item(running_grouped, ps, st, ed)
                            placed = True
                            break
                        if now < st <= now + one_day_ms:
                            add_item(upcoming_grouped, ps, st, ed)
                            placed = True
                            break
                    if placed:
                        break

        # 填充组名与排序
        for gid in list(running_grouped.keys()):
            try:
                running_grouped[gid]["groupTitle"] = await getGroupName(gid)
            except Exception:
                running_grouped[gid]["groupTitle"] = None
            running_grouped[gid]["problemSets"].sort(
                key=lambda x: (int(x["endTime"]) if x["endTime"] is not None else 0)
            )

        for gid in list(upcoming_grouped.keys()):
            try:
                upcoming_grouped[gid]["groupTitle"] = await getGroupName(gid)
            except Exception:
                upcoming_grouped[gid]["groupTitle"] = None
            upcoming_grouped[gid]["problemSets"].sort(
                key=lambda x: (int(x["startTime"]) if x["startTime"] is not None else 0)
            )

        running_result = list(running_grouped.values())
        running_result.sort(
            key=lambda g: (int(g["problemSets"][0]["endTime"]) if g["problemSets"] else 0)
        )

        upcoming_result = list(upcoming_grouped.values())
        upcoming_result.sort(
            key=lambda g: (int(g["problemSets"][0]["startTime"]) if g["problemSets"] else 0)
        )

        return {
            "running": running_result,
            "upcoming": upcoming_result
        }
