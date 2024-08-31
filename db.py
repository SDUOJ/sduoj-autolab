import copy
import json

from sqlalchemy import Column, ForeignKey, Index, UniqueConstraint
from sqlalchemy import create_engine
from sqlalchemy import func
from sqlalchemy.dialects.mysql import INTEGER, VARCHAR, DATETIME, LONGTEXT, \
    FLOAT, BIGINT, TINYINT
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()


class ProblemObjective(Base):
    __tablename__ = 'problem_objective'

    pid = Column(INTEGER, primary_key=True)

    # 0 单选， 1 多选， 2 不定项
    type = Column(INTEGER, nullable=False)

    # json 字符串，{"description": "", "choice": ["", "", ""]}
    content = Column(LONGTEXT, nullable=False)

    # json 字符串，["A", "B"]
    answer = Column(VARCHAR(63), nullable=False)

    # 创建者
    username = Column(VARCHAR(63), nullable=False)
    # 创建时间
    create_time = Column(DATETIME, nullable=False, server_default=func.now())


class ProblemSubjective(Base):
    __tablename__ = 'problem_subjective'

    pid = Column(INTEGER, primary_key=True)

    # 0 文本， 1 文件
    type = Column(INTEGER, nullable=False)

    # json 字符串，提交配置信息，{
    #  最大字数
    # "maxCount": 0,
    #                文件名       文件大小         文件类型
    # "fileList": [{"name": "", "maxSizeMB": 0, "fileType": ""}, ]
    #                   得分项       分数         参考答案
    # "judgeConfig": [{"name": "", "score": "", "answer": ""}, {}]
    # }
    config = Column(LONGTEXT, nullable=False)

    # 题目描述
    description = Column(LONGTEXT, nullable=False)

    # 创建者
    username = Column(VARCHAR(63), nullable=False)
    # 创建时间
    create_time = Column(DATETIME, nullable=False, server_default=func.now())


class ProblemGroup(Base):
    __tablename__ = 'problem_group'

    gid = Column(INTEGER, primary_key=True)
    name = Column(VARCHAR(63), nullable=False)

    # 0 选择题， 1 主观题， 2 编程题
    type = Column(INTEGER, nullable=False)

    # json 字符串， [
    # {"pid": 0, "score": 0, "submitLimit": 0, "antiCheatingRate": 0.85},
    # ]
    problemInfo = Column(LONGTEXT, nullable=False)

    # 管理组
    manageGroupId = Column(INTEGER, nullable=True)
    # 创建者
    username = Column(VARCHAR(63), nullable=False)
    # 创建时间
    create_time = Column(DATETIME, nullable=False, server_default=func.now())


class ProblemSet(Base):
    __tablename__ = 'problem_set'

    psid = Column(INTEGER, primary_key=True)

    name = Column(VARCHAR(63), nullable=False)

    # 题单描述
    description = Column(LONGTEXT, nullable=True)

    # 0 练习模式，1 考试模式
    type = Column(INTEGER, nullable=False)

    # json 字符串， [
    #   {
    #       "gid": 0, "name": "", "score": 0,
    #       "timeSetting": {"tm_start": 0, "tm_end": 0, "punishment": 14}
    #   },
    # ]
    groupInfo = Column(LONGTEXT, nullable=False)

    # json 字符串，{
    #   "useSameSE": false,                 是否使用相同的起止时间
    #
    #   在题单报告中，报告相关配置
    #   开启报告之后，直接按照各个题组的时间，若结束，则给出答案，分数，
    #       日志，显示查重结果，在 Overview 中显示得分等，在具体的题目中显示作答详情
    #
    #   "showReport": true,                 是否开启报告
    #   "showObjectiveAnswer": false,       是否显示客观题参考答案
    #   "showSubjectiveAnswer": false,      是否显示主观题参考答案
    #   "showSubjectiveJudgeLog": false,    是否显示主观题评测日志
    #
    #   补题设定
    #   "usePractice": true,
    #   两个变量：e，p 表示限时成绩与练习成绩，书写 python 表达式计算
    #   "practiceScoreCalculate": "max(e, e + (p - e) * 0.85)",
    #   "practiceTimeSetting": {"tm_start": 0, "tm_end": 0, "punishment": 14}
    #
    #   "showScoreInExam":,         是否显示分值（考试中）
    #   "showProgramScoreInExam":,  是否显示编程题得分（考试中）
    #   "mergerSubjectiveGroup": bool,
    # }
    config = Column(LONGTEXT, nullable=False)

    global_score = Column(FLOAT, nullable=True)

    # 开始与结束时间
    tm_start = Column(DATETIME, nullable=True)
    tm_end = Column(DATETIME, nullable=True)

    # 创建者
    username = Column(VARCHAR(63), nullable=False)
    manageGroupId = Column(INTEGER, nullable=True)

    # 绑定到一个 group
    groupId = Column(INTEGER, nullable=False)
    # 在 group 当中的标签
    tag = Column(VARCHAR(63), nullable=False)

    __table_args__ = (
        Index(
            'ix_problem_set_groupId_tag_type',
            "groupId", "tag", "type"),
    )


