from fastapi import HTTPException
import db
from model.base import baseModel, listQuery, baseQuery
from db import dbSession, ScreenRecord
from datetime import datetime

class screenRecordModel(dbSession, baseModel, listQuery, baseQuery):

    # 根据token找到path
    def get_path_by_token(self, token: str):
        result = self.session.query(ScreenRecord).filter(ScreenRecord.token == token).first()
        return result

    def add_record(self, data: dict):
        record = ScreenRecord(**data)  # 创建一个 ScreenRecord 实例
        self.session.add(record)
        self.session.commit()


    # 根据token更新time
    def add_frame_by_token(self, token: str, datetime_: dict):
        self.session.query(ScreenRecord).filter(
            ScreenRecord.token == token
        ).update(datetime_)
        self.session.commit()