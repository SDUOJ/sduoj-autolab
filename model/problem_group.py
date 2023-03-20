from fastapi import HTTPException
from fastapi_cache.decorator import cache
from sqlalchemy import or_, and_

from cache import class_func_key_builder
from db import ProblemGroup, dbSession
from sduojApi import getGroupName
from ser.base_type import page


class groupModel(dbSession):
    def group_get_obj_by_id(self, gid):
        pg = self.session.query(ProblemGroup).filter(
            ProblemGroup.gid == gid).first()
        if pg is None:
            raise HTTPException(detail="Problem group not found",
                                status_code=404)
        return pg

    def group_create(self, data: dict):
        data = self.jsonDumps(data, ["problemInfo"])
        obj = ProblemGroup(**data)
        self.session.add(obj)
        self.session.flush()
        self.session.commit()
        return obj.gid

    def group_update_by_id(self, gid, data: dict):
        data = self.jsonDumps(data, ["problemInfo"])
        self.session.query(ProblemGroup).filter(
            ProblemGroup.gid == gid).update(data)
        self.session.commit()

    def group_get_list_info_by_page(self, pg: page, username, groups):
        if username != "superadmin":
            cmd = self.session.query(ProblemGroup).filter(
                or_(ProblemGroup.manageGroupId.in_(groups),
                    ProblemGroup.username == username)
            )
        else:
            cmd = self.session.query(ProblemGroup)
        tn = cmd.count()
        data = cmd.offset(pg.offset()).limit(pg.limit())
        return tn, self.dealDataList(data, ["create_time"], ["problemInfo"])

    def group_get_list_info_by_ids(self, ids: list):
        res = []
        for id_ in ids:
            group = self.group_get_obj_by_id(id_)
            group = self.dealData(group, ["create_time"], ["problemInfo"])
            res.append(group)
        return res

    def group_get_problemDetail(self, data):
        tp = data["type"]

        if tp == 2:
            return data

        if tp == 0:
            from model.objective import objectiveModel
            db = objectiveModel()
        else:
            from model.subjective import subjectiveModel
            db = subjectiveModel()

        ids = []
        for pro in data["problemInfo"]:
            ids.append(pro["pid"])

        problemDetail = db.get_info_list_by_ids(ids)

        for i in range(len(problemDetail)):
            data["problemInfo"][i]["detail"] = problemDetail[i]

        return data

    @cache(expire=60, key_builder=class_func_key_builder)
    async def group_get_info_by_id_cache(self, gid):
        return await self.group_get_info_by_id(gid)

    async def group_get_info_by_id(self, gid):
        group = self.group_get_obj_by_id(gid)
        group = self.dealData(group, ["create_time"])
        group = self.jsonLoads(group, ["problemInfo"])
        group = self.group_get_problemDetail(group)
        if group["manageGroupId"] is not None:
            group["managerGroupDTO"] = {
                "groupId": group["manageGroupId"],
                "title": await getGroupName(group["manageGroupId"])
            }
        return group

    def group_get_idName_list_by_key(self, key, username, groups):
        if username == "superadmin":
            data = self.session.query(ProblemGroup).all()
        else:
            data = self.session.query(ProblemGroup).filter(
                and_(or_(ProblemGroup.manageGroupId.in_(groups),
                         ProblemGroup.username == username),
                     ProblemGroup.name.like("%{}%".format(key)))
            ).all()
        ret = []
        for x in data:
            ret.append({"gid": x.gid, "name": x.name, "type": x.type})
        return ret

    @cache(expire=60, key_builder=class_func_key_builder)
    async def group_get_info_c_by_id(self, gid):
        group = self.group_get_obj_by_id(gid)
        group = self.dealData(group, ["create_time"])
        group = self.jsonLoads(group, ["problemInfo"])
        # 填充点选的题型信息
        if group["type"] == 0:
            ids = []
            for pro in group["problemInfo"]:
                ids.append(pro["pid"])
            from model.objective import objectiveModel
            db = objectiveModel()
            problemDetail = db.get_info_list_by_ids(ids)
            for i in range(len(problemDetail)):
                group["problemInfo"][i]["type"] = problemDetail[i]["type"]

        group["problemInfo"] = self.deleteNone(group["problemInfo"])
        return group
