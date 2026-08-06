"""`taskdoc_caseset.py` 的 **golden 授权锚**回归：快照落盘 + 判据锚对账。

—— 为什么单开一个文件 ——
2026-08-05 的 GaussianBlur 真机跑测，唯一阻断是一处判据锚不符：
`spec.golden` 没写 `taskdoc_snapshot`（当时的 spec 刻意低报 `kind=none`、也不绑快照），
而包装层的 `GOLDEN_CONTRACT` 逐字记着任务书 sha —— validator 逐条对账，169 条 case 各记一条
problem，强制 `blocked_golden_unauthorized`。**那不是 golden 有问题，是两份声明没对齐**，
而两侧的判据在生成期就全都在手边。

本文件锁两件事：
  ① 包装层与任务书全文快照**成对产出**——快照不落到 golden 同目录，`verify_authorization`
     就恒返 False（它只读 `<golden 同目录>/task_doc.snapshot.md`），任何 oracle_method 声明都会
     掉到 tier 4 blocked；
  ② 声称有授权却拿不出锚 → **生成期就 fail-closed**，别把这条错留到真机跑完再炸。

跑: cd plugin/acc-common && python3 -m unittest test_taskdoc_caseset_golden_anchor -v
⚠ 本文件不碰任何 numpy/torch/cv2 —— 只校文本产物与契约自洽（同 test_check_golden）。
"""
import hashlib
import io
import os
import shutil
import tempfile
import unittest

import precision_policy
import taskdoc_caseset

_ORIGINAL = '''\
def demo_golden(self, ksize_x, ksize_y, sigmaX, sigmaY):
    return [self]
'''

# 快照原文：第 2 行那句是本测试用的引文，行号与 cite 必须对得上。
_SNAPSHOT = "# 任务书\n| **CV_32F（L1）** | 对标 OpenCV CPU |\n末行\n"

_ORACLE = {"kind": "oracle_method", "cite": "task_doc.snapshot.md:2",
           "quote": "对标 OpenCV CPU"}


def _caseset(taskdoc_sha256):
    """`render_golden_wrapper` 消费到的最小 caseset（不跑 discover/normalize，只钉本支的行为）。"""
    return {
        "taskdoc_sha256": taskdoc_sha256,
        "source_dir": "docs/self_test_case/demo",
        "expect_func": "demo_golden.py:demo_golden",
        "golden_original": {"filename": "demo_golden.py", "function": "demo_golden",
                            "text": _ORIGINAL,
                            "sha256": hashlib.sha256(_ORIGINAL.encode("utf-8")).hexdigest()},
        "golden_contract_derived": {"source": "single_api", "method_kind": "opencv_cpu"},
        "mapping_ir": {
            "golden_contract_derivation": {"reference_api_calls": ["cv2.GaussianBlur"]},
            "golden_call": {"returns": "list_first_element",
                            "positional": [{"param": "self", "kind": "input", "index": 0}]},
        },
    }


def _contract_of(path):
    """执行产出的包装层，取回它的 `GOLDEN_CONTRACT`（同 check_golden 的取法与信任级）。"""
    ns = {}
    with io.open(path, encoding="utf-8") as fh:
        exec(compile(fh.read(), path, "exec"), ns)        # noqa: S102 — 自产文本，同信任级
    return ns["GOLDEN_CONTRACT"]


