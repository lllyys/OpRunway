"""check_manifest_sync 单测——stdlib unittest。

这个门守着「插件能不能加载」（agent 全不加载过的真 bug 就是它没抓住），所以它自己必须被测：
**fail-closed 矩阵**——任何读不了 / 解析不了 / 不认识的输入，都必须打印 `STATUS: DRIFT` 且 exit 1，
**绝不 traceback、绝不假 SYNCED**。

跑: python3 -m unittest test_check_manifest_sync -v   （在 acc-common/ 下）
"""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

import check_manifest_sync as C

_GOOD_FM = ("---\n"
            "name: op-acceptance\n"
            "description: hi\n"
            "skills:\n  - s1\n  - s2\n"
            "agents:\n  - a1\n  - a2\n"
            "---\n# body\n")


def _write(path, text, encoding="utf-8"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=encoding) as f:
        f.write(text)


class _Tree(unittest.TestCase):
    """在临时目录里搭一棵最小 plugin 树；子类改其中一处制造漂移。"""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.build()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def build(self, fm=_GOOD_FM, agents=("a1", "a2"), skills=("s1", "s2"), pj=None):
        for a in agents:
            _write(os.path.join(self.root, "agents", a + ".md"), "---\nname: %s\n---\n" % a)
        for s in skills:
            _write(os.path.join(self.root, "skills", s, "SKILL.md"), "---\nname: %s\n---\n" % s)
        if fm is not None:
            _write(os.path.join(self.root, "AGENTS.md"), fm)
        pj = {"name": "t"} if pj is None else pj
        _write(os.path.join(self.root, ".claude-plugin", "plugin.json"),
               pj if isinstance(pj, str) else json.dumps(pj))

    def run_gate(self):
        """跑 main()，返回 (rc, stdout)。任何异常逃逸都是 bug —— 门必须自己兜住。"""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = C.main(["--plugin-root", self.root])
        return rc, buf.getvalue()

    def assertDrift(self, needle=None):
        rc, out = self.run_gate()
        self.assertEqual(rc, 1, f"期望 exit 1，得 {rc}；输出：{out}")
        self.assertIn("STATUS: DRIFT", out)
        if needle:
            self.assertIn(needle, out)

    def assertSynced(self):
        rc, out = self.run_gate()
        self.assertEqual(rc, 0, f"期望 exit 0，得 {rc}；输出：{out}")
        self.assertIn("STATUS: SYNCED", out)


class HappyPathTest(_Tree):
    def test_synced(self):
        self.assertSynced()


class DriftTest(_Tree):
    def test_agent_file_not_registered(self):
        _write(os.path.join(self.root, "agents", "a3.md"), "---\nname: a3\n---\n")
        self.assertDrift("有文件但未登记")

    def test_agent_declared_but_missing(self):
        os.remove(os.path.join(self.root, "agents", "a2.md"))
        self.assertDrift("声明了但缺文件")

    def test_skill_file_not_registered(self):
        _write(os.path.join(self.root, "skills", "s3", "SKILL.md"), "---\nname: s3\n---\n")
        self.assertDrift("有文件但未登记")

    def test_skill_dir_without_skill_md_is_not_a_skill(self):
        os.makedirs(os.path.join(self.root, "skills", "s9"))   # 空目录不算 skill
        self.assertSynced()

    def test_plugin_json_declares_agents(self):
        """反向门：声明 agents → Claude Code 静默忽略致 Agents(0)。"""
        self.build(pj={"name": "t", "agents": ["./agents/a1.md"]})
        self.assertDrift("plugin.json 声明了 agents 字段")


