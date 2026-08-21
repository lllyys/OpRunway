"""reference 里手写的事实表必须与真源一致。

md 表是给 agent 读的视图，真源是 JSON 能力域和量具里的常量表。
两边手工同步时没有任何东西会红——真机上因此发生过：
`atk-parameter-capabilities.md` 的 aclIntArray 行写着「int 或 int64_t」，
而 `attr_array_dtypes` 里根本没有 int64_t，agent 照文档写，
跑完 `atk case` 才被 C6 判 unsupported_attr_array_dtype，全链路重生成一轮。
同一份文件的 movement 行也写着代码里不存在的 contiguity 轴。

这里锁的不是「原来写的还在」，是「写的和真源相等」。
"""

import json
import re
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import _axis_binding  # noqa: E402
import _coverage_strategy  # noqa: E402

REFERENCES = SKILL_ROOT / "references"
CAPABILITY = json.loads(
    (REFERENCES / "atk-parameter-capabilities.json").read_text(encoding="utf-8"))


def table_rows(path, header_cell):
    """抽出以 `header_cell` 为首列表头的那张表的数据行，按单元格切开。"""
    rows, inside = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            inside = False
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells and cells[0] == header_cell:
            inside = True
            continue
        if not inside or set("".join(cells)) <= set("- :"):
            continue
        rows.append(cells)
    return rows


def code_tokens(cell):
    return re.findall(r"`([^`]+)`", cell)


class CSignatureTableTest(unittest.TestCase):
    """C 签名反查表的「契约 dtype」列必须落在能力域白名单里。"""

    def setUp(self):
        self.rows = table_rows(REFERENCES / "atk-parameter-capabilities.md",
                               "C 签名里的类型")
        self.assertTrue(self.rows, "C 签名反查表没抽到行，表头或格式变了")

    def test_declared_dtypes_are_in_the_capability_domain(self):
        pyaclnn = CAPABILITY["pyaclnn"]
        domains = {
            "attr_array": set(pyaclnn["attr_array_dtypes"]),
            "attr_single": set(pyaclnn["attr_single_dtypes"]),
            "scalar": set(pyaclnn["scalar_dtypes"]),
        }
        checked = 0
        for cells in self.rows:
            yaml_types, dtype_cell = code_tokens(cells[1]), cells[2]
            dtypes = code_tokens(dtype_cell)
            if not dtypes:
                continue  # 「来自 combo」「契约」：这一格不承诺具体白名单
            if any(t in ("attrs", "attr_tuple") for t in yaml_types):
                domain = "attr_array"
            elif "attr" in yaml_types:
                domain = "attr_single"
            else:
                domain = "scalar"
            for dtype in dtypes:
                checked += 1
                with self.subTest(row=cells[0], dtype=dtype):
                    self.assertIn(dtype, domains[domain],
                                  f"{cells[0]} 行写的 {dtype} 不在 "
                                  f"{domain}_dtypes 白名单里")
        self.assertGreaterEqual(checked, 3, "没有一格被真正核对，解析大概坏了")

    def test_int64_is_not_offered_for_int_arrays(self):
        # 真机踩过的那一格，单独钉一条：改回去会红。
        for cells in self.rows:
            if "aclIntArray*" in code_tokens(cells[0]):
                self.assertNotIn("int64_t", code_tokens(cells[2]))
                return
        self.fail("aclIntArray* 行不见了")


class OperatorClassTableTest(unittest.TestCase):
    """算子类别表必须逐格等于 OPERATOR_CLASSES。"""

    def setUp(self):
        self.rows = table_rows(REFERENCES / "case-design.md", "类别")
        self.assertTrue(self.rows, "算子类别表没抽到行，表头或格式变了")

    def test_table_lists_exactly_the_supported_classes(self):
        self.assertEqual(sorted(_coverage_strategy.OPERATOR_CLASSES),
                         sorted(cells[0] for cells in self.rows))

    def test_required_axes_and_default_group_match_the_source(self):
        for cells in self.rows:
            profile = _coverage_strategy.OPERATOR_CLASSES[cells[0]]
            with self.subTest(operator_class=cells[0]):
                axes = [item.strip() for item in cells[1].split("、")]
                group = [item.strip() for item in cells[2].split("×")]
                self.assertEqual(sorted(profile["axes"]), sorted(axes),
                                 "必需轴与 OPERATOR_CLASSES 不一致")
                self.assertEqual(list(profile["group"]), group,
                                 "默认三轴组与 OPERATOR_CLASSES 不一致（顺序也算）")