class TaskdocSnapshotMaterializationTest(unittest.TestCase):
    """快照必须与包装层**同目录、同字节**落盘——它是引文锚的载体，不是可选装饰。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="oprunway_anchor_gen_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.work = os.path.join(self.root, "work")
        os.makedirs(self.work)
        self.snap = os.path.join(self.work, taskdoc_caseset.TASKDOC_SNAPSHOT_NAME)
        with io.open(self.snap, "w", encoding="utf-8", newline="") as fh:
            fh.write(_SNAPSHOT)
        self.sha = hashlib.sha256(_SNAPSHOT.encode("utf-8")).hexdigest()
        self.out = os.path.join(self.root, "out", "golden.py")

    def _render(self, authorization=None, snapshot="default", caseset=None):
        return taskdoc_caseset.render_golden_wrapper(
            caseset or _caseset(self.sha), self.out, authorization=authorization,
            taskdoc_snapshot=(self.snap if snapshot == "default" else snapshot))

    def test_snapshot_lands_beside_wrapper_byte_identical(self):
        """快照落在 golden.py 旁边、逐字节相同。

        ⚠ 逐字节是硬要求：`verify_authorization` 按**行号 + 逐字子串**核引文，
        补一个末尾换行就可能让行号移位，而报出来的错却是「引文与出处对不上」——
        看着像 agent 编造引文，真正的病因反而查不出来。"""
        paths = self._render(authorization=_ORACLE)
        landed = paths["taskdoc_snapshot"]
        self.assertEqual(os.path.dirname(landed), os.path.dirname(paths["wrapper"]))
        self.assertEqual(os.path.basename(landed), taskdoc_caseset.TASKDOC_SNAPSHOT_NAME)
        with io.open(landed, "rb") as fh:
            raw = fh.read()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), self.sha)
        self.assertEqual(raw, _SNAPSHOT.encode("utf-8"))

    def test_generated_pair_verifies_and_reaches_tier1(self):
        """**本支的终点**：产出的「包装层 + 快照」这一对，喂给判档三层要真的走到 tier 1。

        这条不是在测 precision_policy，是在测**我们产的东西能不能被那三层认下来**——
        它才是「授权抬档」有没有落到实处的唯一证据。"""
        paths = self._render(authorization=_ORACLE)
        contract = _contract_of(paths["wrapper"])
        precision_policy.validate_golden_contract(contract, where="产出的 GOLDEN_CONTRACT")
        ok, why = precision_policy.verify_authorization(contract, paths["taskdoc_snapshot"])
        self.assertTrue(ok, why)
        self.assertEqual(precision_policy.derive_golden_tier(contract, ok), (1, False, None))

    def test_authorization_written_verbatim(self):
        """授权是 NL 判断、机器派生不出来 → 逐字取自 spec，**不得**在这里被改写或补默认。"""
        contract = _contract_of(self._render(authorization=_ORACLE)["wrapper"])
        self.assertEqual(contract["authorization"], _ORACLE)
        self.assertEqual(contract["taskdoc_snapshot"], {"sha256": self.sha})

    def test_default_authorization_is_none_and_still_verifies(self):
        """不给 authorization → 缺省 `kind=none`（不构成授权）；此时不需要锚也能核过、判 tier 2。"""
        paths = self._render(authorization=None, snapshot=None)
        contract = _contract_of(paths["wrapper"])
        self.assertEqual(contract["authorization"], {"kind": "none"})
        self.assertIsNone(paths["taskdoc_snapshot"])
        ok, _ = precision_policy.verify_authorization(contract, None)
        self.assertTrue(ok)
        self.assertEqual(precision_policy.derive_golden_tier(contract, ok), (2, False, None))


class MirroredVocabularyTest(unittest.TestCase):
    """本模块**刻意不 import** `precision_policy`（避免 Layer 1 之间不必要的耦合），
    代价是几个词表靠人对齐——那就让机器盯着，别等它们漂到真机上才现形。"""

    def test_local_mirrors_match_precision_policy(self):
        self.assertEqual(taskdoc_caseset.TASKDOC_SNAPSHOT_NAME,
                         precision_policy.TASKDOC_SNAPSHOT_NAME)
        self.assertEqual(set(taskdoc_caseset.AUTHORIZATION_KINDS),
                         set(precision_policy.AUTHORIZATION_KIND))
        # 「哪两档需要锚」在那边是散在 validate_golden_contract / verify_authorization 里的字面量，
        # 没有可 import 的常量 —— 这里按语义钉死：需要锚的 = 全集减去「本就不构成授权」的两档。
        self.assertEqual(set(taskdoc_caseset.ANCHORED_AUTHORIZATION_KINDS),
                         set(precision_policy.AUTHORIZATION_KIND) - {"impl_reference", "none"})

    def test_reference_api_method_kinds_are_controlled(self):
        """`REFERENCE_APIS[*].method_kind` 同属人工对齐的镜像，一并盯住。"""
        for api, meta in taskdoc_caseset.REFERENCE_APIS.items():
            self.assertIn(meta["method_kind"], precision_policy.GOLDEN_METHOD_KIND, api)


class AuthorizationFailClosedTest(unittest.TestCase):
    """声称有授权却拿不出锚 —— 一律在**生成期**拒，别产一份注定被判 blocked 的 golden。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="oprunway_anchor_fail_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.snap = os.path.join(self.root, taskdoc_caseset.TASKDOC_SNAPSHOT_NAME)
        with io.open(self.snap, "w", encoding="utf-8", newline="") as fh:
            fh.write(_SNAPSHOT)
        self.sha = hashlib.sha256(_SNAPSHOT.encode("utf-8")).hexdigest()
        self.out = os.path.join(self.root, "out", "golden.py")

    def _render(self, **kw):
        kw.setdefault("caseset", _caseset(self.sha))
        cs = kw.pop("caseset")
        return taskdoc_caseset.render_golden_wrapper(cs, self.out, **kw)

    def test_oracle_without_snapshot_rejected(self):
        """oracle_method + 没有快照 → 拒。放过去的话，`verify_authorization` 在真机上恒返 False，
        档位掉 tier 4 blocked——错会晚发现整整一轮跑测。"""
        with self.assertRaises(taskdoc_caseset.CasesetError) as cm:
            self._render(authorization=_ORACLE, taskdoc_snapshot=None)
        self.assertIn("快照", str(cm.exception))
        self.assertFalse(os.path.exists(self.out), "拒了就不该留下半份产物")

    def test_oracle_without_quote_rejected(self):
        for missing in ("cite", "quote"):
            auth = {k: v for k, v in _ORACLE.items() if k != missing}
            with self.assertRaises(taskdoc_caseset.CasesetError) as cm:
                self._render(authorization=auth, taskdoc_snapshot=self.snap)
            self.assertIn(missing, str(cm.exception))

    def test_unknown_authorization_kind_rejected(self):
        """受控词表外的 kind → 拒（allowlist，不静默兜底成 none）。"""
        with self.assertRaises(taskdoc_caseset.CasesetError):
            self._render(authorization={"kind": "taskdoc_says_so"}, taskdoc_snapshot=self.snap)

    def test_snapshot_from_a_different_taskdoc_rejected(self):
        """快照指纹 ≠ 本轮 taskdoc_sha256 → 拒：两份不同的任务书，引文锚会指到错误的行。"""
        with self.assertRaises(taskdoc_caseset.CasesetError) as cm:
            self._render(caseset=_caseset("0" * 64), authorization=_ORACLE,
                         taskdoc_snapshot=self.snap)
        self.assertIn("指纹", str(cm.exception))

    def test_symlinked_snapshot_rejected(self):
        """快照是软链 → 拒（防换锚，同 check_golden 的那道）。"""
        link = os.path.join(self.root, "link.md")
        os.symlink(self.snap, link)
        with self.assertRaises(taskdoc_caseset.CasesetError) as cm:
            self._render(authorization=_ORACLE, taskdoc_snapshot=link)
        self.assertIn("符号链接", str(cm.exception))

    def test_resolve_prefers_explicit_and_rejects_missing(self):
        """显式给了却不存在 = 配置错，当场抛；没给就找 work_dir，找不到返 None（不是错）。"""
        self.assertEqual(taskdoc_caseset.resolve_taskdoc_snapshot(None, self.root), self.snap)
        self.assertIsNone(taskdoc_caseset.resolve_taskdoc_snapshot(
            None, os.path.join(self.root, "empty")))
        with self.assertRaises(taskdoc_caseset.CasesetError):
            taskdoc_caseset.resolve_taskdoc_snapshot(os.path.join(self.root, "nope.md"), self.root)


