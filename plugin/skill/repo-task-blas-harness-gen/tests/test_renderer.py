#!/usr/bin/env python3
"""renderer 测试：sasum 渲染逐字节复现 rev1 候选基准 + 确定性双渲。"""

import json
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))
import renderer  # noqa: E402

REV = (SKILL.parents[2] / "reports" / "harness-spike-20260909" / "overlay-rev1"
       / "test" / "asum" / "sasum")
EX = SKILL.parent / "repo-task-blas-case-gen" / "assets" / "example" / "sasum"


def _ir():
    ir = json.loads((SKILL / "tests" / "ir-instances" / "sasum.json").read_text("utf-8"))
    ir["_arch_dir"] = "arch22"
    return ir


class TestRenderer(unittest.TestCase):
    def setUp(self):
        self.csv = (EX / "sasum_test.csv").read_bytes()

    def test_deterministic_double_render(self):
        a = renderer.render_overlay(_ir(), self.csv)
        b = renderer.render_overlay(_ir(), self.csv)
        self.assertEqual(a, b)

    @unittest.skipUnless(REV.is_dir(), "rev1 基准缺席（开发件，不随分发）")
    def test_byte_identical_to_rev1(self):
        out = renderer.render_overlay(_ir(), self.csv)
        for rel, data in out.items():
            name = rel.split("test/asum/sasum/")[1]
            self.assertEqual(data, (REV / name).read_bytes(), name)

    def test_csv_passed_through_verbatim(self):
        out = renderer.render_overlay(_ir(), self.csv)
        key = [k for k in out if k.endswith(".csv")][0]
        self.assertEqual(out[key], self.csv)


class TestRendererGates(unittest.TestCase):
    import ir_validator as _iv

    def _sgemm(self):
        ir = json.loads((SKILL / "tests" / "ir-instances" / "sgemm.json").read_text("utf-8"))
        ir["_arch_dir"] = "arch35"
        return ir

    def test_sgemm_renders_via_cblas_path(self):
        # sgemm 现走 cblas_call 通用路径，渲染 6 件（不再停机）。
        out = renderer.render_overlay(self._sgemm(), b"case_name,description\n")
        self.assertEqual(len(out), 6)
        names = sorted(k.split("/")[-1] for k in out)
        self.assertIn("sgemm_test.cpp", names)
        self.assertIn("sgemm_npu_wrapper.h", names)

    def test_l1_builtin_with_matrix_would_stop(self):
        # builtin_kernel 却带矩阵角色（矛盾形）走 L1 门，UNSUPPORTED_CONTRACT。
        ir = json.loads((SKILL / "tests" / "ir-instances" / "sasum.json").read_text("utf-8"))
        ir["_arch_dir"] = "arch22"
        ir["params"][2]["role"] = "matrix"  # x 改成矩阵，仍 builtin_kernel
        import ir_validator as iv
        with self.assertRaises(iv.ContractReject):
            renderer.render_overlay(ir, self_csv())

    def test_bad_family_rejected(self):
        ir = json.loads((SKILL / "tests" / "ir-instances" / "sasum.json").read_text("utf-8"))
        ir["_arch_dir"] = "arch22"
        ir["family"] = "../escape"
        import ir_validator as iv
        with self.assertRaises(iv.ContractReject):
            renderer.render_overlay(ir, self_csv())


def self_csv():
    return (EX / "sasum_test.csv").read_bytes()


