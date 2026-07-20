"""runner 落点/查找单测（stdlib unittest）——不依赖真机/NPU。

跑: python3 test_runner_lookup.py        （在 acc-common/ 下，全绿即过）
或: python3 -m unittest test_runner_lookup -v

守的契约（工程约定「零持久化配置；所有产物落用户 CWD」）：
- 运行时产物（runner.cpp）落**用户工作目录** `<ops_root>/<op>/`，**不写插件安装目录**；
- 查找顺序 = **只查用户目录**（引擎不回退插件样例，fallback 已退役 2026-07-20）；`source` 恒 `"user"`，
  缺 runner 直接 fail-closed 报错（runner 是引擎的**输出**、非组件，样例只在 `samples/runners/` 作只读参考）；
- `OPRUNWAY_WORK_DIR` 覆盖用户根（默认 = 进程 CWD）；`OPRUNWAY_OPS_DIR` 覆盖 per-op 输入根
  （默认 `<user_root>/.oprunway/ops`）；输入根与跑测输出 `reports/` 分开；
- 两处都没有 → 报错，且错误信息同时给出两条查找路径（fail-closed，不静默兜底）；
- 非法 op_name（`..` / `/` / shell 特殊字符）被拒，runner 路径不得逃出用户目录。
"""
import os, sys, tempfile, unittest
from unittest import mock

import repo_adapter as R

_HERE = os.path.dirname(os.path.abspath(__file__))


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("// stub runner\n")
    return path


class UserRootTest(unittest.TestCase):
    def test_defaults_to_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            real_d = os.path.realpath(d)
            cwd = os.getcwd()
            try:
                os.chdir(real_d)
                with mock.patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("OPRUNWAY_WORK_DIR", None)
                    self.assertEqual(R.user_root(), real_d)
            finally:
                os.chdir(cwd)

    def test_env_overrides_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"OPRUNWAY_WORK_DIR": d}):
                self.assertEqual(R.user_root(), os.path.realpath(d))

    def test_ops_root_defaults_under_user_root(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"OPRUNWAY_WORK_DIR": d}):
                os.environ.pop("OPRUNWAY_OPS_DIR", None)
                self.assertEqual(R.ops_root(),
                                 os.path.join(os.path.realpath(d), ".oprunway", "ops"))

    def test_ops_dir_env_overrides_ops_root(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as e:
            with mock.patch.dict(os.environ, {"OPRUNWAY_WORK_DIR": d, "OPRUNWAY_OPS_DIR": e}):
                self.assertEqual(R.ops_root(), os.path.realpath(e))

    def test_op_dir_is_per_operator(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"OPRUNWAY_WORK_DIR": d}):
                os.environ.pop("OPRUNWAY_OPS_DIR", None)
                self.assertEqual(R.op_dir("IsClose"),
                                 os.path.join(os.path.realpath(d), ".oprunway", "ops", "IsClose"))

    def test_never_points_into_plugin_dir(self):
        """核心契约：用户 per-op 输入目录绝不落在插件安装目录内。"""
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"OPRUNWAY_WORK_DIR": d}):
                os.environ.pop("OPRUNWAY_OPS_DIR", None)
                self.assertFalse(R.op_dir("IsClose").startswith(_HERE + os.sep))
                self.assertFalse(R.ops_root().startswith(_HERE + os.sep))

    def test_ops_root_is_not_reports_dir(self):
        """输入（spec/runner）与跑测输出 reports/ 分开——reports/ 在 .gitignore、语义不同。"""
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"OPRUNWAY_WORK_DIR": d}):
                os.environ.pop("OPRUNWAY_OPS_DIR", None)
                self.assertNotIn(os.sep + "reports", R.ops_root())