class SpecGoldenCrossCheckTest(unittest.TestCase):
    """`spec.golden` ↔ 从任务书 golden 派生的契约，生成期就对账（validator 验收时会再对一遍）。"""

    def setUp(self):
        self.sha = hashlib.sha256(_SNAPSHOT.encode("utf-8")).hexdigest()
        self.caseset = _caseset(self.sha)

    def _spec(self, **override):
        golden = {"source": "single_api", "method_kind": "opencv_cpu",
                  "authorization": dict(_ORACLE),
                  "taskdoc_snapshot": {"sha256": self.sha}}
        golden.update(override)
        return {"op": "Demo", "golden": golden}

    def test_consistent_spec_returns_its_authorization(self):
        self.assertEqual(taskdoc_caseset.cross_check_spec_golden(self._spec(), self.caseset),
                         _ORACLE)

    def test_spec_without_golden_key_is_legacy_not_error(self):
        self.assertIsNone(taskdoc_caseset.cross_check_spec_golden({"op": "Demo"}, self.caseset))

    def test_method_kind_conflict_rejected(self):
        """spec 说 torch_cpu、任务书 golden 实际调 cv2 → 两个独立源不一致，拒。

        放过去的代价是 validator 在真机跑完后逐条判 blocked，且报错只说「判据锚不符」，
        不会告诉你是 spec 写错了还是 golden 换了。"""
        with self.assertRaises(taskdoc_caseset.CasesetError) as cm:
            taskdoc_caseset.cross_check_spec_golden(self._spec(method_kind="torch_cpu"),
                                                    self.caseset)
        self.assertIn("method_kind", str(cm.exception))

    def test_missing_snapshot_sha_rejected(self):
        """**GaussianBlur 那个假阻断的复现**：spec 不绑快照指纹、包装层绑了 → 拒。"""
        spec = self._spec()
        del spec["golden"]["taskdoc_snapshot"]
        with self.assertRaises(taskdoc_caseset.CasesetError) as cm:
            taskdoc_caseset.cross_check_spec_golden(spec, self.caseset)
        self.assertIn(self.sha, str(cm.exception))

    def test_stale_snapshot_sha_rejected(self):
        """spec 绑的是**上一版任务书**的指纹 → 拒（大小写/空白按同一口径规范化后再比）。"""
        with self.assertRaises(taskdoc_caseset.CasesetError):
            taskdoc_caseset.cross_check_spec_golden(
                self._spec(taskdoc_snapshot={"sha256": "1" * 64}), self.caseset)
        self.assertEqual(                      # 只是大写/空白差异 → 视同一致，不该误报
            taskdoc_caseset.cross_check_spec_golden(
                self._spec(taskdoc_snapshot={"sha256": "  " + self.sha.upper() + " "}),
                self.caseset),
            _ORACLE)


if __name__ == "__main__":
    unittest.main()