class DiskScanTest(_Tree):
    def test_directory_named_md_is_not_an_agent(self):
        """名为 `x.md` 的**目录**不得被当成 agent（否则声明 x 会假装同步）。"""
        os.makedirs(os.path.join(self.root, "agents", "a3.md"))
        self.assertSynced()   # a3 不计入磁盘集 → 仍与声明的 {a1,a2} 一致

    def test_broken_symlink_agent_not_counted(self):
        os.symlink(os.path.join(self.root, "nope.md"), os.path.join(self.root, "agents", "a3.md"))
        self.assertSynced()

    def test_symlink_outside_root_rejected(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        _write(os.path.join(outside, "evil.md"), "---\nname: evil\n---\n")
        os.symlink(os.path.join(outside, "evil.md"), os.path.join(self.root, "agents", "a3.md"))
        self.assertSynced()   # 仓外软链不计入 → 不能用外部文件满足门

    def test_missing_agents_dir(self):
        shutil.rmtree(os.path.join(self.root, "agents"))
        self.assertDrift("声明了但缺文件")


class MalformedFrontmatterTest(_Tree):
    """fail-closed：畸形 frontmatter 必须 DRIFT，绝不 traceback、绝不假 SYNCED。"""

    def _fm(self, text):
        self.build(fm=text)
        return self.assertDrift()

    def test_missing_closing_delimiter(self):
        """截断的 frontmatter 即便字段齐全也不得放行（否则假 SYNCED）。"""
        self._fm("---\nname: x\ndescription: d\nskills:\n  - s1\n  - s2\n"
                 "agents:\n  - a1\n  - a2\n")

    def test_no_frontmatter(self):
        self._fm("# just markdown\n")

    def test_empty_file(self):
        self._fm("")

    def test_junk_line(self):
        self._fm("---\nname: x\ndescription: d\n@@@ junk\n---\n")

    def test_duplicate_key(self):
        self._fm("---\nname: x\nname: y\ndescription: d\n---\n")

    def test_duplicate_list_item(self):
        self._fm("---\nname: x\ndescription: d\nagents:\n  - a1\n  - a1\n---\n")

    def test_unbalanced_quote(self):
        self._fm("---\nname: \"x\ndescription: d\n---\n")

    def test_orphan_list_item(self):
        """块列表项没有归属 key。"""
        self._fm("---\nname: x\n  - stray\n---\n")

    def test_illegal_name_path_traversal(self):
        self._fm("---\nname: x\ndescription: d\nskills:\n  - s1\n  - s2\n"
                 "agents:\n  - ../../etc/passwd\n  - a2\n---\n")

    def test_non_utf8_file(self):
        p = os.path.join(self.root, "AGENTS.md")
        with open(p, "wb") as f:
            f.write(b"---\nname: \xff\xfe bad\n---\n")
        self.assertDrift()

    def test_missing_agents_md(self):
        os.remove(os.path.join(self.root, "AGENTS.md"))
        self.assertDrift("缺 AGENTS.md")

    def test_agents_key_absent(self):
        self._fm("---\nname: x\ndescription: d\nskills:\n  - s1\n  - s2\n---\n")

    def test_empty_agents_list(self):
        self._fm("---\nname: x\ndescription: d\nskills:\n  - s1\n  - s2\nagents: []\n---\n")

    def test_flow_list_empty_element(self):
        """`[a1,,a2]` 的多余逗号不得被静默吞掉（否则畸形清单可假 SYNCED）。"""
        self._fm("---\nname: x\ndescription: d\nskills: [s1, s2]\nagents: [a1,,a2]\n---\n")

    def test_flow_list_trailing_comma(self):
        self._fm("---\nname: x\ndescription: d\nskills: [s1, s2]\nagents: [a1, a2,]\n---\n")

    def test_flow_list_duplicate_item(self):
        self._fm("---\nname: x\ndescription: d\nskills: [s1, s2]\nagents: [a1, a1, a2]\n---\n")

    def test_flow_list_quoted_comma(self):
        """引号内逗号不支持 —— 按未配对引号拒掉，不得静默切成两项。"""
        self._fm("---\nname: x\ndescription: d\nagents: [\"a,b\", a2]\n---\n")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root 无视文件权限")
    def test_unreadable_file(self):
        p = os.path.join(self.root, "AGENTS.md")
        os.chmod(p, 0)
        try:                       # 必须在 tearDown 的 rmtree 之前恢复权限
            self.assertDrift()
        finally:
            os.chmod(p, 0o644)


class MalformedPluginJsonTest(_Tree):
    def test_invalid_json(self):
        self.build(pj="{not json")
        self.assertDrift("plugin.json 读取/解析失败")

    def test_top_level_null(self):
        self.build(pj="null")
        self.assertDrift("顶层须是 JSON 对象")

    def test_top_level_array(self):
        self.build(pj="[1, 2]")
        self.assertDrift("顶层须是 JSON 对象")

    def test_top_level_number(self):
        """`\"agents\" in 3` 会抛 TypeError —— 门必须先校类型，不能让它逃逸。"""
        self.build(pj="3")
        self.assertDrift("顶层须是 JSON 对象")

    def test_top_level_true(self):
        self.build(pj="true")
        self.assertDrift("顶层须是 JSON 对象")

    def test_missing_plugin_json(self):
        os.remove(os.path.join(self.root, ".claude-plugin", "plugin.json"))
        self.assertDrift("缺 .claude-plugin/plugin.json")


class ParserAcceptTest(_Tree):
    """收窄后仍必须接受的合法写法（原 test_validate_acceptance_state 里的 parser 用例搬来）。"""

    def _parse(self, text):
        p = os.path.join(self.root, "AGENTS.md")
        _write(p, text)
        return C._parse_frontmatter(p)

    def test_block_list(self):
        self.assertEqual(self._parse("---\nname: x\nskills:\n  - a\n  - b\n---\n")["skills"], ["a", "b"])

    def test_flow_list(self):
        self.assertEqual(self._parse("---\nname: x\nskills: [a, b]\n---\n")["skills"], ["a", "b"])

    def test_flow_list_empty(self):
        self.assertEqual(self._parse("---\nname: x\nskills: []\n---\n")["skills"], [])

    def test_comment_and_blank_skipped(self):
        self.assertEqual(self._parse("---\nname: x\n# c\n\nskills:\n  - a\n---\n")["skills"], ["a"])

    def test_scalar(self):
        self.assertEqual(self._parse("---\nname: op-x\ndescription: hi\n---\n")["name"], "op-x")

    def test_quoted_scalar(self):
        self.assertEqual(self._parse("---\nname: \"op-x\"\n---\n")["name"], "op-x")

    def test_crlf_line_endings(self):
        """CRLF 不得让定界符/块列表识别失效。"""
        fm = self._parse("---\r\nname: x\r\nskills:\r\n  - a\r\n  - b\r\n---\r\n")
        self.assertEqual(fm["skills"], ["a", "b"])
        self.assertEqual(fm["name"], "x")


if __name__ == "__main__":
    unittest.main()