class ProblemSetAnswerSheet(Base):
    __tablename__ = 'problem_set_answer_sheet'

    asid = Column(INTEGER, primary_key=True)

    # 对应用户
    username = Column(VARCHAR(63), nullable=False)

    # 对应题单
    psid = Column(
        INTEGER, ForeignKey("problem_set.psid"), nullable=False, index=True
    )

    finish = Column(INTEGER)
    finish_time = Column(DATETIME, nullable=True)

    # 监考截屏的 token
    # pic_token = Column(VARCHAR(63))
    # pic_heart_time = Column(DATETIME, nullable=True)

    # 提交 IP
    submit_ip_set = Column(LONGTEXT, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "username", "psid",
            name="un_problem_set_answer_sheet_username_psid"),
        Index(
            'ix_problem_set_answer_sheet_username_psid',
            "username", "psid"),
    )


class ProblemSetAnswerSheetDetail(Base):
    __tablename__ = 'problem_set_answer_sheet_detail'
    asd_id = Column(INTEGER, primary_key=True)
    asid = Column(INTEGER, ForeignKey("problem_set_answer_sheet.asid"),
                  nullable=True, index=True)

    gid = Column(INTEGER)
    pid = Column(INTEGER)

    #  "submission": ["sid1", "sid2"],
    #  "choice": [],
    #  "str": [],
    #  "fileList": ["fid1", "fid2"]
    answer = Column(LONGTEXT, nullable=True)
    collect = Column(INTEGER, default=0)
    mark = Column(VARCHAR(255), nullable=True)

    # 提交时间
    tm_answer_submit = Column(DATETIME, nullable=True)

    judgeLock_username = Column(VARCHAR(63), nullable=True)

    # [{"name": "", "score": 0, "jScore": 0}]
    judgeLog = Column(LONGTEXT, nullable=True)

    judgeComment = Column(LONGTEXT, nullable=True)

    # {"summary": 0.92, "detail": [{"id": "", "rate": 0.92}]}
    antiCheatingResult = Column(LONGTEXT, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "asid", "gid", "pid",
            name="un_problem_set_answer_sheet_detail_asid_gid_pid"),
        Index(
            'ix_problem_set_answer_sheet_detail_asid_gid_pid',
            "asid", "gid", "pid", ),
    )


class SignIn(Base):
    __tablename__ = 'sign_in'

    sid = Column(INTEGER, primary_key=True)

    name = Column(VARCHAR(63), nullable=False)
    tag = Column(VARCHAR(63), nullable=False)

    # 签到的开始与结束时间
    tm_start = Column(DATETIME, nullable=True)
    tm_end = Column(DATETIME, nullable=True)

    # 创建者
    username = Column(VARCHAR(63), nullable=False)
    manageGroupId = Column(INTEGER, nullable=True)

    # 创建时间
    create_time = Column(DATETIME, nullable=False, server_default=func.now())

    # 绑定到一个 group
    groupId = Column(INTEGER, nullable=False)

    global_score = Column(FLOAT, nullable=True)


class SignInUser(Base):
    __tablename__ = "sign_in_user"

    suid = Column(INTEGER, primary_key=True)

    sid = Column(INTEGER, primary_key=True)
    username = Column(VARCHAR(63), nullable=False)

    # 创建时间
    create_time = Column(DATETIME, nullable=False, server_default=func.now())

    # 是否签到成功
    # 0 未签到  1 已签到  2 等待审核
    signIn = Column(INTEGER, default=0)

    # 可以上传请假条
    file_id = Column(INTEGER, nullable=True)


