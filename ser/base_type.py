from typing import List

from pydantic import BaseModel


class page(BaseModel):
    pageSize: int
    pageNow: int

    def offset(self):
        return (max(1, self.pageNow) - 1) * self.pageSize

    def limit(self):
        return self.pageSize


class pageResult(BaseModel):
    pageIndex: int
    pageSize: int
    totalNum: int
    rows: List


class problem_pid(BaseModel):
    pid: int


class userSessionType(BaseModel):
    userId: int
    username: str
    nickname: str
    email: str
    studentId: str
    roles: List[str]
    groups: List[int]
    ipv4: str
    userAgent: str
