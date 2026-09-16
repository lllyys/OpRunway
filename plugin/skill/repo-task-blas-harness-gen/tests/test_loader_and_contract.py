#!/usr/bin/env python3
"""harness-gen Step 1 测试：装载器三道门、§4 预筛、contract 衔接与 CLI。

跑法（skill 目录）：
    cd plugin/skill/repo-task-blas-harness-gen && \
        PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v

fixture：只读引用兄弟 skill repo-task-blas-case-gen 的 assets/example（数据件，
不 import 其代码）；一切变异在 tempfile 副本上做，仓内零写入。
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
EXAMPLES = SKILL.parent / "repo-task-blas-case-gen" / "assets" / "example"
sys.path.insert(0, str(SCRIPTS))

import contract  # noqa: E402
import package_loader as pl  # noqa: E402

SASUM_HEADER = [
    "case_name", "description", "n", "x_fill", "incx", "expect_result", "random_seed",
]


class _TmpPkg(unittest.TestCase):
    """带临时包克隆与字节替换工具的基类。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="hg-test-")
        self.addCleanup(self._tmp.cleanup)

    def clone(self, op="sasum"):
        src = EXAMPLES / op
        self.assertTrue(src.is_dir(), f"fixture 缺席：{src}（不 skip，按失败处理）")
        dst = Path(self._tmp.name) / op
        shutil.copytree(src, dst)
        return dst

    @staticmethod
    def patch_bytes(path, old, new, count=1):
        data = path.read_bytes()
        assert data.count(old) >= count, f"补丁锚点未命中：{old!r}"
        path.write_bytes(data.replace(old, new, count))

    def assert_reject(self, pkg, code, needle=""):
        with self.assertRaises(pl.LoaderReject) as ctx:
            pl.load_package(pkg)
        self.assertEqual(ctx.exception.code, code, ctx.exception.reason)
        self.assertIn(needle, ctx.exception.reason)


class TestPositive(_TmpPkg):
    def test_sasum_all_gates_and_contract(self):
        """sasum 全门通过；IR 衔接（原 T2/T4 语义在新宿主上的重述）。
        原 T1 不变量随附：装载（含通用区 exec）期间 stdout/stderr 必须为空。"""
        import contextlib, io
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            facts, specs, header = pl.load_package(EXAMPLES / "sasum")
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(header, SASUM_HEADER)
        self.assertEqual([s["name"] for s in specs], header)
        for spec in specs:
            self.assertTrue({"name", "kind", "source"} <= set(spec))
        ir = contract.compile_contract({"facts": facts, "column_specs": specs})
        self.assertEqual(ir["version"], contract.CONTRACT_VERSION)
        want = json.loads(
            (SKILL / "tests" / "ir-instances" / "sasum.json").read_text("utf-8"))
        self.assertEqual(ir, want)  # F-01：FACTS→compile→实例逐字段相等
        json.dumps(ir)  # IR 必须可 JSON 序列化

    def test_cherk_gates_pass_prescreen_rejects(self):
        """cherk（18 列、复数/nullable）过三道装载门、被 §4 预筛拒——装载≠支持。"""
        pkg = EXAMPLES / "cherk"
        data = (pkg / "gen_csv.py").read_bytes()
        markers = pl._find_markers(data)
        sha = pl._common_region_sha256(data, markers)
        facts = pl._extract_facts(data, pkg / "gen_csv.py", markers)
        pl._gate_abi_version(facts, sha)
        ns = pl._exec_module(data, pkg / "gen_csv.py")
        pl._gate_abi_surface(ns, facts)
        specs, header, _csv_raw = pl._gate_same_source(
            ns, facts, pkg / f"{facts['op']}_test.csv"
        )
        self.assertEqual(len(header), 18)
        self.assertEqual([s["name"] for s in specs], header)
        self.assert_reject(pkg, "UNSUPPORTED_CONTRACT", "nullable")

    def test_contract_rejects_empty_columns(self):
        """原 T4：column_specs 为空必须拒绝。"""
        facts, _specs, _header = pl.load_package(EXAMPLES / "sasum")
        with self.assertRaises(ValueError):
            contract.compile_contract({"facts": facts, "column_specs": []})