class FindRunnerTest(unittest.TestCase):
    def test_user_dir_runner_found(self):
        """用户目录放了 runner → 命中、source=user、远端名由 op_name 定死。"""
        with tempfile.TemporaryDirectory() as d:
            want = _touch(os.path.join(d, ".oprunway", "ops", "IsClose", "oprunway_isclose_runner.cpp"))
            with mock.patch.dict(os.environ, {"OPRUNWAY_WORK_DIR": d}):
                os.environ.pop("OPRUNWAY_OPS_DIR", None)
                path, source, remote = R.find_runner("IsClose")
            self.assertEqual(source, "user")
            self.assertEqual(os.path.realpath(path), os.path.realpath(want))
            self.assertEqual(remote, "oprunway_isclose_runner.cpp")   # 远端名由 op_name 定死

    def test_missing_user_runner_no_fallback_raises(self):
        """用户目录没有 runner → **不回退插件样例**、直接 fail-closed 报错（fallback 已退役 2026-07-20）。
        即便是插件曾自带样例的 IsClose，也不再回退——引擎不含算子 runner。"""
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"OPRUNWAY_WORK_DIR": d}):
                os.environ.pop("OPRUNWAY_OPS_DIR", None)
                with self.assertRaises(ValueError) as cm:
                    R.find_runner("IsClose")
            self.assertIn("不回退", str(cm.exception))

    def test_missing_runner_raises_with_user_path_and_guidance(self):
        """缺 runner → fail-closed 报错，给出用户路径 + acc-runner 补法 + samples/runners 只读样例 + env 覆盖口。"""
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"OPRUNWAY_WORK_DIR": d}):
                os.environ.pop("OPRUNWAY_OPS_DIR", None)
                with self.assertRaises(ValueError) as cm:
                    R.find_runner("NoSuchOpXyz")
            msg = str(cm.exception)
            self.assertIn(".oprunway", msg)          # 指出用户目录
            self.assertIn("samples/runners", msg)    # 只读样例位置（非回退靶）
            self.assertIn("acc-runner", msg)         # 指出怎么补
            self.assertIn("OPRUNWAY_OPS_DIR", msg)   # env 覆盖口

    def test_case_insensitive_filename(self):
        with tempfile.TemporaryDirectory() as d:
            _touch(os.path.join(d, ".oprunway", "ops", "MyOp", "oprunway_myop_runner.cpp"))
            with mock.patch.dict(os.environ, {"OPRUNWAY_WORK_DIR": d}):
                os.environ.pop("OPRUNWAY_OPS_DIR", None)
                path, source, remote = R.find_runner("MyOp")     # 大写驼峰 → 文件名小写
            self.assertEqual(source, "user")
            self.assertTrue(path.endswith("oprunway_myop_runner.cpp"))
            self.assertEqual(remote, "oprunway_myop_runner.cpp")


class FindRunnerSecurityTest(unittest.TestCase):
    def test_rejects_traversal_op_name(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"OPRUNWAY_WORK_DIR": d}):
                os.environ.pop("OPRUNWAY_OPS_DIR", None)
                for bad in ("..", "../x", "a/b", "-rf", "a b", "a;rm", "."):
                    with self.assertRaises(ValueError, msg=f"未拒非法 op_name {bad!r}"):
                        R.find_runner(bad)

    def test_rejects_non_string_op_name(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"OPRUNWAY_WORK_DIR": d}):
                os.environ.pop("OPRUNWAY_OPS_DIR", None)
                for bad in (None, 3, [], {}):
                    with self.assertRaises(ValueError):
                        R.find_runner(bad)

    def test_directory_named_like_runner_is_not_accepted(self):
        """同名**目录**不算 runner（isfile 而非 exists）。"""
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".oprunway", "ops", "DirOp", "oprunway_dirop_runner.cpp"))
            with mock.patch.dict(os.environ, {"OPRUNWAY_WORK_DIR": d}):
                os.environ.pop("OPRUNWAY_OPS_DIR", None)
                with self.assertRaises(ValueError):
                    R.find_runner("DirOp")     # 用户侧是目录、插件侧无此样例 → 应报缺


