"""msopprof 产物解析器的单元测试：每条用例真铺目录、真写 CSV，不打桩绕过。

被测对象是 `assets/template/verify_performance.py` 里的 `parse_op_summary`，
外加采集前后的两个环境判据 helper（`_collection_failure_marker`、`_check_free_space`）。
模板顶部有 `@@OP@@` 这类渲染占位符，不能直接 import，所以先用 `package.render_runtime()`
渲染出一份可执行副本再按路径加载——与 repo-task-blas-accept 的
`tests/test_retest_integration.py` 同一套加载做法。

解析器是整条性能链上唯一一处出错会把 FAIL 写成 PASS 的地方：漏计任何一行
`Task Duration(us)` 都让求和偏小、ratio 虚高。所以用例一律造真 CSV，让解析器走完
递归 glob、路径去重、读列、数值校验与截断判定的全程。
"""

import collections
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import package  # noqa: E402


def _load_gauge():
    """渲染一份量具副本并按路径导入。渲染目录由模块全局持有到进程结束。"""
    package.render_runtime(
        "sger", "sger", ["m", "n"], _RENDER_DIR.name, "0" * 64,
    )
    name = "blas_perf_gauge_rendered"
    spec = importlib.util.spec_from_file_location(
        name, Path(_RENDER_DIR.name) / "verify_performance.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_RENDER_DIR = tempfile.TemporaryDirectory()
GAUGE = _load_gauge()

# 真机 OpBasicInfo.csv 的九列，没有 Task Type 列（A3 实测，2026-09-23）。
HEADER = (
    "Op Name,Op Type,Task Duration(us),Block Dim,Mix Block Dim,"
    "Device Id,Pid,Current Freq,Rated Freq"
)
PROF_DIR = "OPPROF_20260923112233_abcdef"
KERNEL = "sger_kernel_mix_aic"


def _csv_text(durations, header=HEADER):
    """按九列表头铺一份 CSV 正文，durations 每项一个数据行。"""
    lines = [header]
    for index, duration in enumerate(durations):
        lines.append(f"sger,MatMul,{duration},8,0,0,{40000 + index},1800,1800")
    return "\n".join(lines) + "\n"


def _write_csv(path, durations, header=HEADER):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_csv_text(durations, header), encoding="utf-8")
    return path


class ProfileDirCase(unittest.TestCase):
    """脚手架：一个空的采样输出目录，各用例往里铺两种布局的产物。"""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.out = self.root / "r1"
        self.out.mkdir(parents=True)

    def fresh(self, tag):
        """另起一个空的采样输出目录，供 subTest 逐项互不污染。"""
        out = self.root / tag
        out.mkdir(parents=True)
        return out

    def flat(self, durations, header=HEADER):
        """扁平布局：采到 1 个 launch 时的形态。"""
        return _write_csv(self.out / PROF_DIR / "OpBasicInfo.csv", durations, header)

    def nested(self, index, durations, header=HEADER):
        """嵌套布局：采到多个 launch 时按 kernel 符号名与序号分目录。"""
        return _write_csv(
            self.out / PROF_DIR / KERNEL / str(index)
            / f"OpBasicInfo_202609231122{index:02d}.csv",
            durations,
            header,
        )


class TestLayouts(ProfileDirCase):
    """两种产物布局都要扫到，且同一份文件只计一次。"""

    def test_flat_single_row(self):
        self.flat([23.78])
        self.assertEqual(GAUGE.parse_op_summary(self.out), (23.78, 1))

    def test_nested_rows_are_summed(self):
        """多 launch 算子的正常形态：多目录多行求和，launches 即行数。"""
        self.nested(0, [10.5, 2.25])
        self.nested(1, [4.0])
        self.nested(2, [1.25])
        kernel_us, launches = GAUGE.parse_op_summary(self.out)
        self.assertAlmostEqual(kernel_us, 18.0, places=9)
        self.assertEqual(launches, 4)

    def test_both_layouts_counted_once(self):
        """扁平文件被两条 glob 同时命中，去重失效会让它的行翻倍计入。"""
        self.flat([10.0, 3.0])
        self.nested(0, [1.5])
        kernel_us, launches = GAUGE.parse_op_summary(self.out)
        self.assertAlmostEqual(kernel_us, 14.5, places=9)
        self.assertEqual(launches, 3)

    def test_no_files(self):
        """一份产物都没有：返回 0 让调用方判 NO_KERNEL，不抛异常。"""
        self.assertEqual(GAUGE.parse_op_summary(self.out), (0.0, 0))

    def test_header_only_is_rejected(self):
        """只有表头的 CSV 是产物异常，不与零文件同一去向。

        零文件意味着这一次根本没采到，判 NO_KERNEL 正确；CSV 在而数据行不在，
        意味着工具建了文件却没写进去——那一次 launch 的耗时丢了。把它当零行放过，
        多文件场景下剩余的行会被当成完整读数，少算的部分直接抬高 ratio。
        （push 前 audit 抓到，2026-09-24）"""
        self.flat([])
        with self.assertRaises(GAUGE.ProfileParseError) as ctx:
            GAUGE.parse_op_summary(self.out)
        self.assertIn("只有表头", str(ctx.exception))


