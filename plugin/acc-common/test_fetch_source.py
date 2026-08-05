"""fetch_source.py 的 PR URL 解析单测（U2）——stdlib unittest，不打真网络。

跑: python3 -m unittest test_fetch_source -v   （在 acc-common/ 下）

覆盖:
  - 各种 URL 形态都能解析成同一 (owner, repo, num)：/pull/N（GitHub 单数）· /pulls/N · /merge_requests/N
  - 形态不认识 → fail-loud（抛 ValueError）+ 错误信息可操作（给出正确形态）
  - 回归: 解析失败**绝不产出空壳 pr_facts**（out 目录里不落 pr_facts.json）
  - 区分两种失败: URL 认识但网络取不到 → 不抛、记 notes、仍写 pr_facts（含 source_repo），
    且 notes 不把它说成「URL 格式错」（免得误导用户改 URL）
  - Gap-1: `aclnn_*.h` 接口头是**一等 key_file**——不受 `/op_host/` 档 `[:4]` 截断、优先取、
    剔 `*_impl.h`、层级不预设、只收目标算子目录下的、去重、缺席时 notes 点名
不打真网络: URL 形态用例走纯函数 _parse_pr_url（解析在网络之前）; 网络分支用桩替掉 fetch_source._get。
"""
import os, json, shutil, tempfile, unittest
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


class InterfaceKindDetectTest(unittest.TestCase):
    """批 6b B-core：`_detect_interface_kind` 的 5 条规则（据实 clone 4 仓分类得出）逐条钉死。

    合成最小 fixture（不依赖 repos/ 在场）；真实 example 的端到端验证在 workflow wf_873486e1 里。"""

    def test_aclnn_2stage_and_entry(self):
        c = ("aclnnGeluGetWorkspaceSize(self, out, &ws, &exe);\n"
             "aclnnGelu(wsAddr, ws, exe, stream);\n")
        ik, entry, _ = fs._detect_interface_kind({"activation/gelu/examples/test_aclnn_gelu.cpp": c})
        self.assertEqual(ik, "aclnn_2stage")
        self.assertEqual(entry, "aclnnGelu")

    def test_entry_is_real_name_not_op_derived(self):
        """带版本后缀的入口从 test_aclnn 抽真实名（aclnnPromptFlashAttentionV3），非目录名派生。

        这是 Equal 血教训 + transformer V3/V5 实测的落地：runner 锚定用真实函数名。"""
        c = ("aclnnPromptFlashAttentionV3GetWorkspaceSize(q, k, v, &ws, &exe);\n"
             "aclnnPromptFlashAttentionV3(a, ws, exe, stream);\n")
        _, entry, _ = fs._detect_interface_kind({"attention/pfa/examples/test_aclnn_pfa.cpp": c})
        self.assertEqual(entry, "aclnnPromptFlashAttentionV3")

    def test_hccl_is_distributed_blocked(self):
        c = ('#include "hccl/hccl.h"\n'
             "aclnnAllGatherMatmulGetWorkspaceSize(x, &ws, &exe);\n"
             "aclnnAllGatherMatmul(a, ws, exe, stream);\nHcclComm comm;\n")
        ik, _, _ = fs._detect_interface_kind({"mc2/agm/examples/test_aclnn_agm.cpp": c})
        self.assertEqual(ik, "aclnn_2stage_distributed")

    def test_library_header_no_aclnn(self):
        ik, entry, _ = fs._detect_interface_kind(
            {"include/solver.h": "aclsolverCreate(&handle);", "README.md": "x"})
        self.assertEqual(ik, "library_header")
        self.assertIsNone(entry)

    def test_ws_without_second_stage_not_aclnn(self):
        """只有 GetWorkspaceSize、无配对第二段 → **不**判 aclnn_2stage（fail-closed 到 unknown）。"""
        c = "aclnnGeluGetWorkspaceSize(self, out, &ws, &exe);\n"   # 无 aclnnGelu(...executor...)
        ik, _, _ = fs._detect_interface_kind(
            {"examples/test_aclnn_gelu.cpp": c, "op_host/gelu_def.cpp": 'AddConfig("ascend950")'})
        self.assertEqual(ik, "unknown")

    def test_op_def_without_aclnn_is_unknown_failclosed(self):
        """有 op_def 迹象但探不到 aclnn 配对 → unknown（fail-closed，不猜成可放行）。"""
        ik, _, _ = fs._detect_interface_kind({"op_host/foo_def.cpp": 'AddConfig("ascend950")'})
        self.assertEqual(ik, "unknown")

    def test_empty_keyfiles_is_library_header(self):
        """取不到任何 key_files（网络/无 PR）→ library_header，下游 fail-closed，不假装 aclnn。"""
        ik, entry, _ = fs._detect_interface_kind({})
        self.assertEqual(ik, "library_header")
        self.assertIsNone(entry)

    def test_commented_aclnn_not_matched(self):
        """注释掉的 aclnn 调用不算（codex 审：`// aclnnFooGetWorkspaceSize(...)` 曾被误判成 aclnn）。"""
        c = ("// aclnnFakeGetWorkspaceSize(a, &ws, &exe);\n"
             "/* aclnnFake(b, ws, exe, stream); */\nge::Session s;\n")
        ik, _, _ = fs._detect_interface_kind({"x/examples/test_geir_x.cpp": c})
        self.assertNotEqual(ik, "aclnn_2stage")

    def test_hccl_in_auxiliary_file_still_distributed(self):
        """HCCL include 落在辅助文件（非命中 aclnn 的那个）→ 仍判 distributed（codex 审跨文件加固）。"""
        kf = {"mc2/agm/examples/test_aclnn_agm.cpp": "aclnnAgmGetWorkspaceSize(a,&ws,&exe);\naclnnAgm(b,ws,exe,stream);",
              "mc2/agm/examples/hccl_helper.cpp": '#include "hccl/hccl.h"\nHcclComm comm;'}
        ik, _, _ = fs._detect_interface_kind(kf)
        self.assertEqual(ik, "aclnn_2stage_distributed")

    def test_geir_graph_engine_detected(self):
        """ops-nn 混有 geir 图引擎算子（celu/bnll 用 test_geir_*.cpp + ge::Session）→ geir（BLOCKED-另立），非 aclnn。

        批 6b B-core 18 算子逐个核暴露：ops-nn **不是清一色 aclnn**。探测器对它们本就 fail-closed（不误放行），
        此测试钉住「显式识别 geir、给准类别」——比笼统 unknown 更利于下游归类。"""
        c = ("op::Celu celu;\nge::Session session(options);\n"
             "session.AddGraph(0, graph);\nsession.RunGraph(0, inputs, outputs);\n")
        ik, entry, _ = fs._detect_interface_kind(
            {"activation/celu/examples/test_geir_celu.cpp": c,
             "activation/celu/op_host/celu_def.cpp": 'AddConfig("ascend950")'})
        self.assertEqual(ik, "geir")
        self.assertIsNone(entry)


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


