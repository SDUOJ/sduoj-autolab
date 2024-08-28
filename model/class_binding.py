from io import BytesIO

import pandas as pd
from fastapi import HTTPException, File, UploadFile
from sqlalchemy import and_, func, delete

from db import dbSession, ojClass, ojSeat, ojClassUser, ojUserSeatList, ojClassManageUser


# /model/class_binding.py ------------------------------------------
class classBindingModel(dbSession):

    # 新建教室和座位
    # input: c_name, c_seat_num, c_description ,address ,[不可用的s_number]
    def classroom_create(self, data: dict):
        c_name = data.get("c_name")
        c_seat_num = data.get("c_seat_num")
        c_description = data.get("c_description")
        c_is_available = data.get("c_is_available")
        address = data.get("address")
        # 获取不可用座位
        no_use_seat = data.get("no_use_seat", [])
        data = {
            "c_name": c_name,
            "c_seat_num": c_seat_num,
            "c_description": c_description,
            "c_is_available": c_is_available,
            "address": address
        }
        new_class = ojClass(**data)
        self.session.add(new_class)
        self.session.flush()
        self.session.commit()

        # 获取插入记录的c_id
        c_id = new_class.c_id

        for i in range(1, c_seat_num + 1):
            seatData = {
                "s_number": i,
                "c_id": c_id,
                "s_tag": 1 if i not in no_use_seat else 0,
                "s_ip": None
            }
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
    # input: c_name，c_description, c_is_available,[不可用的s_number],address
    def classroom_edit(self, data: dict):
        # 所有得到的信息取出来
        c_name = data.get("c_name")
        c_description = data.get("c_description")
        c_is_available = data.get("c_is_available")
        address = data.get("address")

        # 取出不可用的座位
        no_use_seat = data.get("s_number", [])

        update_cdata = {}  # 有关教室的更新
        update_sdata = {"no_use_seat_id": []}  # 有关座位的更新

        c_id = self.get_c_id_by_c_name(c_name)
        for i in no_use_seat:
            s_id = self.get_s_id_by_s_number_and_c_id(i, c_id) if i is not None else None
            update_sdata["no_use_seat_id"].append(s_id)

        if c_name is not None:

            # if c_seat_num is not None:
            #     update_cdata["c_seat_num"] = c_seat_num
            if c_description is not None:
                update_cdata["c_description"] = c_description
            if c_is_available is not None:
                update_cdata["c_is_available"] = c_is_available
            if address is not None:
                update_cdata["address"] = address

        if update_cdata:
            self.session.query(ojClass).filter(
                ojClass.c_id == c_id
            ).update(update_cdata)
            self.session.commit()
        if update_sdata:
            for s_id in update_sdata["no_use_seat_id"]:
                if s_id is not None:
                    self.session.query(ojSeat).filter(
                        ojSeat.s_id == s_id
                    ).update({"s_tag": 0})
                    self.session.commit()

    # 获取当前教室信息
    # input:c_name, usl_id
    def get_classroom_info(self, data: dict):
        c_name = data.get("c_name")
        # usl_id = data.get("usl_id")
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
        s_number = [obj.s_number for obj in qs if obj.s_tag == 1]
        data["s_number"] = s_number
        # # 查询对应座位的username
        # data["usl"] = []
        # for i in s_id:
        #     username = None
        #     # 判断是否是对应题单的数据
        #     qlist = self.session.query(ojClassUser).filter(
        #         ojClassUser.s_id == i and ojClassUser.usl_id == usl_id
        #     )
        #     user_obj = qlist.first()
        #
        #     if user_obj:
        #         username = user_obj.username  # 访问对象的 username 属性
        #
        #     s_number, c_id = self.get_s_number_and_c_id_by_s_id(i)
        #     data["usl"].append([username, s_number])

        return data

    # 查询所有可用教室
    # input: pageNow, pageSize
    def get_available_classroom(self, pageNow=None, pageSize=None):
        res = {"rows": []}
        # 求数据总数量
        query = self.session.query(func.count(ojClass.c_id)).filter(
            ojClass.c_is_available == 1
        )
        totalNum = query.scalar()

        if totalNum == 0:
            return HTTPException(status_code=400, detail="没有可用教室")

        res["totalNum"] = totalNum
        if pageNow is None:
            pageNow = 1
        if pageSize is None:
            pageSize = totalNum
        # 求总页数
        totalPage = totalNum // pageSize
        res["totalPage"] = totalPage

        query = self.session.query(ojClass).filter(
            ojClass.c_is_available == 1
        ).offset((pageNow - 1) * pageSize).limit(pageSize).all()

        for obj in query:
            # 输出的s_number表示不可用的座位
            data = {
                "c_id": obj.c_id,
                "c_name": obj.c_name,
                "c_seat_num": obj.c_seat_num,
                "c_description": obj.c_description,
                "address": obj.address,
                "s_number": []
            }
            qc = self.session.query(ojSeat).filter(
                ojSeat.c_id == obj.c_id
            ).all()
            for i in qc:
                if i.s_tag == 0:
                    data["s_number"].append(i.s_number)
            res["rows"].append(data)

        return res

    # 新建用户座位名单
    # input:name, groupId
    def create_seat_list(self, data: dict):
        usl_id = data.get("usl_id")
        name = data.get("name")
        groupId = data.get("groupId")
        data = {
            "usl_id": usl_id,
            "name": name,
            "groupId": groupId
        }
        self.session.add(ojUserSeatList(**data))
        self.session.flush()
        self.session.commit()

    # 编辑用户座位名单和教室座位绑定表
    # input:已经存在的usl_id, name, groupId
    def edit_seat_list(self, data: dict):
        usl_id = data.get("usl_id")
        name = data.get("name")
        groupId = data.get("groupId")

        data = {
            "usl_id": usl_id,
            "name": name,
            "groupId": groupId
        }
        self.session.query(ojUserSeatList).filter(
            ojUserSeatList.usl_id == usl_id
        ).update(data)
        self.session.commit()

    # 查询用户座位名单的列表user_seat_list
    # input: pageNow, pageSize
    def get_user_seat_list_info(self, pageNow: int = None, pageSize: int = None):
        data = {"res": []}
        # 求数据总数量
        query = self.session.query(func.count(ojUserSeatList.usl_id)).filter()
        totalNum = query.scalar()
        data["totalNum"] = totalNum

        if pageNow is None:
            pageNow = 1
        if pageSize is None:
            pageSize = totalNum
        if totalNum == 0:
            return HTTPException(status_code=400, detail="没有数据")

        # 求总页数
        totalPage = totalNum // pageSize
        data["totalPage"] = totalPage

        # 列出所有符合条件的数据
        qc = self.session.query(ojUserSeatList).filter().all()

        query = self.session.query(ojUserSeatList).filter().offset((pageNow - 1) * pageSize).limit(pageSize).all()

        for obj in query:
            res = {
                "name": obj.name,
                "groupId": obj.groupId,
            }
            data["res"].append(res)

        return data

    # 根据名单名称查询整个名单，教室，座号，助教名称
    # input: name, pageNow, pageSize
    def get_all_info(self, name: str, pageNow: int = None, pageSize: int = None):
        res = {"data": []}

        # 根据name查usl_id
        q_usl_id = self.session.query(ojUserSeatList).filter(
            ojUserSeatList.name == name
        )
        usl_id = q_usl_id.first().usl_id

        # 列出所有符合条件的数据
        query = self.session.query(func.count(ojClassUser.usl_id)).filter(
            ojClassUser.usl_id == usl_id
        )
        totalNum = query.scalar()
        res["totalNum"] = totalNum

        if pageNow is None:
            pageNow = 1
        if pageSize is None:
            pageSize = totalNum
        if totalNum == 0:
            return HTTPException(status_code=400, detail="没有数据")

        # 求总页数
        totalPage = totalNum // pageSize
        res["totalPage"] = totalPage

        query = self.session.query(ojClassUser).filter(
            ojClassUser.usl_id == usl_id
        ).offset((pageNow - 1) * pageSize).limit(pageSize).all()

        for obj in query:
            username = obj.username
            s_id = obj.s_id
            # 由s_id查询c_id和s_number
            q_c_id_s_number = self.session.query(ojSeat).filter(
                ojSeat.s_id == s_id
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
                "usl_id": usl_id,
                "username": username,
                "c_name": c_name,
                "s_number": s_number,
                "TA_name": TA_name
            }
            res["data"].append(data)

        return res

    # 查询单人信息
    # input: groupId, username
    def get_single_user_info(self, groupId: int, username: int, pageNow: int = None, pageSize: int = None):
        res = {"data": []}

        # 根据groupId查usl_id
        q_usl_id = self.session.query(ojUserSeatList).filter(
            ojUserSeatList.groupId == groupId
        )
        usl_id = q_usl_id.first().usl_id

        # 列出所有符合条件的数据
        query = self.session.query(func.count(ojClassUser.usl_id)).filter(
            and_(ojClassUser.usl_id == usl_id, ojClassUser.username == username)
        )

        totalNum = query.scalar()
        res["totalNum"] = totalNum

        if pageNow is None:
            pageNow = 1
        if pageSize is None:
            pageSize = totalNum
        if totalNum == 0:
            return HTTPException(status_code=400, detail="没有数据")

        # 求总页数
        totalPage = totalNum // pageSize
        res["totalPage"] = totalPage

        query = self.session.query(ojClassUser).filter(
            and_(ojClassUser.usl_id == usl_id, ojClassUser.username == username)
        ).offset((pageNow - 1) * pageSize).limit(pageSize).all()

        for obj in query:
            s_id = obj.s_id
            # 由s_id查询c_id和s_number
            q_c_id_s_number = self.session.query(ojSeat).filter(
                ojSeat.s_id == obj.s_id
            ).first()
            c_id = q_c_id_s_number.c_id
            s_number = q_c_id_s_number.s_number

            # 查询c_name和address
            q_c_name = self.session.query(ojClass).filter(
                ojClass.c_id == c_id
            ).first()
            c_name = q_c_name.c_name if q_c_name.c_name is not None else None
            address = q_c_name.address

            # 查询TA_name
            q_TA_name = self.session.query(ojClassManageUser).filter(
                and_(ojClassManageUser.usl_id == usl_id, ojClassManageUser.c_id == c_id)
            ).first()
            TA_name = q_TA_name.TA_name if q_TA_name.TA_name is not None else None
            data = {
                "username": username,
                "c_name": c_name,
                "s_number": s_number,
                "TA_name": TA_name,
                "address": address
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

    # 查询教室名是否已存在
    # input: c_name
    def c_name_is_exist(self, data: dict):
        c_name = data.get("c_name")
        query = self.session.query(ojClass).filter()
        for i in query:
            if i.c_name == c_name:
                return True

        return False

    # 查询名单的助教
    # input: usl_id, pageNow, pageSize
    def check_TA_info(self, usl_id: int, pageNow: int = None, pageSize: int = None):
        # 求数据总数量
        query = self.session.query(func.count(ojClassManageUser.TA_id)).filter(
            ojClassManageUser.usl_id == usl_id
        )
        totalNum = query.scalar()

        if totalNum == 0:
            return HTTPException(status_code=400, detail="没有助教")

        if pageNow is None:
            pageNow = 1
        if pageSize is None:
            pageSize = totalNum

        totalPage = totalNum // pageSize

        query = self.session.query(ojClassManageUser).filter(
            ojClassManageUser.usl_id == usl_id
        ).offset((pageNow - 1) * pageSize).limit(pageSize).all()

        res = {
            "rows": [],
            "totalNum": totalNum,
            "totalPage": totalPage
        }
        for obj in query:
            TA_id = obj.TA_id
            TA_name = obj.TA_name

            c_id = obj.c_id
            c_name = self.session.query(ojClass).filter(
                ojClass.c_id == c_id
            ).first().c_name
            address = self.session.query(ojClass).filter(
                ojClass.c_id == c_id
            ).first().address
            data = {
                "TA_id": TA_id,
                "TA_name": TA_name,
                "c_name": c_name,
                "address": address
            }
            res["rows"].append(data)
        return res

    # 查询整个名单的用户，教室，座号
    def check_list_info(self, usl_id: int, pageNow: int = None, pageSize: int = None):
        query = self.session.query(func.count(ojClassUser.usl_id)).filter(
            ojClassUser.usl_id == usl_id
        )
        res = {"data": []}
        totalNum = query.scalar()

        if totalNum == 0:
            return HTTPException(status_code=400, detail="没有数据")

        if pageNow is None:
            pageNow = 1
        if pageSize is None:
            pageSize = totalNum

        totalPage = totalNum // pageSize
        res["totalNums"] = totalNum
        res["totalPages"] = totalPage

        query = self.session.query(ojClassUser).filter(
            ojClassUser.usl_id == usl_id
        ).offset((pageNow - 1) * pageSize).limit(pageSize).all()

        for obj in query:
            tempSid = obj.s_id

            c_id = self.session.query(ojSeat).filter(
                ojSeat.s_id == tempSid
            ).first().c_id
            c_name = self.session.query(ojClass).filter(
                ojClass.c_id == c_id
            ).first().c_name

            Id = obj.id
            username = obj.username
            s_id = obj.s_id

            s_number = self.session.query(ojSeat).filter(
                ojSeat.s_id == s_id
            ).first().s_number

            res["data"].append({
                "id": Id,
                "username": username,
                "c_name": c_name,
                "s_number": s_number
            })
        return res

    # 新建助教
    # input: TA_name, c_name, usl_id
    def create_TA(self, data: dict):
        TA_name = data.get("TA_name")
        c_name = data.get("c_name")
        usl_id = data.get("usl_id")

        c_id = self.get_c_id_by_c_name(c_name)

        data = {
            "TA_name": TA_name,
            "usl_id": usl_id,
            "c_id": c_id
        }

        self.session.add(ojClassManageUser(**data))
        self.session.flush()
        self.session.commit()

    # 删除助教
    # input:TA_name
    def delete_TA(self, data: dict):
        TA_name = data.get("TA_name")
        query = self.session.query(ojClassManageUser).filter(
            ojClassManageUser.TA_name == TA_name
        )
        for obj in query:
            self.session.delete(obj)
            self.session.flush()
            self.session.commit()

    # 编辑助教
    # input: TA_id, TA_name, c_name
    def edit_TA(self, data: dict):
        TA_id = data.get("TA_id")
        TA_name = data.get("TA_name")
        c_name = data.get("c_name")
        c_id = self.get_c_id_by_c_name(c_name)

        data = {
            "TA_id": TA_id,
            "TA_name": TA_name,
            "c_id": c_id
        }
        self.session.query(ojClassManageUser).filter(
            and_(ojClassManageUser.TA_id == TA_id, ojClassManageUser.TA_name == TA_name)
        ).update(data)
        self.session.commit()

    # 批量绑定IP
    # input:Excel文件，第一列为c_id，第二列为s_number，第三列为IP
    # （每次绑定都先删除之前的IP，然后绑定，从而实现删除和编辑的功能）
    async def multi_ip_binding(self, file: UploadFile):
        # 删除之前的ip
        self.session.query(ojSeat).update({"s_ip": None})

        # 使用 BytesIO 读取上传的文件
        file_content = await file.read()  # 读取文件内容到内存
        excel_stream = BytesIO(file_content)  # 创建 BytesIO 对象

        # 读取上传的 Excel 文件
        df = pd.read_excel(excel_stream)

        # 绑定新的 IP
        for index, row in df.iterrows():
            c_id = row[0]
            s_number = row[1]
            ip = row[2]
            self.session.query(ojSeat).filter(
                and_(ojSeat.c_id == c_id, ojSeat.s_number == s_number)
            ).update({"s_ip": ip})

        self.session.commit()

    # 批量绑定用户座次
    # input: excel文件,第一列为username，第二列为c_id，第三列为s_number
    # （每次绑定都先删除之前的绑定信息，然后绑定，从而实现删除和编辑的功能）
    async def multi_seats_binding(self, file: UploadFile):
        # 删除之前的绑定信息
        stmt = delete(ojClassUser)
        self.session.execute(stmt)
        self.session.commit()

        # 使用 BytesIO 读取上传的文件
        file_content = await file.read()  # 读取文件内容到内存
        excel_stream = BytesIO(file_content)  # 创建 BytesIO 对象

        # 读取上传的 Excel 文件
        df = pd.read_excel(excel_stream)

        # 绑定新的信息
        for index, row in df.iterrows():
            usl_id = row[0]
            username = row[1]
            c_id = row[2]
            s_number = row[3]

            s_id = self.session.query(ojSeat).filter(
                and_(ojSeat.c_id == c_id, ojSeat.s_number == s_number)
            ).first().s_id

            # 使用merge()方法插入或更新记录
            record = ojClassUser(
                usl_id=usl_id,
                username=username,
                s_id=s_id
            )

            self.session.merge(record)
            self.session.commit()