class TestMalformedRows(ProfileDirCase):
    """产物坏掉的各种形态一律抛 ProfileParseError，不静默少算。"""

    def test_missing_duration_column(self):
        header = "Op Name,Op Type,Block Dim,Device Id,Pid,Current Freq,Rated Freq"
        path = self.out / PROF_DIR / "OpBasicInfo.csv"
        path.parent.mkdir(parents=True)
        path.write_text(f"{header}\nsger,MatMul,8,0,40000,1800,1800\n", encoding="utf-8")
        with self.assertRaises(GAUGE.ProfileParseError) as ctx:
            GAUGE.parse_op_summary(self.out)
        self.assertIn("Task Duration(us)", str(ctx.exception))

    def test_non_numeric(self):
        self.flat(["abc"])
        with self.assertRaises(GAUGE.ProfileParseError):
            GAUGE.parse_op_summary(self.out)

    def test_non_finite(self):
        """inf 与 nan 能被 float() 读出来，只有有限性检查拦得住。"""
        for index, text in enumerate(("inf", "-inf", "nan")):
            with self.subTest(duration=text):
                out = self.fresh(f"nonfinite{index}")
                _write_csv(out / PROF_DIR / "OpBasicInfo.csv", [text])
                with self.assertRaises(GAUGE.ProfileParseError):
                    GAUGE.parse_op_summary(out)

    def test_non_positive(self):
        """负值与零都不是合法 kernel 耗时。"""
        for index, value in enumerate(("-1.5", "0", "0.0")):
            with self.subTest(duration=value):
                out = self.fresh(f"nonpositive{index}")
                _write_csv(out / PROF_DIR / "OpBasicInfo.csv", [value])
                with self.assertRaises(GAUGE.ProfileParseError):
                    GAUGE.parse_op_summary(out)

    def test_one_bad_row_fails_whole_parse(self):
        """坏行与好行混在一起也要整份抛错，不能只求和好的那部分。"""
        self.nested(0, [10.0])
        self.nested(1, ["-3.0"])
        with self.assertRaises(GAUGE.ProfileParseError):
            GAUGE.parse_op_summary(self.out)


class TestTruncation(ProfileDirCase):
    """撞上 --launch-count 上限即判截断，这是唯一会把 FAIL 写成 PASS 的路径。"""

    def test_truncated_at_limit(self):
        self.nested(0, [1.0, 2.0])
        self.nested(1, [3.0])
        with self.assertRaises(GAUGE.ProfileTruncatedError) as ctx:
            GAUGE.parse_op_summary(self.out, launch_limit=3)
        self.assertIn("当前采集口径不支持该用例的 launch 规模", str(ctx.exception))

    def test_truncated_is_parse_error_subclass(self):
        """调用方按类型分去向：截断要落在采集口径的措辞上，不能说成算子失败。"""
        self.assertTrue(
            issubclass(GAUGE.ProfileTruncatedError, GAUGE.ProfileParseError)
        )

    def test_below_limit_returns(self):
        self.nested(0, [1.0, 2.0])
        kernel_us, launches = GAUGE.parse_op_summary(self.out, launch_limit=3)
        self.assertAlmostEqual(kernel_us, 3.0, places=9)
        self.assertEqual(launches, 2)

    def test_no_limit_never_truncates(self):
        """不给上限时（默认 None）行数再多也照常返回。"""
        self.nested(0, [1.0, 2.0, 3.0])
        self.assertEqual(GAUGE.parse_op_summary(self.out), (6.0, 3))


