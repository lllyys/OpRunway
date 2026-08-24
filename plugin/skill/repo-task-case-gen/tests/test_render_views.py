"""平级 skill 缺少另一侧专属视图时仍能渲染。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _paths import SCRIPTS


sys.path.insert(0, str(SCRIPTS))

import render_views  # noqa: E402


class MissingFencedViewTest(unittest.TestCase):
    def test_missing_fenced_target_is_skipped_for_check_and_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "other-side-only.md"
            views = ((missing, lambda _: "内容"),)
            fenced = ((missing, "目标", lambda _: "内容"),)
            for mode in ("--check", "--write"):
                with self.subTest(mode=mode), \
                     mock.patch.object(render_views, "VIEWS", views), \
                     mock.patch.object(render_views, "FENCED_VIEWS", fenced), \
                     mock.patch.object(render_views, "load", return_value={}), \
                     mock.patch.object(
                         render_views._handoff_contract, "load", return_value={}), \
                     mock.patch.object(sys, "argv", ["render_views.py", mode]):
                    self.assertEqual(0, render_views.main())


if __name__ == "__main__":
    unittest.main()
