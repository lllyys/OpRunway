"""fetch_source.py 的 PR URL 解析单测（U2）——stdlib unittest，不打真网络。

跑: python3 -m unittest test_fetch_source -v   （在 acc-common/ 下）

覆盖:
  - 各种 URL 形态都能解析成同一 (owner, repo, num)：/pull/N（GitHub 单数）· /pulls/N · /merge_requests/N
  - 形态不认识 → fail-loud（抛 ValueError）+ 错误信息可操作（给出正确形态）
  - 回归: 解析失败**绝不产出空壳 pr_facts**（out 目录里不落 pr_facts.json）
  - 区分两种失败: URL 认识但网络取不到 → 不抛、记 notes、仍写 pr_facts（含 source_repo），
    且 notes 不把它说成「URL 格式错」（免得误导用户改 URL）
不打真网络: URL 形态用例走纯函数 _parse_pr_url（解析在网络之前）; 网络分支用桩替掉 fetch_source._get。
"""
import os, json, tempfile, unittest
import fetch_source as fs


class ParsePrUrlTest(unittest.TestCase):
    """URL 形态解析——纯函数 _parse_pr_url，无网络。"""

    def test_gitcode_native_merge_requests(self):
        self.assertEqual(fs._parse_pr_url("https://gitcode.com/cann/ops-math/merge_requests/2663"),
                         ("cann", "ops-math", "2663"))

    def test_github_style_singular_pull(self):
        # 用户实测粘的就是这个形态（GitHub 习惯单数 pull）→ 早先版本认不出、静默糊过
        self.assertEqual(fs._parse_pr_url("https://gitcode.com/cann/ops-math/pull/2663"),
                         ("cann", "ops-math", "2663"))

    def test_plural_pulls(self):
        self.assertEqual(fs._parse_pr_url("https://gitcode.com/cann/ops-math/pulls/2663"),
                         ("cann", "ops-math", "2663"))

    def test_three_forms_normalize_to_same_triplet(self):
        forms = [
            "https://gitcode.com/cann/ops-math/pull/2663",
            "https://gitcode.com/cann/ops-math/pulls/2663",
            "https://gitcode.com/cann/ops-math/merge_requests/2663",
        ]
        parsed = {fs._parse_pr_url(u) for u in forms}
        self.assertEqual(parsed, {("cann", "ops-math", "2663")})

    def test_http_scheme_accepted(self):
        self.assertEqual(fs._parse_pr_url("http://gitcode.com/o/r/merge_requests/7"),
                         ("o", "r", "7"))

    def test_trailing_subpath_query_slash_tolerated(self):
        # /files 子路径、?tab=... query、末尾斜杠都不该破坏三段抽取
        self.assertEqual(fs._parse_pr_url("https://gitcode.com/cann/ops-math/pull/2663/files"),
                         ("cann", "ops-math", "2663"))
        self.assertEqual(fs._parse_pr_url("https://gitcode.com/cann/ops-math/merge_requests/2663?tab=diff"),
                         ("cann", "ops-math", "2663"))
        self.assertEqual(fs._parse_pr_url("https://gitcode.com/cann/ops-math/merge_requests/2663/"),
                         ("cann", "ops-math", "2663"))

    def test_surrounding_whitespace_stripped(self):
        self.assertEqual(fs._parse_pr_url("  https://gitcode.com/o/r/pull/9\n"),
                         ("o", "r", "9"))

    # --- fail-loud: 形态不认识 ---
    def test_unrecognized_forms_raise_valueerror(self):
        bads = [
            "https://gitcode.com/cann/ops-math",            # 缺 PR 段与编号
            "https://gitcode.com/cann/ops-math/pull",       # 缺编号
            "https://gitcode.com/cann/ops-math/pull/",      # 缺编号（有斜杠）
            "https://gitcode.com/cann/ops-math/pull/abc",   # 编号非数字
            "https://gitcode.com/cann/ops-math/pull/12ab",  # 残尾字母（\b 应挡掉）
            "https://gitcode.com/cann/ops-math/issues/5",   # 是 issue 不是 PR
            "https://github.com/cann/ops-math/pull/2663",   # host 非 gitcode
            "ftp://gitcode.com/o/r/pull/1",                 # 协议非 http(s)
            "cann/ops-math/pull/2663",                      # 无协议+host
            "not a url",
            "",
            None,
        ]
        for bad in bads:
            with self.assertRaises(ValueError, msg=f"应对 {bad!r} fail-loud（抛 ValueError）"):
                fs._parse_pr_url(bad)

    def test_error_message_actionable(self):
        # 错误信息要给出正确形态：提到 gitcode.com + merge_requests + pull，用户据此能自纠
        try:
            fs._parse_pr_url("https://github.com/cann/ops-math/pull/1")
        except ValueError as e:
            msg = str(e)
            self.assertIn("gitcode.com", msg)
            self.assertIn("merge_requests", msg)
            self.assertIn("pull", msg)
        else:
            self.fail("形态不认识应抛 ValueError")