class TestCollectionFailureMarker(unittest.TestCase):
    """磁盘满时工具仍退 0 且不产 CSV，只有日志里这几个串能把它与 NO_KERNEL 分开。"""

    def test_markers_hit(self):
        for marker in ("Copy failed", "Failed to save", "No space left"):
            with self.subTest(marker=marker):
                log = f"[INFO] profiling start\n[WARN] {marker}: /home/prof\n[INFO] done\n"
                self.assertEqual(GAUGE._collection_failure_marker(log), marker)

    def test_clean_log(self):
        log = "[INFO] profiling start\n[INFO] Profiling data has been saved\n"
        self.assertIsNone(GAUGE._collection_failure_marker(log))

    def test_empty_input(self):
        self.assertIsNone(GAUGE._collection_failure_marker(""))
        self.assertIsNone(GAUGE._collection_failure_marker(None))


Usage = collections.namedtuple("Usage", "total used free")


class TestCheckFreeSpace(unittest.TestCase):
    """采样前按本次采集上限估空间，不按上一例的实际占用外推。"""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.out = Path(temporary.name) / "r1"
        self.out.mkdir(parents=True)

    def usage(self, free_mb):
        total = 4096 * 1024 * 1024
        free = free_mb * 1024 * 1024
        return mock.patch.object(
            GAUGE.shutil, "disk_usage",
            return_value=Usage(total, total - free, free),
        )

    def test_enough_space(self):
        with self.usage(4000):
            self.assertIsNone(GAUGE._check_free_space(self.out, 512))

    def test_short_of_space(self):
        # 64 个 launch × 2.2 MB × 1.5 峰值系数 = 211 MB，可用只有 8 MB。
        with self.usage(8):
            message = GAUGE._check_free_space(self.out, 64)
        self.assertIsNotNone(message)
        self.assertIn("可用 8 MB", message)
        self.assertIn("211 MB", message)
        self.assertIn("--launch-count=64", message)

    def test_estimate_scales_with_limit(self):
        """所需量随上限线性增长：同样 8 MB 可用，上限翻倍则需求翻倍。"""
        with self.usage(8):
            message = GAUGE._check_free_space(self.out, 128)
        self.assertIn("422 MB", message)


if __name__ == "__main__":
    unittest.main()


class ProfileSharedCollectionTest(unittest.TestCase):
    """blas 与 sparse_frame 渲染出的采集通路必须是同一份代码。

    量具模板由 render_runtime 对所有 harness_profile 共用，所以换采集后端会同时
    改掉 sparse 新渲染出的量具。这条测试把「共用」从口头约定变成机械判据：
    两个 profile 的渲染产物只允许在构建与绑卡约定常量上不同，采集通路一旦分叉
    就会红——那意味着 blas 的真机证据不再覆盖 sparse。
    """

    #: 允许存在差异的渲染常量。除这两项外，两份产物必须逐字节相同。
    PROFILE_CONSTANTS = ("HARNESS_PROFILE", "BUILD_CONVENTION")

    def _render(self, profile, out_dir):
        package.render_runtime(
            op="x", family="y", perf_key=["n"], out_dir=out_dir,
            csv_sha256="0" * 64, harness_profile=profile,
        )
        return (out_dir / "verify_performance.py").read_text(encoding="utf-8")

    def test_only_build_convention_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blas = self._render("blas", root / "blas").split("\n")
            sparse = self._render("sparse_frame", root / "sparse").split("\n")
        self.assertEqual(len(blas), len(sparse), "两个 profile 的行数应当一致")
        differing = [
            (n, b) for n, (b, s) in enumerate(zip(blas, sparse), 1) if b != s
        ]
        for number, line in differing:
            self.assertTrue(
                any(line.lstrip().startswith(k) for k in self.PROFILE_CONSTANTS),
                f"第 {number} 行在两个 profile 下不同，但它不是构建/绑卡常量：{line[:70]}",
            )
        self.assertEqual(
            len(differing), len(self.PROFILE_CONSTANTS),
            "差异行数应当恰好等于允许分叉的常量数",
        )


