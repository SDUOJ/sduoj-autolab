import datetime
import uuid

from fastapi import HTTPException
from sqlalchemy import and_, func

from db import dbSession, ojSignUser, ojSign, ojSeat, SignIn

from sduojApi import getGroupMember
# /model/sign_in_record.py-------------------------
class signInRecordModel(dbSession):
    # 新建一个签到  input: mode  group_id  m_group_id  title  gmtStart  gmtEnd  seat_bind
    async def createSign(self, data: dict):
        # 在sign表中建立签到
        gmtStart = data["gmtStart"]
        gmtEnd = data["gmtEnd"]
        u_gmt_create = data["u_gmt_create"]
        u_gmt_modified = data["u_gmt_modified"]
        u_gmt_create = u_gmt_create.strftime("%Y-%m-%d %H:%M:%S")
        u_gmt_modified = u_gmt_modified.strftime("%Y-%m-%d %H:%M:%S")
        data["gmtStart"] = gmtStart
        data["gmtEnd"] = gmtEnd
        data["u_gmt_create"] = u_gmt_create
        data["u_gmt_modified"] = u_gmt_modified
        data = self.jsonDumps(data, ["mode", "group_id", "m_group_id", "seat_bind", "usl_id"])
        self.session.add(ojSign(**data))
        self.session.flush()
        #self.session.commit()
        # 根据 gourp_id 拉取学生名单初始化 uer_sign 表
        get_sg_id = self.session.query(ojSign).filter(
            ojSign.group_id == data["group_id"], ojSign.m_group_id == data["m_group_id"],
            ojSign.usl_id == data["usl_id"], ojSign.seat_bind == data["seat_bind"],
            ojSign.mode == data["mode"], ojSign.gmtStart == data["gmtStart"],
            ojSign.gmtEnd == data["gmtEnd"], ojSign.u_gmt_create == u_gmt_create,
            ojSign.u_gmt_modified == u_gmt_modified
        ).first()
        info = {}
        sg_id = get_sg_id.sg_id
        info["sg_id"] = sg_id
        names = await getGroupMember(data["group_id"])
        names = names.get("members")
        for obj in names:
            q = obj["username"]
            info["username"] = q
            self.session.add(ojSignUser(**info))
            self.session.flush()
        self.session.commit()


    # 修改签到信息
    def editSign(self,data: dict, sg_id: int):
        is_deleted = self.session.query(ojSign).filter(
            ojSign.sg_id == sg_id
        ).first().sign_is_deleted
        if is_deleted == 1:
            raise HTTPException(status_code=404, detail="查询无该签到")
        #获取数据
        mode = data.get("mode")
        group_id = data.get("group_id")
        m_group_id = data.get("m_group_id")
        title = data.get("title")
        gmtStart = data.get("gmtStart")
        gmtEnd = data.get("gmtEnd")
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
        if gmtStart is not None:
            edit_list["gmtStart"] = gmtStart
        if gmtEnd is not None:
            edit_list["gmtEnd"] = gmtEnd

        edit_list["u_gmt_modified"] = data["u_gmt_modified"]
        #向数据库中更新数据
        if edit_list:
            self.session.query(ojSign).filter(
                ojSign.sg_id == sg_id
            ).update(edit_list)
            self.session.commit()


    # 删除签到信息 003
    def deleteSign(self, sg_id:int):
        is_deleted = self.session.query(ojSign).filter(
            ojSign.sg_id == sg_id
        ).first()
        if is_deleted is None:
            raise HTTPException(status_code=404, detail="查询无该签到")
        if is_deleted.sign_is_deleted == 1:
            raise HTTPException(status_code=404, detail="签到已处于被删除状态")
        self.session.query(ojSign).filter(
            ojSign.sg_id == sg_id
        ).update({"sign_is_deleted": 1})
        self.session.commit()


    # 查询签到信息 004
    def getSign(self, sg_id: int):
        is_deleted = self.session.query(ojSign).filter(
            ojSign.sg_id == sg_id
        ).first().sign_is_deleted
        if is_deleted == 1:
            raise HTTPException(status_code=404, detail="查询无该签到")

        info = {}
        signInfo = self.session.query(ojSign).filter(
            ojSign.sg_id == sg_id
        ).first()

        gmtStart = signInfo.gmtStart.timestamp() * 1000
        gmtEnd = signInfo.gmtEnd.timestamp() * 1000

        info["mode"] = signInfo.mode
        info["group_id"] = signInfo.group_id
        info["m_group_id"] = signInfo.group_id
        info["title"] = signInfo.title
        info["seat_bind"] = signInfo.seat_bind
        info["gmtStart"] = gmtStart
        info["gmtEnd"] = gmtEnd

        return info


    # 查询所有的签到信息
    def getSignList(self, pageNow: int, pageSize: int):
        info = {"rows": []}
        query = self.session.query(func.count(ojSign.sg_id)).filter(
            ojSign.sign_is_deleted != 1
        )

        datanum = query.scalar()
        if datanum == 0:
            return None
        info["totalNum"] = datanum
        info["totalPage"] = datanum // pageSize
        offsets = (pageNow - 1) * pageSize
        query = self.session.query(ojSign).filter(
            ojSign.sign_is_deleted != 1
        ).offset(offsets).limit(pageSize).all()

        for obj in query:
            gmtStart = obj.gmtStart.timestamp() * 1000.0
            gmtEnd = obj.gmtEnd.timestamp() * 1000.0
            data = {
                "sg_id": obj.sg_id,
                "mode": obj.mode,
                "group_id": obj.group_id,
                "m_group_id": obj.m_group_id,
                "title": obj.title,
                "gmtStart": gmtStart,
                "gmtEnd": gmtEnd,
                "seat_bind": obj.seat_bind
            }
            info["rows"].append(data)

        return info


    # 查询一个签到中的所有签到用户 006
    def getUserSign(self, pageSize: int, pageNow:int, sg_id:int, mode:int):
        info = {"rows": []}
        # 判断sg_id合法性
        is_deleted = self.session.query(ojSign).filter(
            ojSign.sg_id == sg_id
        ).first().sign_is_deleted
        if is_deleted == 1:
            raise HTTPException(status_code=404, detail="查询无该签到")
        # 计算数据量
        querys = self.session.query(ojSign).filter()
        if mode == 0:
            querys = self.session.query(func.count(ojSignUser.sg_u_id)).filter(
                ojSignUser.sg_time.is_(None),
                ojSignUser.sg_id == sg_id,
                ojSignUser.is_deleted.is_(None)
            )
        elif mode == 1:
            querys = self.session.query(func.count(ojSignUser.sg_u_id)).filter(
                ojSignUser.sg_time.is_not(None),
                ojSignUser.sg_id == sg_id,
                ojSignUser.is_deleted.is_(None)
            )
        elif mode == 2:
            querys = self.session.query(func.count(ojSignUser.sg_u_id)).filter(
                ojSignUser.sg_id == sg_id,
                ojSignUser.is_deleted.is_(None)
            )
        elif mode == 3:
            querys = self.session.query(func.count(ojSignUser.sg_u_id)).filter(
                ojSignUser.sg_id == sg_id,
                ojSignUser.is_deleted.is_(None),
                ojSignUser.sg_user_message.is_not(None)
            )

        datanum = querys.scalar()
        if datanum == 0:
            return info
        # 计算数据偏移量
        offsets = pageSize * (pageNow - 1)
        info["totalNum"] = datanum
        info["totalPage"] = datanum // pageSize
        # 得到相关签到数据集
        # 0:未签到  1：签到  2：所有  3：筛选出sg_message不为空的数据
        if mode == 0:
            querys = self.session.query(ojSignUser).filter(
                ojSignUser.sg_id == sg_id ,
                ojSignUser.is_deleted.is_(None) ,
                ojSignUser.sg_time.is_(None)
            )
        elif mode == 1:
            querys = self.session.query(ojSignUser).filter(
                ojSignUser.sg_id == sg_id,
                ojSignUser.is_deleted.is_(None),
                ojSignUser.sg_time. is_not(None)
            )
        elif mode == 2:
            querys = self.session.query(ojSignUser).filter(
                ojSignUser.sg_id == sg_id ,
                ojSignUser.is_deleted.is_(None)
            )
        elif mode == 3:
            querys = self.session.query(ojSignUser).filter(
                ojSignUser.sg_id == sg_id ,
                ojSignUser.is_deleted.is_(None),
                ojSignUser.sg_user_message.is_not(None)
            )
        for obj in querys:
            if obj.sg_time is not None:
                sg_time = obj.sg_time.timestamp() * 1000.0
            else:
                sg_time = 0
            data = {
            "sg_u_id": obj.sg_u_id ,
            "username": obj.username ,
            "sg_time": sg_time ,
            "seat_id": obj.seat_id ,
            "sg_u_message": obj.sg_user_message ,
            "sg_absence_pass": obj.sg_absence_pass
            }
            info["rows"].append(data)

        return info


    # 用户签到  input: username  sg_id  ip  sg_user_message
    def signIn(self, data: dict):
        sg_time = data["sg_time"]
        ip = data["ip"]
        #检查ip，防止一个机器多次签到
        is_same_ip = self.session.query(ojSignUser).filter(
            ojSignUser.sg_id == data["sg_id"]
        )

        for obj in is_same_ip:
            if obj.is_deleted == 1 :
                raise HTTPException(status_code=404, detail="该用户已被删除")
            elif obj.ip == ip and ip is not None:
                raise HTTPException(status_code=400, detail="该ip已经被使用过")
            #elif obj.sg_time is not None:
                #raise HTTPException(status_code=400, detail="该用户已签到过")

        data["sg_time"] = sg_time.strftime("%Y-%m-%d %H:%M:%S")
        data["seat_id"] = None
        # 区分前端是否传入ip
        if ip is not None:
            seat_id = self.session.query(ojSeat).filter(
                ojSeat.s_ip == ip
            ).first()
            if seat_id is None:
                raise HTTPException(status_code=404, detail="该ip未与座位绑定")
            data["seat_id"] = seat_id.s_id

        edit_row = self.session.query(ojSignUser).filter(
                    ojSignUser.sg_id == data["sg_id"], ojSignUser.username == data["username"]
                    )
        if edit_row.first() is None:
            raise HTTPException(status_code=404, detail="查询无该用户")
        data = self.jsonDumps(data, [ "sg_id", "seat_id", "sg_absence_pass"])
        edit_row.update(data)
        self.session.flush()
        self.session.commit()



    def getUserInfo(self, sg_id: int, username: str):
        is_sg_id_is_none = self.session.query(ojSign).filter(
            ojSign.sg_id == sg_id
        )
        if is_sg_id_is_none.first() is None:
            raise HTTPException(status_code=404, detail="查询无该签到")

        is_deleted = self.session.query(ojSign).filter(
            ojSign.sg_id == sg_id
        ).first().sign_is_deleted
        if is_deleted == 1:
            raise HTTPException(status_code=404, detail="查询无该签到")

        info = {}
        data = self.session.query(ojSignUser).filter(
            ojSignUser.username == username, ojSignUser.sg_id == sg_id
        ).all()
        for i in data:
            if i.sg_time is not None:
                sg_time = i.sg_time.timestamp() * 1000.0
            else:
                sg_time = 0

            info = {
                "sg_u_id": i.sg_id,
                "user_name": i.username,
                "sg_id": i.sg_id,
                "sg_time": sg_time,
                "seat_id": i.seat_id,
                "sg_user_message": i.sg_user_message,
                "sg_absence_pass": i.sg_absence_pass
            }
        return info


    # 所有用户签到信息查询
    def getUserInfoList(self, group_id: int, username: str, pageNow:int, pageSize:int):
        res = {"rows": []}
        q = self.session.query(ojSignUser, ojSign).join(ojSign).filter(
             ojSignUser.username == username, ojSign.group_id == group_id, ojSign.sign_is_deleted != 1
        )
        datanum = q.count()
        res["totalNum"] = datanum
        # 得到页数
        totalpage = datanum // pageSize
        res["totalPage"] = totalpage

        # 得到相关数据
        q = self.session.query(ojSignUser).join(ojSign).filter(
            ojSignUser.username == username, ojSign.group_id == group_id, ojSign.sign_is_deleted != 1
        ).offset((pageNow-1) * pageSize).limit(pageSize).all()

        for i in q:
            get_sign = self.session.query(ojSign).filter(
                ojSign.sg_id == i.sg_id, ojSign.sign_is_deleted != 1
            ).first()
            if get_sign.gmtStart is not None:
                gmtStart = get_sign.gmtStart.timestamp() * 1000.0
            else:
                gmtStart = 0

            if get_sign.gmtEnd is not None:
                gmtEnd = get_sign.gmtEnd.timestamp() * 1000.0
            else:
                gmtEnd = 0

            if i.sg_time is not None:
                sg_time = i.sg_time.timestamp() * 1000.0
            else:
                sg_time = 0

            info = {
                "sg_u_id": i.sg_u_id,
                "sg_id": i.sg_id,
                "mode": get_sign.mode,
                "gmtStart": gmtStart,
                "gmtEnd": gmtEnd,
                "sg_time": sg_time,
                "seat_id": i.seat_id,
                "sg_user_message": i.sg_user_message,
                "sg_absence_pass": i.sg_absence_pass
            }
            res["rows"].append(info)

        return res

    # 用户提交请假信息
    def submitLeaveInfo(self, data: dict):
        sg_u_id = data.get("sg_u_id")
        is_existed = self.session.query(ojSignUser).filter(
            ojSignUser.sg_u_id == sg_u_id
        ).first()
        if is_existed is None:
            raise HTTPException(status_code=404, detail = "未查询到该用户的签到信息")

        self.session.query(ojSignUser).filter(
            ojSignUser.sg_u_id == sg_u_id
        ).update(data)
        self.session.commit()

    # 后台审批请假信息
    def checkLeaveInfo(self, data: dict):
        sg_u_id = data.get("sg_u_id")

        is_existed = self.session.query(ojSignUser).filter(
            ojSignUser.sg_u_id == sg_u_id
        ).first()
        if is_existed is None:
            raise HTTPException(status_code=404, detail="未查询到该用户的签到信息")

        if is_existed.sg_user_message is None:
            raise HTTPException(status_code=500, detail="该用户没有提交请假申请")

        self.session.query(ojSignUser).filter(
            ojSignUser.sg_u_id == sg_u_id
        ).update(data)
        self.session.commit()

    # 删除用户签到信息  1:已经被删除了
    def deleteLeaveInfo(self, sg_u_id: int):
        is_existed = self.session.query(ojSignUser).filter(
            ojSignUser.sg_u_id == sg_u_id
        )
        if is_existed.first() is None :
            raise HTTPException(status_code=404, detail="未找到该用户的签到信息")
        elif is_existed.first().is_deleted == 1 :
            raise HTTPException(status_code=404, detail="该用户不存在")
        data = {}
        data["is_deleted"] = 1
        self.session.query(ojSignUser).filter(
            ojSignUser.sg_u_id == sg_u_id
        ).update(data)
        self.session.commit()

    def get_sg_id_by_username(self, username: str):
        info = self.session.query(ojSignUser).filter(
            ojSignUser.username == username
        )
        sg_id = info.first().sg_id
        return sg_id

    # 提交部分用户信息
    def committoken(self, data: dict):
        info = {}
        user_query = self.session.query(ojSignUser).filter(
            ojSignUser.username == data["username"],
            ojSignUser.sg_id == data["sg_id"]
        ).first()
        if user_query:
            if user_query.token:
                info["token"] = user_query.token
            else:
                Token = uuid.uuid4().hex
                info["token"] = Token
                self.session.query(ojSignUser).filter(
                    ojSignUser.username == user_query.username,
                    ojSignUser.sg_id == user_query.sg_id
                ).update(info)
                self.session.commit()
            return info
        else:
            Token = uuid.uuid4().hex
            data["token"] = Token
            info["token"] = Token
            data = self.jsonDumps(data, ["sg_u_id", "seat_id", "sg_time", "sg_user_message", "sg_absence_pass"])
            self.session.add(ojSignUser(**data))
            self.session.flush()
            self.session.commit()
            return info

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
        res = []
        info = self.session.query(ojSignUser).join(ojSign).filter(
            ojSignUser.username == username, ojSign.group_id == group_id
        ).all()

        if res is None:
            return None

        for obj in info:
            query = self.session.query(ojSign).filter(
                ojSign.sg_id == obj.sg_id, ojSign.sign_is_deleted != 1
            ).first()
            mode = query.mode
            if obj.sg_time is not None:
                sg_time = obj.sg_time.timestamp() * 1000.0
            else:
                sg_time = 0
            data = {
                "sg_u_id": obj.sg_u_id,
                "sg_id": obj.sg_id,
                "sg_time": sg_time,
                "seat_id": obj.seat_id,
                "mode": mode
            }
            res.append(data)

        return res


    def getSignListForWeChat(self):
        info = {"title": []}
        query = self.session.query(ojSign).all()
        if query is None:
            return info
        for obj in query:
            info["title"].append(obj.title)
        return info

    def convert_to_timestamp(self,dateitem):
        # 将日期字符串转换为 datetime 对象
        date_time_obj = datetime.datetime.strftime(dateitem, "%Y-%m-%d %H:%M:%S")
        # 将 datetime 对象转换为时间戳
        timestamp = date_time_obj.timestamp()
        return {"date_str": dateitem, "timestamp": timestamp}