"""Progress and rankings must survive edits to an existing problem set."""

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from model.answer_sheet import answerSheetModel


class StaleAnswerDetailsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.model = object.__new__(answerSheetModel)
        self.model.session = MagicMock()
        self.sheets = [
            SimpleNamespace(asid=1, username="student-a", finish=0,
                            finish_time=None, submit_ip_set=None),
            SimpleNamespace(asid=2, username="student-b", finish=0,
                            finish_time=None, submit_ip_set=None),
        ]
        self.details = [
            SimpleNamespace(asid=1, gid=131, pid=81, answer=["old report"], collect=1),
            SimpleNamespace(asid=1, gid=145, pid=9999, answer=["removed problem"], collect=1),
            SimpleNamespace(asid=1, gid=145, pid=1188, answer=["code"], collect=0),
            SimpleNamespace(asid=1, gid=176, pid=108, answer=["current report"], collect=1),
            SimpleNamespace(asid=2, gid=131, pid=81, answer=["old report"], collect=0),
        ]
        self.model.get_gid_pid2indexDict_cache = AsyncMock(return_value=(
            {"145-1188": (0, 0, 2), "176-108": (1, 0, 1)}, [1, 1]
        ))
        self.model.get_detail_info_by_asd_obj = AsyncMock(
            side_effect=lambda obj: vars(obj).copy()
        )
        self.model.check_group_finish_by_psid_gi = AsyncMock(return_value=True)
        self.model.get_pro_report = AsyncMock(side_effect=self.report)
        self.model.ps_get_info_detail_by_id_cache = AsyncMock(return_value={"psid": 234})
        self.model.dealData = lambda obj, *args: vars(obj).copy()
        self.model.jsonLoads = lambda data, keys: data

    @staticmethod
    async def report(psid, detail, gid, pid, *args):
        # A stale answer must never be scored as a current answer.
        return {"score": {1188: 60, 108: 15}[pid], "hasJudge": True}

    async def test_personal_progress_keeps_current_answers_and_skips_removed_ones(self):
        query = self.model.session.query.return_value.filter.return_value
        query.first.return_value = self.sheets[0]
        query.all.return_value = self.details[:4]

        result = await self.model.get_user_progress(234, "student-a")

        self.assertEqual([len(group) for group in result], [1, 1])
        self.assertEqual([group[0]["score"] for group in result], [60, 15])
        self.assertEqual([group[0]["hasAnswer"] for group in result], [True, True])
        self.assertEqual(result[1][0]["collect"], 1)
        self.assertEqual(self.model.get_pro_report.await_count, 2)
        self.model.session.commit.assert_not_called()
        self.model.session.delete.assert_not_called()

    async def test_ranking_excludes_stale_scores_and_keeps_students_without_current_answers(self):
        query = self.model.session.query.return_value
        query.filter.return_value.all.return_value = self.sheets
        query.outerjoin.return_value.filter.return_value.all.return_value = self.details
        method = inspect.unwrap(answerSheetModel.get_all_progress_cache)
        with patch("model.answer_sheet.getSubmissionScore", new=AsyncMock(return_value={})), \
                patch("model.answer_sheet.getNickName", new=AsyncMock(return_value="Student")):
            result = await method(self.model, 234, 0)

        rows = result["data"]
        self.assertEqual([row["username"] for row in rows], ["student-a", "student-b"])
        self.assertEqual([row["sum_score"] for row in rows], [75, 0])
        self.assertEqual([row["rank"] for row in rows], [1, 2])
        self.assertEqual(rows[0]["1-1"]["s"], 60)
        self.assertEqual(rows[0]["2-1"]["s"], 15)
        self.assertFalse(rows[1]["1-1"]["h"])
        self.assertEqual(self.model.get_pro_report.await_count, 2)
        self.model.session.commit.assert_not_called()
        self.model.session.delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