class PinnedAxisTableTest(unittest.TestCase):
    """轴取值表必须逐格等于 PINNED_AXIS_VALUES。

    这张表是 agent 唯一会读的那份；它和量具漂开时 agent 照文档写、被门禁拒，
    只能回去读量具源码反推——分面轴取值本来就是为了不让它自己发挥才钉死的。
    """

    def setUp(self):
        self.rows = table_rows(REFERENCES / "case-design.md", "轴")
        self.assertTrue(self.rows, "轴取值表没抽到行，表头或格式变了")

    def test_table_lists_exactly_the_pinned_axes(self):
        documented = set()
        for cells in self.rows:
            documented.update(name.strip(" `") for name in cells[0].split("/"))
        self.assertEqual(set(_axis_binding.PINNED_AXIS_VALUES), documented)

    def test_documented_values_match_the_source(self):
        for cells in self.rows:
            values = [item.strip() for item in
                      cells[1].replace("`", "").split()]
            for name in (n.strip(" `") for n in cells[0].split("/")):
                with self.subTest(axis=name):
                    expected = [str(v) for v
                                in _axis_binding.PINNED_AXIS_VALUES[name]]
                    self.assertEqual(expected, values, "顺序也算")


class ComparatorSelectionTableTest(unittest.TestCase):
    """比较器选择表必须与探针实测的能力域一致。

    median 那轮真机上，agent 为了确认「mixed_tolerance 认不认整型」去读了
    ATK 源码——文档只写了「整型用逐元素相等」，没写声明混合容差会怎样。
    这里锁的就是那个缺口：表里每一格都能在能力域 JSON 里找到对应事实。
    """

    SEMANTICS = CAPABILITY["semantics"]["comparator"]

    def test_float_row_lists_exactly_the_supported_dtypes(self):
        rows = table_rows(REFERENCES / "experimental_standard.md", "输出 dtype")
        self.assertTrue(rows, "比较器选择表没抽到行，表头或格式变了")
        documented = {name.strip() for name in rows[0][0].split("/")}
        self.assertEqual(set(self.SEMANTICS["mixed_tolerance_supported_dtypes"]),
                         documented)

    def test_only_int8_is_documented_as_unsafe(self):
        rejects = self.SEMANTICS["mixed_tolerance_rejects_off_by_one_by_dtype"]
        unsafe = sorted(name for name, ok in rejects.items() if not ok)
        self.assertEqual(["int8"], unsafe,
                         "探针结果变了，比较器选择表的 int8 例外要一起改")
        text = (REFERENCES / "experimental_standard.md").read_text(encoding="utf-8")
        self.assertIn("int8 要先判它是不是量化输出", text)


