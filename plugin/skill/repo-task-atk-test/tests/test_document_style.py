"""SKILL.md 与 reference 的行文与结构约束。

本文件里剩下的断言按事故索引：一次跑测发现一条事实，就钉一条断言防止它被删。
它们的价值在防删（本项目两次在精简中删掉过真机验证过的修复），
局限在于结构上发现不了缺失——只知道「原来写的还在」。

发现缺失的职责在 tests/test_contracts.py 的四条结构不变量。
新增知识优先登记进 references/artifact-contracts.json，让不变量覆盖，
只有不变量表达不了的一次性事实才在这里钉断言。
"""

import re
import subprocess
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILL_FILES = [
    SKILL_ROOT / "SKILL.md",
    SKILL_ROOT / "case-gen" / "SKILL.md",
    SKILL_ROOT / "acceptance" / "SKILL.md",
]
CLAUDE_FILE = SKILL_ROOT / "CLAUDE.md"
REFERENCES = SKILL_ROOT / "references"


def relative_path(path):
    return path.relative_to(SKILL_ROOT).as_posix()


def skill_text():
    return "\n".join(path.read_text(encoding="utf-8") for path in SKILL_FILES)


def prose_lines(path):
    in_code = False
    in_comment = False
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if line.startswith("<!--"):
            in_comment = True
        if line.endswith("-->"):
            in_comment = False
            continue
        if line.startswith("```"):
            in_code = not in_code
            continue
        if not line or in_code or in_comment:
            continue
        if line.startswith(("#", "|", "---")):
            continue
        yield number, line


LIST_ITEM = re.compile(r"^(?:[-*+]\s|\d+[.)]\s)")

# 规则正文在 .claude/rules/prose-style.md，随仓根 CLAUDE.md 自动导入。
# 这里只实现其中可判定的四条，判断「并列还是递进」的责任在写字的人。
#
# 存量基线按每文件违规计数记，不按行号——行号随每次编辑漂移，计数不会。
# 改好一处把数字减一，减到 0 就删掉那一行。基线只减不增，这是棘轮。
#
# 2026-08-19 合计从 88 变 276：数字变大不是欠债变多，是计数单位从「一串」
# 换成「一段」（见 _flush_streak）。原先一串不管多长都只记 1 处，于是每一处
# 存量都是一个能免费加段的口子。
PROSE_BASELINE = {
    "case-gen/SKILL.md": 2,
    "acceptance/SKILL.md": 1,
    "references/plugin-authoring.md": 49,
    "references/case-design.md": 45,
    "references/yaml-schema.md": 33,
    "references/build-deploy.md": 23,
    "references/atk-parameter-capabilities.md": 20,
    "references/atk-case.md": 4,
    "references/atk-cli.md": 16,
    "references/reporting.md": 15,
    "references/intake.md": 15,
    "references/environment.md": 7,
    "references/execution.md": 7,
    "references/experimental_standard.md": 11,
    "references/performance.md": 9,
    "references/builtin-baseline.md": 9,
    "references/atk-pitfalls.md": 5,
    "references/decision-points.md": 2,
    "references/gate-inventory.md": 1,
}


def prose_blocks(path):
    """按空行切自然段，跳过代码块与 HTML 注释。产出 (起始行号, [行])。"""
    in_code = in_comment = False
    current, start = [], 0
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if line.startswith("<!--"):
            in_comment = True
        if in_comment:
            if line.endswith("-->"):
                in_comment = False
            continue
        if line.startswith("```"):
            in_code = not in_code
            if current:
                yield start, current
                current = []
            continue
        if in_code:
            continue
        if not line:
            if current:
                yield start, current
                current = []
            continue
        if not current:
            start = number
        current.append(line)
    if current:
        yield start, current


def is_structural(block):
    """表格、标题、引用、分隔线和整块列表不受行文规则约束。"""
    if any(line.startswith(("|", "#", ">", "!")) or line.startswith("---")
           for line in block):
        return True
    return all(LIST_ITEM.match(line) for line in block)


