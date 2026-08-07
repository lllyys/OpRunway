import os
import shutil
import tempfile
import unittest

import check_acceptance_entrypoints as C


GOOD = """
三条真机通路：new_example、aclnn_py、cpp_extension。
SOURCE_ACQUIRED → HEAD_VERIFIED → BUILD_VERIFIED → WORKFLOW_STARTED。
远端入口使用 set -Eeuo pipefail。
历史 Median 60/60 PASS 只证明旧 caseset，不得沿用。
"""


class _GateTestBase(unittest.TestCase):
    def setUp(self):
        # 仓布局照实搭：plugin_root = <repo>/plugin，仓根 AGENTS.md 在 dirname(plugin_root)。
        self.repo = tempfile.mkdtemp()
        self.root = os.path.join(self.repo, "plugin")
        for rel in C.ENTRYPOINTS:
            path = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(GOOD)
        for rel in C.REPO_ROOT_ENTRYPOINTS:
            path = os.path.join(self.repo, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(GOOD)

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def write(self, rel, text):
        with open(os.path.join(self.root, rel), "w", encoding="utf-8") as f:
            f.write(text)

    def write_repo(self, rel, text):
        with open(os.path.join(self.repo, rel), "w", encoding="utf-8") as f:
            f.write(text)


class AcceptanceEntrypointGateTest(_GateTestBase):
    def test_current_entrypoints_pass(self):
        self.assertEqual(C.collect(self.root), [])

    def test_two_paths_regression_fails(self):
        self.write("commands/op-acceptance.md", "只有两条真机通路")
        self.assertTrue(any("两条真机通路" in x for x in C.collect(self.root)))

    def test_active_old_pass_claim_fails(self):
        self.write("agents/op-acceptance.md", "Median 最新精度 60/60 PASS")
        self.assertTrue(any("旧 Median PASS" in x for x in C.collect(self.root)))

    def test_explicit_historical_old_pass_is_allowed(self):
        self.write("agents/op-acceptance.md", "历史 Median 60/60 PASS 已失效，不得沿用。")
        self.assertEqual(C.collect(self.root), [])

    def test_missing_source_gate_token_fails(self):
        self.write("agents/acc-verify-rootcause.md", GOOD.replace("HEAD_VERIFIED", "HEAD_OK"))
        self.assertTrue(any("HEAD_VERIFIED" in x for x in C.collect(self.root)))

    def test_missing_file_fails_closed(self):
        os.remove(os.path.join(self.root, "skills", "acc-runner", "SKILL.md"))
        self.assertTrue(any("读取失败" in x for x in C.collect(self.root)))

    def test_missing_repo_root_agents_fails_closed(self):
        os.remove(os.path.join(self.repo, "AGENTS.md"))
        errs = C.collect(self.root)
        self.assertTrue(any("读取失败" in x and "AGENTS.md" in x for x in errs), errs)


class SourceFactsFlagGateTest(_GateTestBase):
    """CP-D 调用模板必带 `--source-facts`（`run_workflow` 验收通路上缺席即拒跑）。"""

    def errors(self):
        return [e for e in C.collect(self.root) if "缺 --source-facts" in e]

    def test_full_invocation_without_flag_fails(self):
        self.write("agents/acc-verify-rootcause.md", GOOD + (
            "\n`python3 ${OPRUNWAY_PLUGIN_ROOT}/acc-common/run_workflow.py <op>.spec.json"
            " --mode <mode> --out reports/<op>/`\n"))
        self.assertEqual(len(self.errors()), 1, C.collect(self.root))

    def test_full_invocation_with_flag_passes(self):
        self.write("agents/acc-verify-rootcause.md", GOOD + (
            "\n`python3 ${OPRUNWAY_PLUGIN_ROOT}/acc-common/run_workflow.py <op>.spec.json"
            " --mode <mode> --out reports/<op>/ --source-facts <取材目录>/source_facts.json`\n"))
        self.assertEqual(self.errors(), [])

    def test_abbreviated_dispatch_form_without_flag_fails(self):
        # CP-D 编排文里的缩写式 `run_workflow.py --mode <mode>` 同样算调用模板。
        self.write("commands/op-acceptance.md", GOOD + "\n→ `run_workflow.py --mode <mode>`（…）\n")
        self.assertEqual(len(self.errors()), 1, C.collect(self.root))

    def test_backslash_continued_command_block_is_joined(self):
        # 仓根 AGENTS.md 的主入口是多行 bash 块；不折续行就会把一条完整命令误判成三条残片。
        self.write_repo("AGENTS.md", GOOD + (
            '\n```bash\npython3 "${OPRUNWAY_PLUGIN_ROOT}/acc-common/run_workflow.py" \\\n'
            "  <spec.json> --mode <mode> --out <报告目录> \\\n"
            "  --source-facts <取材目录>/source_facts.json\n```\n"))
        self.assertEqual(self.errors(), [])
        self.write_repo("AGENTS.md", GOOD + (
            '\n```bash\npython3 "${OPRUNWAY_PLUGIN_ROOT}/acc-common/run_workflow.py" \\\n'
            "  <spec.json> --mode <mode> --out <报告目录>\n```\n"))
        self.assertEqual(len(self.errors()), 1, C.collect(self.root))

    def test_lookalike_flag_token_does_not_satisfy_gate(self):
        # `--source-facts-note` 含 `--source-facts` 子串；整行子串搜索会被它骗过。
        self.write("agents/acc-verify-rootcause.md", GOOD + (
            "\n`run_workflow.py <op>.spec.json --mode <mode> --out reports/<op>/ --source-facts-note x`\n"))
        self.assertEqual(len(self.errors()), 1, C.collect(self.root))

    def test_second_invocation_on_same_line_does_not_cover_the_first(self):
        # 同一逻辑行两条命令，只有后一条带 flag：整行搜索会认为两条都合格。
        self.write("commands/op-acceptance.md", GOOD + (
            "\n验收：`run_workflow.py a.spec.json --mode <mode> --out r/`；"
            "另见 `run_workflow.py b.spec.json --mode <mode> --out r2/ --source-facts f.json`\n"))
        errs = self.errors()
        self.assertEqual(len(errs), 1, C.collect(self.root))

    def test_abbrev_with_spec_positional_is_still_a_template(self):
        # 判据若写死字面量 `run_workflow.py --mode <`，spec 位置参数一插进来就漏判。
        self.write("commands/op-acceptance.md", GOOD + "\n`run_workflow.py <spec> --mode <mode>`\n")
        self.assertEqual(len(self.errors()), 1, C.collect(self.root))

    def test_explicit_mock_command_is_exempt(self):
        # mock 通路物理上不产 acceptance.json / verdict.json，run_workflow 不要求 --source-facts。
        self.write("commands/op-acceptance.md", GOOD + (
            "\n本地自检：`run_workflow.py <op>.spec.json --mode mock --out reports/<op>/`\n"))
        self.assertEqual(self.errors(), [])

    def test_mock_mention_does_not_exempt_a_real_cpd_template(self):
        # 豁免只认「写死跑 mock」；模板里顺带提一句 mock 不得让本门对整行闭嘴。
        self.write("commands/op-acceptance.md", GOOD + (
            "\n`run_workflow.py --mode <mode> --out reports/<op>/`（`mock` / `--mode mock` 派生不出、"
            "须显式指定；`--allow-experimental-form` 的开发级路径不产裁决）\n"))
        self.assertEqual(len(self.errors()), 1, C.collect(self.root))

    def test_experimental_flag_mention_does_not_exempt_a_full_template(self):
        """⚠ 钉死「别拿 `--allow-experimental-form` 当豁免依据」。

        解释「非验收通路不受此强制」的散文里天然带这个词。一旦把它写进豁免条件，
        「在真模板旁边写一句说明」就能让本门对整行闭嘴——具体 mode 值 + 无占位符时尤其致命，
        因为 `--mode <占位符>` 那道保险在这种行上不生效。
        """
        self.write("commands/op-acceptance.md", GOOD + (
            "\n`run_workflow.py <op>.spec.json --mode cpp_extension --out reports/<op>/`"
            "（`cpp` / `aclnn_py` 须加 `--allow-experimental-form`，只产开发级产物）\n"))
        self.assertEqual(len(self.errors()), 1, C.collect(self.root))

    def test_prose_mentions_are_not_flagged(self):
        # 这四种真实存在于仓内、都**不是**可照抄执行的模板，不得误报（否则门会逼出无意义的噪声）。
        self.write("agents/op-acceptance.md", GOOD + "\n".join([
            "",
            "| `spec.runner_form` | `run_workflow.py --mode` | 能否产验收裁决 |",   # 只点名 flag
            "`cpp_extension` → `--mode cpp_extension`（见 `run_workflow.py`）",
            "`run_workflow.py --gpu-baseline <外部 GPU 标杆 JSON>` 出 NPU↔GPU 对比。",
            "三级门是 `run_workflow.py` 内部的一环，非阶段间实时阻断。",
            "",
        ]))
        self.assertEqual(self.errors(), [])


class RetiredDispatchGateTest(_GateTestBase):
    """已退役 runner form / mode 不得在活跃编排文本里被派生或调用。

    ⚠ 这道规则守的是本轮那条最贵的死路：代码 2026-08-06 已把派生表收敛到 `cpp_extension`
    一条、逃生阀一并删除，**但 NL 文本没跟上**——spec skill 还指示可选 `aclnn_py`、
    workflow 还写「三种 form 依次派生」、CP-D 还写 `--mode aclnn_py`。agent 照着这些**活跃指令**
    抽 spec、配环境、跑完 CP-C，最后在 `_resolve_mode` 撞门，昂贵准备全白做。
    """

    def errors(self):
        keys = ("退役", "逃生阀", "历史区")
        return [e for e in C.collect(self.root) if any(k in e for k in keys)]

    def test_arrow_derivation_fails(self):
        self.write("commands/op-acceptance.md", GOOD + (
            "\n`cpp_extension` → `--mode cpp_extension`；`cpp` → `--mode new_example`；"
            "`aclnn_py` → `--mode aclnn_py`。\n"))
        errs = self.errors()
        self.assertTrue(any("派生关系" in e for e in errs), errs)

    def test_bare_arrow_derivation_fails(self):
        # 表格脚注里的紧凑写法：`cpp→new_example、aclnn_py→aclnn_py`，无反引号无空格。
        self.write("skills/acceptance-workflow/SKILL.md", GOOD + (
            "\nmode 据 form 派生：cpp→new_example、aclnn_py→aclnn_py、cpp_extension→cpp_extension\n"))
        self.assertTrue(any("派生关系" in e for e in self.errors()), C.collect(self.root))

    def test_derivation_set_literal_fails(self):
        self.write("skills/acceptance-workflow/SKILL.md", GOOD + (
            "\n`spec.runner_form` 受控词表为 `{cpp, aclnn_py, cpp_extension}`，"
            "依次派生 `{new_example, aclnn_py, cpp_extension}`。\n"))
        self.assertTrue(any("派生集合" in e for e in self.errors()), C.collect(self.root))

    def test_derivation_table_row_fails(self):
        self.write("agents/acc-verify-rootcause.md", GOOD + (
            "\n| `spec.runner_form` | `--mode` | 能否产验收裁决 |\n|---|---|---|\n"
            "| `aclnn_py` | `aclnn_py` | ❌ 不能 |\n"))
        self.assertTrue(any("派生表" in e for e in self.errors()), C.collect(self.root))

    def test_admitted_form_row_is_not_flagged(self):
        # `cpp_extension` → `cpp_extension` 是当前唯一合法派生，不得误报。
        self.write("agents/acc-verify-rootcause.md", GOOD + (
            "\n| `cpp_extension`（或未声明） | `cpp_extension` | ✅ 当前唯一准入形态 |\n"
            "| `cpp` | （无）| ⛔ 停止准入 |\n"))
        self.assertEqual(self.errors(), [])

    def test_retired_mode_flag_needs_a_retirement_marker(self):
        self.write("commands/op-acceptance.md", GOOD + "\n真机跑测用 `--mode aclnn_py`。\n")
        self.assertTrue(any("`--mode` 调用" in e for e in self.errors()), C.collect(self.root))
        self.write("commands/op-acceptance.md", GOOD + (
            "\n⛔ 停止准入：显式 `--mode aclnn_py` 同样被拒。\n"))
        self.assertEqual(self.errors(), [])

    def test_retired_mode_inside_invocation_is_rejected_regardless_of_wording(self):
        """⚠ 措辞豁免**不许**溢到可照抄执行的命令上。

        「写句『已停止准入』再把命令原样贴出来」是本门最现实的绕法：读的人照抄那条命令，
        旁边那句说明一个字都不会拦住他。所以调用段那一档无条件拒。
        """
        self.write("agents/acc-verify-rootcause.md", GOOD + (
            "\n⛔ 已停止准入，仅作历史保留：`run_workflow.py <op>.spec.json --mode new_example"
            " --out reports/<op>/ --source-facts f.json`\n"))
        self.assertTrue(any("调用段" in e for e in self.errors()), C.collect(self.root))

    def test_experimental_flag_needs_a_removal_marker(self):
        self.write("skills/acc-spec/SKILL.md", GOOD + (
            "\n要跑须加 `--allow-experimental-form`，只产开发级产物。\n"))
        self.assertTrue(any("逃生阀" in e for e in self.errors()), C.collect(self.root))
        self.write("skills/acc-spec/SKILL.md", GOOD + (
            "\n逃生阀 `--allow-experimental-form` 已删除（2026-08-06），别加回来。\n"))
        self.assertEqual(self.errors(), [])

    def test_experimental_flag_inside_invocation_is_rejected_regardless_of_wording(self):
        self.write("commands/op-acceptance.md", GOOD + (
            "\n（已删除，仅存档）`run_workflow.py a.spec.json --mode cpp_extension --out r/"
            " --source-facts f.json --allow-experimental-form`\n"))
        self.assertTrue(any("调用段" in e for e in self.errors()), C.collect(self.root))

    def test_negated_derivation_prose_is_not_flagged(self):
        """仓内真实存在的**否定句**不得误报，否则门会逼着把正确的警告删掉。"""
        self.write("agents/op-acceptance.md", GOOD + "\n".join([
            "",
            "| `cpp` | （无）| ⛔ **停止准入**：派生表无条目，省 `--mode` 派不出、显式 `--mode new_example` 也拒 |",
            "`_REAL_MACHINE_MODES = {new_example, aclnn_py, cpp_extension}` 是「哪些 mode 在真机上跑」的表，必须保留全部三项。",
            "缺省若是 `cpp`，「spec 漏写这个字段」派生出的就是 `new_example`，一步撞上准入门。",
            "",
        ]))
        self.assertEqual(self.errors(), [])


class RetiredRegionTest(_GateTestBase):
    """历史区：退役机制留着有参考价值，但必须让 agent 一眼看出「不要照做」。"""

    BAD = "`cpp` → `--mode new_example`；`aclnn_py` → `--mode aclnn_py`。"

    def errors(self):
        keys = ("退役", "逃生阀", "历史区")
        return [e for e in C.collect(self.root) if any(k in e for k in keys)]

    def wrap(self, body, banner=C._REGION_BANNER, end=True):
        text = [GOOD, C._REGION_BEGIN]
        if banner:
            text.append(banner)
        text.append(body)
        if end:
            text.append(C._REGION_END)
        return "\n".join(text) + "\n"

    def test_banner_region_exempts(self):
        self.write("commands/op-acceptance.md", self.wrap(self.BAD))
        self.assertEqual(self.errors(), [])

    def test_region_without_banner_does_not_exempt(self):
        self.write("commands/op-acceptance.md", self.wrap(self.BAD, banner=None))
        errs = self.errors()
        self.assertTrue(any("缺横幅" in e for e in errs), errs)
        self.assertTrue(any("派生关系" in e for e in errs), errs)

    def test_unclosed_region_does_not_exempt(self):
        """⚠ fail-closed：否则在文件顶上写一个开启标记就能让整个文件免检。"""
        self.write("commands/op-acceptance.md", self.wrap(self.BAD, end=False))
        errs = self.errors()
        self.assertTrue(any("没有闭合" in e for e in errs), errs)
        self.assertTrue(any("派生关系" in e for e in errs), errs)

    def test_stray_end_marker_is_reported(self):
        self.write("commands/op-acceptance.md", GOOD + "\n" + C._REGION_END + "\n")
        self.assertTrue(any("没有对应的开启标记" in e for e in self.errors()), C.collect(self.root))

    def test_nested_begin_is_reported(self):
        self.write("commands/op-acceptance.md", "\n".join([
            GOOD, C._REGION_BEGIN, C._REGION_BANNER, C._REGION_BEGIN,
            C._REGION_BANNER, self.BAD, C._REGION_END, ""]))
        self.assertTrue(any("未闭合就再次开启" in e for e in self.errors()), C.collect(self.root))


class RealRepoGateTest(unittest.TestCase):
    """⚠ 把门接上电的那根线，别删。

    上面所有用例喂的都是 tmpdir 里的合成文本——**门对真实仓文件一次都没跑过**。于是
    `run_workflow.py` 把 `--source-facts` 加成必填、仓内文档模板没跟上时，整套 pytest 照样全绿
    （2026-08-05 实际发生过：代码里的门是真的，仓内正式验收调用路径已经跑不起来）。
    这里直接拿本仓真文件跑一遍，让这类漂移在测试里就红，而不是等真机上炸。
    """

    def test_real_repo_entrypoints_are_synced(self):
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(C.__file__)))
        repo_root = os.path.dirname(plugin_root)
        self.assertEqual(
            C.collect(plugin_root, repo_root=repo_root), [],
            "仓内验收入口文本已漂移；跑 `python3 check_acceptance_entrypoints.py` 看全部详情。\n"
            "⚠ 若报的是「读取失败 …/AGENTS.md」：本门把**仓根** AGENTS.md 也纳入守护（它带着\n"
            "  run_workflow.py 主入口命令块），投测打包必须连它一起带上——\n"
            "  `tar -czf <D>.tgz plugin AGENTS.md`。**这是打包要补，不是把本条测试放宽。**")
        # ⚠ 这里**不做**「文件不在就 skip」：那样一来仓根 AGENTS.md 被删、权限坏或编码坏时，
        #   本测试会把 collect 已经正确报出的「读取失败」过滤掉再标 skipped，整套 pytest 照样绿
        #   ——正是本仓最贵的那类 fail-open。打包不全就让它红，红了去补打包。


