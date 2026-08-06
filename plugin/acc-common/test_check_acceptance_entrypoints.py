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
            "`cpp_extension` → `--mode cpp_extension`；`cpp` → `--mode new_example`（见 `run_workflow.py`）",
            "`run_workflow.py --gpu-baseline <外部 GPU 标杆 JSON>` 出 NPU↔GPU 对比。",
            "三级门是 `run_workflow.py` 内部的一环，非阶段间实时阻断。",
            "",
        ]))
        self.assertEqual(self.errors(), [])


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


if __name__ == "__main__":
    unittest.main()
