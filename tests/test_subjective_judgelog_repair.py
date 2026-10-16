"""Pure matching and CLI checks; never load the configured application DB."""
import subprocess
import sys
import unittest
from pathlib import Path

import fix_subjective_judgelog_names as repair


class SubjectiveRepairTest(unittest.TestCase):
    def test_reordered_unique_names_keep_result_order_and_use_matching_scores(self):
        result = {"judgeComment": "retained", "judgeLog": []}
        rows = [repair.JudgeRow("first", 10, 1), repair.JudgeRow("second", 10, 2)]
        logs = [repair.JudgeRow("second", 10, 8), repair.JudgeRow("first", 10, 9)]
        corrected, mapping, _ = repair._build_corrected_result(result, rows, logs, 1.0)
        self.assertEqual(mapping, {0: 1, 1: 0})
        self.assertEqual([row["name"] for row in corrected["judgeLog"]], ["first", "second"])
        self.assertEqual([row["jScore"] for row in corrected["judgeLog"]], [9, 8])
        self.assertEqual(corrected["judgeComment"], "retained")
        self.assertEqual(result["judgeLog"], [])

    def test_unmatched_rows_keep_scores_and_nonfinite_values_are_not_accepted(self):
        rows = [repair.JudgeRow("unrelated", 10, 4)]
        corrected, mapping, _ = repair._build_corrected_result({}, rows, [repair.JudgeRow("different", 10, 9)], 1.0)
        self.assertEqual(mapping, {})
        self.assertEqual(corrected["judgeLog"][0]["jScore"], 4)
        self.assertEqual(repair._to_float(float("inf")), 0)
        self.assertEqual(repair._to_float(float("nan")), 0)

    def test_import_and_help_do_not_import_application_database(self):
        code = "import sys, runpy; sys.modules['db']=None; sys.argv=['repair','--help']; runpy.run_path('fix_subjective_judgelog_names.py', run_name='__main__')"
        result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--commit", result.stdout)
        self.assertIn("default dry-run", result.stdout)


if __name__ == "__main__":
    unittest.main()