def _flush_streak(streak, found):
    """一串 N 个连续单句自然段记 N-2 处，从第三个起每段一处，各带自己的行号。

    2026-08-19 从「整串记 1 处」改过来。原先的记法有个洞：往一串已有的里面
    继续加单句段，串变长而处数不变，棘轮不红。实测 `case-design.md` 末尾
    追加三个单句段，计数仍是 14 —— 102 处存量里每一处都是这样一个能免费
    长大的口子。按段记之后每加一段就多一处，加不进去了。

    减 2 是因为规则 3 判的是「连续三个以上」，前两个不算违规。
    """
    for position, start in enumerate(streak[2:], 3):
        found.append((3, start, f"第 {position}/{len(streak)} 个连续单句自然段"))


def prose_violations(path):
    """返回 (规则号, 行号, 说明)。规则号对应 .claude/rules/prose-style.md 的编号。"""
    found, streak = [], []
    for start, block in prose_blocks(path):
        structural = is_structural(block)
        lone = (not structural and len(block) == 1
                and block[0].endswith("。") and block[0].count("。") == 1)
        if lone:
            streak.append(start)
        else:
            _flush_streak(streak, found)
            streak = []
        if structural:
            continue
        run = []
        for offset, line in enumerate(block):
            if line.endswith("。") and not LIST_ITEM.match(line):
                run.append(start + offset)
            else:
                if len(run) >= 3:
                    found.append((1, run[0], f"段内 {len(run)} 行独立并列句"))
                run = []
        if len(run) >= 3 and len(block) > 1:
            found.append((1, run[0], f"段内 {len(run)} 行独立并列句"))
        count = sum(line.count("。") for line in block)
        if count > 5:
            found.append((7, start, f"自然段 {count} 句"))
    _flush_streak(streak, found)
    return found