class AuditRegressionTest(unittest.TestCase):
    """push 前 audit 抓到的两条 FAIL→PASS 路径的定向回归。

    两条都是「工具退 0、CSV 合法、gtest 证据合格，但耗时少算」——少算直接抬高
    ratio，是本协议唯一会把不通过写成通过的形状。
    """

    def _gauge(self, tmp):
        root = Path(tmp)
        package.render_runtime(op="x", family="y", perf_key=["n"],
                               out_dir=root / "r", csv_sha256="0" * 64)
        spec = importlib.util.spec_from_file_location(
            "g_audit", root / "r" / "verify_performance.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, root

    def test_header_only_file_mixed_in_is_rejected(self):
        """混入只有表头的 CSV → 拒绝，不是把剩下的行照常求和。

        改之前这里返回 (10.0, 1)：丢掉的那次 launch 的耗时被静默抹掉。"""
        with tempfile.TemporaryDirectory() as tmp:
            g, root = self._gauge(tmp)
            d = root / "out"
            (d / "OPPROF_a" / "k1" / "0").mkdir(parents=True)
            (d / "OPPROF_a" / "k1" / "0" / "OpBasicInfo_1.csv").write_text(
                "Op Name,Task Duration(us)\nk1,10.0\n", encoding="utf-8")
            (d / "OPPROF_a" / "k1" / "1").mkdir(parents=True)
            (d / "OPPROF_a" / "k1" / "1" / "OpBasicInfo_2.csv").write_text(
                "Op Name,Task Duration(us)\n", encoding="utf-8")
            with self.assertRaises(g.ProfileParseError) as cm:
                g.parse_op_summary(d, 512)
            self.assertIn("只有表头", str(cm.exception))

    def test_launch_dir_without_csv_is_rejected(self):
        """采集目录在而 CSV 不在 → 拒绝。只数读到的行数发现不了这种缺口。"""
        with tempfile.TemporaryDirectory() as tmp:
            g, root = self._gauge(tmp)
            d = root / "out"
            (d / "OPPROF_a" / "k1" / "0").mkdir(parents=True)
            (d / "OPPROF_a" / "k1" / "0" / "OpBasicInfo_1.csv").write_text(
                "Op Name,Task Duration(us)\nk1,10.0\n", encoding="utf-8")
            (d / "OPPROF_a" / "k1" / "1").mkdir(parents=True)   # 目录在，CSV 没落
            with self.assertRaises(g.ProfileParseError) as cm:
                g.parse_op_summary(d, 512)
            self.assertIn("没有 OpBasicInfo", str(cm.exception))

    def test_partial_failure_log_is_a_collection_failure(self):
        """工具打「N success, M failed」后仍退 0 → 按采集失败处理。

        msopprof 在部分 kernel 解析失败时只打 WARN 并照常返回
        （op_prof_data_parse.cpp:114）。不认这句，剩下的行会被当成完整读数。"""
        with tempfile.TemporaryDirectory() as tmp:
            g, _ = self._gauge(tmp)
            hit = g._collection_failure_marker(
                "[WARN]  Profiling kernels result is: 1 success, 1 failed. Please check")
            self.assertIsNotNone(hit)
            self.assertIn("1", hit)

    def test_all_success_summary_is_not_a_failure(self):
        """「N success, 0 failed」是正常完成的汇总行，不能误判成失败。

        这条守住上一条不要退化成 WARN 黑名单。"""
        with tempfile.TemporaryDirectory() as tmp:
            g, _ = self._gauge(tmp)
            self.assertIsNone(g._collection_failure_marker(
                "[INFO]  Profiling kernels result is: 6 success, 0 failed. Please check"))
            self.assertIsNone(g._collection_failure_marker(
                "[WARN]  The option \"--application\" will be deprecated"))

    def test_launch_failure_is_not_a_case_crash(self):
        """采集进程起不来 → LAUNCH_FAILED（调用方转环境中止），不是逐例 CRASH。"""
        with tempfile.TemporaryDirectory() as tmp:
            g, _ = self._gauge(tmp)
            code, output, problem = g._run_process(
                ["/nonexistent/msopprof-does-not-exist"], timeout=5)
            self.assertIsNone(code)
            self.assertEqual(problem, "LAUNCH_FAILED")
