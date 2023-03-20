import copy
import json
import math

from fastapi import HTTPException
from fastapi_cache.decorator import cache
from sqlalchemy import and_

from cache import class_func_key_builder
from db import ProblemSetAnswerSheet, \
    ProblemSetAnswerSheetDetail, ProblemObjective, \
    ProblemSubjective
from model.problem_group import groupModel
from model.problem_set import problemSetModel
from sduojApi import getProblemInfo, programSubmit, getSubmissionScoreAll, \
    getNickName, getSubmissionScore, getUserId
from ser.answer_sheet import routerTypeWithUsername, \
    routerTypeWithData, routerTypeBase, routerTypeBaseWithUsername
from utils import get_random_list_by_str, change_order, get_group_hash_name, \
    get_pro_hash_name, change_choice_order
from utilsTime import afterTime, cover_to_dt, getNowTime, inTime, getMsTime


class answerSheetModel(problemSetModel, groupModel):

    def add_ipv4_by_psid_username(self, psid, username, ip):
        data = self.get_info_by_psid_username(psid, username)
        ips = data["submit_ip_set"]
        if ips is None:
            ips = []
        if ip not in ips:
            ips.append(ip)
            self.update_by_psid_username(
                psid, username, {"submit_ip_set": ips}
            )

    def finish_by_psid_username(self, psid, username):
        self.update_by_psid_username(psid, username, {
            "finish": 1,
            "finish_time": getNowTime()
        })

    def update_by_psid_username(self, psid, username, data):
        data = self.jsonDumps(data, ["submit_ip_set"])
        cover_to_dt(data, "finish_time")
        self.session.query(ProblemSetAnswerSheet).filter(
            and_(ProblemSetAnswerSheet.psid == psid,
                 ProblemSetAnswerSheet.username == username)
        ).update(data)
        self.session.commit()

    def update_detail_by_asd_id(self, asd_id, data):
        data = self.jsonDumps(
            data, ["answer", "mark", "judgeLog", "antiCheatingResult"]
        )
        cover_to_dt(data, "tm_answer_submit")
        self.session.query(ProblemSetAnswerSheetDetail).filter(
            ProblemSetAnswerSheetDetail.asd_id == asd_id
        ).update(data)
        self.session.commit()

    def get_info_by_psid_username(self, psid, username):
        obj = self.get_obj_by_psid_username(psid, username)
        data = self.jsonLoads(
            self.dealData(obj, ["finish_time"]), ["submit_ip_set"]
        )
        return data

    def get_obj_by_psid_username(self, psid, username):
        ast = self.session.query(ProblemSetAnswerSheet).filter(
            and_(ProblemSetAnswerSheet.psid == psid,
                 ProblemSetAnswerSheet.username == username)
        ).first()
        if ast is None:
            ast = ProblemSetAnswerSheet(
                username=username,
                psid=psid,
                finish=0
            )
            self.session.add(ast)
            self.session.commit()
        ast = self.session.query(ProblemSetAnswerSheet).filter(
            and_(ProblemSetAnswerSheet.psid == psid,
                 ProblemSetAnswerSheet.username == username)
        ).first()
        return ast

    async def get_detail_obj_by_asid_gid_pid(self, asid, gid, pid):
        ASDetail = self.session.query(ProblemSetAnswerSheetDetail).filter(
            and_(ProblemSetAnswerSheetDetail.asid == asid,
                 ProblemSetAnswerSheetDetail.gid == gid,
                 ProblemSetAnswerSheetDetail.pid == pid)
        ).first()
        if ASDetail is None:
            ASDetail = ProblemSetAnswerSheetDetail(
                asid=asid,
                gid=gid,
                pid=pid,
            )
            self.session.add(ASDetail)
            self.session.commit()
        ASDetail = self.session.query(ProblemSetAnswerSheetDetail).filter(
            and_(ProblemSetAnswerSheetDetail.asid == asid,
                 ProblemSetAnswerSheetDetail.gid == gid,
                 ProblemSetAnswerSheetDetail.pid == pid)
        ).first()
        return ASDetail

    @cache(expire=60, key_builder=class_func_key_builder)
    async def get_gid_pid_by_psid_gi_pi_cache(self, psid, g_index, p_index):
        ps = await self.ps_get_info_by_id_cache(psid)
        try:
            gid = ps["groupInfo"][g_index]["gid"]
            group = await self.group_get_info_by_id_cache(gid)
            pid = group["problemInfo"][p_index]["pid"]
        except:
            raise HTTPException(detail="Index error",
                                status_code=404)
        return gid, pid

    async def get_detail_obj_by_psid_username_gi_pi(
            self, psid, username, g_index, p_index):
        gid, pid = await self.get_gid_pid_by_psid_gi_pi_cache(psid, g_index,
                                                              p_index)
        as_obj = self.get_obj_by_psid_username(psid, username)
        asd_obj = await self.get_detail_obj_by_asid_gid_pid(as_obj.asid, gid,
                                                            pid)
        return asd_obj

    async def get_detail_info_by_psid_username_gi_pi(
            self, psid, username, g_index, p_index, popKeys=None):
        asd_obj = await self.get_detail_obj_by_psid_username_gi_pi(
            psid, username, g_index, p_index)
        asd_data = self.jsonLoads(
            self.dealData(asd_obj, [], popKeys),
            ["answer", "mark", "judgeLog", "antiCheatingResult",
             "submit_ip_set"]
        )
        return asd_data

    async def get_detail_info_by_asd_obj(self, obj):
        asd_data = self.jsonLoads(
            self.dealData(obj, ["tm_answer_submit"]),
            ["answer", "mark", "judgeLog", "antiCheatingResult"]
        )
        return asd_data

    @cache(expire=60, key_builder=class_func_key_builder)
    async def get_gid_pid2indexDict_cache(self, psid):
        res = {}
        sz_list = []
        ps = await self.ps_get_info_by_id_cache(psid)
        i = 0
        for group in ps["groupInfo"]:
            g = await self.group_get_info_by_id_cache(group["gid"])
            j = 0
            for problem in g["problemInfo"]:
                res[str(group["gid"]) + "-" + str(problem["pid"])] = (
                    i, j, g["type"])
                j += 1
            sz_list.append(j)
            i += 1
        return res, sz_list

    @cache(expire=60, key_builder=class_func_key_builder)
    async def get_group_sz_and_type_cache(self, psid, gi):
        ps = await self.ps_get_info_by_id_cache(psid)
        gid = ps["groupInfo"][gi]["gid"]
        group = await self.group_get_info_by_id_cache(gid)
        return len(group["problemInfo"]), group["type"]

    @cache(expire=60, key_builder=class_func_key_builder)
    async def get_group_tm_start_end_cache(self, psid, gi: int):
        config = await self.ps_get_config_by_psid_cache(psid)
        if config["useSameSE"] == 1:
            if config["usePractice"] == 0:
                tm_start, tm_end = await self.ps_get_tm_by_psid_cache(psid)
            else:
                ts = config["practiceTimeSetting"]
                tm_start = ts[0]["tm_start"]
                tm_end = ts[len(ts) - 1]["tm_end"]
        else:
            groupInfo = await self.ps_get_groupInfo_by_psid_cache(psid)
            ts = groupInfo[gi]
            tm_start = ts[0]["tm_start"]
            tm_end = ts[len(ts) - 1]["tm_end"]
        return int(tm_start), int(tm_end)

    async def check_group_finish_by_psid_gi(self, psid, gi):
        # if config["useSameSE"] == 1:
        # tm_start, tm_end = await self.get_group_tm_start_end_cache(psid, gi)
        # TODO 目前仅支持统一时间的题单，是要正式模式结束，即显示报告
        config = await self.ps_get_config_by_psid_cache(psid)
        tm_start, tm_end = await self.ps_get_tm_by_psid_cache(psid)
        return afterTime(int(tm_start), int(tm_end)) and config[
            "showReport"] == 1

    async def get_user_pro_progress_by_gid_pid(self, psid, username, gi, pi):
        gid, pid = await self.get_gid_pid_by_psid_gi_pi_cache(psid, gi, pi)
        as_obj = self.get_obj_by_psid_username(psid, username)
        asid = as_obj.asid
        x = self.session.query(ProblemSetAnswerSheetDetail).filter(
            and_(ProblemSetAnswerSheetDetail.asid == asid,
                 ProblemSetAnswerSheetDetail.gid == gid,
                 ProblemSetAnswerSheetDetail.pid == pid)
        ).first()
        x = await self.get_detail_info_by_asd_obj(x)
        if await self.check_group_finish_by_psid_gi(psid, gi):
            report = await self.get_pro_report(
                psid, x, gid, pid, gi, pi, username
            )
            return report
        return {}

    # @cache(expire=60, key_builder=class_func_key_builder)
    async def get_user_progress(self, psid, username):
        as_obj = self.get_obj_by_psid_username(psid, username)
        asid = as_obj.asid

        # 获取这个题单所有答题卡的详细信息
        asd_data = self.session.query(ProblemSetAnswerSheetDetail).filter(
            ProblemSetAnswerSheetDetail.asid == asid,
        ).all()

        id2index, sz_list = await self.get_gid_pid2indexDict_cache(psid)

        # 初始化
        data = []
        for i in range(len(sz_list)):
            r = []
            for j in range(sz_list[i]):
                r.append({"index": j, "hasAnswer": False, "collect": 0})
            data.append(r)

        # 详细统计每个题的信息
        for x in asd_data:
            asd = await self.get_detail_info_by_asd_obj(x)
            gid, pid, answer = asd["gid"], asd["pid"], asd["answer"]
            i, j, tp = id2index[str(gid) + "-" + str(pid)]
            data[i][j].update({
                "hasAnswer": (answer is not None) and len(answer) != 0,
                "collect": asd["collect"]
            })
            # 编程题的评测结果可以实时显示
            if tp == 2 or await self.check_group_finish_by_psid_gi(psid, i):
                report = await self.get_pro_report(
                    psid, asd, gid, pid, i, j, username
                )
                data[i][j].update(report)
        return data

    @cache(expire=60, key_builder=class_func_key_builder)
    async def get_all_progress_cache(self, psid, get_code):
        asts = self.session.query(ProblemSetAnswerSheet).filter(
            ProblemSetAnswerSheet.psid == psid,
        ).all()

        id2index, sz_list = await self.get_gid_pid2indexDict_cache(psid)

        pre = []
        for i in range(len(sz_list)):
            r = []
            for j in range(sz_list[i]):
                r.append({"index": j, "h": False, "s": 0, "j": True})
            pre.append(r)

        ids2username = {}
        username2ips = {}
        username2finish = {}
        ret_data = {}

        for ast in asts:
            ast_ = self.jsonLoads(self.dealData(
                ast, ["finish_time"], []
            ), ["submit_ip_set"])
            ids2username[ast_["asid"]] = ast_["username"]
            username2finish[ast_["username"]] = (
                ast_["finish"], ast_["finish_time"])
            username2ips[ast_["username"]] = ast_["submit_ip_set"]
            if ast_["username"] not in ret_data:
                ret_data[ast_["username"]] = copy.deepcopy(pre)

        asd_data = self.session.query(
            ProblemSetAnswerSheetDetail).outerjoin(
            ProblemSetAnswerSheet,
            ProblemSetAnswerSheet.asid == ProblemSetAnswerSheetDetail.asid
        ).filter(
            ProblemSetAnswerSheet.psid == psid,
        ).all()

        allSubmission = await getSubmissionScore(psid, None, None, get_code)

        for x in asd_data:
            i, j, tp = id2index[str(x.gid) + "-" + str(x.pid)]
            username = ids2username[x.asid]
            x = await self.get_detail_info_by_asd_obj(x)
            answer = x["answer"]
            hasAnswer = (answer is not None) and len(answer) != 0
            res = {}
            if hasAnswer:
                report = await self.get_pro_report(
                    psid, x, x["gid"], x["pid"], i, j, username,
                    get_code, allSubmission
                )
                res.update({
                    "h": hasAnswer,
                    "s": report["score"]
                })
                if "hasJudge" in report:
                    res.update({
                        "j": report["hasJudge"]
                    })
                if get_code == 1:
                    if "e_code" in report:
                        res.update({
                            "code": report["e_code"]
                        })
                    if "p_code" in report:
                        res.update({
                            "code_p": report["p_code"]
                        })
            ret_data[username][i][j].update(res)

        res = []
        for x in ret_data:
            tp = {}
            s = 0
            for i in range(len(ret_data[x])):
                y = ret_data[x][i]
                for z in y:
                    idn = str(i + 1) + "-" + str(z["index"] + 1)
                    tp[idn] = {**z}
                    s += z["s"]
                    tp[idn].pop("index")

            res.append({
                "username": x,
                "ips": username2ips[x],
                "finish": username2finish[x][0],
                "finish_time": username2finish[x][1],
                "nickname": await getNickName(x),
                "sum_score": s,
                **tp
            })
        res.sort(key=lambda x: x["sum_score"], reverse=True)
        for i in range(len(res)):
            if i == 0:
                res[i]["rank"] = (i + 1)
            else:
                if res[i]["sum_score"] == res[i - 1]["sum_score"]:
                    res[i]["rank"] = res[i - 1]["rank"]
                else:
                    res[i]["rank"] = (i + 1)
        return {
            "data": res,
            "lastUpdate": getNowTime(),
            "info": await self.ps_get_info_detail_by_id_cache(psid)
        }

    # 补充题单中的细节详细数据
    @cache(expire=60, key_builder=class_func_key_builder)
    async def ps_get_info_detail_by_id_cache(self, psid, getAnswer=False):
        data = await self.ps_get_info_by_id_cache(psid)
        # 计算组的分数
        gs = 0
        for i in range(len(data["groupInfo"])):
            gs += data["groupInfo"][i]["score"]

        for i in range(len(data["groupInfo"])):
            gid = data["groupInfo"][i]["gid"]
            s = data["groupInfo"][i]["score"]
            data["groupInfo"][i].pop("gid")
            data["groupInfo"][i].pop("score")
            data["groupInfo"][i]["index"] = i
            data["groupInfo"][i]["point"] = s / gs * 100

            group = await self.group_get_info_c_by_id(gid)
            data["groupInfo"][i]["type"] = group["type"]

            problemInfo = group["problemInfo"]
            proInfo = []
            # 计算单个题目的成绩
            ps = 0
            for j in range(len(problemInfo)):
                ps += problemInfo[j]["score"]

            for j in range(len(problemInfo)):

                # 将题目答案集成进数据中
                if getAnswer:
                    pid = problemInfo[j]["pid"]
                    if group["type"] == 0:
                        pro = self.session.query(ProblemObjective).filter(
                            ProblemObjective.pid == pid
                        ).first()
                        problemInfo[j]["answer"] = json.loads(pro.answer)
                        problemInfo[j]["type"] = pro.type
                    elif group["type"] == 1:
                        pro = self.session.query(ProblemSubjective).filter(
                            ProblemSubjective.pid == pid
                        ).first()
                        problemInfo[j]["config"] = json.loads(pro.config)

                problemInfo[j]["index"] = j
                problemInfo[j]["point"] = problemInfo[j]["score"] / ps * \
                                          data["groupInfo"][i]["point"]
                problemInfo[j].pop("pid")
                problemInfo[j].pop("score")
                proInfo.append(problemInfo[j])

            data["groupInfo"][i]["problemInfo"] = proInfo
        return data

    # 获取题单详细数据
    async def ps_get_info_c_by_id(self, id_, username):
        data = await self.ps_get_info_detail_by_id_cache(id_)
        up = await self.get_user_progress(id_, username)
        for i in range(len(data["groupInfo"])):
            # 维护分值的显示
            if data["config"]["showScoreInRunning"] == 0:
                data["groupInfo"][i].pop("point")
            for j in range(len(data["groupInfo"][i]["problemInfo"])):
                data["groupInfo"][i]["problemInfo"][j].update(up[i][j])
                # 维护分值的显示
                if data["config"]["showScoreInRunning"] == 0:
                    data["groupInfo"][i]["problemInfo"][j].pop("point")
            if data["groupInfo"][i]["type"] == 0:
                pro_len = len(data["groupInfo"][i]["problemInfo"])
                o1, o2 = get_random_list_by_str(
                    pro_len, get_group_hash_name(id_, i, username)
                )
                data["groupInfo"][i]["problemInfo"] = change_order(
                    data["groupInfo"][i]["problemInfo"], o1
                )
        return data

    # 获取单个题目详细信息
    @cache(expire=60, key_builder=class_func_key_builder)
    async def ps_get_proInfo_cache(self, psid, gi, pi):
        gid, pid = await self.get_gid_pid_by_psid_gi_pi_cache(psid, gi, pi)
        group = await self.group_get_info_by_id_cache(gid)

        if group["type"] == 0:
            pro = self.session.query(ProblemObjective).filter(
                ProblemObjective.pid == pid
            ).first()
            data = json.loads(pro.content)
            return data
        if group["type"] == 1:
            pro = self.session.query(ProblemSubjective).filter(
                ProblemSubjective.pid == pid
            ).first()
            pro = self.dealDataToy(pro, [], ["type", "config", "description"])
            pro = self.jsonLoads(pro, ["config"])
            if "judgeConfig" in pro["config"]:
                pro["config"].pop("judgeConfig")
            return pro
        if group["type"] == 2:
            pro = await getProblemInfo(
                pid, group["problemInfo"][pi]["desId"]
            )
            pro["problemTitle"] = group["problemInfo"][pi]["name"]
            groupInfo = await self.ps_get_groupInfo_by_psid_cache(psid)
            pro["problemCode"] = groupInfo[gi]["name"] + "-" + str(pi + 1)
            pro["submitNum"] = group["problemInfo"][pi]["submitLimit"]
            return pro

    # 获取单个题目详细信息
    async def ps_get_proInfo(self, data: routerTypeWithUsername):
        res = await self.ps_get_proInfo_cache(
            data.router.psid, data.router.gid, data.router.pid
        )
        if "choice" in res:
            o1, o2 = get_random_list_by_str(
                len(res["choice"]),
                get_pro_hash_name(
                    data.router.psid, data.router.gid, data.router.pid,
                    data.username
                )
            )
            res["choice"] = change_order(res["choice"], o1)
        return res

    # 获取整个题单的起止时间
    @cache(expire=60, key_builder=class_func_key_builder)
    async def ps_get_all_tm_by_psid_cache(self, psid):
        info = await self.ps_get_info_by_id_cache(psid)
        tm_start, tm_end = math.inf, -math.inf
        if info["config"]["useSameSE"] == 1:
            s_tm_start, s_tm_end = await self.ps_get_tm_by_psid_cache(psid)
            tm_start = min(tm_start, int(s_tm_start))
            tm_end = max(tm_end, int(s_tm_end))
            if info["config"]["usePractice"] == 1:
                for tm in info["config"]["practiceTimeSetting"]:
                    tm_start = min(tm_start, int(tm["tm_start"]))
                    tm_end = max(tm_end, int(tm["tm_end"]))
        else:
            for g in info["groupInfo"]:
                g_tm_start, g_tm_end = await self.get_group_tm_start_end_cache(
                    psid, g["gid"]
                )
                tm_start = min(tm_start, int(g_tm_start))
                tm_end = max(tm_end, int(g_tm_end))
        return tm_start, tm_end

    def get_user_finish(self, psid, username):
        as_info = self.get_info_by_psid_username(psid, username)
        return as_info["finish"]

    # 获取题单的公开信息
    @cache(expire=60, key_builder=class_func_key_builder)
    async def ps_get_public_info_by_psid(self, psid):
        info = await self.ps_get_info_by_id_cache(psid)
        tm_start, tm_end = await self.ps_get_all_tm_by_psid_cache(psid)
        return {
            "name": info["name"],
            "description": info["description"],
            "tm_start": tm_start,
            "tm_end": tm_end,
        }

    # 对题目添加收藏
    async def collect(self, data: routerTypeWithUsername):
        obj = await self.get_detail_obj_by_psid_username_gi_pi(
            data.router.psid, data.username, data.router.gid, data.router.pid
        )
        self.update_detail_by_asd_id(obj.asd_id, {"collect": 1 - obj.collect})

    # 对客观题执行标记
    async def mark(self, data: routerTypeWithData):
        obj = await self.get_detail_obj_by_psid_username_gi_pi(
            data.router.psid, data.username, data.router.gid, data.router.pid
        )
        answer = json.loads(obj.answer if obj.answer is not None else "[]")
        mark = json.loads(obj.mark if obj.mark is not None else "[]")
        SID = data.data
        if SID in mark:
            mark.remove(SID)
        else:
            mark.append(SID)
            if SID in answer:
                answer.remove(SID)
        answer.sort()
        mark.sort()
        self.update_detail_by_asd_id(obj.asd_id, {
            "mark": mark,
            "answer": answer
        })
        return {"hasAnswer": len(answer) != 0}

    # 获取某个时间的得分权重, 返回的第二个参数为，是否在限时测试中
    async def get_now_weight(self, psid, gi, time=None):
        ps_info = await self.ps_get_info_by_id_cache(psid)
        if time is None:
            time = getNowTime()
        if type(time) != int:
            time = int(time)
        if ps_info["config"]["useSameSE"] == 1:
            if inTime(time, int(ps_info["tm_start"]), int(ps_info["tm_end"])):
                return 1, True
            if ps_info["config"]["usePractice"] == 1:
                for x in ps_info["config"]["practiceTimeSetting"]:
                    if inTime(time, x["tm_start"], x["tm_end"]):
                        return x["weight"], False
        else:
            g_timeSettingList = ps_info["groupInfo"][gi]["timeSetting"]
            for x in g_timeSettingList:
                if inTime(time, x["tm_start"], x["tm_end"]):
                    return x["weight"], False
        return 0, False

    @cache(expire=60, key_builder=class_func_key_builder)
    async def get_ps_pro_info_detail_cache(self, psid, gi, pi):
        ps_info = await self.ps_get_info_detail_by_id_cache(psid, True)
        return ps_info["groupInfo"][gi]["problemInfo"][pi]

    # 获取题目完成的总结性数据
    async def get_pro_report(
            self, psid, asd, gid, pid, gi, pi, username, get_code=True,
            cache_program=None):
        # TODO 接入查重
        res = {}
        # 记录了题目的答案，分值等信息
        proInfo = await self.get_ps_pro_info_detail_cache(psid, gi, pi)
        group = await self.group_get_info_by_id_cache(gid)
        ps_config = await self.ps_get_config_by_psid_cache(psid)

        if group["type"] == 0:
            point = proInfo["point"]
            answer = proInfo["answer"]
            answer_m = asd["answer"]
            mark = asd["mark"]

            if answer is None:
                answer = []
            if answer_m is None:
                answer_m = []
            if mark is None:
                mark = []

            answer.sort()
            answer_m.sort()

            weight, _ = await self.get_now_weight(
                psid, gi, asd["tm_answer_submit"]
            )
            res.update({
                "mark": mark,
                "score": (point if answer == answer_m else 0) * weight,
                "answer": answer,
                "answer_m": answer_m,
                "weight": weight,
                "submit_time": asd["tm_answer_submit"]
            })

        if group["type"] == 1:
            point = proInfo["point"]
            config = proInfo["config"]
            answer_m = asd["answer"]
            judgeLog = asd["judgeLog"]
            score = 0
            hasJudge = (judgeLog is not None) and (len(judgeLog) != 0)
            judgeLock = asd["judgeLock_username"]

            if hasJudge:
                ms = 0
                for x in judgeLog:
                    ms += x["jScore"]
                al = 0
                for x in config["judgeConfig"]:
                    al += x["score"]
                score = ms / al * point

            weight, _ = await self.get_now_weight(
                psid, gi, asd["tm_answer_submit"]
            )

            res.update({
                "score": score * weight,
                "answer_m": answer_m,
                "judgeLog": judgeLog,
                "judgeConfig": config["judgeConfig"],
                "hasJudge": hasJudge,
                "judgeLock": judgeLock,
                "weight": weight,
                "submit_time": asd["tm_answer_submit"],
                "judgeComment": asd["judgeComment"]
            })

        if group["type"] == 2:
            uid = await getUserId(username)
            id_ = str(uid) + "-" + str(pid)
            if cache_program is not None and id_ in cache_program:
                proResList = cache_program[id_]
            else:
                proResList = await getSubmissionScoreAll(
                    psid, pid, username, exportCode=(get_code == 1)
                )
            mxp, mxpId, pw, pt, pc = -1, "", None, None, ""
            mxe, mxeId, ew, et, ec = -1, "", None, None, ""
            mxp0 = -1
            ps, es = None, None
            hasJudge = True

            for pro in proResList:
                if int(pro['judgeResult']) <= 0:
                    hasJudge = False
                weight, inE = await self.get_now_weight(
                    psid, gi, pro["gmtCreate"]
                )
                s = pro["judgeScore"] / pro["fullScore"] * proInfo[
                    "point"] * weight
                scoreWithoutWeight = pro["judgeScore"] / pro["fullScore"]
                if inE:
                    if s > mxe:
                        mxe, ew = s, weight
                        mxeId = pro["submissionId"]
                        et, ec = pro["gmtCreate"], pro["code"]
                        es = pro['judgeResult']
                else:
                    # 如果补题模式没有权重，则选择一个真实分数最大的
                    if (s == mxp and scoreWithoutWeight > mxp0) or (s > mxp):
                        mxp0 = scoreWithoutWeight
                        mxp, pw = s, weight
                        mxpId = pro["submissionId"]
                        pt, pc = pro["gmtCreate"], pro["code"]
                        ps = pro['judgeResult']

            if mxp == -1:
                mxp = 0
            if mxe == -1:
                mxe = 0

            def addE():
                res.update({
                    "e_id": mxeId,
                    "e_weight": ew,
                    "e_submit_time": et,
                    "e_code": ec,
                    "e_status": es,
                })

            def addP():
                res.update({
                    "p_id": mxpId,
                    "p_weight": pw,
                    "p_submit_time": pt,
                    "p_code": pc,
                    "p_status": ps
                })

            if ps_config["useSameSE"] == 1:
                if ps_config["usePractice"] == 1:
                    calc = ps_config["practiceScoreCalculate"]
                    calc = calc.replace("e", str(mxe))
                    calc = calc.replace("p", str(mxp))
                    res.update({
                        "score": eval(calc),
                        "type": "sameTimeAndPractice",
                    })
                    addE()
                    addP()
                else:
                    res.update({
                        "score": mxe,
                        "type": "sameTime",
                    })
                    addE()
            else:
                res.update({
                    "score": mxp,
                    "type": "practice",
                })
                addP()

            res.update({"hasJudge": hasJudge})

        if ps_config["showObjectiveAnswer"] == 0:
            if group["type"] == 0:
                res.pop("answer")
        if ps_config["showSubjectiveAnswer"] == 0:
            if group["type"] == 1:
                res.pop("judgeConfig")
        if ps_config["showSubjectiveJudgeLog"] == 0:
            if group["type"] == 1:
                res.pop("judgeLog")
        return res

    # 获取答题卡信息
    async def get_info(self, data: routerTypeWithUsername):
        info = await self.get_detail_info_by_psid_username_gi_pi(
            data.router.psid, data.username, data.router.gid, data.router.pid
        )
        ext = await self.get_user_pro_progress_by_gid_pid(
            data.router.psid, data.username, data.router.gid,
            data.router.pid
        )
        # 获取题目类型
        _, tp = await self.get_group_sz_and_type_cache(
            data.router.psid, data.router.gid
        )
        if tp == 0:
            o1, o2 = await self.get_pro_random_list_cache(
                data.router, data.username)
            if "answer" in ext:
                ext["answer"] = change_choice_order(ext["answer"], o1)
            return {
                **ext,
                "answer_m": change_choice_order(info["answer"], o1),
                "mark": change_choice_order(info["mark"], o1),
            }
        else:
            return {
                **ext,
                "answer_m": info["answer"],
                "mark": info["mark"],
            }

    # 更新答案信息
    async def update_answer(self, data: routerTypeWithData):
        psid = data.router.psid
        gi, pi = data.router.gid, data.router.pid
        gid, pid = await self.get_gid_pid_by_psid_gi_pi_cache(psid, gi, pi)
        group = await self.group_get_info_by_id_cache(gid)
        pro = await self.get_detail_info_by_psid_username_gi_pi(
            psid, data.username, gi, pi
        )
        answer = pro["answer"]
        if answer is None:
            answer = []

        if group["type"] == 0:
            mark = pro["mark"]
            if mark is None:
                mark = []
            SID = data.data

            # 单选题，控制只能选一个
            # TODO 可能有性能问题
            ps_info = await self.ps_get_info_detail_by_id_cache(psid, True)
            proInfo = ps_info["groupInfo"][gi]["problemInfo"][pi]
            if proInfo["type"] == 0:
                if len(answer) != 0 and SID not in answer:
                    answer = []

            if SID in answer:
                answer.remove(SID)
            else:
                answer.append(SID)
                if SID in mark:
                    mark.remove(SID)
            answer.sort()
            mark.sort()
            self.update_detail_by_asd_id(
                pro["asd_id"], {
                    "answer": answer,
                    "mark": mark,
                    "tm_answer_submit": getNowTime()
                }
            )
            return len(answer) != 0
        if group["type"] == 1:
            if len(data.data) == 1:
                if len(data.data[0].strip()) == 0:
                    data.data = []
            if pro["judgeLock_username"] is not None:
                raise HTTPException(detail="Already Judged", status_code=403)
            self.update_detail_by_asd_id(
                pro["asd_id"], {
                    "answer": data.data,
                    "tm_answer_submit": getNowTime()
                }
            )
            return len(data.data) != 0
        if group["type"] == 2:
            res = await programSubmit(
                problemSetId=data.router.psid,
                judgeTemplateId=data.data.judgeTemplateId,
                code=data.data.code,
                zipFileId=data.data.zipFileId,
                pid=pid,
                ipv4=data.data.ipv4,
                username=data.username
            )
            submissionId = hex(int(res))[2:]
            answer.append(submissionId)
            self.update_detail_by_asd_id(
                pro["asd_id"], {
                    "answer": answer,
                    "tm_answer_submit": getNowTime()
                }
            )
            return submissionId

    # 获取带评测列表
    async def get_judge_list_by_psid_page(
            self, psid, pg, gi, pi, username, judgeLock, hasJudge):

        # 题单结束之前，不能评阅主观题 （暂时不使用这个设定）
        # tm_start, tm_end = await self.ps_get_all_tm_by_psid_cache(psid)
        # if not afterTime(tm_start, tm_end):
        #     return 0, []

        asts = self.session.query(ProblemSetAnswerSheet).filter(
            ProblemSetAnswerSheet.psid == psid,
        ).all()

        # 找到所有题单内的答题卡
        ids = []
        ids2username = {}
        for ast in asts:
            ids.append(ast.asid)
            ids2username[ast.asid] = ast.username

        # 找到所有主观题的题组
        subjective_group_ls = []
        ps_info = await self.ps_get_info_by_id_cache(psid)
        for g in ps_info["groupInfo"]:
            gid = g["gid"]
            group = await self.group_get_info_by_id_cache(gid)
            if group["type"] == 1:
                subjective_group_ls.append(gid)

        base_ls = [
            ProblemSetAnswerSheet.psid == psid,
            ProblemSetAnswerSheetDetail.gid.in_(subjective_group_ls),
            ProblemSetAnswerSheetDetail.tm_answer_submit != None
        ]
        if username is not None:
            base_ls.append(ProblemSetAnswerSheet.username == username)

        if gi is not None:
            gid, pid = await self.get_gid_pid_by_psid_gi_pi_cache(psid, gi,
                                                                  pi)
            base_ls.append(ProblemSetAnswerSheetDetail.gid == gid)
            base_ls.append(ProblemSetAnswerSheetDetail.pid == pid)

        if judgeLock is not None:
            base_ls.append(
                ProblemSetAnswerSheetDetail.judgeLock_username == judgeLock
            )

        if hasJudge is not None:
            if hasJudge == 0:
                base_ls.append(
                    ProblemSetAnswerSheetDetail.judgeLog == None
                )
            else:
                base_ls.append(
                    ProblemSetAnswerSheetDetail.judgeLog != None
                )

        # 获取所有包含主观题题目细节的列表
        cmd = self.session.query(ProblemSetAnswerSheetDetail).outerjoin(
            ProblemSetAnswerSheet,
            ProblemSetAnswerSheetDetail.asid == ProblemSetAnswerSheet.asid
        ).filter(and_(*base_ls))
        tn = cmd.count()
        asd_data = cmd.offset(pg.offset()).limit(pg.limit())

        gid2gi = {}
        gid_pid2pi = {}
        for i in range(len(ps_info["groupInfo"])):
            g = ps_info["groupInfo"][i]
            if g["gid"] in subjective_group_ls:
                gid2gi[g["gid"]] = i
                g_info = await self.group_get_info_by_id_cache(g["gid"])
                for j in range(len(g_info["problemInfo"])):
                    p = g_info["problemInfo"][j]
                    gid_pid2pi[str(g["gid"]) + "-" + str(p["pid"])] = j

        data = []
        for x in asd_data:
            name = ps_info["groupInfo"][gid2gi[x.gid]]["name"]
            name += "-" + str(gid_pid2pi[str(x.gid) + "-" + str(x.pid)] + 1)
            data.append({
                "gid": gid2gi[x.gid],
                "pid": gid_pid2pi[str(x.gid) + "-" + str(x.pid)],
                "name": name,
                "username": ids2username[x.asid],
                "tm_answer_submit": getMsTime(
                    x.tm_answer_submit) if x.tm_answer_submit is not None else None,
                "judgeLock": x.judgeLock_username,
                "hasJudge": x.judgeLog is not None
            })
        return tn, data

    async def get_judge_info_by_psid_gi_pi_username(
            self, psid, gi, pi, username, judger):
        gid, pid = await self.get_gid_pid_by_psid_gi_pi_cache(psid, gi, pi)
        asd = self.session.query(ProblemSetAnswerSheetDetail).outerjoin(
            ProblemSetAnswerSheet,
            ProblemSetAnswerSheetDetail.asid == ProblemSetAnswerSheet.asid
        ).filter(and_(
            ProblemSetAnswerSheetDetail.pid == pid,
            ProblemSetAnswerSheetDetail.gid == gid,
            ProblemSetAnswerSheet.psid == psid,
            ProblemSetAnswerSheet.username == username
        )).first()
        asd = self.jsonLoads(
            self.dealData(asd, ["tm_answer_submit"], ["mark", "gid", "pid"]),
            ["answer", "judgeLog", "antiCheatingResult"]
        )
        pro = self.session.query(ProblemSubjective).filter(
            ProblemSubjective.pid == pid
        ).first()
        config = json.loads(pro.config)["judgeConfig"]
        asd["judgeConfig"] = config
        if asd["judgeLock_username"] is None:
            self.update_detail_by_asd_id(
                asd["asd_id"], {"judgeLock_username": judger}
            )
            asd["judgeLock_username"] = judger
        asd["description"] = pro.description
        asd["username"] = username

        return asd

    async def update_judgeLog_by_psid_gi_pi_username(
            self, psid, gi, pi, username, judgeLog, cancel, judgeComment):
        gid, pid = await self.get_gid_pid_by_psid_gi_pi_cache(psid, gi, pi)
        asd = self.session.query(ProblemSetAnswerSheetDetail).outerjoin(
            ProblemSetAnswerSheet,
            ProblemSetAnswerSheetDetail.asid == ProblemSetAnswerSheet.asid
        ).filter(and_(
            ProblemSetAnswerSheetDetail.pid == pid,
            ProblemSetAnswerSheetDetail.gid == gid,
            ProblemSetAnswerSheet.psid == psid,
            ProblemSetAnswerSheet.username == username
        )).first()
        if cancel == 1:
            self.update_detail_by_asd_id(asd.asd_id, {
                "judgeLog": None,
                "judgeLock_username": None
            })
        else:
            self.update_detail_by_asd_id(asd.asd_id, {
                "judgeLog": judgeLog,
                "judgeComment": judgeComment
            })

    @cache(expire=60, key_builder=class_func_key_builder)
    async def get_group_random_list_cache(
            self, router: routerTypeBase, username):

        # 获取题目类型，以及每个类型的题数
        sz, tp = await self.get_group_sz_and_type_cache(
            router.psid, router.gid
        )
        o1, o2 = get_random_list_by_str(
            sz,
            get_group_hash_name(router.psid, router.gid, username)
        )
        return o1, o2, tp

    @cache(expire=60, key_builder=class_func_key_builder)
    async def get_pro_random_list_cache(
            self, router: routerTypeBase, username):
        pro = await self.ps_get_proInfo_cache(
            router.psid, router.gid, router.pid
        )
        o1, o2 = get_random_list_by_str(
            len(pro["choice"]),
            get_pro_hash_name(router.psid, router.gid, router.pid, username)
        )
        return o1, o2

    async def get_rank_preview_info(self, router: routerTypeBaseWithUsername):
        pro = await self.ps_get_proInfo_cache(
            router.psid, router.gid, router.pid
        )
        pro_info = await self.get_ps_pro_info_detail_cache(
            router.psid, router.gid, router.pid
        )
        if "answer" in pro_info:
            pro.update({"answer": pro_info["answer"]})
        asd_obj = await self.get_detail_obj_by_psid_username_gi_pi(
            router.psid, router.username, router.gid, router.pid
        )
        asd = await self.get_detail_info_by_asd_obj(asd_obj)
        return {"problemInfo": pro, "answerSheet": asd}