class DocumentStyleTest(unittest.TestCase):
    def test_execution_reference_is_split_by_stage(self):
        expected = {
            "environment.md": ["环境", "阶段归属"],
            "workdir-freeze.md": ["工作目录", "冻结输入", "无效用例"],
            "execution.md": ["目录", "无效用例", "部署门禁", "中止"],
        }
        for name, headings in expected.items():
            with self.subTest(reference=name):
                text = (REFERENCES / name).read_text(encoding="utf-8")
                actual = re.findall(r"^## (.+)$", text, flags=re.MULTILINE)
                self.assertEqual(headings, actual)

    def test_main_skill_stays_compact(self):
        limits = {
            "SKILL.md": 80,
            "case-gen/SKILL.md": 300,
            "acceptance/SKILL.md": 300,
        }
        for path in SKILL_FILES:
            with self.subTest(path=relative_path(path)):
                lines = path.read_text(encoding="utf-8").splitlines()
                self.assertLessEqual(len(lines), limits[relative_path(path)])

    def test_skill_source_contains_no_tracked_python_bytecode(self):
        # skill 部署到跑测机后就是一个普通目录，不在 git 工作区里。
        # 「有没有提交字节码」在那里没有意义，跳过而不是失败——
        # 否则 agent 在跑测机上跑 tests/ 自查量具时会看到一条假红。
        probe = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                               capture_output=True, text=True)
        if probe.returncode != 0:
            self.skipTest("不在 git 工作区，无法核对已提交的字节码")
        result = subprocess.run(
            ["git", "ls-files", "skill/repo-task-atk-test", "*.pyc"],
            check=True,
            capture_output=True,
            text=True,
        )
        tracked_bytecode = [
            line for line in result.stdout.splitlines()
            if line.endswith(".pyc")
        ]
        self.assertEqual([], tracked_bytecode)

    def test_every_script_has_a_documentation_route(self):
        # 反向核对：脚本存在但没有任何文档入口，零上下文 agent 就产不出
        # SKILL.md 点名的那件产物。下划线开头的是内部模块，不需要入口。
        paths = [*SKILL_FILES, *REFERENCES.glob("*.md")]
        text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        orphans = sorted(
            path.name
            for path in (SKILL_ROOT / "scripts").glob("*.py")
            if not path.name.startswith("_") and path.name not in text
        )
        self.assertEqual([], orphans)

    def test_documented_script_names_exist(self):
        # 这条的由来：SKILL.md 一度写「接线字段的受控改写走 `rewire_adapter.py`」，
        # 而那个脚本当时全仓库不存在（句子里已附了就近披露，所以不算 bug）。
        # 它暴露的是 test_declared_producer_exists 覆盖不到的洞：正文里随手提
        # 一句尚不存在的脚本名，没有任何东西会红。脚本现已产出，这条继续守着
        # 「未附披露的脚本名必须真实存在」。
        script_ref = re.compile(r"`(?:scripts/)?(\w+\.py)`")
        disclosure_markers = ("未产出", "不存在", "尚未")
        scripts_dir = SKILL_ROOT / "scripts"
        missing = []
        for path in (*SKILL_FILES, *REFERENCES.glob("*.md")):
            for number, line in prose_lines(path):
                for match in script_ref.finditer(line):
                    name = match.group(1)
                    if (scripts_dir / name).exists():
                        continue
                    if any(marker in line for marker in disclosure_markers):
                        continue
                    missing.append(f"{path.name}:{number} {name}")
        self.assertEqual([], missing,
                         "正文提到的脚本名在 scripts/ 下不存在，"
                         "且没有「未产出/不存在/尚未」这类就近披露")

    def test_precision_standard_keeps_the_threshold_table(self):
        # 阈值真源只有这一份，表被删掉后全 skill 再没有第二处可查。
        text = (REFERENCES / "experimental_standard.md").read_text(encoding="utf-8")
        for token in ("1.95e-3", "1.56e-2", "9.77e-4", "1.53e-5",
                      "0.0625", "0.125", "0.99", "32×ULP"):
            self.assertIn(token, text)
        self.assertIn("与 ATK 默认值的差异", text)
        self.assertIn("2^20", text)
        self.assertIn("2^31", text)

    def test_api_fields_have_documented_semantics(self):
        # 真机现象：`api` / `api_type` 全 skill 只被列在字段清单里，没有语义，
        # agent 为搞清它们连读 11 次 ATK 源码。语义没了就会再读一遍。
        text = (REFERENCES / "yaml-schema.md").read_text(encoding="utf-8")
        self.assertIn("纯元数据", text)
        self.assertIn("子串", text)
        self.assertIn("tensor_input.bin", text)
        self.assertIn("method_input.bin", text)

    def test_c_signature_reverse_lookup_is_complete(self):
        """从 aclnn C 类型倒推 YAML 声明的那张表，删了就会再去读 ATK 源码。

        真机现象：一个 attr 是 int 列表的算子，C 签名要 `aclIntArray*`，
        skill 里查不到「哪种 YAML 声明产出它」，agent 连读 6 次 ATK 源码
        才拼出 attrs + dtype=int 这条路径。
        """
        text = (REFERENCES / "atk-parameter-capabilities.md").read_text(encoding="utf-8")
        for c_type in ("aclTensorList*", "aclScalarList*", "aclIntArray*",
                       "aclFloatArray*", "aclBoolArray*"):
            with self.subTest(c_type=c_type):
                self.assertIn(c_type, text)
        # 三条当场炸的边界，每条都对应一处真实抛错点
        self.assertIn("All elements must be of type", text)
        self.assertIn("空组拿不到 typed null", text)
        self.assertIn("第一个元素", text)

    def test_three_registration_keys_are_told_apart(self):
        # 两轮真机各花 10 次以上调用去读 ATK 源码，问的都是「谁选中了我的插件」。
        # 三个注册键分管三侧，说清一次就够；说漏了就会再读一遍源码。
        text = (REFERENCES / "plugin-authoring.md").read_text(encoding="utf-8")
        for key in ("`generate`", "`api_type`", "`aclnn_api_type`"):
            self.assertIn(key, text)
        self.assertIn("加载到不等于用上", text)

    def test_case_output_path_is_documented(self):
        # 产物路径只在 ATK 日志里，猜 atk_output/ 必然落空。
        text = (REFERENCES / "atk-case.md").read_text(encoding="utf-8")
        self.assertIn("save case json file:", text)
        self.assertIn("result/<yaml 文件名>/json/", text)

    def test_soc_declaration_gate_is_terminal(self):
        # 换 SoC 重建能编过，但换掉的是验收前提。这条禁令删了就会再走一遍
        # 「重建→重装→冒烟→设备侧报错」的十几分钟弯路。
        text = (REFERENCES / "build-deploy.md").read_text(encoding="utf-8")
        self.assertIn("--build-log", text)
        self.assertIn("不得改用其他 SoC 重建", text)
        self.assertIn("退出码 3", text)

    def test_perf_grid_example_carries_required_dtype_classes(self):
        # select_perf_cases.py 用 spec["dtype_classes"] 硬取，样例缺它照抄必崩。
        text = (REFERENCES / "performance.md").read_text(encoding="utf-8")
        self.assertIn("dtype_classes", text)

    def test_atk_hardcoded_upper_border_trap_is_documented(self):
        text = (REFERENCES / "case-design.md").read_text(encoding="utf-8")
        self.assertIn("2^31+1", text)

    def test_max_length_is_documented_as_bytes(self):
        text = (REFERENCES / "yaml-schema.md").read_text(encoding="utf-8")
        self.assertIn("`max_length` 是单张量的**字节**预算", text)

    def test_long_references_have_toc(self):
        for path in REFERENCES.glob("*.md"):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                if len(text.splitlines()) > 100:
                    self.assertIn("## 目录", text)

    def test_prose_lines_stay_within_width(self):
        # 「每行至多一个句号」在 2026-08-18 删除：它把段落层级压没了，
        # 823 个正文自然段里 726 个只剩一句话。宽度上限保留，防单行溢出。
        paths = [*SKILL_FILES, CLAUDE_FILE, *REFERENCES.glob("*.md")]
        failures = []
        for path in paths:
            for number, line in prose_lines(path):
                if len(line) > 100:
                    failures.append(f"{path.name}:{number}: {len(line)} 字符")
        self.assertEqual([], failures)

    def test_prose_structure_debt_does_not_grow(self):
        # 棘轮：每文件违规数不得超过基线。新文件基线为 0，写进来就必须合规。
        paths = [*SKILL_FILES, CLAUDE_FILE, *sorted(REFERENCES.glob("*.md"))]
        regressions = []
        for path in paths:
            found = prose_violations(path)
            key = relative_path(path)
            budget = PROSE_BASELINE.get(key, 0)
            if len(found) > budget:
                sample = "; ".join(
                    f"规则{rule}@{line}({note})" for rule, line, note in found[:3])
                regressions.append(
                    f"{key}: {len(found)} 处 > 基线 {budget} —— {sample}")
        self.assertEqual([], regressions)

    def test_prose_baseline_has_no_stale_entries(self):
        # 基线只减不增：某文件已经改干净了，基线行要删掉，否则棘轮松一格。
        all_paths = [*SKILL_FILES, *REFERENCES.glob("*.md")]
        paths = {relative_path(path): path for path in all_paths}
        stale = []
        for name, budget in PROSE_BASELINE.items():
            path = paths.get(name)
            if path is None:
                stale.append(f"{name}: 基线里有但文件不存在")
                continue
            actual = len(prose_violations(path))
            if actual < budget:
                stale.append(f"{name}: 实际 {actual} < 基线 {budget}，把基线降到 {actual}")
        self.assertEqual([], stale)

    def test_references_do_not_link_other_references(self):
        failures = []
        for path in REFERENCES.glob("*.md"):
            text = path.read_text(encoding="utf-8")
            if re.search(r"\]\([^):]+\.md(?:#[^)]+)?\)", text):
                failures.append(path.name)
        self.assertEqual([], failures)

    def test_removed_preflight_name_does_not_return(self):
        text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in SKILL_ROOT.rglob("*")
            if path.is_file()
            and path != Path(__file__)
            and path.suffix in {".md", ".py", ".json"}
        )
        self.assertNotIn("preflight_cases.py", text)

    def test_interface_facets_keep_once_only_generation_rule(self):
        skill = skill_text()
        design = (REFERENCES / "case-design.md").read_text(encoding="utf-8")
        self.assertIn("每个接口分面仍只运行一次 `atk case`", skill)
        self.assertIn("每个分面只运行一次 `atk case`", design)
        self.assertIn("最终裁决必须汇总全部任务书要求的接口分面", skill)

    def test_optional_attr_rule_forbids_empty_compound_group(self):
        plugin = (REFERENCES / "plugin-authoring.md").read_text(encoding="utf-8")
        self.assertIn("不要把缺省参数表示为空的复合输入组", plugin)
        self.assertIn('`type="attr"`、`range_values=None`', plugin)

    def test_smoke_requires_report_and_candidate_provenance(self):
        execution = (REFERENCES / "execution.md").read_text(encoding="utf-8")
        cli = (REFERENCES / "atk-cli.md").read_text(encoding="utf-8")
        self.assertIn("ATK CLI 退出码为 0 不代表冒烟通过", execution)
        self.assertIn("加载路径必须属于本轮安装的 vendor", execution)
        self.assertIn("执行成功数必须为 1", cli)

    def test_acceptance_header_source_rules_are_self_contained(self):
        handoff = (REFERENCES / "handoff-intake.md").read_text(encoding="utf-8")
        acceptance = (SKILL_ROOT / "acceptance" / "SKILL.md").read_text(
            encoding="utf-8")
        for fact in ("operator_project.path", "/usr/local/Ascend",
                     "ASCEND_TOOLKIT_HOME", "同名不代表同签名", "不要换目录"):
            with self.subTest(fact=fact):
                self.assertIn(fact, handoff)
        normalized = re.sub(r"\s+", " ", acceptance)
        self.assertIn(
            "规则见 [handoff-intake.md](../references/handoff-intake.md)",
            normalized,
        )

    def test_pyaclnn_flow_keeps_abi_and_soc_gates(self):
        execution = (REFERENCES / "execution.md").read_text(encoding="utf-8")
        cli = (REFERENCES / "atk-cli.md").read_text(encoding="utf-8")
        deploy = (REFERENCES / "build-deploy.md").read_text(encoding="utf-8")
        self.assertIn("--cpp_func_signature_type_path", cli)
        self.assertIn("check_soc_binding.py", deploy)
        self.assertIn("-o evidence/soc_binding.json", execution)

    def test_adapter_repair_is_bounded_and_evidence_backed(self):
        skill = skill_text()
        plugin = (REFERENCES / "plugin-authoring.md").read_text(encoding="utf-8")
        self.assertIn("修正一次适配器", skill)
        self.assertIn("不得枚举常量、空指针或参数顺序", skill)
        self.assertIn("最多修正一次适配器", plugin)

    def test_adapter_guidance_separates_c_abi_from_atk_object_contract(self):
        skill = skill_text()
        plugin = (REFERENCES / "plugin-authoring.md").read_text(encoding="utf-8")
        self.assertIn("运行时契约表", skill)
        self.assertIn("不能替代 ATK 运行时契约表", skill)
        self.assertIn("不得使用 `ctypes.c_void_p(0)`", plugin)
        self.assertIn("type(output_package)()", plugin)
        self.assertIn("optional pointer 的构造方式", plugin)
        self.assertIn("typed pointer alias 或 factory", plugin)

    def test_adapter_guidance_requires_runtime_contract_and_semantic_smoke(self):
        skill = skill_text()
        plugin = (REFERENCES / "plugin-authoring.md").read_text(encoding="utf-8")
        capability = (REFERENCES / "atk-parameter-capabilities.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("运行时契约表", skill)
        self.assertIn("同类型参数的语义顺序", plugin)
        self.assertIn("workspace 和 executor", plugin)
        self.assertIn("唯一注册名", plugin)
        self.assertIn("最小真机语义冒烟", plugin)
        self.assertIn("不是 typed `aclTensor*`", capability)

    def test_capability_probe_records_typed_optional_tensor_factory(self):
        probe = (SKILL_ROOT / "scripts" / "probe_atk_capabilities.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("TensorPtr", probe)
        self.assertIn("typed_optional_tensor_pointer", probe)

    def test_runtime_report_path_is_documented(self):
        # 生成期与执行期产物在两个地方。skill 只写了生成期那个，
        # 真机上 agent 为找执行期报告花了约 8 次调用。
        text = (REFERENCES / "atk-cli.md").read_text(encoding="utf-8")
        self.assertIn("atk_output/", text)
        self.assertIn("report/", text)
        self.assertIn("save result excel file:", text)

    def test_frequent_jargon_is_defined_in_the_glossary(self):
        # 真机复盘：SKILL.md 和 references 高频使用「分面」「物化」「接线字段」
        # 等词却零定义，零上下文 agent 只能靠猜。术语表是运行期知识的一部分，
        # 不能只存在于开发者文档里。
        glossary = (REFERENCES / "glossary.md").read_text(encoding="utf-8")
        text = "\n".join(path.read_text(encoding="utf-8")
                         for path in [*SKILL_FILES, *REFERENCES.glob("*.md")]
                         if path.name != "glossary.md")
        missing = [word for word in
                   ("分面", "物化", "组合表", "投影", "定位字段", "接线字段",
                    "适配器", "能力域", "冻结", "替身", "量具")
                   if word in text and f"| {word} |" not in glossary]
        self.assertEqual([], missing)

    def test_unreliable_truth_gates_state_their_exit(self):
        # 判据不可靠的门必须在报错现场给出口，否则 agent 只能改产物或改量具——
        # median 那轮 22 分钟就是这么烧掉的。
        cap = (SKILL_ROOT / "scripts" / "_atk_capabilities.py").read_text(encoding="utf-8")
        self.assertIn("probe_atk_capabilities.py 重探", cap)
        self.assertIn("atk_versions", cap)
        validate = (SKILL_ROOT / "scripts" / "validate_cases.py").read_text(encoding="utf-8")
        self.assertIn("extract 规则没把轴读对", validate)

    def test_unknown_operator_class_is_extensible_not_refused(self):
        # 算子类别是开放的。`OPERATOR_CLASSES` 只是已经积累到的那几类，
        # 没命中必须给出补画像的路，不能写成「不支持」把整类算子挡在门外。
        strategy = (SKILL_ROOT / "scripts" / "_coverage_strategy.py").read_text(
            encoding="utf-8")
        self.assertIn("这不是「不支持」", strategy)
        self.assertIn("class_profile", strategy)
        text = (REFERENCES / "case-design.md").read_text(encoding="utf-8")
        self.assertIn("class_profile", text)

    def test_build_entry_may_live_in_the_parent_repository(self):
        # 真机实测：README 指向上级仓的 build.sh，agent 在工程目录里找了 6 次。
        text = (REFERENCES / "build-deploy.md").read_text(encoding="utf-8")
        self.assertIn("上级仓", text)

    def test_semantic_variants_are_out_of_scope_until_the_user_asks(self):
        # 真机实测（median，2026-08-16）：任务书提了 aclnnMedianDim、工程设计
        # 文档写了「indicesOut 传空是全局中位数」，agent 就把这两种形态都拆成
        # 分面，一轮验收 4 份 YAML、141 条用例、4 轮冻结与冒烟。任务书的精度
        # 性能目标一条也没多覆盖。范围必须由目标划定，形态默认不构造。
        paths = [*SKILL_FILES,
                 REFERENCES / "intake.md",
                     REFERENCES / "case-design.md",
                 REFERENCES / "glossary.md"]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            if path in SKILL_FILES:
                continue
            self.assertIn("语义形态", text, path.name)
        skill = skill_text()
        self.assertIn("语义形态", skill)
        self.assertIn("验收范围由任务书的精度与性能目标划定", skill)
        self.assertIn("一份签名，一份用例集", skill)
        self.assertIn("必须用户明确说了要", skill)

    def test_atk_signature_self_check_greps_the_install_tree(self):
        # 真机实测：同一个根因在 median 三次验收里从零重推了三次，每次 30-45
        # 分钟。ATK 按 aclnn_name 在一个目录里 grep 头文件，选目录时
        # ASCEND_OPP_PATH 最后生效、落到装机根，社区算子与官方重名就必踩。
        # 更要命的是搜错了它不停，自己改调用只打 warning。
        text = (REFERENCES / "build-deploy.md").read_text(encoding="utf-8")
        self.assertIn("ASCEND_OPP_PATH", text)
        self.assertIn("软链接", text)
        binding = (SKILL_ROOT / "scripts" / "_opapi_binding.py").read_text(
            encoding="utf-8")
        self.assertIn("参数数量不匹配", binding)
        self.assertIn("参数类型不匹配", binding)


if __name__ == "__main__":
    unittest.main()