class AclnnHeaderIsFirstClassKeyFileTest(unittest.TestCase):
    """Gap-1（2026-07-25 dogfood 逼出）：`aclnn_*.h` 接口头是 **aclnn 路由的第一依据**，
    必须作**一等 key_file**、不被 `/op_host/` 那一档的 `[:4]` 截断挤掉。

    实测病灶（median PR6429）：接口头在 `<op_subdir>/op_host/op_api/aclnn_median.h`，与一堆
    `op_host/*.cpp` 挤在同一档、排在第 5 位后被截断 → `key_files` 里没有头 → 下游只能凭 example
    的调用写法或算子名猜符号（= 「验的不是 PR、是 CANN 内置同名实现」那条假 PASS 的入口）。
    桩掉 `_get`/`_repo_file`，绝不打真网络。"""

    OP_DIR = "experimental/index/median"
    HDR = OP_DIR + "/op_host/op_api/aclnn_median.h"

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self._get, self._file = fs._get, fs._repo_file
        self.asked = []

    def tearDown(self):
        fs._get, fs._repo_file = self._get, self._file

    def _stub(self, files):
        def g(url, params=None, timeout=30):
            if url.endswith("/files"):
                return 200, [{"filename": f} for f in files]
            return 200, {"title": "t", "state": "open", "base": {"ref": "master"},
                         "head": {"ref": "feat", "sha": "abc123", "repo": {"full_name": "cann/ops-nn"}}}
        fs._get = g
        fs._repo_file = lambda o, r, p, ref=None: (self.asked.append(p) or f"// {p}") if ref else None

    def _facts(self, files, num=6429):
        self._stub(files)
        fs.fetch_pr(f"https://gitcode.com/cann/ops-nn/merge_requests/{num}", self.d)
        with open(os.path.join(self.d, "pr_facts.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_header_survives_when_op_host_files_exceed_the_cap(self):
        """op_host 下先摆 6 个 .cpp（超过 `[:4]`）→ 接口头仍必须在 key_files 里。"""
        files = [f"{self.OP_DIR}/op_host/median_tiling_{i}.cpp" for i in range(6)]
        files.append(self.HDR)
        facts = self._facts(files)
        self.assertIn(self.HDR, facts["key_files"],
                      f"接口头被截断挤掉了（key_files={sorted(facts['key_files'])}）——"
                      f"aclnn 路由的第一依据不得受 [:4] 截断")

    def test_header_is_fetched_first(self):
        """顺序即优先级：接口头排在所有其它候选之前被取（取材失败时先保住最要紧的那份）。"""
        files = [f"{self.OP_DIR}/op_host/a{i}.cpp" for i in range(5)] + [
            f"{self.OP_DIR}/examples/test_aclnn_median.cpp", self.HDR]
        self._facts(files)
        self.assertEqual(self.asked[0], self.HDR, self.asked)

    def test_impl_header_excluded(self):
        """`*_impl.h` 是内部实现头、不是对外两段式接口 → **不得算作一等接口头**（与 aclnn_adapter 同口径）。

        ⚠ 这里断言的是 `aclnn_headers`（一等档）而**不是** `key_files`：`_impl.h` 落在 `/op_host/` 那一档、
        被当作普通上下文文件捞进 `key_files` 是无害且合理的（多一份实现上下文没坏处）。
        真正要防的是它**冒充第一依据**——尤其别把「缺席告警」压掉（它同样匹配 `aclnn_*.h` 正则）。"""
        impl = self.OP_DIR + "/op_host/op_api/aclnn_median_impl.h"
        facts = self._facts([impl, f"{self.OP_DIR}/op_host/median.cpp"])
        self.assertNotIn(impl, facts["aclnn_headers"], "*_impl.h 不得被当成对外接口头")
        self.assertEqual(facts["aclnn_headers"], [], "只有 impl 头 = 一等接口头仍然缺席")
        self.assertTrue(any("aclnn 接口头" in n for n in facts["notes"]),
                        f"只有 *_impl.h 时必须仍报「第一依据缺席」，不许被压掉（fail-open）：{facts['notes']}")

    def test_header_layer_is_not_hardcoded(self):
        """落点**不预设层级**：`op_api/` 直挂在算子目录下的布局同样要收（钉死一层会把真 PR 判成非域内）。"""
        flat = self.OP_DIR + "/op_api/aclnn_median.h"
        facts = self._facts([flat, f"{self.OP_DIR}/op_host/median.cpp"])
        self.assertIn(flat, facts["key_files"])

    def test_other_ops_header_not_collected(self):
        """一等档只收**目标算子目录下**的头——同 PR 里别的算子的接口头不得冒充第一依据（会拿错签名）。

        ⚠ 同 `test_impl_header_excluded`：断言在 `aclnn_headers` 上。别的算子的头被 `/op_host/` 档
        当普通上下文捞进 `key_files` 无害；有害的是它**冒充本算子的签名来源**。"""
        other = "experimental/index/other_op/op_host/op_api/aclnn_other.h"
        # 目标算子目录由**首个**匹配的改动文件判出 → 把本算子的文件摆首位，别让桩自己跑偏
        facts = self._facts([f"{self.OP_DIR}/op_host/median.cpp", other, self.HDR])
        self.assertEqual(facts["aclnn_headers"], [self.HDR],
                         f"一等接口头只该有本算子那份：{facts['aclnn_headers']}")

    def test_other_ops_header_does_not_suppress_missing_note(self):
        """回归（fail-open）：本算子**没有**接口头、但同 PR 里别的算子有 → 缺席告警**仍须报**。

        旧写法拿 `_ACLNN_HDR_RE` 扫整个 `key_files`，`other_op` 的头会把告警压掉，
        下游于是在「第一依据缺席」的情况下静默去猜符号——这正是「验的不是 PR、是同名内置」那条路。"""
        other = "experimental/index/other_op/op_host/op_api/aclnn_other.h"
        facts = self._facts([f"{self.OP_DIR}/op_host/median.cpp", other])
        self.assertEqual(facts["aclnn_headers"], [])
        self.assertTrue(any("aclnn 接口头" in n for n in facts["notes"]), facts["notes"])

    def test_header_not_requested_twice(self):
        """接口头同时命中「一等档」与「/op_host/ 档」→ 去重，别多打一次请求。"""
        self._facts([self.HDR, f"{self.OP_DIR}/op_host/median.cpp"])
        self.assertEqual(self.asked.count(self.HDR), 1, self.asked)

    def test_missing_header_is_called_out_in_notes(self):
        """一份接口头都没取到 → notes 必须点名说「第一依据缺席」，不许静默往下走。"""
        facts = self._facts([f"{self.OP_DIR}/op_host/median.cpp"])
        self.assertTrue(any("aclnn 接口头" in n for n in facts["notes"]), facts["notes"])

    def test_header_present_no_missing_note(self):
        """取到了就不该再报缺席（免得告警噪声把真信号淹掉）。"""
        facts = self._facts([self.HDR])
        self.assertFalse(any("没有取到 aclnn 接口头" in n for n in facts["notes"]), facts["notes"])


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
        work_snap = os.path.join(out, "task_doc.snapshot.md")
        self.assertTrue(os.path.isfile(work_snap), text)
        with open(work_snap, "rb") as got, open(self.src, "rb") as want:
            self.assertEqual(got.read(), want.read())

    def test_changed_taskdoc_leaves_existing_workdir_byte_identical(self):
        out = os.path.join(self.d, "out")
        ops = os.path.join(self.d, "ops", "Op")
        fs.main(["--taskdoc", self.src, "--out", out, "--snapshot-into", ops])
        paths = [
            os.path.join(out, "task_doc.md"),
            os.path.join(out, "task_doc.snapshot.md"),
            os.path.join(ops, "task_doc.snapshot.md"),
        ]
        before = {path: open(path, "rb").read() for path in paths}
        with open(self.src, "wb") as changed:
            changed.write("新版任务书".encode("utf-8"))
        with self.assertRaisesRegex(RuntimeError, "未写任何新任务书工件"):
            fs.main([
                "--taskdoc", self.src, "--out", out,
                "--snapshot-into", ops,
            ])
        after = {path: open(path, "rb").read() for path in paths}
        self.assertEqual(after, before)

    def test_snapshot_symlink_is_rejected(self):
        target = os.path.join(self.d, "victim")
        with open(target, "wb") as out:
            out.write(b"do not touch")
        snapshot = os.path.join(self.d, "task_doc.snapshot.md")
        os.symlink(target, snapshot)
        with self.assertRaisesRegex(RuntimeError, "符号链接"):
            fs.write_taskdoc_snapshot(self.src, snapshot)
        with open(target, "rb") as src:
            self.assertEqual(src.read(), b"do not touch")


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


class SourceFactsTest(unittest.TestCase):
    def _facts(self):
        sha = "a" * 40
        return {
            "pr_url": "https://gitcode.com/cann/ops-nn/pull/6429",
            "source_repo": "cann/ops-nn",
            "head_sha": sha,
            "head_repo": "contributor/ops-nn",
            "is_fork": True,
            "state": "opened",
            "changed_files": ["index/median/op_host/aclnn_median.h"],
            "key_files": {"index/median/op_host/aclnn_median.h": "void aclnnMedian();"},
            "key_files_ref": {"index/median/op_host/aclnn_median.h": sha},
            "aclnn_headers": ["index/median/op_host/aclnn_median.h"],
            "op": "median",
            "target_dir": "index/median",
            "interface_kind": "aclnn_2stage",
            "aclnn_entry": "aclnnMedian",
        }

    def test_complete_payload_binds_taskdoc_head_and_key_file(self):
        with tempfile.TemporaryDirectory() as d:
            task = os.path.join(d, "task_doc.md")
            with open(task, "wb") as out:
                out.write("任务书".encode())
            payload = fs.build_source_facts(task, self._facts())
        self.assertEqual(payload["completeness"], {"status": "complete", "reasons": []})
        self.assertEqual(payload["pr"]["head_sha"], "a" * 40)
        self.assertRegex(payload["taskdoc"]["bytes_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(payload["taskdoc"]["snapshot_sha256"],
                         payload["taskdoc"]["bytes_sha256"])
        self.assertRegex(payload["key_files"][0]["bytes_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("content", payload["key_files"][0])

    def test_wrong_key_ref_and_partial_facts_are_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            task = os.path.join(d, "task_doc.md")
            with open(task, "wb") as out:
                out.write(b"x")
            facts = self._facts()
            facts["key_files_ref"] = {
                "index/median/op_host/aclnn_median.h": "b" * 40}
            payload = fs.build_source_facts(task, facts)
            self.assertEqual(payload["completeness"]["status"], "blocked")
            self.assertIn("key_file_ref_not_head:index/median/op_host/aclnn_median.h",
                          payload["completeness"]["reasons"])

            partial = {"pr_url": facts["pr_url"], "source_repo": "cann/ops-nn",
                       "notes": ["network down"], "blocked": "missing_head_sha"}
            payload = fs.build_source_facts(task, partial)
            self.assertEqual(payload["completeness"]["status"], "blocked")
            self.assertIn("missing_or_invalid_head_sha", payload["completeness"]["reasons"])

    def test_write_round_trip_and_taskdoc_change_changes_digest(self):
        import content_address as ca

        with tempfile.TemporaryDirectory() as d:
            task = os.path.join(d, "task_doc.md")
            with open(task, "wb") as out:
                out.write(b"v1")
            path = fs.write_source_facts(task, self._facts(), d)
            first = json.load(open(path, encoding="utf-8"))
            payload = ca.read_artifact(
                d, "source_facts.json", "oprunway/source-facts/v1")
            self.assertEqual(payload["completeness"]["status"], "complete")
            with open(task, "wb") as out:
                out.write(b"v2")
            fs.write_source_facts(task, self._facts(), d)
            second = json.load(open(path, encoding="utf-8"))
            self.assertNotEqual(first["digest"], second["digest"])


class RootDigestTest(unittest.TestCase):
    """`compute_root_digest` —— 本地通路的 provenance 锚，确定性与三个已知坑。"""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.repo = os.path.join(self.d, "repo")
        os.makedirs(os.path.join(self.repo, "op/sub"), exist_ok=True)
        self._write("op/a.cpp", b"int a;")
        self._write("op/sub/b.h", b"void b();")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _write(self, rel, data):
        full = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(data)
        return full

    def _digest(self):
        return fs.compute_root_digest(self.repo, "op")

    def test_deterministic_across_runs(self):
        self.assertEqual(self._digest(), self._digest())
        self.assertRegex(self._digest(), r"^[0-9a-f]{64}$")

    def test_content_change_changes_digest(self):
        before = self._digest()
        self._write("op/a.cpp", b"int a; // changed")
        self.assertNotEqual(before, self._digest())

    def test_excluded_dirs_do_not_affect_digest(self):
        """`.git/` `__pycache__/` `build/` `*.pyc` 的变动不得进摘要——否则本地跑一次编译就换个身份。"""
        before = self._digest()
        self._write("op/.git/HEAD", b"ref: refs/heads/x")
        self._write("op/__pycache__/x.cpython-311.pyc", b"\x00\x01")
        self._write("op/sub/__pycache__/deep.pyc", b"\x00\x02")   # 嵌套层也要排除，不只首段
        self._write("op/build/out.o", b"obj")
        self._write("op/stale.pyc", b"\x00\x03")
        self.assertEqual(before, self._digest())

    def test_empty_directory_is_counted(self):
        """⭐ 删掉目录里最后一个文件，摘要必须变——空目录不计入就查不出这种删除。"""
        os.makedirs(os.path.join(self.repo, "op/empty"), exist_ok=True)
        with_empty = self._digest()
        os.rmdir(os.path.join(self.repo, "op/empty"))
        self.assertNotEqual(with_empty, self._digest())

        os.remove(os.path.join(self.repo, "op/sub/b.h"))          # sub/ 变空目录
        self.assertNotEqual(with_empty, self._digest())

    def test_symlink_and_regular_file_do_not_collide(self):
        """⭐ 把文件换成指向别处的软链，摘要必须变——kind 前缀区分 f/l 就是为这个。"""
        target = os.path.join(self.repo, "op/link_me")
        with open(target, "wb") as fh:
            fh.write(b"payload")
        as_file = self._digest()
        os.remove(target)
        os.symlink("payload", target)                             # 内容相同的软链（目标名恰好也是 payload）
        self.assertNotEqual(as_file, self._digest())

    def test_directory_symlink_is_counted(self):
        """⭐ 目录软链必须进摘要。

        `os.walk` 会把目录软链放进 `dirnames`、又因 `followlinks=False` 不递归进去，
        于是整条**完全不进摘要**——加一个、删一个都查不出来。这里锁死它。
        """
        before = self._digest()
        os.symlink("sub", os.path.join(self.repo, "op/sub_link"))
        after_add = self._digest()
        self.assertNotEqual(before, after_add, "加目录软链后摘要没变（漏计）")
        os.remove(os.path.join(self.repo, "op/sub_link"))
        os.symlink("elsewhere", os.path.join(self.repo, "op/sub_link"))
        self.assertNotEqual(after_add, self._digest(), "改目录软链目标后摘要没变")

    def test_executable_bit_change_changes_digest(self):
        """⭐ build 脚本 644→755 会改构建行为，摘要必须跟着变。"""
        script = self._write("op/build.sh", b"#!/bin/sh\necho hi\n")
        os.chmod(script, 0o644)
        before = self._digest()
        os.chmod(script, 0o755)
        self.assertNotEqual(before, self._digest())

    def test_unreadable_directory_is_not_silently_skipped(self):
        """⭐ 遍历出错不许吞：少读一棵子树照样算得出 digest = 一份假摘要。"""
        if os.geteuid() == 0:
            self.skipTest("root 无视目录权限位，构造不出不可读目录")
        blocked = os.path.join(self.repo, "op/locked")
        os.makedirs(blocked, exist_ok=True)
        self._write("op/locked/inner.cpp", b"x")
        os.chmod(blocked, 0o000)
        try:
            with self.assertRaises(RuntimeError):
                self._digest()
        finally:
            os.chmod(blocked, 0o755)

    def test_non_regular_entry_rejected(self):
        """FIFO 的被测语义未定义 → fail-closed，不静默跳过（跳过 = 摘要少覆盖一块）。"""
        fifo = os.path.join(self.repo, "op/pipe")
        try:
            os.mkfifo(fifo)
        except (AttributeError, OSError):
            self.skipTest("本平台不支持 mkfifo")
        with self.assertRaises(RuntimeError) as cm:
            self._digest()
        self.assertIn("fail-closed", str(cm.exception))

    def test_op_subdir_escape_rejected(self):
        with self.assertRaises(RuntimeError):
            fs.compute_root_digest(self.repo, "../")

    def test_missing_op_subdir_rejected(self):
        with self.assertRaises(RuntimeError):
            fs.compute_root_digest(self.repo, "no_such_dir")

    def test_digest_policy_is_structured_and_versioned(self):
        """收据里的排除规则必须是结构化 + 版本化的，不是看起来像 glob 的字符串。"""
        pol = fs.digest_policy()
        self.assertEqual(pol["algorithm"], fs.DIGEST_ALGORITHM)
        self.assertIsInstance(pol["algorithm_version"], int)
        self.assertIn("__pycache__", pol["excluded_segment_names"])
        self.assertIn(".pyc", pol["excluded_basename_suffixes"])


@unittest.skipUnless(shutil.which("git"), "需要 git 可执行文件")
class LocalCheckoutFetchTest(unittest.TestCase):
    """`--local-repo` 全链路：产物形态、completeness 分支、dirty 门、互斥校验。"""

    OP_SUBDIR = "experimental/math/roll"

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.repo = os.path.join(self.d, "ops")
        self.out = os.path.join(self.d, "out")
        os.makedirs(self.out, exist_ok=True)
        self._write(f"{self.OP_SUBDIR}/op_host/op_api/aclnn_roll.h",
                    "aclnnStatus aclnnRollGetWorkspaceSize(const aclTensor*, aclTensor*,"
                    " uint64_t*, aclOpExecutor**);\n")
        self._write(f"{self.OP_SUBDIR}/examples/test_aclnn_roll.cpp",
                    "int main(){ aclnnRollGetWorkspaceSize(x, y, &ws, &exe);"
                    " aclnnRoll(ws, exe, stream); }\n")
        self._write(f"{self.OP_SUBDIR}/op_host/roll_def.cpp", "// op def\n")
        self.task = os.path.join(self.d, "task.md")
        with open(self.task, "wb") as fh:
            fh.write("# Roll 任务书\n".encode())

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _write(self, rel, text):
        full = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)

    def _git(self, *args):
        import subprocess
        return subprocess.run(("git", "-C", self.repo) + args,
                              capture_output=True, text=True, check=True)

    def _init_git(self, base_branch="master"):
        self._git("init", "-q", "-b", base_branch)
        self._git("config", "user.email", "t@example.invalid")
        self._git("config", "user.name", "t")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "base")

    def _payload(self):
        import content_address as ca
        return ca.read_artifact(self.out, "source_facts.json", "oprunway/source-facts/v1")

    def test_clean_git_repo_with_base_ref_is_complete(self):
        self._init_git()
        self._git("checkout", "-q", "-b", "feature")
        self._write(f"{self.OP_SUBDIR}/op_host/roll_def.cpp", "// op def v2\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "change")
        rc = fs.main(["--taskdoc", self.task, "--local-repo", self.repo,
                      "--op-subdir", self.OP_SUBDIR, "--base-ref", "master",
                      "--out", self.out])
        self.assertEqual(rc, 0)
        for name in ("task_doc.md", "task_doc.snapshot.md", "pr_facts.json", "source_facts.json"):
            self.assertTrue(os.path.exists(os.path.join(self.out, name)), name)
        p = self._payload()
        self.assertEqual(p["completeness"]["status"], "complete")
        self.assertEqual(p["completeness"]["reasons"], [])
        self.assertNotIn("warnings", p["completeness"])
        self.assertEqual(p["dut_source"], "local_checkout")
        self.assertNotIn("pr", p, "本地事实不得伪装成 PR 事实")
        self.assertRegex(p["local_checkout"]["root_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(p["local_checkout"]["digest_policy"], fs.digest_policy())
        self.assertEqual(p["local_checkout"]["op_subdir"], self.OP_SUBDIR)
        self.assertFalse(p["local_checkout"]["git"]["dirty"])
        self.assertEqual(p["changed_files"], [f"{self.OP_SUBDIR}/op_host/roll_def.cpp"])
        # 关键文件锚 = root_digest（不是 head_sha，也不是 None）
        self.assertEqual({k["ref"] for k in p["key_files"]},
                         {p["local_checkout"]["root_digest"]})

    def test_non_git_directory_still_complete_without_git_key(self):
        rc = fs.main(["--taskdoc", self.task, "--local-repo", self.repo,
                      "--op-subdir", self.OP_SUBDIR, "--out", self.out])
        self.assertEqual(rc, 0)
        p = self._payload()
        self.assertEqual(p["completeness"]["status"], "complete")
        self.assertNotIn("git", p["local_checkout"], "非 git 仓不得写空壳 git 键")

    def test_missing_base_ref_marks_unavailable_not_empty_list(self):
        """⭐ `[]` 的语义是「没有改动」，会让下游以为 PR 什么都没改——必须是 'unavailable'。"""
        self._init_git()
        rc = fs.main(["--taskdoc", self.task, "--local-repo", self.repo,
                      "--op-subdir", self.OP_SUBDIR, "--out", self.out])
        self.assertEqual(rc, 0)
        p = self._payload()
        self.assertEqual(p["changed_files"], "unavailable")
        self.assertNotEqual(p["changed_files"], [])
        self.assertEqual(p["completeness"]["status"], "complete")     # 非阻塞
        self.assertEqual(p["completeness"]["warnings"], ["changed_files_unavailable"])

    def test_dirty_worktree_blocked_without_allow_dirty(self):
        self._init_git()
        self._write(f"{self.OP_SUBDIR}/op_host/roll_def.cpp", "// uncommitted\n")
        rc = fs.main(["--taskdoc", self.task, "--local-repo", self.repo,
                      "--op-subdir", self.OP_SUBDIR, "--out", self.out])
        # ⭐ 落盘 ≠ 成功：blocked 必须非 0 退出，否则只看退出码的 shell 调用方会照常往下走。
        self.assertEqual(rc, 3)
        p = self._payload()
        self.assertEqual(p["completeness"]["status"], "blocked")
        self.assertIn("dirty_worktree_not_allowed", p["completeness"]["reasons"])

    def test_dirty_worktree_allowed_with_full_accounting_and_warning(self):
        self._init_git()
        self._write(f"{self.OP_SUBDIR}/op_host/roll_def.cpp", "// uncommitted\n")
        rc = fs.main(["--taskdoc", self.task, "--local-repo", self.repo,
                      "--op-subdir", self.OP_SUBDIR, "--allow-dirty", "--out", self.out])
        self.assertEqual(rc, 0)
        p = self._payload()
        self.assertEqual(p["completeness"]["status"], "complete")
        # ⭐ 降级必须留痕：dirty 放行却没有 warning，就是以干净 pass 混过去
        self.assertIn(fs.WARN_DIRTY_WORKTREE_ALLOWED, p["completeness"]["warnings"])
        git = p["local_checkout"]["git"]
        self.assertTrue(git["dirty"])
        self.assertIn(f"{self.OP_SUBDIR}/op_host/roll_def.cpp", git["dirty_files"])
        self.assertIn(f"{self.OP_SUBDIR}/op_host/roll_def.cpp", git["dirty_files_in_op_subdir"])

    def test_dirty_path_with_space_and_unicode_is_recorded_intact(self):
        """porcelain 解析：带空格/非 ASCII 的路径不能被 C-quoting 或 strip 记错。"""
        self._init_git()
        weird = f"{self.OP_SUBDIR}/op_host/a b 中文.cpp"
        self._write(weird, "// x\n")
        rc = fs.main(["--taskdoc", self.task, "--local-repo", self.repo,
                      "--op-subdir", self.OP_SUBDIR, "--allow-dirty", "--out", self.out])
        self.assertEqual(rc, 0)
        git = self._payload()["local_checkout"]["git"]
        self.assertIn(weird, git["dirty_files"])
        self.assertIn(weird, git["dirty_files_in_op_subdir"])

    def test_key_files_are_independent_of_base_ref(self):
        """⭐ 给不给 --base-ref 不得改变关键文件集合——取证方式不该改变被测语义。"""
        self._init_git()
        self._git("checkout", "-q", "-b", "feature")
        self._write(f"{self.OP_SUBDIR}/op_host/roll_def.cpp", "// v2\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "c")
        fs.main(["--taskdoc", self.task, "--local-repo", self.repo,
                 "--op-subdir", self.OP_SUBDIR, "--base-ref", "master", "--out", self.out])
        with_base = {k["path"] for k in self._payload()["key_files"]}
        out2 = os.path.join(self.d, "out_nb")
        fs.main(["--taskdoc", self.task, "--local-repo", self.repo,
                 "--op-subdir", self.OP_SUBDIR, "--out", out2])
        import content_address as ca
        without_base = {k["path"] for k in
                        ca.read_artifact(out2, "source_facts.json",
                                         "oprunway/source-facts/v1")["key_files"]}
        self.assertEqual(with_base, without_base)

    def test_key_files_never_escape_the_digested_subtree(self):
        """⭐ 关键文件的 ref 记的是只覆盖 op_subdir 的 root_digest，所以候选必须锁在子树内。"""
        self._write("other_op/examples/test_aclnn_other.cpp", "int main(){}\n")
        fs.main(["--taskdoc", self.task, "--local-repo", self.repo,
                 "--op-subdir", self.OP_SUBDIR, "--out", self.out])
        p = self._payload()
        for item in p["key_files"]:
            self.assertTrue(item["path"].startswith(self.OP_SUBDIR + "/"),
                            f"关键文件 {item['path']} 在 root_digest 覆盖范围之外")

    def test_pr_and_local_repo_are_mutually_exclusive_and_leave_no_artifacts(self):
        with self.assertRaises(SystemExit):
            fs.main(["--taskdoc", self.task,
                     "--pr", "https://gitcode.com/cann/ops-nn/pull/6429",
                     "--local-repo", self.repo, "--op-subdir", self.OP_SUBDIR,
                     "--out", os.path.join(self.d, "out2")])
        self.assertFalse(os.path.exists(os.path.join(self.d, "out2")))

    def test_local_repo_without_op_subdir_rejected(self):
        with self.assertRaises(SystemExit):
            fs.main(["--taskdoc", self.task, "--local-repo", self.repo,
                     "--out", os.path.join(self.d, "out3")])
        self.assertFalse(os.path.exists(os.path.join(self.d, "out3")))

    def test_neither_source_warns_and_exits_nonzero_without_source_facts(self):
        out = os.path.join(self.d, "out4")
        rc = fs.main(["--taskdoc", self.task, "--out", out])
        self.assertEqual(rc, 2)
        self.assertTrue(os.path.exists(os.path.join(out, "task_doc.md")))
        self.assertFalse(os.path.exists(os.path.join(out, "source_facts.json")))
        self.assertFalse(os.path.exists(os.path.join(out, "pr_facts.json")))

    def test_local_payload_passes_preparation_state_validator(self):
        """Step 1 的另一半：`validate_preparation_state` 必须认本地形态。"""
        import validate_preparation_state as vps
        self._init_git()
        fs.main(["--taskdoc", self.task, "--local-repo", self.repo,
                 "--op-subdir", self.OP_SUBDIR, "--allow-dirty", "--out", self.out])
        vps._validate_source_payload(self._payload())          # 不抛即通过

    def test_validator_rejects_mixed_source_facts(self):
        """收据同时带 `pr` 与 `local_checkout` → 来源身份不可信，拒。"""
        import validate_preparation_state as vps
        self._init_git()
        fs.main(["--taskdoc", self.task, "--local-repo", self.repo,
                 "--op-subdir", self.OP_SUBDIR, "--out", self.out])
        p = self._payload()
        p["pr"] = {"canonical_url": "https://gitcode.com/o/r/pull/1", "head_sha": "a" * 40}
        with self.assertRaises(Exception) as cm:
            vps._validate_source_payload(p)
        self.assertIn("混装", str(cm.exception))

    def test_validator_rejects_weakened_digest_policy(self):
        """⭐ 伪造/弱化排除规则的摘要外表与正常摘要毫无区别——校验端必须逐字核策略。"""
        import validate_preparation_state as vps
        self._init_git()
        fs.main(["--taskdoc", self.task, "--local-repo", self.repo,
                 "--op-subdir", self.OP_SUBDIR, "--out", self.out])
        p = self._payload()
        p["local_checkout"]["digest_policy"] = dict(p["local_checkout"]["digest_policy"])
        p["local_checkout"]["digest_policy"]["excluded_segment_names"] = ["op_host"]  # 把源码排掉
        with self.assertRaises(Exception) as cm:
            vps._validate_source_payload(p)
        self.assertIn("digest_policy", str(cm.exception))

    def test_validator_rejects_blocking_reason_smuggled_into_warnings(self):
        """⭐ 把阻塞原因写成 warning + reasons=[] 就能干净过门——受控词表必须堵死。"""
        import validate_preparation_state as vps
        self._init_git()
        fs.main(["--taskdoc", self.task, "--local-repo", self.repo,
                 "--op-subdir", self.OP_SUBDIR, "--out", self.out])
        p = self._payload()
        p["completeness"]["warnings"] = list(p["completeness"].get("warnings", [])) + \
            ["dirty_worktree_not_allowed"]
        with self.assertRaises(Exception) as cm:
            vps._validate_source_payload(p)
        self.assertIn("词表外", str(cm.exception))

    def test_validator_rejects_missing_degradation_warning(self):
        """⭐ 反向：降级发生了却把 warning 抹掉 → 报告里就看不出 provenance 弱在哪。"""
        import validate_preparation_state as vps
        self._init_git()
        self._write(f"{self.OP_SUBDIR}/op_host/roll_def.cpp", "// uncommitted\n")
        fs.main(["--taskdoc", self.task, "--local-repo", self.repo,
                 "--op-subdir", self.OP_SUBDIR, "--allow-dirty", "--out", self.out])
        p = self._payload()
        del p["completeness"]["warnings"]
        with self.assertRaises(Exception) as cm:
            vps._validate_source_payload(p)
        self.assertIn("降级必须留痕", str(cm.exception))

    def test_producer_declared_warnings_must_match_derived(self):
        """producer 自报的 warnings 与从事实重新派生的必须一致（自报不算数）。"""
        facts = {
            "dut_source": "local_checkout",
            "warnings": ["changed_files_unavailable", "dirty_worktree_allowed"],  # 多报一条
            "changed_files": "unavailable",
            "local_checkout": {"root_digest": "a" * 64, "op_subdir": "op",
                               "digest_policy": fs.digest_policy()},
            "key_files": {"op/x.h": "void x();"},
            "key_files_ref": {"op/x.h": "a" * 64},
            "op": "x", "target_dir": "op", "aclnn_headers": ["op/x.h"],
        }
        payload = fs.build_source_facts(self.task, facts)
        self.assertEqual(payload["completeness"]["status"], "blocked")
        self.assertIn("declared_warnings_mismatch", payload["completeness"]["reasons"])

    def _local_facts(self, **over):
        facts = {
            "dut_source": "local_checkout",
            "changed_files": ["op/x.h"],
            "local_checkout": {"root_digest": "a" * 64, "op_subdir": "op",
                               "digest_policy": fs.digest_policy()},
            "key_files": {"op/x.h": "void x();"},
            "key_files_ref": {"op/x.h": "a" * 64},
            "op": "x", "target_dir": "op", "aclnn_headers": ["op/x.h"],
        }
        facts.update(over)
        return facts

    def test_changed_files_string_is_not_iterated_into_a_fake_list(self):
        """⭐ `changed_files="abc"` 既非空、又不是哨兵——放行的话下游生成式会把它

        按字符迭代成 `["a","b","c"]`：一份凭空捏出来、形态还完全合法的改动清单。
        """
        payload = fs.build_source_facts(self.task, self._local_facts(changed_files="abc"))
        self.assertEqual(payload["completeness"]["status"], "blocked")
        self.assertIn("missing_changed_files", payload["completeness"]["reasons"])

    def test_dirty_false_with_nonempty_list_blocks_instead_of_dropping_the_warning(self):
        """⭐ warnings 是按 `git.dirty` 派生的：改成 false 就能让降级留痕整条消失。"""
        facts = self._local_facts()
        facts["local_checkout"]["git"] = {
            "head_sha": "b" * 40, "remote_url": None, "base_ref": "master",
            "dirty": False, "dirty_files": ["op/x.h"], "dirty_files_in_op_subdir": [],
        }
        payload = fs.build_source_facts(self.task, facts)
        self.assertEqual(payload["completeness"]["status"], "blocked")
        self.assertIn("inconsistent_dirty_flag", payload["completeness"]["reasons"])
        self.assertNotIn("warnings", payload["completeness"])

    def test_malformed_git_dirty_files_blocks(self):
        facts = self._local_facts()
        facts["local_checkout"]["git"] = {
            "dirty": True, "dirty_files": "op/x.h", "dirty_files_in_op_subdir": []}
        payload = fs.build_source_facts(self.task, facts)
        self.assertIn("malformed_local_git_facts", payload["completeness"]["reasons"])

    def test_clean_worktree_with_empty_dirty_list_is_still_complete(self):
        """收紧不能误伤正例：`dirty_files == []` 正是干净 worktree 的正确表示。"""
        facts = self._local_facts()
        facts["local_checkout"]["git"] = {
            "head_sha": "b" * 40, "remote_url": None, "base_ref": "master",
            "dirty": False, "dirty_files": [], "dirty_files_in_op_subdir": []}
        payload = fs.build_source_facts(self.task, facts)
        self.assertEqual(payload["completeness"]["status"], "complete")


    # ---- 关键文件取不到时**不许静默跳过** -------------------------------------
    # 跳过后 completeness 仍是 complete，而事实包已经少了一份 header/example/op_def；
    # `aclnn_*.h` 缺席直接动摇 aclnn 路由的第一依据（symbol / 形参顺序 / out_role）。
    # 这正是「证据不完整被静默升级为可裁决」。

    def test_escaping_symlink_key_file_aborts_instead_of_being_dropped(self):
        outside = os.path.join(self.d, "outside_aclnn_evil.h")
        with open(outside, "w", encoding="utf-8") as fh:
            fh.write("aclnnStatus aclnnEvilGetWorkspaceSize();\n")
        link = os.path.join(self.repo, self.OP_SUBDIR, "op_host/op_api/aclnn_evil.h")
        os.symlink(outside, link)
        with self.assertRaisesRegex(RuntimeError, "逃逸软链"):
            fs.fetch_local(self.repo, self.OP_SUBDIR, self.out)

    def test_rename_out_of_subtree_records_both_names(self):
        """⭐ 只记新名的话，把文件挪出被测子树会让 `dirty_files_in_op_subdir` 变成 0——

        收据于是宣称「被测子树内没有未提交改动」，而子树里实际少了一个文件。
        """
        self._init_git()
        moved = os.path.join(self.d, "ops", "moved_def.cpp")
        self._git("mv", f"{self.OP_SUBDIR}/op_host/roll_def.cpp", "moved_def.cpp")
        self.assertTrue(os.path.exists(moved))
        git = fs.probe_local_git(self.repo, self.OP_SUBDIR)
        self.assertTrue(git["dirty"])
        self.assertIn(f"{self.OP_SUBDIR}/op_host/roll_def.cpp", git["dirty_files"])
        self.assertEqual([f"{self.OP_SUBDIR}/op_host/roll_def.cpp"],
                         git["dirty_files_in_op_subdir"])


class DutSourceApiTest(unittest.TestCase):
    """判别式自身的两条硬边界。"""

    def test_expected_kind_has_no_default(self):
        """⭐ 「忘了传」与「确认过没有对照物」必须在调用点长得不一样。

        有默认值的话，一个漏传就让「收据自称 PR + 任意 40 位 hex」走进 PR 分支，
        本地锚的等值校验整条不执行。
        """
        import dut_source as ds
        with self.assertRaises(TypeError):
            ds.validate_build_receipt_source({"repo": "r", "pr_head_sha": "a" * 40})

    def test_both_anchors_present_is_rejected(self):
        import dut_source as ds
        with self.assertRaisesRegex(ds.DutSourceError, "另一条通路的锚"):
            ds.validate_build_receipt_source(
                {"repo": "r", "pr_head_sha": "a" * 40, "local_root_digest": "b" * 64},
                expected_kind=ds.NO_EXPECTED_KIND)


class PullRequestPayloadUnchangedTest(unittest.TestCase):
    """⭐ 安全绳：PR 通路 payload **去掉 `producer` 后**与改动前逐字节相同。

    ⚠ 不能断言整个 digest 不变——`producer.logic_sha256` 是 `fetch_source.py` **自身源码**
    的哈希，改一个字节它必然变（这是有意的 provenance 设计，不是 bug）。
    因此安全绳只锁业务字段：去掉 `producer` 之后的 canonical JSON 必须逐字节相同。
    这里把「改动前」的期望值写死成字面量，避免拿改动后的代码自己证明自己。
    """

    EXPECTED_WITHOUT_PRODUCER = {
        "changed_files": ["index/median/op_host/aclnn_median.h"],
        "completeness": {"reasons": [], "status": "complete"},
        "contract_version": 1,
        "derived": {
            "aclnn_entry": "aclnnMedian",
            "aclnn_headers": ["index/median/op_host/aclnn_median.h"],
            "interface_kind": "aclnn_2stage",
            "op": "median",
            "target_dir": "index/median",
        },
        "key_files": [{
            "bytes_sha256": "0d1d2d40a6bd7ea08f8ee0dbf3b0d4b6b64f1de8ca4d0b31de4a25a83e42e4bd",
            "path": "index/median/op_host/aclnn_median.h",
            "ref": "a" * 40,
            "size": 19,
        }],
        "pr": {
            "canonical_url": "https://gitcode.com/cann/ops-nn/pull/6429",
            "head_repo": "contributor/ops-nn",
            "head_sha": "a" * 40,
            "is_fork": True,
            "number": 6429,
            "source_repo": "cann/ops-nn",
            "state": "opened",
        },
        "taskdoc": {
            "bytes_sha256": "f1b6f2e0a0b9d6c9b8a9e1a4c0e8a1b9f2c3d4e5f60718293a4b5c6d7e8f9012",
            "size": 9,
            "snapshot_sha256": "f1b6f2e0a0b9d6c9b8a9e1a4c0e8a1b9f2c3d4e5f60718293a4b5c6d7e8f9012",
            "source_locator": "<local-file>",
        },
    }

    def test_pr_payload_business_fields_are_byte_identical(self):
        import content_address as ca
        sha = "a" * 40
        facts = {
            "pr_url": "https://gitcode.com/cann/ops-nn/pull/6429",
            "source_repo": "cann/ops-nn", "head_sha": sha,
            "head_repo": "contributor/ops-nn", "is_fork": True, "state": "opened",
            "changed_files": ["index/median/op_host/aclnn_median.h"],
            "key_files": {"index/median/op_host/aclnn_median.h": "void aclnnMedian();"},
            "key_files_ref": {"index/median/op_host/aclnn_median.h": sha},
            "aclnn_headers": ["index/median/op_host/aclnn_median.h"],
            "op": "median", "target_dir": "index/median",
            "interface_kind": "aclnn_2stage", "aclnn_entry": "aclnnMedian",
        }
        with tempfile.TemporaryDirectory() as d:
            task = os.path.join(d, "task_doc.md")
            with open(task, "wb") as out:
                out.write("任务书".encode())                       # 9 字节 UTF-8
            payload = fs.build_source_facts(task, facts)
        self.assertNotIn("dut_source", payload, "PR 通路不得写 dut_source 键（业务字段必须不变）")
        self.assertNotIn("local_checkout", payload)
        self.assertNotIn("warnings", payload["completeness"], "warnings 恒空时必须整键省略")
        expected = dict(self.EXPECTED_WITHOUT_PRODUCER)
        # 任务书摘要与 key_file 摘要随夹具算，不写死具体值（写死了改夹具就得改两处）
        expected["taskdoc"] = dict(expected["taskdoc"])
        expected["taskdoc"]["bytes_sha256"] = payload["taskdoc"]["bytes_sha256"]
        expected["taskdoc"]["snapshot_sha256"] = payload["taskdoc"]["snapshot_sha256"]
        expected["key_files"] = [dict(expected["key_files"][0])]
        expected["key_files"][0]["bytes_sha256"] = payload["key_files"][0]["bytes_sha256"]
        actual = {k: v for k, v in payload.items() if k != "producer"}
        self.assertEqual(ca.canonical_json_bytes(actual), ca.canonical_json_bytes(expected))


if __name__ == "__main__":
    unittest.main()
