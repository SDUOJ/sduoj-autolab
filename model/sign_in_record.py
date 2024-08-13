import datetime

from fastapi import HTTPException
from sqlalchemy import and_, func

from db import dbSession, ojSignUser, ojSign, ojSeat


# /model/sign_in_record.py-------------------------
class signInRecordModel(dbSession):

    # 新建一个签到  input: mode  group_id  m_group_id  title  start_time  end_time  seat_bind
    def createSign(self, data: dict):
        start_time = data["start_time"]
        end_time = data["end_time"]
        u_gmt_create = data["u_gmt_create"]
        u_gmt_modified = data["u_gmt_modified"]
        start_time = start_time.strftime("%Y-%m-%d %H:%M:%S")
        end_time = end_time.strftime("%Y-%m-%d %H:%M:%S")
        u_gmt_create = u_gmt_create.strftime("%Y-%m-%d %H:%M:%S")
        u_gmt_modified = u_gmt_modified.strftime("%Y-%m-%d %H:%M:%S")
        data["start_time"] = start_time
        data["end_time"] = end_time
        data["u_gmt_create"] = u_gmt_create
        data["u_gmt_modified"] = u_gmt_modified
        data = self.jsonDumps(data,["mode", "group_id", "m_group_id", "title", "seat_bind", "usl_id"])
        self.session.add(ojSign(**data))
        self.session.flush()
        self.session.commit()


    # 修改签到信息
    def editSign(self,data: dict, sg_id:str):
        #获取数据
        mode = data["mode"]
        group_id = data["group_id"]
        m_group_id = data["m_group_id"]
        u_gmt_modified = datetime.datetime.now()
        title = data["title"]
        start_time = data["start_time"]
        end_time = data["end_time"]
        seat_bind = data["seat_bind"]
        usl_id = data["usl_id"]
    # 判断传入的数据
        edit_list = {}
        if mode is not None:
            edit_list["mode"] = mode
        if group_id is not None:
            edit_list["group_id"] = group_id
        if m_group_id is not None:
            edit_list["m_group_id"] = m_group_id
        if title is not None:
            edit_list["title"] = title
        if seat_bind is not None:
            edit_list["seat_bind"] = seat_bind
        if usl_id is not None:
            edit_list["usl_id"] = usl_id
        #向数据库中更新数据
        if edit_list:
            self.session.query(ojSign).filter(
                ojSign.sg_id == sg_id
            ).update(edit_list)
            self.session.commit()



    # 删除签到信息
    def deleteSign(self, sg_id:int):
        editInfo = self.session.query(ojSign).filter(
            ojSign.sg_id == sg_id
        ).delete()
        self.session.commit()

    # 查询签到信息
    def getSign(self, sg_id: int):
        info = []
        signInfo = self.session.query(ojSign).filter(
            ojSign.sg_id == sg_id
        ).first()
        return signInfo

    # 查询用户的签到
    def getUserSign(self, username: str):
        sg_id = self.get_sg_id_by_username(username)
        user_info = self.session.query(ojSign).filter(
            ojSign.sg_id == sg_id
        ).first()
        return user_info



    # 用户签到  input: sg_u_id  username  sg_id  seat_id  sg_user_message
    def signIn(self,data: dict):
        sg_time = data["sg_time"]
        sg_time = sg_time.strftime("%Y-%m-%d %H:%M:%S")
        data["sg_time"] = sg_time
        sg_user_message = data["sg_user_message"]
        sg_user_message = sg_user_message.strip()
        data = self.jsonDumps(data, ["sg_u_id",  "sg_id", "seat_id", "sg_absence_pass"])
        self.session.add(ojSignUser(**data))
        self.session.flush()
        self.session.commit()


    def getUserInfo(self, sg_u_id: int):
        info = []
        data = self.session.query(ojSignUser).filter(
            ojSignUser.sg_id == sg_u_id
        ).all()
        for i in data:
            datas = [i.sg_u_id, i.username,i.sg_id, i.seat_id, i.sg_user_message, i.sg_absence_pass]
            info.append(datas)
        return info


    # 所有用户签到信息查询
    def getUserInfoList(self, sg_id: int):
        res = []
        q = self.session.query(ojSignUser).filter(
            ojSignUser.sg_id == sg_id
        ).all()
        for i in q:
            data = [i.sg_u_id, i.username, i.sg_id, i.sg_time, i.seat_id, i.sg_user_message, i.sg_absence_pass]
            res.append(data)

        return res

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

    def get_sg_id_by_username(self, username: str):
        info = self.session.query(ojSignUser).filter(
            ojSignUser.username == username
        )
        sg_id = info.first().sg_id
        return sg_id

    # 提交部分用户信息
    def committoken(self, data: dict):
        data = self.jsonDumps(data, ["sg_u_id", "seat_id", "sg_time", "sg_user_message", "sg_absence_pass"])
        self.session.add(ojSignUser(**data))
        self.session.flush()
        self.session.commit()

    # 验证token一致，完善签到信息
    def checktoken(self, data: dict):
        seat_id = self.session.query(ojSeat).filter(
            ojSeat.c_id == data["c_id"], ojSeat.s_number == data["s_number"]
        ).first().s_id
        print(seat_id)
        datas = {
            "sg_time": data["sg_time"],
            "seat_id": seat_id,
        }
        sg_time = datas["sg_time"]
        sg_time = sg_time.strftime("%Y-%m-%d %H:%M:%S")
        datas["sg_time"] = sg_time
        self.session.query(ojSignUser).filter(
            ojSignUser.token == data["token"]
        ).update(datas)
        self.session.commit()