class ojClass(Base):
    # 教室信息表：用于记录所有的教室信息
    __tablename__ = 'oj_class'

    # 教室id
    c_id = Column(INTEGER, primary_key=True, nullable=False, unique=True, index=True, autoincrement=True)

    # 教室名
    c_name = Column(VARCHAR(50), nullable=False, unique=True, index=True)

    # 座位数量
    c_seat_num = Column(INTEGER, nullable=False)

    # 教室描述
    c_description = Column(LONGTEXT, nullable=False)

    # 教室是否可用，0不可用1可用
    c_is_available = Column(TINYINT, nullable=False)

    # 区分物理地址(哪个楼上)
    address = Column(VARCHAR(50), nullable=False)


class ojSeat(Base):
    # 座位信息表：用于记录所有的座位信息
    __tablename__ = 'oj_seat'

    # 座位id，唯一标识
    s_id = Column(BIGINT, primary_key=True, nullable=False, unique=True, index=True)

    # 座位号
    s_number = Column(TINYINT, nullable=False)

    # 所属教室id(外键)
    c_id = Column(INTEGER, ForeignKey("oj_class.c_id"), nullable=False, index=True)

    # 座位标签(如正常，损坏等)
    s_tag = Column(TINYINT, nullable=False)

    # 座位ip
    s_ip = Column(VARCHAR(20), unique=True)


class ojUserSeatList(Base):
    # 学生座位名单表，绑定学生与座位
    __tablename__ = 'oj_user_seat_list'

    # 学生座位名单id
    usl_id = Column(BIGINT, primary_key=True, nullable=False, unique=True, index=True, autoincrement=True)

    # 名单名称
    name = Column(VARCHAR(50), nullable=False, unique=True, index=True)

    # 绑定组信息
    groupId = Column(BIGINT, nullable=False)


class ojClassUser(Base):
    # 教室座位绑定表，用于记录教室用户的绑定信息
    __tablename__ = 'oj_class_user'

    # id,主键
    id = Column(BIGINT, primary_key=True, nullable=False, unique=True, index=True)

    # usl_id,外键
    usl_id = Column(BIGINT, ForeignKey("oj_user_seat_list.usl_id"), nullable=False)

    # 用户名(学号)
    username = Column(VARCHAR(50), nullable=False, index=True)

    # 座位id,外键,唯一标识
    s_id = Column(BIGINT, ForeignKey("oj_seat.s_id"), nullable=False)


class ojClassManageUser(Base):
    # 教室助教绑定表，用于记录教室助教的绑定信息
    __tablename__ = 'oj_class_manage_user'

    # 助教id,主键
    TA_id = Column(INTEGER, primary_key=True, nullable=False, autoincrement=True)

    # 助教姓名
    TA_name = Column(VARCHAR(20), nullable=False)

    # 学生座位名单id,外键
    usl_id = Column(BIGINT, ForeignKey("oj_user_seat_list.usl_id"), nullable=False)

    # 教室id,外键
    c_id = Column(INTEGER, ForeignKey("oj_class.c_id"), nullable=False)


class ScreenRecord(Base):
    # 录屏表，用于记录录屏信息
    __tablename__ = "screen_record"

    # 视频唯一凭证
    token = Column(VARCHAR(50), nullable=False, primary_key=True, unique=True)  # 假设token应该是唯一的

    # 业务类型 业务ID
    bs_type = Column(INTEGER, nullable=False)
    bs_id = Column(INTEGER, nullable=False)

    # 视频存储路径
    v_path = Column(VARCHAR(200), nullable=False)

    # 用户id
    u_id = Column(INTEGER, nullable=False)
    u_name = Column(VARCHAR(10), nullable=False)

    # 创建时间 最后修改时间
    start_time = Column(DATETIME, nullable=False)
    modify_time = Column(DATETIME, nullable=False)

    cnt_frame = Column(INTEGER, nullable=False)


class ojSign(Base):
    __tablename__ = "oj_sign"
    sg_id = Column(INTEGER, primary_key=True, autoincrement=True, comment="签到id")

    # 签到模式
    mode = Column(INTEGER, nullable=False, comment="签到模式")

    # 用户组id
    group_id = Column(INTEGER, nullable=False, comment="用户组Id")

    # 管理组id
    m_group_id = Column(INTEGER, nullable=False, comment="管理组id")

    # 创造时间
    u_gmt_create = Column(DATETIME, nullable=False, server_default=func.now(), comment="创建时间")

    # 修改时间
    u_gmt_modified = Column(DATETIME, nullable=False, comment="最后修改时间")

    # 签到标签
    title = Column(VARCHAR(63), nullable=False, comment="签到标签")

    # 开始时间
    gmtStart = Column(DATETIME, nullable=False, comment="签到开始时间")

    # 结束时间
    gmtEnd = Column(DATETIME, nullable=False, comment="签到结束时间")

    # 是否指定座位
    # 1 绑定  0 未绑定
    seat_bind = Column(INTEGER, nullable=False, comment="是否绑定座位：1 绑定  0 未绑定")

    # 名单id
    usl_id = Column(INTEGER, nullable=True, comment="名单id")

    #是否已被删除
    sign_is_deleted = Column(INTEGER, default=0, comment="是否已被删除：0 否  1 是")