class RealRepoRetiredDispatchMutationTest(unittest.TestCase):
    """新规则必须在**真实仓文件**上真的生效，不只是在合成文本上绿。

    ⚠ 本门此前正是「假门」：所有用例喂的都是 tmpdir 合成文本，对真文件一次没跑过。
    新加的退役派生 / 逃生阀规则如果也只有合成用例，会重蹈覆辙——真文件里恢复一行
    `aclnn_py → --mode aclnn_py`，整套 pytest 照样全绿。这里把真文件复制出来当底本，
    注入退化再断言门会红；顺带证明真文件里那些历史区**确实在承重**。
    """

    PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(C.__file__)))
    REPO_ROOT = os.path.dirname(PLUGIN_ROOT)

    # 只认「内容规则」，把历史区自身的结构报错（缺横幅 / 没闭合 / 孤儿标记）排除在外——
    # 否则摘掉一个开启标记必然留下孤儿收尾标记，那条结构报错会让 mutation 断言无条件成立。
    _STRUCTURE = ("缺横幅", "没有闭合", "没有对应的开启标记", "未闭合就再次开启", "写在同一行")

    @classmethod
    def _content_errors(cls, errors):
        keys = ("退役", "逃生阀")
        return [e for e in errors
                if any(k in e for k in keys) and not any(s in e for s in cls._STRUCTURE)]

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.root = os.path.join(self.repo, "plugin")
        for rel in C.ENTRYPOINTS:
            dst = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(os.path.join(self.PLUGIN_ROOT, rel), dst)
        for rel in C.REPO_ROOT_ENTRYPOINTS:
            shutil.copyfile(os.path.join(self.REPO_ROOT, rel), os.path.join(self.repo, rel))

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def _append(self, rel, extra):
        with open(os.path.join(self.root, rel), "a", encoding="utf-8") as f:
            f.write(extra)

    def test_real_copies_are_clean(self):
        # 底本必须是干净的，否则下面的 mutation 断言分不清红是谁造成的。
        self.assertEqual(C.collect(self.root), [])

    def test_real_workflow_regression_to_old_derivation_is_caught(self):
        # 逐字取自本轮改掉的那句（旧 CP-D 「`<mode>` 据 `spec.runner_form` 定」）。
        self._append("skills/acceptance-workflow/SKILL.md",
                     "\n  - **`<mode>` 据 `spec.runner_form` 定**：cpp runner v1 → `--mode new_example`；"
                     "`runner_form==aclnn_py`（torch 对标）→ `--mode aclnn_py`。\n")
        errs = C.collect(self.root)
        self.assertTrue(any("派生关系" in e for e in self._content_errors(errs)), errs)

    def test_real_spec_skill_regression_to_escape_valve_is_caught(self):
        # 逐字取自本轮改掉的 acc-spec 指示（「可选 aclnn_py」+ 逃生阀）。
        self._append("skills/acc-spec/SKILL.md",
                     "\n② PR 是标准 aclnn 两段式工程但任务书不是 torch 对标 → 可选 "
                     "`runner_form=\"aclnn_py\"`；要跑须加 `--allow-experimental-form`。\n")
        errs = C.collect(self.root)
        self.assertTrue(any("逃生阀" in e for e in self._content_errors(errs)), errs)

    def test_real_env_confirm_regression_to_three_form_derivation_is_caught(self):
        self._append("skills/acceptance-workflow/SKILL.md",
                     "\n`spec.runner_form` 受控词表为 `{cpp, aclnn_py, cpp_extension}`，"
                     "依次派生 `{new_example, aclnn_py, cpp_extension}`。\n")
        errs = C.collect(self.root)
        self.assertTrue(any("派生集合" in e for e in self._content_errors(errs)), errs)

    def test_real_retired_regions_are_load_bearing(self):
        """历史区必须**确实在承重**：把它的起止标记摘掉，门就该红。

        ⚠ 否则「挂了个历史区标记」只是装饰——区块里其实没有任何被豁免的内容，
        下一个人顺手删掉标记不会有任何信号，这个机制就成了摆设。
        """
        load_bearing = []
        for rel in C.ENTRYPOINTS:
            path = os.path.join(self.root, rel)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            if C._REGION_BEGIN not in text:
                continue
            stripped = text.replace(C._REGION_BEGIN, "").replace(C._REGION_END, "")
            with open(path, "w", encoding="utf-8") as f:
                f.write(stripped)
            try:
                if self._content_errors(C.collect(self.root)):
                    load_bearing.append(rel)
            finally:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
        self.assertTrue(
            load_bearing,
            "仓内每个历史区摘掉标记后门都不红 = 区块里没有任何真正被豁免的退役内容，"
            "这个机制成了装饰。要么把退役机制描述真的搬进去，要么别挂空标记。")


if __name__ == "__main__":
    unittest.main()