class FindRunnerHardeningTest(unittest.TestCase):
    def test_user_runner_symlink_is_rejected(self):
        """用户 runner 是符号链接 → 拒绝（防 realpath 逃逸 + 远端名注入）。"""
        with tempfile.TemporaryDirectory() as d:
            opd = os.path.join(d, ".oprunway", "ops", "IsClose")
            os.makedirs(opd)
            target = _touch(os.path.join(d, "elsewhere.cpp"))
            link = os.path.join(opd, "oprunway_isclose_runner.cpp")
            os.symlink(target, link)
            with mock.patch.dict(os.environ, {"OPRUNWAY_WORK_DIR": d}):
                os.environ.pop("OPRUNWAY_OPS_DIR", None)
                with self.assertRaises(ValueError) as cm:
                    R.find_runner("IsClose")
            self.assertIn("符号链接", str(cm.exception))

    def test_remote_name_fixed_even_if_link_name_differs(self):
        """即便攻击者把软链命名成怪名字，也在 islink 处就被拒——远端名永不来自路径 basename。"""
        with tempfile.TemporaryDirectory() as d:
            opd = os.path.join(d, ".oprunway", "ops", "IsClose")
            os.makedirs(opd)
            evil = _touch(os.path.join(d, "bad;touch PWN.cpp"))
            os.symlink(evil, os.path.join(opd, "oprunway_isclose_runner.cpp"))
            with mock.patch.dict(os.environ, {"OPRUNWAY_WORK_DIR": d}):
                os.environ.pop("OPRUNWAY_OPS_DIR", None)
                with self.assertRaises(ValueError):
                    R.find_runner("IsClose")

    def test_ops_dir_override_into_plugin_tree_is_rejected(self):
        """OPRUNWAY_OPS_DIR 指向插件安装目录内 → 拒绝（否则绕过『不写插件目录』+ 样例被误标 user）。
        须覆盖整个 plugin/ 树，不只 acc-common/——含 skills/ agents/ 等兄弟目录。"""
        _PLUGIN = os.path.realpath(os.path.join(_HERE, os.pardir))   # plugin/
        for target in (_HERE,                                        # plugin/acc-common
                       _PLUGIN,                                      # plugin/（根）
                       os.path.join(_PLUGIN, "skills"),              # plugin/skills —— 之前的漏网点
                       os.path.join(_PLUGIN, "agents", "sub")):      # 深层子目录
            with mock.patch.dict(os.environ, {"OPRUNWAY_OPS_DIR": target}):
                with self.assertRaises(ValueError, msg=f"未拒 OPS_DIR={target}") as cm:
                    R.ops_root()
            self.assertIn("插件", str(cm.exception))

    def test_ops_dir_override_must_be_absolute(self):
        with mock.patch.dict(os.environ, {"OPRUNWAY_OPS_DIR": "rel/ative"}):
            with self.assertRaises(ValueError):
                R.ops_root()

    def test_empty_env_treated_as_unset(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"OPRUNWAY_WORK_DIR": d, "OPRUNWAY_OPS_DIR": ""}):
                self.assertEqual(R.ops_root(), os.path.join(os.path.realpath(d), ".oprunway", "ops"))

    def test_unreadable_user_runner_does_not_silently_fallback(self):
        """用户 runner 存在但不可读（EACCES）→ 抛错，不静默改跑插件样例。"""
        if os.geteuid() == 0:
            self.skipTest("root 无视权限位")
        with tempfile.TemporaryDirectory() as d:
            opd = os.path.join(d, ".oprunway", "ops", "IsClose")
            os.makedirs(opd)
            f = _touch(os.path.join(opd, "oprunway_isclose_runner.cpp"))
            os.chmod(opd, 0o000)                     # 父目录不可搜索 → lstat 抛 EACCES(非 ENOENT)
            try:
                with mock.patch.dict(os.environ, {"OPRUNWAY_WORK_DIR": d}):
                    os.environ.pop("OPRUNWAY_OPS_DIR", None)
                    with self.assertRaises(ValueError) as cm:
                        R.find_runner("IsClose")
                self.assertIn("不可访问", str(cm.exception))
            finally:
                os.chmod(opd, 0o755)


class RunnerSourceGateTest(unittest.TestCase):
    """fail-closed 门：new_example 模式 runner_source 必须为 'user'（fallback 已退役、runner 是引擎输出非组件）。"""
    def test_exit_code_mapping(self):
        import run_workflow as W
        self.assertEqual(W._exit_code("PASS"), 0)
        self.assertEqual(W._exit_code("PASSED_WITH_RISK"), 2)      # 挂起转人工
        self.assertEqual(W._exit_code("FAIL(精度)"), 1)
        self.assertEqual(W._exit_code("NEEDS_REVIEW"), 1)          # 非干净 PASS、非挂起

    def test_non_user_runner_source_blocked(self):
        """new_example 下 runner_source 非 user（缺失/未知/伪造 builtin_sample）→ BLOCKED、退出码 1、非干净 PASS。"""
        import run_workflow as W
        for bad in ("BLOCKED(runner_source 非 user/缺失: None)",
                    "BLOCKED(runner_source 非 user/缺失: 'builtin_sample')"):
            self.assertEqual(W._exit_code(bad), 1)
            self.assertEqual(W._canonical_state(bad, {"status": "ok"}), "BLOCKED_EVIDENCE_INCOMPLETE")

    def test_run_new_example_return_carries_runner_source(self):
        """契约：run_new_example 的返回字典带 runner_source（provenance 进 evidence，恒 user）。"""
        import inspect, repo_adapter as R
        src = inspect.getsource(R.run_new_example)
        self.assertIn('"runner_source": runner_source', src)
        self.assertIn('"runner_path": runner', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