class TestStagingChain(unittest.TestCase):
    """H3→H5 工件链：_check_staging_chain 对额外文件/改动 SHA fail-closed。"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        sys.path.insert(0, str(SKILL / "scripts"))

    def test_chain_detects_extra_file(self):
        import harness, json as j
        from pathlib import Path as P
        wd = P(self._tmp.name)
        st = wd / "staging" / "test" / "asum" / "sasum"
        st.mkdir(parents=True)
        (st / "CMakeLists.txt").write_text("x\n")
        import hashlib
        sha = hashlib.sha256(b"x\n").hexdigest()
        prov = {"op": "sasum", "family": "asum", "arch": "arch22",
                "file_sha256": {"test/asum/sasum/CMakeLists.txt": sha}}
        (wd / "staging" / "provenance.json").write_text(j.dumps(prov))
        (st / "sneak.txt").write_text("sneaky\n")  # 未记录的额外文件
        import os
        cwd = os.getcwd(); os.chdir(wd)
        try:
            import installer
            with self.assertRaises(installer.InstallReject):
                harness._check_staging_chain({"op": "sasum", "family": "asum"})
        finally:
            os.chdir(cwd)

    def test_chain_detects_nested_provenance_bypass(self):
        import harness, json as j
        from pathlib import Path as P
        import hashlib, os
        wd = P(self._tmp.name)
        st = wd / "staging" / "test" / "asum" / "sasum"
        st.mkdir(parents=True)
        (st / "CMakeLists.txt").write_text("x\n")
        sha = hashlib.sha256(b"x\n").hexdigest()
        prov = {"op": "sasum", "family": "asum", "arch": "arch22",
                "file_sha256": {"test/asum/sasum/CMakeLists.txt": sha}}
        (wd / "staging" / "provenance.json").write_text(j.dumps(prov))
        # 嵌套同名 provenance.json：basename 排除会漏，相对路径排除能抓
        (st / "provenance.json").write_text("sneaky\n")
        cwd = os.getcwd(); os.chdir(wd)
        try:
            import installer
            with self.assertRaises(installer.InstallReject):
                harness._check_staging_chain({"op": "sasum", "family": "asum"})
        finally:
            os.chdir(cwd)


class TestSgerZeroChange(unittest.TestCase):
    """Step 5 证伪：sger（新 cblas 算子）零改动接入——只加新文件，代码无算子特判。"""

    def test_sger_renders_via_general_path(self):
        ir = json.loads((SKILL / "tests" / "ir-instances" / "sger.json").read_text("utf-8"))
        ir["_arch_dir"] = "arch22"
        out = renderer.render_overlay(ir, b"case_name,description\n")
        self.assertEqual(len(out), 6)
        w = [d.decode() for k, d in out.items() if k.endswith("wrapper.h")][0]
        self.assertNotIn("k > 0", w)          # upload_guard=None → 无 k>0 守卫
        self.assertEqual(w.count("copyFromHost"), 3)  # x/y/A 无条件上卡
        g = [d.decode() for k, d in out.items() if k.endswith("golden.h")][0]
        self.assertIn("cblas_sger(", g)
        self.assertIn("ACLBLAS_STATUS_HANDLE_IS_NULLPTR", g)

    def test_no_operator_specialcasing_in_code(self):
        """renderer/contract/ir_validator 不得有 op 名/symbol 键的特判分支。"""
        import re
        for fn in ("renderer.py", "contract.py", "ir_validator.py"):
            src = (SKILL / "scripts" / fn).read_text("utf-8")
            # 逐行剔除注释与 docstring 行后，查禁止的算子名字面
            for i, line in enumerate(src.splitlines(), 1):
                code = line.split("#")[0]
                if code.strip().startswith(('"""', "'''", "*", "#")):
                    continue
                for lit in ('"sger"', "'sger'", '"sgemm"', "'sgemm'",
                            '"sasum"', "'sasum'", '"cblas_sger"', '"cblas_sgemm"'):
                    self.assertNotIn(lit, code, f"{fn}:{i} 含算子特判 {lit}")


