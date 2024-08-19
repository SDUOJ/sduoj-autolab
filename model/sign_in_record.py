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
    def editSign(self,data: dict, sg_id: int):
        #获取数据
        mode = data.get("mode")
        group_id = data.get("group_id")
        m_group_id = data.get("m_group_id")
        title = data.get("title")
        start_time = data.get("start_time")
        end_time = data.get("end_time")
        seat_bind = data.get("seat_bind")
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
        if start_time is not None:
            edit_list["start_time"] = start_time
        if end_time is not None:
            edit_list["end_time"] = end_time

        edit_list["u_gmt_modified"] = data["u_gmt_modified"]
        #向数据库中更新数据
        if edit_list:
            self.session.query(ojSign).filter(
                ojSign.sg_id == sg_id
            ).update(edit_list)
            self.session.commit()



    # 删除签到信息 003
    def deleteSign(self, sg_id:int):
        editInfo = self.session.query(ojSign).filter(
            ojSign.sg_id == sg_id
        ).delete()
        self.session.commit()

    # 查询签到信息 004
    def getSign(self, sg_id: int):
        info = {}
        signInfo = self.session.query(ojSign).filter(
            ojSign.sg_id == sg_id
        ).first()
        info["mode"] = signInfo.mode
        info["group_id"] = signInfo.group_id
        info["m_group_id"] = signInfo.group_id
        info["title"] = signInfo.title
        info["seat_bind"] = signInfo.seat_bind
        info["start_time"] = signInfo.start_time
        info["end_time"] = signInfo.end_time

        return info


    # 查询所有的签到信息
    def getSignList(self, pageNow: int, pageSize: int):
        info = {"rows": []}
        query = self.session.query(func.count(ojSign.sg_id))
        datanum = query.scalar()
        if datanum == 0:
            return None
        info["totalNums"] = datanum
        info["totalPages"] = datanum // pageSize
        offsets = (pageNow - 1) * pageSize
        query = self.session.query(ojSign).offset(offsets).limit(pageSize).all()

        for obj in query:
            data = {
                "sg_id": obj.sg_id,
                "mode": obj.mode,
                "group_id": obj.group_id,
                "m_group_id": obj.m_group_id,
                "title": obj.title,
                "start_time": obj.start_time,
                "end_time": obj.end_time,
                "seat_bind": obj.seat_bind
            }
            info["rows"].append(data)

        return info

    # 查询一个签到中的所有签到用户 006
    def getUserSign(self, sg_id: int, pageNow: int , pageSize: int):
        info = {"rows": []}
        # 得到数据量
        query = self.session.query(func.count(ojSignUser.sg_id)).filter(
            ojSignUser.sg_id == sg_id
        )
        datanum = query.scalar()
        info["totalNums"] = datanum
        # 得到页数
        totalpage = datanum // pageSize
        info["totalPages"] = totalpage

        # 得到相关签到数据集
        query = self.session.query(ojSignUser).filter(
            ojSignUser.sg_id == sg_id
        ).all()
        offets = (pageNow - 1) * pageSize
        begin = 0
        getinfonum = 1
        pagesize = pageSize
        for obj in query:
            signInfo = self.session.query(ojSignUser).filter(
                ojSignUser.sg_id == obj.sg_id
            ).first()
            data = {
                "sg_u_id": signInfo.sg_u_id,
                "user_name": obj.username,
                "sg_time": obj.sg_time,
                "seat_id": obj.seat_id,
                "sg_u_message": obj.sg_user_message,
                "sg_absence_pass": obj.sg_absence_pass
            }

            if begin >= offets:
                if getinfonum <= pagesize:
                    info["rows"].append(data)
                    getinfonum += 1
            else:
                begin += 1

        return info


    # 用户签到  input: username  sg_id  seat_id  sg_user_message
    def signIn(self,data: dict):
        sg_time = data["sg_time"]
        sg_time = sg_time.strftime("%Y-%m-%d %H:%M:%S")
        data["sg_time"] = sg_time
        sg_user_message = data["sg_user_message"]
        sg_user_message = sg_user_message.strip()
        data = self.jsonDumps(data, ["sg_id", "seat_id", "sg_absence_pass"])
        self.session.add(ojSignUser(**data))
        self.session.flush()
        self.session.commit()


    def getUserInfo(self, sg_id: int, username: str):
        info = {"data": []}
        data = self.session.query(ojSignUser).filter(
            ojSignUser.username == username, ojSignUser.sg_id == sg_id
        ).all()
        for i in data:
            temp = {
                "sg_u_id": i.sg_id,
                "user_name": i.username,
                "sg_id": i.sg_id,
                "sg_time": i.sg_time,
                "seat_id": i.seat_id,
                "sg_user_message": i.sg_user_message,
                "sg_absence_pass": i.sg_absence_pass
            }
            info["data"].append(temp)
        return info



    # 所有用户签到信息查询
    def getUserInfoList(self, group_id: int, username: str, page: dict):
        res = {"rows": []}
        q = self.session.query(ojSignUser, ojSign).join(ojSign).filter(
             ojSignUser.username == username, ojSign.group_id == group_id
        )
        datanum = q.count()
        res["totalNums"] = datanum
        # 得到页数
        totalpage = datanum // page["pageSize"]
        res["totalPages"] = totalpage

        # 得到相关数据
        q = self.session.query(ojSignUser).join(ojSign).filter(
            ojSignUser.username == username, ojSign.group_id == group_id
        ).offset((page["pageNow"]-1) * page["pageSize"]).limit(page["pageSize"]).all()
        for i in q:
            get_time = self.session.query(ojSign).filter(
                ojSign.sg_id == i.sg_id
            ).first()
            info = {
                "sg_u_id": 3,
                "user_name": i.username,
                "sg_id": i.sg_id,
                "startime": get_time.start_time,
                "endtime": get_time.end_time,
                "sg_time": i.sg_time,
                "seat_id": i.seat_id,
                "sg_user_message": i.sg_user_message,
                "sg_absence_pass": i.sg_absence_pass
            }
            res["rows"].append(info)

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


    def getOneUserInfo(self, group_id:int, username: str):
        res = {"data": []}
        info = self.session.query(ojSignUser).join(ojSign).filter(
            ojSignUser.username == username, ojSign.group_id == group_id
        ).all()

        if res is None:
            return None

        for obj in info:
            query = self.session.query(ojSign).filter(
                ojSign.sg_id == obj.sg_id
            ).first()
            data = {
                "sg_u_id": obj.sg_u_id,
                "sg_id": obj.sg_id,
                "sg_time": obj.sg_time,
                "seat_id": obj.seat_id
            }
            res["data"].append(data)

        return res