from fastapi import HTTPException
from sqlalchemy import and_, func

from db import dbSession, ojClass, ojSeat, ojClassUser, ojUserSeatList, ojClassManageUser, ojSign, ojSignUser


# /model/sign_in_record.py-------------------------
class signInRecordModel(dbSession):
    def submitLeaveInfo(self, data: dict):
        sg_u_id = data.get("sg_u_id")
        self.session.query(ojSignUser).filter(
            ojSignUser.sg_u_id == sg_u_id
        ).update(data)
        self.session.commit()