# 用户签到表
class ojSignUser(Base):
    __tablename__ = "oj_sign_user"
    # 学生签到id
    sg_u_id = Column(INTEGER, primary_key=True, autoincrement=True, comment="学生签到id")

    # 座位号
    seat_id = Column(INTEGER, comment="座位号")

    # 学生用户名
    username = Column(VARCHAR(63), nullable=False, comment="学生用户名")

    # 签到id
    sg_id = Column(INTEGER, ForeignKey("oj_sign.sg_id"), nullable=False, index=True, comment="签到id")

    # 签到时间
    sg_time = Column(DATETIME, nullable=True, comment="签到时间")

    # 签到唯一凭证
    token = Column(VARCHAR(63), nullable=True, unique=True, comment="签到唯一凭证")

    # 请假信息
    sg_user_message = Column(LONGTEXT, nullable=True, comment="请假信息")

    # 是否通过审批
    # 1 通过  0 未通过  none 审批中  2 无请假
    sg_absence_pass = Column(INTEGER, nullable=True, comment="审批信息：1 通过  0 未通过  none 审批中")

    #登录ip地址
    ip = Column(VARCHAR(63), nullable=True, unique=True, comment="登录ip地址")

    #是否已经被删除
    is_deleted = Column(INTEGER, nullable=True, comment="是否被删除")

    # 让sg_id  username  seat_id 联合唯一
    _table_aegs_ = (
        UniqueConstraint("sg_id", "username", "seat_id", name="u_id_seat"),
    )


from const import Mysql_addr, Mysql_user, Mysql_pass, Mysql_db

link = "mysql+pymysql://{}:{}@{}/{}".format(
    Mysql_user, Mysql_pass, Mysql_addr, Mysql_db)


def init_db():
    engine = create_engine(
        link,
        encoding="utf-8",
        echo=True
    )
    Base.metadata.create_all(engine)


class dbSession:
    session = None

    def __init__(self):
        engine = create_engine(
            link,
            encoding="utf-8"
        )
        DBSession = sessionmaker(bind=engine)
        self.session = DBSession()

    def getSession(self):
        return self.session

    def jsonDumps(self, data, keys):
        for key in keys:
            if key in data and data[key] is not None:
                data[key] = json.dumps(data[key])
        return data

    def jsonLoads(self, data, keys):
        for key in keys:
            if key in data and data[key] is not None:
                data[key] = json.loads(data[key])
        return data

    # 待处理的查出数据，要转换的时间数据，要删除的数据
    def dealData(self, data, timeKeys=None, popKeys=None):
        from utilsTime import getMsTime
        dict_: dict = copy.deepcopy(data.__dict__)
        dict_.pop("_sa_instance_state")
        if popKeys is not None:
            for key in popKeys:
                if key in dict_:
                    dict_.pop(key)
        if timeKeys is not None:
            for key in timeKeys:
                if key in dict_ and dict_[key] is not None:
                    dict_[key] = getMsTime(dict_[key])
        return dict_

    def dealDataToy(self, data, timeKeys=None, saveKeys=None):
        from utilsTime import getMsTime
        dict_: dict = copy.deepcopy(data.__dict__)
        dict_.pop("_sa_instance_state")
        if saveKeys is not None:
            ls = []
            for key in dict_:
                if key not in saveKeys:
                    ls.append(key)
            for x in ls:
                dict_.pop(x)
        if timeKeys is not None:
            for key in timeKeys:
                if key in dict_ and dict_[key] is not None:
                    dict_[key] = getMsTime(dict_[key])
        return dict_

    def deleteNone(self, data):
        if type(data) == list:
            for i in range(len(data)):
                data[i] = self.deleteNone(data[i])
        elif type(data) == dict:
            data = {key: value for key, value in data.items() if
                    value is not None}

        return data

    def dealDataList(self, data, timeKeys=None, popKeys=None):
        dicts = []
        for d in data:
            dicts.append(self.dealData(d, timeKeys, popKeys))
        return dicts

    def __del__(self):
        self.session.close()


if __name__ == "__main__":
    init_db()
