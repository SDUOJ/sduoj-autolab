from fastapi import HTTPException

from model.base import baseModel, listQuery, baseQuery
from db import dbSession, ProblemObjective


class objectiveModel(dbSession, baseModel, listQuery, baseQuery):

    def get_obj_by_id(self, pid):
        op = self.session.query(ProblemObjective).filter(
            ProblemObjective.pid == pid
        ).first()
        if op is None:
            raise HTTPException(detail="Problem not found",
                                status_code=404)
        return op

    def create(self, data: dict):
        data = self.jsonDumps(data, ["content", "answer"])
        obj = ProblemObjective(**data)
        self.session.add(obj)
        self.session.flush()
        self.session.commit()
        return obj.pid

    def update_by_id(self, pid, data: dict):
        data = self.jsonDumps(data, ["content", "answer"])
        self.session.query(ProblemObjective).filter(
            ProblemObjective.pid == pid
        ).update(data)
        self.session.commit()

    # def get_content_preview_by_id(self, pid):
    #     return self.get_content_preview(self.get_obj_by_id(pid).__dict__)

    def get_content_preview(self, data):
        d = data["content"]
        s = ""
        s += d["description"] + "\n"
        i = 0
        for c in d["choice"]:
            s += chr(ord("A") + i) + ". " + c + "\n"
            i += 1
        return s[:80]

    def get_info_by_id(self, pid, popKeys=None):
        if popKeys is None:
            popKeys = ["username", "create_time"]
        op = self.get_obj_by_id(pid)

        popC = False
        if "content" in popKeys:
            popKeys.remove("content")
            popC = True

        data = self.dealData(op, ["create_time"], popKeys)
        data = self.jsonLoads(data, ["content", "answer"])
        data["preview"] = self.get_content_preview(data)

        if popC:
            data.pop("content")

        return data

    def get_info_list_by_ids(self, pids):
        ls = []
        for pid in pids:
            ls.append(self.get_info_by_id(pid, ["content", "answer"]))
        return ls
