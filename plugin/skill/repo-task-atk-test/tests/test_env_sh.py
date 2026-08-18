"""`evidence/env.sh`：每个 Bash 都是新 shell，环境要有载体。

真机实测（roll，2026-08-15）：手工拼环境前缀 29 次，其中两次漏掉 CANN 环境，
同一个错误同一轮踩两遍；另有一次用 find 现找 skill 目录，跑到了陈旧副本上。

这些用例锁的是「载体里该有什么、顺序对不对」，不是「probe_env 探得准不准」。
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import probe_env  # noqa: E402

PROBE = SKILL_ROOT / "scripts" / "probe_env.py"

SAMPLE = {
    "selected_python": "/home/u/conda/bin/python3",
    "atk_cli": "/home/u/conda/bin/atk",
    "cann": {"set_env": ["/usr/local/Ascend/ascend-toolkit/set_env.sh"],
             "sourced": True, "version": "8.0"},
    "devices": {"selected": 3, "selected_name": "Ascend910B4"},
}


class RenderEnvShTest(unittest.TestCase):
    def test_skill_root_points_at_this_skill(self):
        # 基址必须是「正在跑的这一份」，不是靠 find 找出来的某一份。
        self.assertTrue((Path(probe_env.SKILL_ROOT) / "SKILL.md").exists())

    def test_carries_every_prefix_the_commands_need(self):
        text = probe_env.render_env_sh(SAMPLE)
        self.assertIn("export ATK_SKILL_DIR=", text)
        self.assertIn("export ATK_PYTHON=", text)
        self.assertIn("export ATK_CLI=", text)
        self.assertIn("export ATK_DEVICE=3", text)
        self.assertIn("/usr/local/Ascend/ascend-toolkit/set_env.sh", text)

    def test_cann_is_sourced_before_vendor(self):
        # vendor 的 set_env.bash 依赖 CANN 已经加载，顺序反了就白 source。
        text = probe_env.render_env_sh(
            SAMPLE, vendor_env="/opt/pkg/vendors/x/bin/set_env.bash")
        cann = text.index("ascend-toolkit/set_env.sh")
        vendor = text.index("vendors/x/bin/set_env.bash")
        self.assertLess(cann, vendor)

    def test_vendor_and_custom_opp_are_absent_until_s3(self):
        # S1 还没装包，写进去就是假的；S3 装完重生成才有。
        text = probe_env.render_env_sh(SAMPLE)
        self.assertNotIn("set_env.bash", text)
        self.assertNotIn("ATK_CUSTOM_OPP_PATH", text)

    def test_custom_opp_is_absolute(self):
        text = probe_env.render_env_sh(SAMPLE, custom_opp="rel/libcust_opapi.so")
        line = [row for row in text.splitlines()
                if "ATK_CUSTOM_OPP_PATH" in row][0]
        self.assertIn(os.path.abspath("rel/libcust_opapi.so"), line)

    def test_custom_opp_is_linked_outside_the_install_tree(self):
        # ATK 的签名自检按 aclnn_name 在一个目录里 grep 头文件，选目录时
        # ASCEND_OPP_PATH 最后生效，库若在 opp 里目录就落到装机根，
        # 搜到官方同名算子的签名。软链到 opp 外面，那个分支才不成立。
        with tempfile.TemporaryDirectory() as temp_dir:
            install = Path(temp_dir) / "opp" / "vendors" / "v" / "op_api" / "lib"
            install.mkdir(parents=True)
            library = install / "libcust_opapi.so"
            library.write_bytes(b"candidate")
            evidence = Path(temp_dir) / "evidence"
            evidence.mkdir()

            link = Path(probe_env.link_custom_opp(str(library), str(evidence)))

            self.assertTrue(link.is_symlink())
            self.assertEqual(library.resolve(), link.resolve())
            self.assertFalse(str(link).startswith(str(Path(temp_dir) / "opp")))

    def test_custom_opp_link_survives_a_second_probe(self):
        # S3 装完包要重跑一次 probe_env，软链接已经在了不能炸。
        with tempfile.TemporaryDirectory() as temp_dir:
            library = Path(temp_dir) / "libcust_opapi.so"
            library.write_bytes(b"candidate")
            evidence = Path(temp_dir) / "evidence"
            evidence.mkdir()

            probe_env.link_custom_opp(str(library), str(evidence))
            link = Path(probe_env.link_custom_opp(str(library), str(evidence)))

            self.assertEqual(library.resolve(), link.resolve())

    def test_selected_python_also_wins_the_bare_python_command(self):
        # 真机实测（roll，2026-08-16）：前 12 条命令用的都是裸 `python`，
        # PATH 里排第一的是 conda base 那个没装 atk 的解释器，
        # 直到 make_yaml.py 炸掉才发现——报错还落在 torch_npu 的告警上。
        # 只导出 ATK_PYTHON 挡不住裸 python，必须同时前置它所在的目录。
        text = probe_env.render_env_sh(SAMPLE)
        line = [row for row in text.splitlines() if row.startswith("export PATH=")]
        self.assertTrue(line, "env.sh 没有前置 selected_python 所在目录")
        self.assertIn("/home/u/conda/bin", line[0])
        self.assertIn('"$PATH"', line[0])
        self.assertLess(text.index("export ATK_PYTHON="), text.index("export PATH="))

    def test_no_python_means_no_path_line(self):
        text = probe_env.render_env_sh({"cann": {}, "devices": {}})
        self.assertNotIn("export PATH=", text)

    def test_missing_facts_do_not_emit_empty_exports(self):
        # 探不到就不写这一行，写成 export ATK_CLI= 会让后面的命令拼出空串，
        # 报错落在 ATK 身上，方向全错。
        text = probe_env.render_env_sh({"cann": {}, "devices": {}})
        self.assertNotIn("export ATK_CLI=", text)
        self.assertNotIn("export ATK_DEVICE=", text)


class EnvShCliTest(unittest.TestCase):
    def test_vendor_env_without_env_sh_is_rejected(self):
        # --vendor-env 只有写进 env.sh 才有意义，单独给等于什么也没做。
        result = subprocess.run(
            [sys.executable, str(PROBE), "--vendor-env", "/tmp/x"],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(2, result.returncode)
        self.assertIn("--env-sh", result.stderr)

    def test_cann_root_without_set_env_is_rejected(self):
        result = subprocess.run(
            [sys.executable, str(PROBE), "--cann-root", "/definitely/not/here"],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(2, result.returncode)
        self.assertIn("set_env.sh", result.stderr)


class CannSelectionTest(unittest.TestCase):
    """装了多份 CANN 时，「用哪一份」必须是显式参数。

    真机实测（roll，2026-08-16）：默认倒序挑中的那份缺 libhccl.so 本体，
    agent 读了三段本脚本源码才推断出隐式办法「先 source 再跑 probe」，
    最后靠人工介入才定下用哪一份。
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "set_env.sh").write_text("true\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_preferred_root_comes_first(self):
        scripts = probe_env.cann_set_env_scripts(str(self.root))
        self.assertEqual(str(self.root / "set_env.sh"), scripts[0])

    def test_preferred_root_is_preloaded_for_the_probes(self):
        # env.sh 只 source 第一个；探测若不在同一份 CANN 下做，
        # 就会出现「探针全绿、跑测全挂」。
        info = probe_env.cann_info(str(self.root))
        self.assertEqual(str(self.root / "set_env.sh"), info["preloaded"])

    def test_under_cann_sources_before_running(self):
        marker = self.root / "loaded"
        (self.root / "set_env.sh").write_text(
            f"touch {marker}\n", encoding="utf-8")
        cmd = probe_env.under_cann(["true"], str(self.root / "set_env.sh"))
        subprocess.run(cmd, capture_output=True, timeout=60)
        self.assertTrue(marker.exists(), "set_env.sh 没有在命令之前被加载")

    def test_no_set_env_means_no_wrapper(self):
        self.assertEqual(["true"], probe_env.under_cann(["true"], None))


class ProvenanceTest(unittest.TestCase):
    """待验收算子工程的来源版本：复现包要说得清「这次测的是哪一版代码」。"""

    def test_none_path_returns_none(self):
        self.assertIsNone(probe_env.project_provenance(None))

    def test_missing_directory_is_recorded_not_raised(self):
        info = probe_env.project_provenance("/definitely/not/here")
        self.assertIn("error", info)

    def test_this_repository_resolves_to_a_commit(self):
        info = probe_env.project_provenance(str(SKILL_ROOT))
        if info.get("commit") is None:
            self.skipTest("本机没有 git，或不在工作区里")
        self.assertEqual(40, len(info["commit"]))
        self.assertIn("dirty", info)


if __name__ == "__main__":
    unittest.main()
