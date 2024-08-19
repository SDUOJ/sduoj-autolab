from fastapi import HTTPException
from sqlalchemy import and_, func

from db import dbSession, ojClass, ojSeat, ojClassUser, ojUserSeatList, ojClassManageUser


class IDGenerator:
    # 生成唯一标识c_id和s_id
    def __init__(self):
        self.next_c_id = 1

    def get_next_id(self):
        current_id = self.next_c_id
        self.next_c_id += 1
        return current_id


# s_id自增器
s_id_generator = IDGenerator()


# /model/class_binding.py ------------------------------------------
class classBindingModel(dbSession):
    global s_id_generator

    def get_c_id_available(self, c_id):
        result = self.session.query(ojClass).filter(
            ojClass.c_id == c_id
        ).first()
        while result is not None:
            c_id += 1
            result = self.session.query(ojClass).filter(
                ojClass.c_id == c_id
            ).first()
        return c_id

    def get_s_id_available(self, s_id):
        result = self.session.query(ojSeat).filter(
            ojSeat.s_id == s_id
        ).first()
        while result is not None:
            s_id += 1
            result = self.session.query(ojSeat).filter(
                ojSeat.s_id == s_id
            ).first()
        return s_id

    def get_usl_id_available(self, usl_id):
        result = self.session.query(ojUserSeatList).filter(
            ojUserSeatList.usl_id == usl_id
        ).first()
        while result is not None:
            usl_id += 1
            result = self.session.query(ojUserSeatList).filter(
                ojUserSeatList.usl_id == usl_id
            ).first()
        return usl_id

    def get_ojClassUser_id_available(self, ojClassUser_id):
        result = self.session.query(ojClassUser).filter(
            ojClassUser.id == ojClassUser_id
        ).first()
        while result is not None:
            ojClassUser_id += 1
            result = self.session.query(ojClassUser).filter(
                ojClassUser.id == ojClassUser_id
            ).first()
        return ojClassUser_id

    # 新建教室和座位
    # input: c_name, c_seat_num, c_description ,address
    def classroom_create(self, data: dict):
        c_id = data.get("c_id")
        c_seat_num = data.get("c_seat_num")

        data = self.jsonDumps(data, ["c_id", "c_name", "c_seat_num", "c_description", "c_is_available", "address"])
        self.session.add(ojClass(**data))
        self.session.flush()
        self.session.commit()

        for i in range(1, c_seat_num + 1):
            s_id = s_id_generator.get_next_id()
            s_id = self.get_s_id_available(s_id)
            seatData = {
                "s_id": s_id,
                "s_number": i,
                "c_id": c_id,
                "s_tag": 0,
                "s_ip": None
            }
            seatData = self.jsonDumps(seatData, ["s_id", "s_number", "c_id", "s_tag", "s_ip"])
            self.session.add(ojSeat(**seatData))
            self.session.flush()
            self.session.commit()

    # 通过c_id找到c_name
    def get_c_name_by_c_id(self, c_id):
        result = self.session.query(ojClass).filter(
            ojClass.c_id == c_id
        ).first()

        if result is None:
            raise HTTPException(status_code=404,
                                detail="未找到对应教室")

        return result.c_name

    # 通过c_name找到c_id
    def get_c_id_by_c_name(self, c_name):
        result = self.session.query(ojClass).filter(
            ojClass.c_name == c_name
        ).first()

        if result is None:
            raise HTTPException(status_code=404,
                                detail="未找到对应教室")

        return result.c_id

    # 通过s_number和c_id找到唯一的s_id
    def get_s_id_by_s_number_and_c_id(self, s_number, c_id):
        result = self.session.query(ojSeat).filter(
            and_(ojSeat.s_number == s_number, ojSeat.c_id == c_id)
        ).first()

        if result is None:
            raise HTTPException(status_code=404,
                                detail="未找到对应座位")

        return result.s_id

    # 通过s_id找到唯一的s_number和c_id
    def get_s_number_and_c_id_by_s_id(self, s_id):
        result = self.session.query(ojSeat).filter(
            ojSeat.s_id == s_id
        ).first()

        if result is None:
            raise HTTPException(status_code=404,
                                detail="未找到对应座位")

        return result.s_number, result.c_id

    # 修改教室信息(可更新教室描述，座位状态)
    # input: c_name，c_description, c_is_available, s_number, s_tag
    def classroom_edit(self, data: dict):
        # 所有得到的信息取出来
        c_name = data.get("c_name")
        c_seat_num = data.get("c_seat_num")
        c_description = data.get("c_description")
        c_is_available = data.get("c_is_available")

        s_number = data.get("s_number")

        c_id = self.get_c_id_by_c_name(c_name)
        s_id = self.get_s_id_by_s_number_and_c_id(s_number, c_id) if s_number is not None else None

        update_cdata = {}  # 有关教室的更新

        if c_name is not None:

            # if c_seat_num is not None:
            #     update_cdata["c_seat_num"] = c_seat_num
            if c_description is not None:
                update_cdata["c_description"] = c_description
            if c_is_available is not None:
                update_cdata["c_is_available"] = c_is_available

        if update_cdata:
            self.session.query(ojClass).filter(
                ojClass.c_id == c_id
            ).update(update_cdata)
            self.session.commit()

    # 获取当前教室信息
    # input:c_name, usl_id
    def get_classroom_info(self, data: dict):
        c_name = data.get("c_name")
        usl_id = data.get("usl_id")
        c_id = self.get_c_id_by_c_name(c_name)
        data = {}
        # 查出指定教室的全部信息
        qc = self.session.query(ojClass).filter(
            ojClass.c_id == c_id
        ).all()

        if qc:
            c_seat_num = qc[0].c_seat_num
            c_description = qc[0].c_description
        else:
            c_seat_num = c_description = None  # 处理没有结果的情况

        data["c_seat_num"] = c_seat_num
        data["c_description"] = c_description

        # 查出指定教室中全部座位的信息
        qs = self.session.query(ojSeat).filter(
            ojSeat.c_id == c_id
        ).all()
        s_id = [obj.s_id for obj in qs]

        # 查询对应座位的username
        data["usl"] = []
        for i in s_id:
            username = None
            # 判断是否是对应题单的数据
            qlist = self.session.query(ojClassUser).filter(
                ojClassUser.s_id == i and ojClassUser.usl_id == usl_id
            )
            user_obj = qlist.first()

            if user_obj:
                username = user_obj.username  # 访问对象的 username 属性

            s_number, c_id = self.get_s_number_and_c_id_by_s_id(i)
            data["usl"].append([username, s_number])

        return data

    # 查询所有可用教室
    # input: pageNow, pageSize
    def get_available_classroom(self, pageNow=None, pageSize=None):
        res = {"data": []}
        # 求数据总数量
        query = self.session.query(func.count(ojClass.c_id)).filter(
            ojClass.c_is_available == 1
        )
        totalNum = query.scalar()
        res["totalNum"] = totalNum
        if pageNow is None:
            pageNow = 1
        if pageSize is None:
            pageSize = totalNum
        # 求总页数
        totalPage = totalNum // pageSize
        res["totalPage"] = totalPage
        # 列出所有符合条件的数据
        qc = self.session.query(ojClass).filter(
            ojClass.c_is_available == 1
        ).all()

        query = self.session.query(ojClass).filter(
            ojClass.c_is_available == 1
        ).offset((pageNow - 1) * pageSize).limit(pageSize).all()

        for obj in query:
            data = {
                "c_id": obj.c_id,
                "c_seat_num": obj.c_seat_num,
                "c_is_available": obj.c_is_available
            }
            res["data"].append(data)

        return res

    # 新建用户座位名单
    # input:name, groupId
    def create_seat_list(self, data: dict):
        data = self.jsonDumps(data, ["usl_id", "name", "groupId"])
        self.session.add(ojUserSeatList(**data))
        self.session.flush()
        self.session.commit()

    # 编辑用户座位名单和教室座位绑定表(修改username)
    # input:name, groupId, username, c_name, s_number, s_tag
    def edit_seat_list(self, data: dict):
        name = data.get("name")
        groupId = data.get("groupId")

        username = data.get("username")

        c_name = data.get("c_name")
        s_number = data.get("s_number")

        s_tag = data.get("s_tag")
        if s_tag == 0:
            raise HTTPException(status_code=400, detail="此座位不可用")

        # 查usl_id明确要改哪个名单
        q_usl_id = self.session.query(ojUserSeatList).filter(
            and_(ojUserSeatList.name == name, ojUserSeatList.groupId == groupId)
        )
        usl_id = q_usl_id.first().usl_id

        # 查c_id
        q_c_id = self.session.query(ojClass).filter(
            ojClass.c_name == c_name
        )
        c_id = q_c_id.first().c_id

        # 查s_id明确要改哪个座位
        q_s_id = self.session.query(ojSeat).filter(
            and_(ojSeat.c_id == c_id, ojSeat.s_number == s_number)
        )
        s_id = q_s_id.first().s_id

        # 修改对应座位上的username
        update_data = {"username": username}
        self.session.query(ojClassUser).filter(
            and_(ojClassUser.s_id == s_id, ojClassUser.usl_id == usl_id)
        ).update(update_data)
        self.session.commit()

    # 查询用户座位名单的列表user_seat_list
    # input: pageNow, pageSize
    def get_user_seat_list_info(self, pageNow: int, pageSize: int):
        res = {"data": []}
        # 求数据总数量
        query = self.session.query(func.count(ojUserSeatList.usl_id)).filter()
        totalNum = query.scalar()
        res["totalNum"] = totalNum

        # 求总页数
        totalPage = totalNum // pageSize
        res["totalPage"] = totalPage

        # 列出所有符合条件的数据
        qc = self.session.query(ojUserSeatList).filter().all()

        query = self.session.query(ojUserSeatList).filter().offset((pageNow - 1) * pageSize).limit(pageSize).all()

        for obj in query:
            data = {
                "name": obj.name,
                "groupId": obj.groupId,
            }
            res["data"].append(data)

        return res

    # 根据名单名称查询整个名单，教室，座号，助教名称
    # input: name
    def get_all_info(self, name: str):
        res = {"data": []}

        # 根据name查usl_id
        q_usl_id = self.session.query(ojUserSeatList).filter(
            ojUserSeatList.name == name
        )
        usl_id = q_usl_id.first().usl_id

        # 列出所有符合条件的数据
        query = self.session.query(ojClassUser).filter(
            ojClassUser.usl_id == usl_id
        ).all()

        for obj in query:
            data = {}
            username = obj.username
            s_id = obj.s_id

            # 由s_id查询c_id和s_number
            q_c_id_s_number = self.session.query(ojSeat).filter(
                ojSeat.s_id == obj.s_id
            ).first()
            c_id = q_c_id_s_number.c_id
            s_number = q_c_id_s_number.s_number

            # 查询c_name
            q_c_name = self.session.query(ojClass).filter(
                ojClass.c_id == c_id
            ).first()
            c_name = q_c_name.c_name

            # 查询TA_name
            q_TA_name = self.session.query(ojClassManageUser).filter(
                and_(ojClassManageUser.usl_id == usl_id, ojClassManageUser.c_id == c_id)
            ).first()
            TA_name = q_TA_name.TA_name
            data = {
                "username": username,
                "c_name": c_name,
                "s_number": s_number,
                "TA_name": TA_name
            }
            res["data"].append(data)

        return res

    # 查询单人信息
    # input: groupId, username
    def get_single_user_info(self, groupId: int, username: int):
        res = {"data": []}

        # 根据groupId查usl_id
        q_usl_id = self.session.query(ojUserSeatList).filter(
            ojUserSeatList.groupId == groupId
        )
        usl_id = q_usl_id.first().usl_id

        # 列出所有符合条件的数据
        query = self.session.query(ojClassUser).filter(
            and_(ojClassUser.usl_id == usl_id, ojClassUser.username == username)
        ).all()

        for obj in query:
            s_id = obj.s_id
            # 由s_id查询c_id和s_number
            q_c_id_s_number = self.session.query(ojSeat).filter(
                ojSeat.s_id == obj.s_id
            ).first()
            c_id = q_c_id_s_number.c_id
            s_number = q_c_id_s_number.s_number

            # 查询c_name
            q_c_name = self.session.query(ojClass).filter(
                ojClass.c_id == c_id
            ).first()
            c_name = q_c_name.c_name if q_c_name.c_name is not None else None

            # 查询TA_name
            q_TA_name = self.session.query(ojClassManageUser).filter(
                and_(ojClassManageUser.usl_id == usl_id, ojClassManageUser.c_id == c_id)
            ).first()
            TA_name = q_TA_name.TA_name if q_TA_name.TA_name is not None else None
            data = {
                "username": username,
                "c_name": c_name,
                "s_number": s_number,
                "TA_name": TA_name
            }
            res["data"].append(data)

        return res

    # 查询座位IP
    # input: groupId, username
    def search_s_ip(self, data: dict):

        groupId = data.get("groupId")
        username = data.get("username")

        # 根据groupId查usl_id
        q_usl_id = self.session.query(ojUserSeatList).filter(
            ojUserSeatList.groupId == groupId
        )
        usl_id = q_usl_id.first().usl_id

        # 根据usl_id和username查s_id
        query = self.session.query(ojClassUser).filter(
            and_(ojClassUser.usl_id == usl_id, ojClassUser.username == username)
        )
        s_id = query.first().s_id

        q_ip = self.session.query(ojSeat).filter(
            ojSeat.s_id == s_id
        )
        s_ip = q_ip.first().s_ip
        return s_ip
