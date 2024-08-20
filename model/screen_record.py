import db
from db import dbSession, ScreenRecord
from model.base import baseModel, listQuery, baseQuery


class screenRecordModel(dbSession, baseModel, listQuery, baseQuery):

    # 根据token找到path
    def get_path_by_token(self, token: str):
        result = self.session.query(ScreenRecord).filter(ScreenRecord.token == token).first()
        return result

    # 增加记录
    def add_record(self, data: dict):
        record = ScreenRecord(**data)  # 创建一个 ScreenRecord 实例
        self.session.add(record)
        self.session.commit()

    # 根据token更新time
    def add_frame_by_token(self, token: str, data: dict):
        self.session.query(ScreenRecord).filter(
            ScreenRecord.token == token
        ).update(data)
        self.session.commit()

    # 查询视频列表
    def get_video_list(self, data: int):
        result = self.session.query(ScreenRecord).filter(
            ScreenRecord.bs_id == data,
        ).all()
        return result

    # 通过token删除记录和视频
    def delete_by_token(self, token: str):
        result = self.session.query(ScreenRecord).filter(ScreenRecord.token == token).first()
        if result:
            # 删除数据库中的记录
            self.session.delete(result)
            self.session.commit()

    # 获取有录屏记录的题单列表
    def get_ps_list(self):
        query = self.session.query(ScreenRecord, db.ProblemSet).join(
            db.ProblemSet, ScreenRecord.bs_id == db.ProblemSet.psid
        ).group_by(db.ProblemSet.psid)
        return query.all()

    # 获取题单类型
    def get_ps_type(self, bs_id: int):
        result = self.session.query(db.ProblemSet)
        return result.filter(db.ProblemSet.psid == bs_id).first()
