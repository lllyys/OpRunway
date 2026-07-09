#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""init.sh 对抗门负例测试（沙盒实跑）。

覆盖 12 条 CONFIRMED 漏洞的修复断言，全部在 /tmp 隔离沙盒 + 隔离 HOME 下**实跑**真脚本
（`plugin/init.sh`），绝不碰真仓/真 HOME。每个用例自建：
  - 假 PLUGIN_ROOT（合成 AGENTS.md + skills/ + agents/，含空格/glob/换行的刁钻名）；
  - 假 BASE（OPRUNWAY_TARGET_DIR 指向沙盒目录）；
  - 假 HOME（HOME 指向沙盒目录，验「不碰 ~/.config、不改 shell rc」）。

只读、stdlib（unittest/subprocess/tempfile/shutil），无第三方依赖。
运行: python3 test_init_sh.py   或   python3 -m unittest test_init_sh -v
"""
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
INIT_SH = os.path.abspath(os.path.join(HERE, "..", "init.sh"))

# 与 init.sh 中的常量逐字一致（用于预植伪标记）
MANAGED_BEGIN = "# >>> OpRunway plugin (managed by init.sh — 勿手改块内) >>>"
MANAGED_END = "# <<< OpRunway plugin (managed by init.sh) <<<"


# ── 沙盒脚手架 ────────────────────────────────────────────────────────────────
def _write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def build_plugin(root, skill_names, agent_names):
    """在 root 下造一个合成插件源：AGENTS.md + skills/<name>/ + agents/<name>.md。"""
    os.makedirs(root, exist_ok=True)
    _write(os.path.join(root, "AGENTS.md"), "# 合成 AGENTS.md（测试用）\n")
    sk = os.path.join(root, "skills")
    ag = os.path.join(root, "agents")
    os.makedirs(sk, exist_ok=True)
    os.makedirs(ag, exist_ok=True)
    for name in skill_names:
        d = os.path.join(sk, name)
        os.makedirs(d, exist_ok=True)
        _write(os.path.join(d, "SKILL.md"), "# %r\n" % name)
    for name in agent_names:
        _write(os.path.join(ag, name), "# agent %r\n" % name)
    return root


def snapshot(top):
    """递归快照：{relpath: 描述}。symlink 记 target；文件记内容 hash；目录记 'dir/'。
    捕获整棵树用于「零写入 / 前后一致」断言。"""
    snap = {}
    if not os.path.exists(top) and not os.path.islink(top):
        return snap
    for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
        for d in list(dirnames):
            full = os.path.join(dirpath, d)
            rel = os.path.relpath(full, top)
            if os.path.islink(full):
                snap[rel + " (symlink)"] = "-> " + os.readlink(full)
                dirnames.remove(d)  # 不下钻 symlink 目录
            else:
                snap[rel + "/"] = "dir"
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, top)
            if os.path.islink(full):
                snap[rel + " (symlink)"] = "-> " + os.readlink(full)
            else:
                snap[rel] = hashlib.sha256(_read_bytes(full)).hexdigest()[:16]
    return snap


class InitShTestBase(unittest.TestCase):
    def setUp(self):
        self.sbx = tempfile.mkdtemp(prefix="oprunway-initsh-")
        self.plugin = os.path.join(self.sbx, "plugin_src")
        self.base = os.path.join(self.sbx, "target")
        self.home = os.path.join(self.sbx, "home")
        os.makedirs(self.base, exist_ok=True)
        os.makedirs(self.home, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.sbx, ignore_errors=True)

    def run_init(self, *args, extra_env=None, path_prepend=None):
        env = dict(os.environ)
        env["OPRUNWAY_PLUGIN_ROOT"] = self.plugin
        env["OPRUNWAY_TARGET_DIR"] = self.base
        env["HOME"] = self.home
        env.pop("CLAUDE_PLUGIN_ROOT", None)
        if path_prepend:
            env["PATH"] = path_prepend + os.pathsep + env.get("PATH", "")
        if extra_env:
            env.update(extra_env)
        # 用 bytes 捕获（不 text=True）：产物路径可能含换行/非 ASCII 字节，text 解码会崩。
        return subprocess.run(
            ["bash", INIT_SH, *args],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
        )

    def out(self, cp):
        so = (cp.stdout or b"").decode("utf-8", "replace")
        se = (cp.stderr or b"").decode("utf-8", "replace")
        return so + se


# ── 冒烟：正常安装/卸载端到端 ─────────────────────────────────────────────────
class TestHappyPath(InitShTestBase):
    def test_install_then_uninstall_roundtrip(self):
        build_plugin(self.plugin, ["acc-one", "acc-two"], ["a1.md", "a2.md"])
        cp = self.run_init("--tool", "claude", "--level", "project", "--yes")
        self.assertEqual(cp.returncode, 0, self.out(cp))
        sk = os.path.join(self.base, ".claude", "skills")
        ag = os.path.join(self.base, ".claude", "agents")
        self.assertTrue(os.path.islink(os.path.join(sk, "acc-one")))
        self.assertTrue(os.path.islink(os.path.join(ag, "a1.md")))
        # symlink 指向插件源
        tgt = os.path.realpath(os.path.join(sk, "acc-one"))
        self.assertTrue(tgt.startswith(os.path.realpath(self.plugin)), tgt)
        # 托管块恰 1 份
        reg = os.path.join(self.base, "CLAUDE.md")
        body = _read_text(reg)
        self.assertEqual(body.count(MANAGED_BEGIN), 1, body)
        self.assertEqual(body.count(MANAGED_END), 1, body)
        # 卸载
        cp2 = self.run_init("--tool", "claude", "--level", "project", "--uninstall", "--yes")
        self.assertEqual(cp2.returncode, 0, self.out(cp2))
        self.assertFalse(os.path.lexists(os.path.join(sk, "acc-one")))
        self.assertFalse(os.path.lexists(os.path.join(ag, "a1.md")))
        body2 = _read_text(reg)
        self.assertEqual(body2.count(MANAGED_BEGIN), 0, body2)

    def test_idempotent_repeat_install(self):
        """幂等：重复 install 后托管块计数仍为 1，且第二遍打印幂等跳过。"""
        build_plugin(self.plugin, ["acc-one"], ["a1.md"])
        cp1 = self.run_init("--tool", "claude", "--level", "project", "--yes")
        self.assertEqual(cp1.returncode, 0, self.out(cp1))
        cp2 = self.run_init("--tool", "claude", "--level", "project", "--yes")
        self.assertEqual(cp2.returncode, 0, self.out(cp2))
        reg = os.path.join(self.base, "CLAUDE.md")
        body = _read_text(reg)
        self.assertEqual(body.count(MANAGED_BEGIN), 1, body)
        self.assertIn("幂等跳过", self.out(cp2))


# ── HIGH-1：非 claude 卸载强制干跑（零文件系统写）────────────────────────────
class TestHigh1NonClaudeUninstallDryRun(InitShTestBase):
    def test_opencode_uninstall_is_zero_write(self):
        build_plugin(self.plugin, ["acc-one"], ["a1.md"])
        before_base = snapshot(self.base)
        before_home = snapshot(self.home)
        cp = self.run_init("--tool", "opencode", "--uninstall", "--yes")
        self.assertEqual(cp.returncode, 0, self.out(cp))
        # 关键：仍是 DRY-RUN，绝不是「真实卸载」
        self.assertIn("DRY-RUN", self.out(cp))
        self.assertNotIn("真实卸载", self.out(cp))
        self.assertEqual(snapshot(self.base), before_base, "opencode 卸载竟改动了 BASE")
        self.assertEqual(snapshot(self.home), before_home, "opencode 卸载竟改动了 HOME")

    def test_opencode_install_is_zero_write(self):
        build_plugin(self.plugin, ["acc-one"], ["a1.md"])
        before = snapshot(self.base)
        cp = self.run_init("--tool", "opencode", "--yes")
        self.assertEqual(cp.returncode, 0, self.out(cp))
        self.assertIn("DRY-RUN", self.out(cp))
        self.assertEqual(snapshot(self.base), before)


# ── HIGH-2：祖先 symlink 拒绝，外部目录零写入 ────────────────────────────────
class TestHigh2AncestorSymlink(InitShTestBase):
    def test_claude_dir_symlink_to_external_is_rejected(self):
        build_plugin(self.plugin, ["acc-one", "acc-two"], ["a1.md"])
        external = os.path.join(self.sbx, "EXTERNAL")
        os.makedirs(external, exist_ok=True)
        # 预植 target/.claude -> EXTERNAL（祖先 symlink 重定向）
        os.symlink(external, os.path.join(self.base, ".claude"))
        ext_before = snapshot(external)
        reg = os.path.join(self.base, "CLAUDE.md")

        cp = self.run_init("--tool", "claude", "--level", "project", "--yes")
        self.assertNotEqual(cp.returncode, 0, "祖先 symlink 竟未被拒绝")
        self.assertIn("安全阻断", self.out(cp))
        # 外部目录零写入
        self.assertEqual(snapshot(external), ext_before, "外部目录被写入了（越界重定向未挡住）")
        self.assertEqual(os.listdir(external), [], "外部目录非空")
        # 注册块也没抢先写（preflight 先于任何写）
        self.assertFalse(os.path.exists(reg), "CLAUDE.md 竟被抢先写入（半装）")


# ── HIGH-3：托管块标记异常一律 abort 不改文件 ────────────────────────────────
class TestHigh3BlockIntegrity(InitShTestBase):
    def test_lone_begin_install_aborts_not_idempotent(self):
        """仅伪 BEGIN（无 END）→ install 必须 abort，且绝不报「幂等跳过」，文件一字不少。"""
        build_plugin(self.plugin, ["acc-one"], ["a1.md"])
        reg = os.path.join(self.base, "CLAUDE.md")
        content = "USER LINE 1\n" + MANAGED_BEGIN + "\nUSER LINE 2\nUSER LINE 3\n"
        _write(reg, content)
        before = _read_bytes(reg)

        cp = self.run_init("--tool", "claude", "--level", "project", "--yes")
        self.assertNotEqual(cp.returncode, 0, "伪 BEGIN 竟未 abort")
        self.assertIn("标记异常", self.out(cp))
        self.assertNotIn("幂等跳过", self.out(cp))
        self.assertEqual(_read_bytes(reg), before, "文件被改动了（应一字不少）")

    def test_nested_markers_uninstall_aborts_keeps_user_lines(self):
        """嵌套 BEGIN..BEGIN..END → uninstall 必须 abort，中间真用户行一字不少。"""
        build_plugin(self.plugin, ["acc-one"], ["a1.md"])
        reg = os.path.join(self.base, "CLAUDE.md")
        content = (
            "USER TOP\n"
            + MANAGED_BEGIN + "\n"
            + "REAL USER MIDDLE\n"
            + MANAGED_BEGIN + "\n"
            + "block body\n"
            + MANAGED_END + "\n"
            + "USER BOTTOM\n"
        )
        _write(reg, content)
        before = _read_bytes(reg)

        cp = self.run_init("--tool", "claude", "--level", "project", "--uninstall", "--yes")
        self.assertNotEqual(cp.returncode, 0, "嵌套标记竟未 abort")
        self.assertIn("标记异常", self.out(cp))
        self.assertEqual(_read_bytes(reg), before, "嵌套下用户行被吃掉了")
        self.assertIn("REAL USER MIDDLE", _read_text(reg))


# ── HIGH-4：卸载重写注册文件——临时文件 symlink 预植不得写穿受害文件 ──────────
class TestHigh4TmpSymlinkWriteThrough(InitShTestBase):
    def test_preplanted_tmp_symlink_does_not_clobber_victim(self):
        build_plugin(self.plugin, ["acc-one"], ["a1.md"])
        reg = os.path.join(self.base, "CLAUDE.md")
        # 先正常安装，注册文件里有托管块
        cp0 = self.run_init("--tool", "claude", "--level", "project", "--yes")
        self.assertEqual(cp0.returncode, 0, self.out(cp0))
        self.assertIn(MANAGED_BEGIN, _read_text(reg))
        # 预植受害文件 + 老式固定临时名 CLAUDE.md.tmp -> victim
        victim = os.path.join(self.sbx, "VICTIM.txt")
        victim_bytes = b"SACRED-VICTIM-CONTENT\n"
        with open(victim, "wb") as f:
            f.write(victim_bytes)
        os.symlink(victim, reg + ".tmp")

        cp = self.run_init("--tool", "claude", "--level", "project", "--uninstall", "--yes")
        self.assertEqual(cp.returncode, 0, self.out(cp))
        # victim 字节不变（老式 `> $REG_FILE.tmp` 会写穿它）
        self.assertEqual(_read_bytes(victim), victim_bytes, "victim 被写穿/覆盖了")
        # REG_FILE 仍是真实文件（不是被 mv 盖成指向 victim 的 symlink），且托管块已移除
        self.assertFalse(os.path.islink(reg), "CLAUDE.md 变成了 symlink")
        self.assertNotIn(MANAGED_BEGIN, _read_text(reg))


# ── MEDIUM-5/6：备份唯一名（同秒不覆盖）+ 目录备份 ──────────────────────────
class TestBackupUniqueness(InitShTestBase):
    def _fake_date_bin(self):
        """造一个把时间戳钉死的假 date，逼出「同一秒两次备份」。"""
        binp = os.path.join(self.sbx, "fakebin")
        os.makedirs(binp, exist_ok=True)
        script = (
            "#!/usr/bin/env bash\n"
            "case \"$1\" in\n"
            "  +%Y%m%d%H%M%S) printf '20260101000000\\n' ;;\n"
            "  +%Y-%m-%dT%H:%M:%S) printf '2026-01-01T00:00:00\\n' ;;\n"
            "  *) exec /bin/date \"$@\" ;;\n"
            "esac\n"
        )
        p = os.path.join(binp, "date")
        _write(p, script)
        os.chmod(p, 0o755)
        return binp

    def test_same_second_backups_both_survive(self):
        build_plugin(self.plugin, ["acc-one"], ["a1.md"])
        reg = os.path.join(self.base, "CLAUDE.md")
        _write(reg, "USER PRE-EXISTING\n")
        fb = self._fake_date_bin()
        # install：备份 #1（ts 钉死）+ 追加托管块
        cp1 = self.run_init("--tool", "claude", "--level", "project", "--yes", path_prepend=fb)
        self.assertEqual(cp1.returncode, 0, self.out(cp1))
        # uninstall：备份 #2（同一钉死 ts）+ 移除托管块
        cp2 = self.run_init("--tool", "claude", "--level", "project", "--uninstall", "--yes", path_prepend=fb)
        self.assertEqual(cp2.returncode, 0, self.out(cp2))
        baks = [n for n in os.listdir(self.base) if n.startswith("CLAUDE.md.bak.20260101000000.")]
        self.assertEqual(len(baks), 2, "同秒两次备份未双存（被覆盖了）: %r" % baks)

    def test_force_over_real_dir_backs_up_directory(self):
        """--force 遇已存在真目录：应备份目录（cp -pR）而非因 cp -p 报错 abort。"""
        build_plugin(self.plugin, ["acc-one"], ["a1.md"])
        skdst = os.path.join(self.base, ".claude", "skills")
        os.makedirs(skdst, exist_ok=True)
        # 预植真目录（非 symlink）在 skills/acc-one，带内容
        realdir = os.path.join(skdst, "acc-one")
        os.makedirs(realdir, exist_ok=True)
        _write(os.path.join(realdir, "user_stuff.txt"), "keep me\n")

        cp = self.run_init("--tool", "claude", "--level", "project", "--force", "--yes")
        self.assertEqual(cp.returncode, 0, self.out(cp))
        # 现在应是 symlink 指向插件源
        self.assertTrue(os.path.islink(os.path.join(skdst, "acc-one")))
        # 备份目录存在且内容保留
        baks = [n for n in os.listdir(skdst) if n.startswith("acc-one.bak.")]
        self.assertEqual(len(baks), 1, baks)
        self.assertTrue(
            os.path.exists(os.path.join(skdst, baks[0], "user_stuff.txt")),
            "目录备份内容缺失",
        )


# ── MEDIUM-7：卸载只删指向本插件源的 symlink，外来 symlink 保守跳过 ──────────
class TestMedium7RmPathValidation(InitShTestBase):
    def test_foreign_symlink_not_deleted_on_uninstall(self):
        build_plugin(self.plugin, ["acc-one", "acc-two"], ["a1.md"])
        cp0 = self.run_init("--tool", "claude", "--level", "project", "--yes")
        self.assertEqual(cp0.returncode, 0, self.out(cp0))
        skdst = os.path.join(self.base, ".claude", "skills")
        # 把 acc-one 换成指向外部（非插件源）的 symlink
        foreign = os.path.join(self.sbx, "FOREIGN")
        os.makedirs(foreign, exist_ok=True)
        os.remove(os.path.join(skdst, "acc-one"))
        os.symlink(foreign, os.path.join(skdst, "acc-one"))

        cp = self.run_init("--tool", "claude", "--level", "project", "--uninstall", "--yes")
        self.assertEqual(cp.returncode, 0, self.out(cp))
        # 外来 symlink 未被删（保守跳过 + warn）
        self.assertTrue(os.path.islink(os.path.join(skdst, "acc-one")), "外来 symlink 被误删")
        self.assertIn("不指向本插件源", self.out(cp))
        # 合法的 acc-two 被删
        self.assertFalse(os.path.lexists(os.path.join(skdst, "acc-two")))


# ── MEDIUM-8：materialize 拷贝靠 provenance 佐证才删 ─────────────────────────
class TestMedium8MaterializeProvenance(InitShTestBase):
    def test_materialize_install_then_uninstall_removes_copies(self):
        build_plugin(self.plugin, ["acc-one"], ["a1.md"])
        cp0 = self.run_init("--tool", "claude", "--level", "project", "--materialize", "--yes")
        self.assertEqual(cp0.returncode, 0, self.out(cp0))
        skdst = os.path.join(self.base, ".claude", "skills")
        copy = os.path.join(skdst, "acc-one")
        self.assertTrue(os.path.isdir(copy) and not os.path.islink(copy), "materialize 未拷贝真目录")
        self.assertTrue(os.path.exists(os.path.join(skdst, ".oprunway-provenance")))

        cp = self.run_init("--tool", "claude", "--level", "project", "--uninstall", "--yes")
        self.assertEqual(cp.returncode, 0, self.out(cp))
        self.assertFalse(os.path.lexists(copy), "provenance 佐证的 materialize 拷贝未被删")

    def test_materialize_copy_without_provenance_is_skipped(self):
        """provenance 缺失时，卸载不敢删真目录（保守跳过）。"""
        build_plugin(self.plugin, ["acc-one"], ["a1.md"])
        cp0 = self.run_init("--tool", "claude", "--level", "project", "--materialize", "--yes")
        self.assertEqual(cp0.returncode, 0, self.out(cp0))
        skdst = os.path.join(self.base, ".claude", "skills")
        os.remove(os.path.join(skdst, ".oprunway-provenance"))  # 抹掉佐证

        cp = self.run_init("--tool", "claude", "--level", "project", "--uninstall", "--yes")
        self.assertEqual(cp.returncode, 0, self.out(cp))
        self.assertTrue(os.path.isdir(os.path.join(skdst, "acc-one")), "无佐证竟被删")
        self.assertIn("保守不删", self.out(cp))


# ── MEDIUM-9/10：刁钻 skill 名（空格/glob/换行）不产生伪路径、不 glob 污染 ────
class TestMedium910WeirdNames(InitShTestBase):
    def test_names_with_space_glob_newline(self):
        weird = ["acc alpha", "acc*star", "acc[bracket]", "acc\nnewline"]
        build_plugin(self.plugin, weird, ["a1.md"])
        cp = self.run_init("--tool", "claude", "--level", "project", "--yes")
        self.assertEqual(cp.returncode, 0, self.out(cp))
        skdst = os.path.join(self.base, ".claude", "skills")
        # 排除 materialize 兜底可能写下的 provenance 元文件
        got = set(os.listdir(skdst)) - {".oprunway-provenance"}
        # 恰好这些名字（各一条），无伪路径拆分、无 glob 展开污染
        self.assertEqual(got, set(weird), "刁钻名产生了伪路径或漏装: %r" % got)
        # 每个名字都真装上了：要么 symlink 指向插件源，要么 materialize 真拷贝（如换行名的保守回退）
        plug_real = os.path.realpath(self.plugin)
        for n in weird:
            p = os.path.join(skdst, n)
            if os.path.islink(p):
                self.assertTrue(os.path.realpath(p).startswith(plug_real), "%r symlink 越界" % n)
            else:
                self.assertTrue(os.path.isdir(p), "%r 既非 symlink 也非目录（伪路径?）" % n)


# ── 全局：--dry-run 零文件系统写；HOME 隔离（~/.config=0、无 rc 改动）────────
class TestDryRunAndHomeIsolation(InitShTestBase):
    def test_dry_run_leaves_base_and_home_untouched(self):
        build_plugin(self.plugin, ["acc-one", "acc-two"], ["a1.md", "a2.md"])
        # 预置一点已有内容 + 假 shell rc，验证 dry-run 前后完全一致
        _write(os.path.join(self.base, "CLAUDE.md"), "PRE\n")
        _write(os.path.join(self.home, ".bashrc"), "# sentinel rc\n")
        base_before = snapshot(self.base)
        home_before = snapshot(self.home)

        cp = self.run_init("--tool", "claude", "--level", "project", "--dry-run")
        self.assertEqual(cp.returncode, 0, self.out(cp))
        self.assertEqual(snapshot(self.base), base_before, "dry-run 改动了 BASE")
        self.assertEqual(snapshot(self.home), home_before, "dry-run 改动了 HOME")

    def test_project_install_does_not_touch_home(self):
        """project 级安装只落 BASE；HOME 不产生 ~/.config、不改 shell rc。"""
        build_plugin(self.plugin, ["acc-one"], ["a1.md"])
        _write(os.path.join(self.home, ".bashrc"), "# sentinel rc\n")
        rc_before = _read_bytes(os.path.join(self.home, ".bashrc"))
        home_before = snapshot(self.home)

        cp = self.run_init("--tool", "claude", "--level", "project", "--yes")
        self.assertEqual(cp.returncode, 0, self.out(cp))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config")), "竟建了 ~/.config")
        self.assertEqual(_read_bytes(os.path.join(self.home, ".bashrc")), rc_before, "shell rc 被改")
        self.assertEqual(snapshot(self.home), home_before, "HOME 被改动")


# ── LOW-11：末位 --tool / --level 不因 shift 2 崩溃，给出友好报错 ─────────────
class TestLow11ShiftGuard(InitShTestBase):
    def test_trailing_tool_flag_reports_cleanly(self):
        cp = self.run_init("--tool")  # 末位缺值
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("--tool 需要一个参数", self.out(cp))

    def test_trailing_level_flag_reports_cleanly(self):
        cp = self.run_init("--tool", "claude", "--level")  # 末位缺值
        self.assertNotEqual(cp.returncode, 0)
        self.assertIn("--level 需要一个参数", self.out(cp))


# ── LOW-12：usage 不声称做了没做的事（自动还原备份）─────────────────────────
class TestLow12UsageHonesty(InitShTestBase):
    def test_help_does_not_claim_auto_restore(self):
        cp = self.run_init("--help")
        self.assertEqual(cp.returncode, 0, self.out(cp))
        text = self.out(cp)
        self.assertIn("--uninstall", text)
        self.assertIn("不自动还原", text, "usage 仍暗示会自动还原备份")
        self.assertNotIn("还原备份", text.replace("不自动还原", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