class FetchPrFailModeTest(unittest.TestCase):
    """fetch_pr 层：区分「URL 形态错(fail-loud，不写文件)」与「网络取不到(记 note，仍写文件)」。

    桩掉 fetch_source._get，绝不打真网络。"""

    def setUp(self):
        self._orig_get = fs._get

    def tearDown(self):
        fs._get = self._orig_get

    def test_bad_url_raises_and_writes_no_pr_facts(self):
        # 回归核心：解析失败绝不产出空壳 pr_facts —— 既要抛 ValueError，又要 out 目录里没有 pr_facts.json，
        # 且网络在此之前一次都不该被碰。
        called = {"n": 0}

        def _boom(*a, **k):
            called["n"] += 1
            return 0, "should-not-be-reached"

        fs._get = _boom
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                fs.fetch_pr("https://gitcode.com/cann/ops-math", d)  # 缺 PR 段
            self.assertFalse(os.path.exists(os.path.join(d, "pr_facts.json")),
                             "URL 形态错时严禁落 pr_facts.json（不产空壳往下传）")
        self.assertEqual(called["n"], 0, "形态错应在任何网络调用之前 fail-loud")

    def test_recognized_url_network_fail_records_note_not_raise(self):
        # 环境问题(网络/token)与用户输入错要分开：不抛、仍写 pr_facts（含 source_repo）、notes 有可诊断信息，
        # 且 notes 不说成「URL 格式错」，免得用户误改本来正确的 URL。
        fs._get = lambda *a, **k: (0, "network down")  # 模拟取不到（URL 本身合法）
        with tempfile.TemporaryDirectory() as d:
            path = fs.fetch_pr("https://gitcode.com/cann/ops-math/pull/2663", d)
            self.assertTrue(os.path.exists(path), "URL 认识但网络失败时，应照常落 pr_facts.json")
            with open(path, encoding="utf-8") as f:
                facts = json.load(f)
        self.assertEqual(facts["source_repo"], "cann/ops-math", "URL 合法 → 三段应已抽出、非空壳")
        self.assertTrue(facts["notes"], "网络失败应留下可诊断的 notes")
        joined = " ".join(facts["notes"])
        self.assertNotIn("格式", joined, "网络失败的 notes 不该把它归因成 URL 格式错")

    def test_recognized_url_success_parses_files_and_op(self):
        # URL 认识 + 网络成功：走完整通路，pr_facts 落 source_repo / changed_files / target_dir，无空壳。
        def _fake_get(url, params=None, timeout=30):
            if url.endswith("/pulls/2663"):
                return 200, {"title": "t", "state": "opened", "base": {"ref": "master"},
                             "head": {"ref": "feat"}, "merged": False}
            if url.endswith("/pulls/2663/files"):
                return 200, [{"filename": "math/isclose/op_host/isclose.cpp"}]
            return 0, "n/a"  # 关键文件 contents API：取不到即可，不影响本用例断言

        fs._get = _fake_get
        with tempfile.TemporaryDirectory() as d:
            path = fs.fetch_pr("https://gitcode.com/cann/ops-math/pull/2663", d)
            with open(path, encoding="utf-8") as f:
                facts = json.load(f)
        self.assertEqual(facts["source_repo"], "cann/ops-math")
        self.assertEqual(facts["changed_files"], ["math/isclose/op_host/isclose.cpp"])
        self.assertEqual(facts["op"], "isclose")
        self.assertEqual(facts["target_dir"], "math/isclose")


class MalformedTailRejectedTest(unittest.TestCase):
    """编号后的残尾必须是 / ? # 或串尾——`\\d+\\b` 不够（`12-foo` 处也有词边界 → fail-open）。"""

    def test_digits_followed_by_dash_or_dot_rejected(self):
        for bad in ("https://gitcode.com/cann/catlass/pull/12-foo",
                    "https://gitcode.com/cann/catlass/pull/12.xyz",
                    "https://gitcode.com/cann/catlass/merge_requests/7_old"):
            with self.assertRaises(ValueError, msg=f"应拒: {bad}"):
                fs._parse_pr_url(bad)

    def test_legit_tails_still_accepted(self):
        """对照：合法残尾（/ 子路径、?query、#fragment、纯结尾）仍接受，证不是把整条路堵死。"""
        for ok in ("https://gitcode.com/cann/catlass/pull/12",
                   "https://gitcode.com/cann/catlass/pull/12/files",
                   "https://gitcode.com/cann/catlass/pulls/12?tab=diff",
                   "https://gitcode.com/cann/catlass/merge_requests/12#note_1"):
            self.assertEqual(fs._parse_pr_url(ok), ("cann", "catlass", "12"), ok)


class MainAbortsBeforeSideEffectsTest(unittest.TestCase):
    """PR URL 形态不认识 → 在**任何网络调用与产物写入之前**中止，绝不落半个产物。

    回归的是一个真实顺序 bug：原 main() 先 makedirs + fetch_taskdoc（任务书是链接时会真发网络请求、
    真写出 task_doc.md），到调 fetch_pr 时才报「PR 格式不认识」——半个产物已经落盘。"""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.called = []
        self._orig = fs.fetch_taskdoc
        fs.fetch_taskdoc = lambda *a, **k: self.called.append("fetch_taskdoc")  # 桩：不打网络

    def tearDown(self):
        fs.fetch_taskdoc = self._orig

    def test_bad_pr_url_aborts_before_taskdoc_and_leaves_no_artifacts(self):
        out = os.path.join(self.d, "out")
        with self.assertRaises(ValueError):
            fs.main(["--taskdoc", "https://example.com/t.md", "--pr",
                     "https://gitcode.com/cann/catlass", "--out", out])
        self.assertEqual(self.called, [], "取任务书在 PR 校验之前被调用了（顺序错）")
        self.assertFalse(os.path.exists(out), "产出目录被建了（不该落任何半产物）")


if __name__ == "__main__":
    unittest.main()
