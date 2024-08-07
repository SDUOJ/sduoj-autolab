from fastapi import HTTPException
from sqlalchemy import and_, func

from db import dbSession, ojSignUser


# /model/sign_in_record.py-------------------------
class signInRecordModel(dbSession):
    # 用户提交请假信息
    def submitLeaveInfo(self, data: dict):
        sg_u_id = data.get("sg_u_id")
        self.session.query(ojSignUser).filter(
            ojSignUser.sg_u_id == sg_u_id
        ).update(data)
        self.session.commit()

    # 后台审批请假信息
    def checkLeaveInfo(self, data: dict):
        sg_u_id = data.get("sg_u_id")
        self.session.query(ojSignUser).filter(
            ojSignUser.sg_u_id == sg_u_id
        ).update(data)
        self.session.commit()

    # 删除用户签到信息
    def deleteLeaveInfo(self, sg_u_id: int):
        self.session.query(ojSignUser).filter(
            ojSignUser.sg_u_id == sg_u_id
        ).delete()
        self.session.commit()