class FlashRunKnowledgeGapsTest(unittest.TestCase):
    """2026-08-17 双路真机跑测（median + roll，CC + deepseek-v4-flash）反查出的
    五处知识缺口。每一处都让 agent 去翻 ATK 源码、装机目录或反复试参数组合，
    删掉任何一条，下一轮就会原样再花一次。
    """

    def _text(self, name):
        if name == "SKILL.md":
            paths = [REFERENCES.parent / "SKILL.md",
                     REFERENCES.parent / "case-gen" / "SKILL.md",
                     REFERENCES.parent / "acceptance" / "SKILL.md"]
            return "\n".join(path.read_text(encoding="utf-8") for path in paths)
        return (REFERENCES / name).read_text(encoding="utf-8")

    def test_multi_output_declaration_is_documented(self):
        # 缺它：多输出算子要翻十几次 ATK 源码才敢写 YAML。
        text = self._text("yaml-schema.md")
        self.assertIn("输出不在 YAML 里声明", text)
        self.assertIn("基线返回值的顺序 = C 声明里出参的顺序", text)
        self.assertIn("aclnn_base_api.py", text)

    def test_product_name_to_build_soc_is_declared_unmappable(self):
        # 缺它：每个算子都要为「A2 对哪个 soc」去 grep 装机目录，且必然写成待确认项。
        text = self._text("intake.md")
        self.assertIn("没有一张能机械核对的对照表", text)
        self.assertIn("它不是待确认项", text)
        self.assertIn("check_soc_binding.py", text)

    def test_name_collision_with_builtin_is_declared_normal(self):
        # 缺它：agent 会去 nm -D 装机的 libopapi.so 求证一件不改变任何决定的事。
        text = self._text("SKILL.md")
        self.assertIn("重名是常态", text)
        self.assertIn("nm -D", text)

    def test_working_directory_requires_an_explicit_cd(self):
        # 缺它：产物落到会话启动目录，下一条命令报文件不存在，再去满盘 find。
        text = self._text("SKILL.md")
        self.assertIn("cd <工作目录> &&", text)

    def test_experimental_build_switch_is_documented(self):
        # 缺它：社区任务算子几乎都在 experimental/ 下，不带开关时报的是「算子不存在」。
        text = self._text("build-deploy.md")
        self.assertIn("--experimental", text)
        self.assertIn("ops_config.txt", text)

    def test_baseline_parameter_names_win_over_the_c_header(self):
        # 缺它：decl 的 parameters 键照 C 头文件抄，第一版必被 make_yaml 退回。
        text = self._text("case-design.md")
        self.assertIn("不要照 C 头文件抄", text)

    def test_movement_class_comparator_is_documented(self):
        # 缺它：搬运类会照「默认 mixed_tolerance_bm」写，再为 int8 白拆一份分面。
        text = self._text("case-design.md")
        self.assertIn("搬运类整份用 `equal`", text)
        self.assertIn("不必为它单独拆一份分面", text)

    def test_equal_facet_documents_the_nan_caveat(self):
        # 缺它：含部分 NaN 的用例会被 torch.equal 误判失败，agent 又拆回去。
        for name in ("case-design.md", "experimental_standard.md"):
            with self.subTest(reference=name):
                self.assertIn("has_infnan", self._text(name))

    def test_float_gate_documents_both_criteria(self):
        # 缺它：钉死的 dtype 轴撞上比例门禁，唯一出口是按 dtype 拆分面。
        text = self._text("case-design.md")
        self.assertIn("浮点一种都不能漏", text)
        self.assertIn("dtype_source", text)

    def test_inf_handling_is_documented(self):
        # 缺它：溢出到 inf 的场景下，两侧都是 inf 时是否 pass 无明确规则。
        text = self._text("experimental_standard.md")
        self.assertIn("inf", text.lower())
        self.assertIn("两侧都包含 inf", text)
        self.assertIn("判通过", text)
        self.assertIn("溢出场景", text)


class InfHandlingSemantics(unittest.TestCase):
    """inf 判定语义必须在能力域 JSON 中有明确记录。"""

    def test_inf_handling_in_capability_json(self):
        semantics = CAPABILITY["semantics"]["comparator"]
        self.assertIn("inf_handling", semantics,
                      "inf_handling 节缺失")

        inf = semantics["inf_handling"]
        # 三条核心判据
        self.assertTrue(inf["baseline_contains_inf_passes_unconditionally"],
                       "基线包含 inf 直接通过")
        self.assertTrue(inf["actual_contains_inf_but_baseline_does_not_fails"],
                       "待测包含 inf 但基线没有应该失败")
        self.assertTrue(inf["both_contain_inf_passes_without_value_check"],
                       "两侧都包含 inf 通过但不检查值")

        # ATK 不区分 +inf 和 -inf
        self.assertTrue(inf["does_not_distinguish_positive_and_negative_inf"],
                       "应标记不区分正负 inf")

        # 必须有证据链
        self.assertIn("evidence", inf)
        self.assertGreater(len(inf["evidence"]), 0,
                          "evidence 字段不能为空")

        # 证据必须指向 ATK 源码
        for evidence in inf["evidence"]:
            self.assertTrue(evidence.startswith("atk/"),
                           f"证据 {evidence} 应指向 atk/ 源码")


if __name__ == "__main__":
    unittest.main()