class TestGate1AbiVersion(_TmpPkg):
    def test_schema_v2_rejected(self):
        pkg = self.clone()
        self.patch_bytes(pkg / "gen_csv.py", b'"schema_version": 1,', b'"schema_version": 2,')
        self.assert_reject(pkg, "UNSUPPORTED_PACKAGE_VERSION", "未知包生成器 ABI")

    def test_common_region_tamper_rejected(self):
        pkg = self.clone()
        self.patch_bytes(pkg / "gen_csv.py",
                         b"GENERATOR_VERSION = 1\n", b"GENERATOR_VERSION = 1  # x\n")
        self.assert_reject(pkg, "UNSUPPORTED_PACKAGE_VERSION", "未知包生成器 ABI")

    def test_missing_interface_function_rejected(self):
        """接口函数缺失报 UNSUPPORTED_PACKAGE_VERSION（须给篡改区登记允许表条目，
        否则 SHA 门先拒、测不到 surface 门）。"""
        pkg = self.clone()
        gen = pkg / "gen_csv.py"
        self.patch_bytes(gen, b"def _column_specs(", b"def _column_specsX(")
        data = gen.read_bytes()
        sha = pl._common_region_sha256(data, pl._find_markers(data))
        original = pl.KNOWN_PACKAGE_ABIS
        pl.KNOWN_PACKAGE_ABIS = frozenset(original | {(1, 1, sha)})
        try:
            self.assert_reject(pkg, "UNSUPPORTED_PACKAGE_VERSION", "缺少函数")
        finally:
            pl.KNOWN_PACKAGE_ABIS = original

    def test_version_gate_precedes_exec(self):
        """通用区改成一执行就爆的代码：若门序正确，SHA 门先拒（UNSUPPORTED_PACKAGE_
        VERSION）；若 exec 先跑，会得到 PACKAGE_MALFORMED（exec 失败）——以码证序。"""
        pkg = self.clone()
        gen = pkg / "gen_csv.py"
        gen.write_bytes(gen.read_bytes() + b'\nraise RuntimeError("boom")\n')
        self.assert_reject(pkg, "UNSUPPORTED_PACKAGE_VERSION", "未知包生成器 ABI")

    def test_allowlist_registration_error_caught_by_cross_check(self):
        """交叉核对抓允许表登记错误：给篡改过版本常量的通用区登记允许表条目后，
        GENERATOR_VERSION 与 FACTS 不一致仍必须被拒。"""
        pkg = self.clone()
        gen = pkg / "gen_csv.py"
        self.patch_bytes(gen, b"GENERATOR_VERSION = 1\n", b"GENERATOR_VERSION = 2\n")
        data = gen.read_bytes()
        sha = pl._common_region_sha256(data, pl._find_markers(data))
        original = pl.KNOWN_PACKAGE_ABIS
        pl.KNOWN_PACKAGE_ABIS = frozenset(original | {(1, 1, sha)})
        try:
            self.assert_reject(pkg, "UNSUPPORTED_PACKAGE_VERSION", "不一致")
        finally:
            pl.KNOWN_PACKAGE_ABIS = original


class TestGate3FunctionSide(_TmpPkg):
    """门 3 的函数侧负例：经允许表登记通道注入分叉的 _header_columns。"""

    def _with_override(self, override_src, code, needle):
        pkg = self.clone()
        gen = pkg / "gen_csv.py"
        gen.write_bytes(gen.read_bytes() + override_src.encode())
        data = gen.read_bytes()
        sha = pl._common_region_sha256(data, pl._find_markers(data))
        original = pl.KNOWN_PACKAGE_ABIS
        pl.KNOWN_PACKAGE_ABIS = frozenset(original | {(1, 1, sha)})
        try:
            self.assert_reject(pkg, code, needle)
        finally:
            pl.KNOWN_PACKAGE_ABIS = original

    def test_header_fn_divergence_rejected(self):
        self._with_override('\ndef _header_columns(facts):\n    return ["a", "b"]\n',
                            "HEADER_MISMATCH", "同源断言失败")

    def test_header_fn_duplicate_rejected(self):
        self._with_override('\ndef _header_columns(facts):\n    return ["n", "n"]\n',
                            "HEADER_MISMATCH", "重复列名")

    def test_nonlist_return_malformed(self):
        # None 可能先在 _header_columns 内部迭代时爆（执行失败）或过形态检查
        # （返回形态非法），两条路径同归 PACKAGE_MALFORMED，只断言停机码。
        self._with_override('\ndef _column_specs(facts):\n    return None\n',
                            "PACKAGE_MALFORMED", "")


