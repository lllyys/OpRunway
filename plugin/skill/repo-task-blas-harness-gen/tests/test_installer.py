#!/usr/bin/env python3
"""installer 测试：加固后的 preflight 七门、install/rollback 事务与路径安全。
自带最小合成 repo，compat revision 绑定临时仓真实 HEAD（无 monkeypatch）。"""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))
import installer  # noqa: E402
import hashlib  # noqa: E402

UPSTREAM = SKILL.parents[2] / "repos" / "ops-blas"
PROTO = ("aclblasStatus_t aclblasSasum(aclblasHandle_t handle, int n, "
         "const float* x, int incx, float* result);\n")


def _ir():
    ir = json.loads((SKILL / "tests" / "ir-instances" / "sasum.json").read_text("utf-8"))
    ir["_arch_dir"] = "arch22"
    return ir


@unittest.skipUnless(UPSTREAM.is_dir(), "repos/ops-blas 缺席（只读参考，不随分发）")
class TestInstaller(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        base = installer.load_compat()
        self.repo.mkdir()
        subprocess.run(["git", "-C", str(self.repo), "init", "-q"], check=True)
        for rel in base["frame_fingerprints"]:
            dst = self.repo / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes((UPSTREAM / rel).read_bytes())
        eh = self.repo / base["entry_header"]
        eh.parent.mkdir(parents=True, exist_ok=True)
        eh.write_text(PROTO)
        (self.repo / "test" / "asum" / "sasum").mkdir(parents=True)
        (self.repo / "test" / "asum" / "sasum" / "placeholder").write_text("x")
        (self.repo / "blas" / "asum" / "sasum" / "arch22").mkdir(parents=True)
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.email=t@t",
                        "-c", "user.name=t", "commit", "-qm", "x"], check=True)
        head = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        # compat 绑定真实 HEAD + 重算本仓 frame 指纹
        self.compat = copy.deepcopy(base)
        self.compat["revision"] = head
        self.compat["frame_fingerprints"] = {
            rel: hashlib.sha256((self.repo / rel).read_bytes()).hexdigest()
            for rel in base["frame_fingerprints"]}
        # staging
        self.staging = Path(self._tmp.name) / "staging"
        d = self.staging / "test" / "asum" / "sasum" / "arch22"
        d.mkdir(parents=True)
        (self.staging / "test/asum/sasum/CMakeLists.txt").write_text("cmake\n")
        (d / "sasum_test.cpp").write_text("cpp\n")

    def pf(self, ir=None, soc="ascend910_93"):
        return installer.preflight(ir or _ir(), self.repo, soc, compat=self.compat)

    def test_preflight_ok(self):
        arch, target = self.pf()
        self.assertEqual((arch, target), ("arch22", "test/asum/sasum"))

    def test_bad_revision(self):
        self.compat["revision"] = "deadbeef"
        with self.assertRaises(installer.InstallReject) as c:
            self.pf()
        self.assertEqual(c.exception.stop_code, "TARGET_INCOMPATIBLE")

    def test_frame_tamper(self):
        f = self.repo / next(iter(self.compat["frame_fingerprints"]))
        f.write_bytes(f.read_bytes() + b"//x\n")
        with self.assertRaises(installer.InstallReject):
            self.pf()

    def test_prototype_mismatch(self):
        (self.repo / self.compat["entry_header"]).write_text(
            "aclblasStatus_t aclblasSasum(aclblasHandle_t h, int n);\n")
        with self.assertRaises(installer.InstallReject) as c:
            self.pf()
        self.assertIn("原型不符", c.exception.reason)

    def test_substring_symbol_not_accepted(self):
        (self.repo / self.compat["entry_header"]).write_text(
            "aclblasStatus_t aclblasSasumEx(aclblasHandle_t h);\n")
        with self.assertRaises(installer.InstallReject):
            self.pf()

    def test_exact_soc_key_only(self):
        with self.assertRaises(installer.InstallReject):
            self.pf(soc="ascend910_93evil")

    def test_missing_impl_dir(self):
        import shutil
        shutil.rmtree(self.repo / "blas" / "asum" / "sasum" / "arch22")
        with self.assertRaises(installer.InstallReject) as c:
            self.pf()
        self.assertIn("blas 实现", c.exception.reason)

    def test_wrong_family_ambiguous(self):
        ir = _ir()
        ir["family"] = "wrongfam"
        with self.assertRaises(installer.InstallReject) as c:
            self.pf(ir=ir)
        self.assertEqual(c.exception.stop_code, "TARGET_AMBIGUOUS")

    def test_path_escape_rejected(self):
        ir = _ir()
        ir["family"] = "wrongfam"  # 合法字符但非 canonical → 上一测；越界另测 renderer 侧
        # 直接测 _contained
        with self.assertRaises(installer.InstallReject):
            installer._contained(self.repo, "../escape")

    def test_install_rollback_roundtrip(self):
        _a, target = self.pf()
        before = (self.repo / "test/asum/sasum/placeholder").read_text()
        installer.install(self.staging, self.repo, target, "arch22")
        self.assertTrue((self.repo / "test/asum/sasum/CMakeLists.txt").is_file())
        self.assertTrue((self.repo / "test/asum/sasum.upstream-backup").is_dir())
        installer.rollback(self.repo)
        self.assertEqual((self.repo / "test/asum/sasum/placeholder").read_text(), before)
        self.assertFalse((self.repo / ".overlay-manifest.json").exists())

    def test_empty_staging_rejected(self):
        _a, target = self.pf()
        import shutil
        shutil.rmtree(self.staging / "test")
        (self.staging / "test").mkdir()
        with self.assertRaises(installer.InstallReject) as c:
            installer.install(self.staging, self.repo, target, "arch22")
        self.assertEqual(c.exception.stop_code, "TARGET_EXISTS")

    def test_second_install_rejected_by_manifest(self):
        _a, target = self.pf()
        installer.install(self.staging, self.repo, target, "arch22")
        with self.assertRaises(installer.InstallReject) as c:
            installer.install(self.staging, self.repo, target, "arch22")
        self.assertIn("manifest", c.exception.reason)

    def test_rollback_extra_file_rejected(self):
        _a, target = self.pf()
        installer.install(self.staging, self.repo, target, "arch22")
        (self.repo / "test/asum/sasum/arch22/extra.txt").write_text("y")
        with self.assertRaises(installer.InstallReject) as c:
            installer.rollback(self.repo)
        self.assertEqual(c.exception.stop_code, "ROLLBACK_REJECTED")

    def test_rollback_tampered_rejected(self):
        _a, target = self.pf()
        installer.install(self.staging, self.repo, target, "arch22")
        (self.repo / "test/asum/sasum/CMakeLists.txt").write_text("tampered\n")
        with self.assertRaises(installer.InstallReject) as c:
            installer.rollback(self.repo)
        self.assertEqual(c.exception.stop_code, "ROLLBACK_REJECTED")

    def test_target_exists_different_rejected(self):
        _a, target = self.pf()
        (self.repo / "test/asum/sasum/CMakeLists.txt").write_text("different\n")
        with self.assertRaises(installer.InstallReject) as c:
            installer.install(self.staging, self.repo, target, "arch22")
        self.assertEqual(c.exception.stop_code, "TARGET_EXISTS")

    def test_impl_path_is_file_rejected(self):
        import shutil
        shutil.rmtree(self.repo / "blas" / "asum" / "sasum" / "arch22")
        (self.repo / "blas" / "asum" / "sasum" / "arch22").write_text("not a dir")
        with self.assertRaises(installer.InstallReject) as c:
            self.pf()
        self.assertIn("blas 实现", c.exception.reason)

    def test_rollback_backup_tampered_rejected(self):
        _a, target = self.pf()
        installer.install(self.staging, self.repo, target, "arch22")
        # 篡改 backup 内文件
        bak = self.repo / "test/asum/sasum.upstream-backup/placeholder"
        bak.write_text("tampered-backup")
        with self.assertRaises(installer.InstallReject) as c:
            installer.rollback(self.repo)
        self.assertEqual(c.exception.stop_code, "ROLLBACK_REJECTED")

    def test_manifest_schema_extra_missing_key_rejected(self):
        _a, target = self.pf()
        installer.install(self.staging, self.repo, target, "arch22")
        mp = self.repo / ".overlay-manifest.json"
        m = json.loads(mp.read_text())
        del m["target_rel"]  # 缺必需键
        m["junk"] = 1        # 加额外键
        mp.write_text(json.dumps(m))
        with self.assertRaises(installer.InstallReject) as c:
            installer.rollback(self.repo)
        self.assertEqual(c.exception.stop_code, "ROLLBACK_REJECTED")

    def test_contained_rejects_symlink_component(self):
        (self.repo / "test" / "evil").symlink_to("/tmp")
        with self.assertRaises(installer.InstallReject):
            installer._contained(self.repo, "test/evil/x")

    def test_rollback_with_symlink_in_backup(self):
        # 备份树含符号链接时仍可回滚（M-07 死代码修复）
        (self.repo / "test/asum/sasum/link").symlink_to("placeholder")
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.email=t@t",
                        "-c", "user.name=t", "commit", "-qm", "link"], check=True)
        self.compat["revision"] = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            capture_output=True, text=True).stdout.strip()
        _a, target = self.pf()
        installer.install(self.staging, self.repo, target, "arch22")
        m = installer.rollback(self.repo)
        import os
        link = self.repo / "test/asum/sasum/link"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), "placeholder")  # 目标精确复原
        self.assertFalse((self.repo / ".overlay-manifest.json").exists())

    def test_rollback_rejects_repointed_backup_symlink(self):
        import os
        (self.repo / "test/asum/sasum/link").symlink_to("placeholder")
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.email=t@t",
                        "-c", "user.name=t", "commit", "-qm", "link"], check=True)
        self.compat["revision"] = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            capture_output=True, text=True).stdout.strip()
        _a, target = self.pf()
        installer.install(self.staging, self.repo, target, "arch22")
        # 篡改 backup 里的符号链接指向
        bl = self.repo / "test/asum/sasum.upstream-backup/link"
        bl.unlink(); bl.symlink_to("/etc/passwd")
        with self.assertRaises(installer.InstallReject) as c:
            installer.rollback(self.repo)
        self.assertEqual(c.exception.stop_code, "ROLLBACK_REJECTED")

    def test_install_write_failure_reports_needs_manual(self):
        # 注入写失败：install 恢复分支不谎称已回退（M-06）
        _a, target = self.pf()
        orig = installer.Path.write_bytes if hasattr(installer.Path, "write_bytes") else None
        import pathlib
        real_wb = pathlib.Path.write_bytes
        calls = {"n": 0}
        def boom(self, data):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("disk full injected")
            return real_wb(self, data)
        pathlib.Path.write_bytes = boom
        try:
            with self.assertRaises(installer.InstallReject) as c:
                installer.install(self.staging, self.repo, target, "arch22")
            self.assertIn(c.exception.stop_code, ("TARGET_EXISTS", "ROLLBACK_REJECTED"))
        finally:
            pathlib.Path.write_bytes = real_wb
        # 备份应已恢复，工程回到安装前
        self.assertEqual((self.repo / "test/asum/sasum/placeholder").read_text(), "x")
        self.assertFalse((self.repo / "test/asum/sasum.upstream-backup").exists())
        self.assertFalse((self.repo / ".overlay-manifest.json").exists())
