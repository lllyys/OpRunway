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


class TaskdocSnapshotTest(unittest.TestCase):
    """R12 / 批 3：任务书**全文快照**入库——整条 golden 来源契约链的**前提**。

    没有它，`precision_policy.verify_authorization` 恒返 False → 任何声称「任务书指定了真值口径」
    的 golden 都被 `derive_golden_tier` 规则② 判 tier 4（unverifiable_authorization）、直接 blocked。
    快照不是可选装饰。"""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        # 刻意造：CRLF + 尾行无换行 + 中文 —— 任何「规范化」都会改字节、进而移行号
        self.src = os.path.join(self.d, "td.md")
        with open(self.src, "wb") as f:
            f.write("第一行\r\n实现方式更改成和cpu一致的比较逻辑值\r\n尾行无换行".encode("utf-8"))

    def test_snapshot_is_byte_identical(self):
        """**逐字节原样**，不许任何规范化。

        改一个字节，行号就可能移位、引文就可能对不上；而那时报出来的是
        「引文与出处对不上」这种**看起来像 agent 编造引文**的错，真病因（快照被规范化过）反而查不出来。"""
        dst = os.path.join(self.d, "ops", "Op", "task_doc.snapshot.md")
        digest, path = fs.write_taskdoc_snapshot(self.src, dst)
        with open(self.src, "rb") as f:
            raw = f.read()
        with open(path, "rb") as f:
            self.assertEqual(f.read(), raw, "快照必须与原文逐字节相同")
        import hashlib
        self.assertEqual(digest, hashlib.sha256(raw).hexdigest())

    def test_identical_content_is_idempotent(self):
        """内容一致时幂等：不重写、返回同一 sha256。"""
        dst = os.path.join(self.d, "ops", "Op", "task_doc.snapshot.md")
        first, _ = fs.write_taskdoc_snapshot(self.src, dst)
        second, _ = fs.write_taskdoc_snapshot(self.src, dst)
        self.assertEqual(first, second)

    def test_upstream_changed_fails_loud_instead_of_silently_keeping_stale(self):
        """上游任务书改版 → **fail-loud**，既不覆盖也不装没事。

        「不覆盖」是对的（引文锚绑着旧快照），但**安静地留着旧快照还打印旧 sha256** 更坏——
        调用方会以为刷新过了，于是验收基于一份自己都不知道过期的引文锚。
        报错要同时给出两个指纹 + 处置方式（删了重来 **并复核 cite 行号**，因为行号极可能移位）。"""
        dst = os.path.join(self.d, "ops", "Op", "task_doc.snapshot.md")
        old_digest, _ = fs.write_taskdoc_snapshot(self.src, dst)
        with open(self.src, "wb") as f:                      # 上游改版
            f.write("完全不同的新版任务书".encode("utf-8"))
        with self.assertRaises(RuntimeError) as cm:
            fs.write_taskdoc_snapshot(self.src, dst)
        msg = str(cm.exception)
        self.assertIn(old_digest, msg, "报错须给出既有快照指纹")
        self.assertIn("cite", msg, "须提醒复核 cite 行号（改版后行号极可能移位）")
        # 且**快照本身没被动过**
        with open(dst, "rb") as f:
            import hashlib
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), old_digest)

    def test_snapshot_unblocks_authorization_and_tampering_is_caught(self):
        """端到端：有快照 → 授权核得过（tier 1）；掉包 / 编造引文 → 当场拒。"""
        import precision_policy as P
        dst = os.path.join(self.d, "ops", "Op", "task_doc.snapshot.md")
        digest, path = fs.write_taskdoc_snapshot(self.src, dst)
        g = {"source": "single_api", "method_kind": "torch_cpu",
             "authorization": {"kind": "oracle_method",
                               "cite": f"{P.TASKDOC_SNAPSHOT_NAME}:2",
                               "quote": "更改成和cpu一致的比较逻辑值"},
             "taskdoc_snapshot": {"sha256": digest}}
        ok, why = P.verify_authorization(g, path)
        self.assertTrue(ok, why)
        self.assertEqual(P.derive_golden_tier(g, ok)[0], 1, "有据可核的任务书授权应落 tier 1")

        tampered = dict(g, taskdoc_snapshot={"sha256": "0" * 64})   # 掉包快照
        ok2, why2 = P.verify_authorization(tampered, path)
        self.assertFalse(ok2)
        self.assertIn("指纹不符", why2)
        self.assertEqual(P.derive_golden_tier(tampered, ok2), (4, True, "unverifiable_authorization"))

        forged = dict(g, authorization=dict(g["authorization"], quote="任务书里没有这句话"))
        ok3, why3 = P.verify_authorization(forged, path)
        self.assertFalse(ok3)
        self.assertIn("逐字子串", why3)

    def test_cli_writes_snapshot_and_prints_digest(self):
        """CLI `--snapshot-into` 落快照并打印 sha256（供 golden 作者直接粘进契约块）。"""
        out = os.path.join(self.d, "out"); ops = os.path.join(self.d, "ops", "Op")
        import contextlib, io as _io
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            fs.main(["--taskdoc", self.src, "--out", out, "--snapshot-into", ops])
        text = buf.getvalue()
        snap = os.path.join(ops, "task_doc.snapshot.md")
        self.assertTrue(os.path.isfile(snap), text)
        self.assertIn("sha256 = ", text)
        self.assertIn("task_doc.snapshot.md", text)