class TestGate2Discipline(_TmpPkg):
    def test_toplevel_statement_rejected(self):
        pkg = self.clone()
        self.patch_bytes(pkg / "gen_csv.py",
                         "# ===== FACTS 区开始 =====\n".encode(),
                         "# ===== FACTS 区开始 =====\nimport os\n".encode())
        self.assert_reject(pkg, "PACKAGE_MALFORMED", "不允许的顶层语句")

    def test_nonliteral_facts_rejected(self):
        pkg = self.clone()
        self.patch_bytes(pkg / "gen_csv.py", b'"op": "sasum",', b'"op": str("sasum"),')
        self.assert_reject(pkg, "PACKAGE_MALFORMED", "非纯字面量")

    def test_missing_marker_rejected(self):
        pkg = self.clone()
        self.patch_bytes(pkg / "gen_csv.py",
                         "# ===== FACTS 区结束 =====\n".encode(), b"")
        self.assert_reject(pkg, "PACKAGE_MALFORMED", "标记行")

    def test_marker_text_in_docstring_not_counted(self):
        """docstring 里出现标记文本不算标记：真标记在场则照常装载。"""
        pkg = self.clone()
        gen = pkg / "gen_csv.py"
        gen.write_bytes(('"""\n# ===== FACTS 区开始 =====\n"""\n').encode()
                        + gen.read_bytes())
        facts, _specs, header = pl.load_package(pkg)
        self.assertEqual(facts["op"], "sasum")
        self.assertEqual(header, SASUM_HEADER)

    def test_marker_only_in_docstring_rejected(self):
        """删掉真标记、只留 docstring 里的同文本行：必须按标记缺失拒绝。"""
        pkg = self.clone()
        gen = pkg / "gen_csv.py"
        data = ('"""\n# ===== FACTS 区开始 =====\n"""\n').encode() + gen.read_bytes()
        data = data.replace("# ===== FACTS 区开始 =====\n".encode(), b"", 1)
        # 上行 replace 会先命中 docstring 里那份，需删注释那份：从尾部定位
        gen.write_bytes(gen.read_bytes().replace(
            "# ===== FACTS 区开始 =====\n".encode(), b"", 1))
        gen.write_bytes(('"""\n# ===== FACTS 区开始 =====\n"""\n').encode()
                        + gen.read_bytes())
        self.assert_reject(pkg, "PACKAGE_MALFORMED", "标记行")

    def test_missing_gen_csv_rejected(self):
        pkg = self.clone()
        (pkg / "gen_csv.py").unlink()
        self.assert_reject(pkg, "PACKAGE_MALFORMED", "缺少 gen_csv.py")


class TestGate3SameSource(_TmpPkg):
    def _swap_header(self, pkg, new_header):
        csv_path = pkg / "sasum_test.csv"
        lines = csv_path.read_text(encoding="utf-8").split("\n")
        lines[0] = new_header
        csv_path.write_text("\n".join(lines), encoding="utf-8")

    def test_column_order_mismatch_rejected(self):
        pkg = self.clone()
        self._swap_header(pkg, "case_name,description,x_fill,n,incx,expect_result,random_seed")
        self.assert_reject(pkg, "HEADER_MISMATCH", "同源断言失败")

    def test_duplicate_column_rejected(self):
        pkg = self.clone()
        self._swap_header(pkg, "case_name,description,n,n,incx,expect_result,random_seed")
        self.assert_reject(pkg, "HEADER_MISMATCH", "重复列名")

    def test_missing_csv_rejected(self):
        pkg = self.clone()
        (pkg / "sasum_test.csv").unlink()
        self.assert_reject(pkg, "PACKAGE_MALFORMED", "缺少 CSV")


