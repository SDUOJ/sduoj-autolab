"""Group problem-set tag bucket consistency (pure-logic).

Empty/NULL tags must collapse to the same bucket in the filter tab list,
the per-tag list filter, and the organized listing.
"""

import unittest


def normalize_tag(tag) -> str:
    # Mirrors model.problem_set.problemSetModel._normalize_tag
    if tag is None:
        return "未分类"
    text = str(tag).strip()
    return text if text else "未分类"


class TestProblemSetTagBucket(unittest.TestCase):
    def test_empty_values_share_uncategorized_bucket(self):
        self.assertEqual(normalize_tag(None), "未分类")
        self.assertEqual(normalize_tag(""), "未分类")
        self.assertEqual(normalize_tag("   "), "未分类")
        self.assertEqual(normalize_tag("实验"), "实验")
        self.assertEqual(normalize_tag(" 实验 "), "实验")

    def test_key_list_and_search_use_same_bucket_names(self):
        self.assertEqual(normalize_tag(None), normalize_tag(""))
        self.assertEqual(normalize_tag("  A "), normalize_tag("A"))


if __name__ == "__main__":
    unittest.main()
