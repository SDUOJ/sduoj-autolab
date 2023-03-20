from fastapi import HTTPException

from model.base import baseModel, listQuery, baseQuery
from db import dbSession, ProblemSubjective


class subjectiveModel(dbSession, baseModel, baseQuery, listQuery):

    def get_obj_by_id(self, pid):
        sp = self.session.query(ProblemSubjective).filter(
            ProblemSubjective.pid == pid
        ).first()

        if sp is None:
            raise HTTPException(detail="Problem not found",
                                status_code=404)
        return sp

    def create(self, data: dict):
        data = self.jsonDumps(data, ["config"])
        obj = ProblemSubjective(**data)
        self.session.add(obj)
        self.session.flush()
        self.session.commit()
        return obj.pid

    def update_by_id(self, pid, data: dict):
        data = self.jsonDumps(data, ["config"])
        self.session.query(ProblemSubjective).filter(
            ProblemSubjective.pid == pid
        ).update(data)
        self.session.commit()

    # def get_content_preview_by_id(self, pid):
    #     self.get_content_preview(self.get_obj_by_id(pid).__dict__)

    def get_content_preview(self, data):
        return data["description"][:80]

    def get_info_by_id(self, pid, popKeys=None):
        if popKeys is None:
            popKeys = ["username", "create_time"]
        sp = self.get_obj_by_id(pid)

        popD = False
        if "description" in popKeys:
            popKeys.remove("description")
            popD = True

        data = self.dealData(sp, ["create_time"], popKeys)
        data = self.jsonLoads(data, ["config"])
        data["preview"] = self.get_content_preview(data)

        if popD:
            data.pop("description")

        return data

    def get_info_list_by_ids(self, pids):
        ls = []
        for pid in pids:
            ls.append(self.get_info_by_id(pid, ["description", "config"]))
        return ls
