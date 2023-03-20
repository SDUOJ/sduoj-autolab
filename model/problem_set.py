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
            res["score"][x.tag] += x.global_score
            res["sum"] += x.global_score

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