class TestSgerVerticalSlice(unittest.TestCase):
    """F-07：sger 完整纵切 FACTS→compile→validate→render，端到端而非假 CSV。"""

    def test_facts_to_render_slice(self):
        import contract
        import ir_validator as iv
        D = SKILL / "tests" / "ir-instances"
        facts = json.loads((D / "sger-facts-draft.json").read_text("utf-8"))
        facts.pop("_provenance", None)
        specs = json.loads((D / "sger-column-specs-draft.json").read_text("utf-8"))
        ir = contract.compile_contract({"facts": facts, "column_specs": specs})
        # compile 出的 IR == 冻结实例
        self.assertEqual(ir, json.loads((D / "sger.json").read_text("utf-8")))
        iv.validate_ir(ir)
        # 用真实多行 sger CSV（含负步长/INT_MIN/quick-return/null 行），非假表头。
        csv = (D / "sger-sample.csv").read_bytes()
        ir["_arch_dir"] = "arch22"
        out = renderer.render_overlay(ir, csv)
        self.assertEqual(len(out), 6)
        g = [d.decode() for k, d in out.items() if k.endswith("golden.h")][0]
        self.assertIn("cblas_sger(", g)
        self.assertIn("INT_MIN", g.replace("-2147483648", "INT_MIN"))
        w = [d.decode() for k, d in out.items() if k.endswith("wrapper.h")][0]
        self.assertIn("ACLBLAS_STATUS_HANDLE_IS_NULLPTR", w)  # F-01：handle 状态从 status_plan
        # A′：设备权威——wrapper 恒调真设备一次并返回其状态，无 quick-return 早返回
        self.assertEqual(w.count("aclblasSger("), 1)          # 恰一个设备调用点
        self.assertIn("kMaxSpanElems", w)                     # 搬运上限
        self.assertIn("&& ok_A)", w)                          # 回读仅在全量搬运时（F-02）
        # F-02：span 全程 int64（含 abs(INT_MIN) 安全），wrapper 无 32 位 0 - incx
        self.assertIn("std::max<int64_t>(static_cast<int64_t>(incx)", w)
        tc = [d.decode() for k, d in out.items() if k.endswith("test.cpp")][0]
        self.assertIn("verifyVector", tc)                     # full 数值比对
        self.assertIn("const bool full =", tc)                # 探针/全量二分
        self.assertIn("buffer span exceeds cap", tc)          # 资源上限守卫（非设备状态）
        self.assertIn("if (!full) return;", tc)               # 非 full 只验状态码
        self.assertIn("std::max<int64_t>(static_cast<int64_t>(p.incx)", tc)  # 造数 span int64
        # int ABI 域门（防 int64 成员→int 形参窄化后 test/wrapper span 不一致致越界）
        self.assertIn("out of device int range", tc)
        self.assertIn("static_cast<int>(p.incx) != p.incx", tc)
        # 快照只在 full 行（非 full 不复制完整输出）
        self.assertIn("if (full) golden_A = h_A;", tc)
        # CSV 逐字节透传：负步长与 INT_MIN 行原样出现在部署的 CSV 件里。
        deployed = [d for k, d in out.items() if k.endswith(".csv")][0]
        self.assertEqual(deployed, csv)
        self.assertIn(b"-2147483648", deployed)
        self.assertIn(b"negative incx", deployed)

    def test_sidecar_closed_schema_rejects_tamper(self):
        """F-05：侧车加未知键、改 format_version、改内部 symbol 均被拒（闭 schema）。"""
        import ir_validator as iv
        import tempfile
        orig = iv.STATUS_PLANS_DIR
        from pathlib import Path as P
        good = json.loads((orig / "cblas_sger.json").read_text("utf-8"))
        tampers = [
            ("未知键 kernel_block", {**good, "kernel_block": "evil()"}),
            ("改 format_version", {**good, "format_version": 2}),
            ("改内部 symbol", {**good, "symbol": "cblas_other"}),
        ]
        for label, bad in tampers:
            with tempfile.TemporaryDirectory() as td:
                (P(td) / "cblas_sger.json").write_text(json.dumps(bad))
                iv.STATUS_PLANS_DIR = P(td)
                try:
                    with self.assertRaises(iv.ContractReject, msg=label):
                        iv.load_status_plan_sidecar("cblas_sger")
                finally:
                    iv.STATUS_PLANS_DIR = orig


class TestMatrixPhysFailClosed(unittest.TestCase):
    """三审复核点：侧车 matrix_phys 键值须语义闭合（键=matrix 参数、值=enum 参数），
    拼写错的条目不得被静默忽略而生成语义错误的转置 harness。"""

    def _compile_with_sidecar(self, matrix_phys):
        import contract, ir_validator as iv, tempfile, json as _json
        from pathlib import Path as P
        D = SKILL / "tests" / "ir-instances"
        facts = _json.loads((D / "sger-facts-draft.json").read_text("utf-8"))
        facts.pop("_provenance", None)
        specs = _json.loads((D / "sger-column-specs-draft.json").read_text("utf-8"))
        good = _json.loads((SKILL / "assets" / "status-plans" / "cblas_sger.json").read_text("utf-8"))
        good["matrix_phys"] = matrix_phys
        orig = iv.STATUS_PLANS_DIR
        with tempfile.TemporaryDirectory() as td:
            (P(td) / "cblas_sger.json").write_text(_json.dumps(good))
            iv.STATUS_PLANS_DIR = P(td)
            try:
                return contract.compile_contract({"facts": facts, "column_specs": specs})
            finally:
                iv.STATUS_PLANS_DIR = orig

    def test_empty_matrix_phys_ok(self):
        import ir_validator as iv
        ir = self._compile_with_sidecar({})   # sger 无转置，空 matrix_phys 合法
        iv.validate_ir(ir)

    def test_key_not_matrix_rejected(self):
        import ir_validator as iv
        with self.assertRaises(iv.ContractReject):
            self._compile_with_sidecar({"nonexistent": "trans"})

    def test_value_not_enum_rejected(self):
        import ir_validator as iv
        # 键 A 是 matrix，但 sger 无 enum 参数，任何值都非 enum → 拒
        with self.assertRaises(iv.ContractReject):
            self._compile_with_sidecar({"A": "trans_typo"})

    def test_value_non_str_rejected(self):
        import ir_validator as iv
        # 非字符串值（list/dict）不得逃成 TypeError，须 ContractReject（fail-closed）
        for bad in ([("A", ["trans"])], [("A", {"x": 1})], [("A", 1)]):
            with self.assertRaises(iv.ContractReject):
                self._compile_with_sidecar(dict(bad))