class TestPrescreen(_TmpPkg):
    def test_golden_kind_variants_rejected(self):
        for kind in ("lapacke", "loop", "composed"):
            pkg = self.clone()
            self.patch_bytes(pkg / "gen_csv.py", b'"kind": "cblas"',
                             b'"kind": "%s"' % kind.encode())
            self.assert_reject(pkg, "UNSUPPORTED_CONTRACT", "golden.kind")
            shutil.rmtree(pkg)

    def test_complex_scalar_rejected(self):
        pkg = self.clone()
        self.patch_bytes(pkg / "gen_csv.py",
                         b'"role": "out_scalar", "dtype": "float32"',
                         b'"role": "out_scalar", "dtype": "complex64"')
        self.assert_reject(pkg, "UNSUPPORTED_CONTRACT", "复数标量")


class TestPrescreenScope(unittest.TestCase):
    """F3：复数 profile 只在存在动态标量时才构成复数标量。"""

    BASE_FACTS = {
        "golden": {"kind": "cblas", "symbol": "cblas_x"},
    }

    def test_dynamic_complex_buffer_alone_passes_prescreen(self):
        facts = dict(self.BASE_FACTS)
        facts["params"] = [
            {"name": "A", "role": "matrix", "dtype_from": "p"},
        ]
        facts["dtype_profiles"] = [{"name": "c64", "scalar_dtype": "complex64"}]
        pl.check_contract_prescreen(facts)  # 不抛即过

    def test_dynamic_complex_scalar_rejected(self):
        facts = dict(self.BASE_FACTS)
        facts["params"] = [
            {"name": "alpha", "role": "scalar", "dtype_from": "p"},
        ]
        facts["dtype_profiles"] = [{"name": "c64", "scalar_dtype": "complex64"}]
        with self.assertRaises(pl.UnsupportedContract):
            pl.check_contract_prescreen(facts)

    def test_illegal_golden_kind_is_malformed(self):
        facts = {"golden": {"kind": "weird"}, "params": [{"name": "n"}]}
        with self.assertRaises(pl.PackageMalformed):
            pl.check_contract_prescreen(facts)


class TestCli(_TmpPkg):
    def _run(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "harness.py"), *args],
            capture_output=True, text=True, cwd=cwd,
            env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"},
        )

    def test_load_success_writes_snapshot(self):
        workdir = Path(self._tmp.name) / "work"
        workdir.mkdir()
        r = self._run("load", "--package", str(EXAMPLES / "sasum"), cwd=workdir)
        self.assertEqual(r.returncode, 0, r.stderr)
        snapshot = json.loads((workdir / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["op"], "sasum")
        self.assertEqual(snapshot["header"], SASUM_HEADER)
        self.assertEqual(snapshot["snapshot_version"], 1)
        self.assertEqual(snapshot["facts"]["symbol"],
                         json.loads(json.dumps(snapshot["facts"]))["symbol"])
        self.assertIn("golden", snapshot["facts"])  # H2 只消费快照即可编译（F1）
        import hashlib
        self.assertEqual(
            snapshot["gen_csv_sha256"],
            hashlib.sha256((EXAMPLES / "sasum" / "gen_csv.py").read_bytes()).hexdigest(),
        )
        self.assertEqual(
            snapshot["csv_sha256"],
            hashlib.sha256((EXAMPLES / "sasum" / "sasum_test.csv").read_bytes()).hexdigest(),
        )

    def test_load_reject_stops_with_code(self):
        workdir = Path(self._tmp.name) / "work2"
        workdir.mkdir()
        r = self._run("load", "--package", str(EXAMPLES / "cherk"), cwd=workdir)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stderr.strip().splitlines()[-1], "STOP UNSUPPORTED_CONTRACT")

    def test_load_bad_path_exit3(self):
        r = self._run("load", "--package", str(Path(self._tmp.name) / "nope"))
        self.assertEqual(r.returncode, 3)



if __name__ == "__main__":
    unittest.main()