class HeadShaPinningTest(unittest.TestCase):
    """U5：**被测对象 = PR head 那个 commit** —— 钉 `head.sha`，不按分支名兜底。

    2026-07-22 真打 gitcode API 实测（cann/ops-math）：
      · MR 3400（open）：`head.repo` 是**贡献者 fork**、`head.ref` 字面就叫 `"master"`；
        按分支名去 base 仓取会拿到 base 的 master（sha `e16a230c` ≠ head `9b494b2d`）——
        **静默取到完全不相干的代码，却仍被记成「取自 PR head」**。
      · MR 2663（merged，正是 Pdist 首跑那个）：head 同样在 fork 上，旧实现记的是 `head=base="master"`、无 sha。
      · `contents?ref=<head_sha>` 对 **base 仓** HTTP 200（**仅这 2 个 PR 实测，非平台保证**）→
        实现以 base 仓为首选、拿不到时用**同一个 sha** 退到 head_repo。
    桩掉 `_get`/`_repo_file`，绝不打真网络。"""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self._get, self._file = fs._get, fs._repo_file
        self.asked = []          # [(owner, repo, ref)]

    def tearDown(self):
        fs._get, fs._repo_file = self._get, self._file

    def _stub(self, head_sha, head_ref="master", head_repo="contrib/ops-math"):
        head = {"ref": head_ref, "sha": head_sha, "repo": {"full_name": head_repo}}

        def g(url, params=None, timeout=30):
            if url.endswith("/files"):
                return 200, [{"filename": "experimental/math/foo/examples/test_aclnn_foo.cpp"}]
            return 200, {"title": "t", "state": "open", "base": {"ref": "master"}, "head": head}
        fs._get = g
        # ⚠ 桩必须记 **(owner, repo, ref) 三元组**：只记 ref 的话，实现哪怕向错误的仓请求，测试也全绿。
        fs._repo_file = lambda o, r, p, ref=None: (self.asked.append((o, r, ref)) or "src") if ref else None

    def test_key_files_pinned_to_head_sha_not_branch_name(self):
        self._stub("9b494b2d835fd8a9")
        fs.fetch_pr("https://gitcode.com/cann/ops-math/merge_requests/3400", self.d)
        facts = json.load(open(os.path.join(self.d, "pr_facts.json"), encoding="utf-8"))
        self.assertEqual(facts["head_sha"], "9b494b2d835fd8a9")
        self.assertTrue(facts["is_fork"], "head.repo 与 base 仓不同 → 应判 fork")
        # 核心断言：**只按 sha 问过**，一次都没拿分支名去问（那正是取错代码的路）
        refs = {a[2] for a in self.asked}
        self.assertEqual(refs, {"9b494b2d835fd8a9"}, self.asked)
        self.assertNotIn("master", refs)
        # 且首选 base 仓（fork 只作 404 退路）——证没有一上来就打 fork
        self.assertEqual(self.asked[0][:2], ("cann", "ops-math"), self.asked[0])

    def test_no_head_sha_fetches_nothing_and_says_why(self):
        """拿不到 head.sha → **一个关键文件都不取**，并说清为什么（宁可没有，不要来源不明的）。"""
        self._stub(None)
        fs.fetch_pr("https://gitcode.com/cann/ops-math/merge_requests/1", self.d)
        facts = json.load(open(os.path.join(self.d, "pr_facts.json"), encoding="utf-8"))
        self.assertEqual(self.asked, [], "无 sha 时不该按任何 ref 取文件")
        self.assertFalse(facts.get("key_files"))
        self.assertEqual(facts.get("blocked"), "missing_head_sha",
                         "缺 head.sha 须给**机读**阻断状态，只记 note 是 fail-open")
        self.assertTrue(any("无法钉死被测 commit" in n for n in facts["notes"]), facts["notes"])


    def test_same_repo_head_is_not_flagged_as_fork_case_insensitive(self):
        """同仓（仅大小写不同）不得误判成 fork——否则会平白多打一次 fork 仓的请求。"""
        self._stub("abc123", head_repo="CANN/Ops-Math")
        fs.fetch_pr("https://gitcode.com/cann/ops-math/merge_requests/7", self.d)
        facts = json.load(open(os.path.join(self.d, "pr_facts.json"), encoding="utf-8"))
        self.assertFalse(facts["is_fork"], facts["head_repo"])
        self.assertEqual({a[:2] for a in self.asked}, {("cann", "ops-math")}, self.asked)

    def test_unknown_head_repo_is_none_not_false(self):
        """`head.repo` 缺失 → is_fork 应为 **None（不知道）**，不是 False（同仓）。

        默认成「同仓」会让下游少一层警觉，正是本仓最忌的「不知道当成没问题」。"""
        self._stub("abc123", head_repo=None)
        fs.fetch_pr("https://gitcode.com/cann/ops-math/merge_requests/8", self.d)
        facts = json.load(open(os.path.join(self.d, "pr_facts.json"), encoding="utf-8"))
        self.assertIsNone(facts["is_fork"])

    def test_falls_back_to_head_repo_when_base_lacks_the_sha(self):
        """base 仓拿不到该 sha → 用**同一个 sha**退到 head_repo（不引入分支名风险）。

        「fork 的 sha 一定能从 base 仓解析」只在实测的两个 PR 上观察到，**不是平台保证**。"""
        self._stub("deadbeef", head_repo="contrib/ops-math")
        base = ("cann", "ops-math")
        real = fs._repo_file
        fs._repo_file = lambda o, r, p, ref=None: None if (o, r) == base else real(o, r, p, ref)
        fs.fetch_pr("https://gitcode.com/cann/ops-math/merge_requests/9", self.d)
        facts = json.load(open(os.path.join(self.d, "pr_facts.json"), encoding="utf-8"))
        self.assertTrue(facts.get("key_files"), "应经 head_repo 退路取到")
        self.assertIn(("contrib", "ops-math", "deadbeef"), self.asked, self.asked)


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
