"""CP · 造例档位与 torch_parity 完整矩阵护栏单测。

跑: cd plugin/acc-common && python3 -m unittest test_gen_cases_case_profile -v
   （纯 numpy 假/样例 golden，**不需 torch**、不需真机。）

这道开关是「对齐参考仓 `Justbin/cannbot-ops-input` 造例规则」那一系列改动的前置护栏：对齐会改掉
gen_cases 的**默认**造例行为，而 4 个已真机验收的 elementwise 算子（IsClose/Sign/Equal/Neg）的
caseset 与全部 .npy 被 sha256 逐字节钉死。故先立字段驱动的档位，后续改动一律只在 `torch_parity` 下生效。

覆盖（全部据 spec 字段驱动、**无算子名分支**，律令 #0）：
  · 受控词表与缺省：未声明 → "legacy"；显式 legacy / torch_parity 各自返回；
  · fail-closed：词表外取值 / 非字符串 / **显式 null** 一律 ValueError，且报错文本含合法词表；
  · 「是否显式声明」信号：`_case_profile_declared`（caseset 账本键出不出，全靠它）；
  · `_plan` 的 meta 带上档位 + 声明与否；
  · **字节安全 pin**（本文件最要紧的一条）：拿真样例 spec `samples/specs/sign.spec.json` 实跑 gen_cases——
    未声明时 caseset **不含** `case_profile` 键；显式声明 legacy 时含该键且值为 "legacy"，
    且两者的 `cases` 列表与落盘 .npy 字节**完全相等**（证明只多了记账、没碰造例）；
  · torch_parity：完整笛卡尔、动态轴 class、禁止 case_target 抽样、缺矩阵 fail-closed；
  · dry-run 回显 + 空模板 `spec_schema_template.jsonc` 已记载该字段（防实现与文档漂移）。
"""
import contextlib, copy, hashlib, io, json, os, shutil, tempfile, unittest

import gen_cases as GC
import _golden_fixture as _gf

_HERE = os.path.dirname(os.path.abspath(__file__))
# 用**真样例 spec**做字节安全 pin（任务要求：别自己编算子）。Sign 的 golden 由共享 fixture 装进临时 ops_root。
_SIGN_SPEC = os.path.join(_HERE, "..", "samples", "specs", "sign.spec.json")
_SCHEMA_TMPL = os.path.join(_HERE, "spec_schema_template.jsonc")


def setUpModule():
    # 共享 fixture：建临时 ops_root + 拷 4 份样例 golden + 设 OPRUNWAY_OPS_DIR。
    # 本文件只有 dry-run 那组真需要它（`_dry_run` 要 load_golden("Sign")）；字节 pin 那组自带更小的 root。
    _gf.install()


def tearDownModule():
    _gf.uninstall()


def _load_spec(path=_SIGN_SPEC):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _with_profile(spec, profile):
    """返回一份**只**多了 `precision.case_profile` 的 spec 深拷贝（原 spec 不动，避免用例互相串味）。"""
    out = copy.deepcopy(spec)
    out.setdefault("precision", {})["case_profile"] = profile
    return out


def _with_parity_matrix(spec):
    """最小的通用 torch_parity 矩阵夹具；结构与 cannbot coverage 轴模型一致。"""
    out = _with_profile(spec, "torch_parity")
    out["precision"]["torch_parity_matrix"] = {
        "source": "test fixture",
        "source_sha256": "0" * 64,
        "ranks": [1, 2],
        "shape_profiles": [
            {"name": "small", "leading_dim": 31},
            {"name": "medium", "leading_dim": 2047},
            {"name": "large", "leading_dim": 262144},
        ],
        "attribute_profiles": [{"name": "attr_00", "attrs": {}}],
        "generator": {"kind": "uniform", "min": -5, "max": 5},
    }
    # Sign 两个 dtype × 2 rank × 3 shape × 1 attr profile。
    out["precision"]["case_target"] = 12
    return out


def _plan_of(spec, case_target=20):
    """按 gen_cases 的口径从 spec 解出 `_plan` 的入参并调用（cost_fn=None：本文件不验规模预算）。"""
    in_params = [p for p in spec["params"] if p["io"] == "in"]
    attrs_default = {p["name"]: p.get("default") for p in spec["params"] if p["io"] == "attr"}
    self_param = next((p for p in in_params if p["name"] == "self"), in_params[0])
    return GC._plan(spec, in_params, self_param["dtype"], attrs_default, spec["op"], case_target)


def _tree_digest(work):
    """工作区内**全部落盘文件**的字节指纹（相对路径 + 内容 sha256，排序后再 hash）。
    比只比 caseset JSON 强：输入 x*.npy 与 golden.npy 的字节也一并钉住。"""
    files = []
    for dirpath, _, names in os.walk(work):
        for n in names:
            p = os.path.join(dirpath, n)
            with open(p, "rb") as fh:
                files.append(f"{os.path.relpath(p, work)} {hashlib.sha256(fh.read()).hexdigest()}")
    return hashlib.sha256("\n".join(sorted(files)).encode()).hexdigest()


class CaseProfileVocabTest(unittest.TestCase):
    """受控词表 / 缺省 / fail-closed —— 纯字段解析层，不跑生成。"""

    def test_vocabulary_is_exactly_two_words(self):
        """词表漂移守卫：多一档少一档都要有人显式改这条断言（档位是护栏，不该被顺手扩）。"""
        self.assertEqual(GC._CASE_PROFILES, ("legacy", "torch_parity"))
        self.assertEqual(GC._DEFAULT_CASE_PROFILE, "legacy")

    def test_undeclared_defaults_to_legacy(self):
        """**整字段省略 → legacy**（= 现行为）。三种「没写」的写法都算没写。"""
        self.assertEqual(GC._case_profile({}), "legacy")
        self.assertEqual(GC._case_profile({"precision": {}}), "legacy")
        self.assertEqual(GC._case_profile({"precision": None}), "legacy")   # precision 整块缺失/为 null

    def test_declared_values_roundtrip(self):
        self.assertEqual(GC._case_profile({"precision": {"case_profile": "legacy"}}), "legacy")
        self.assertEqual(GC._case_profile({"precision": {"case_profile": "torch_parity"}}), "torch_parity")

    def test_illegal_values_fail_closed(self):
        """词表外取值 / 非字符串 / **显式 null** 一律炸，且报错点名两个合法值（照 tolerance_source 口径：
        字段一旦出现就必须是词表内的字符串——不给 null/""/0 悄悄退回缺省档的口子）。"""
        for bad in ("faithful", "cannbot", "", "LEGACY", 123, 1.0, None, True, ["legacy"], {"v": "legacy"}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as cm:
                    GC._case_profile({"precision": {"case_profile": bad}})
                msg = str(cm.exception)
                self.assertIn("legacy", msg)
                self.assertIn("torch_parity", msg)
                self.assertIn("case_profile", msg)

    def test_declared_flag(self):
        """`_case_profile_declared` 要能分出「没写」与「写了 legacy」——caseset 账本键出不出全靠它。"""
        self.assertFalse(GC._case_profile_declared({}))
        self.assertFalse(GC._case_profile_declared({"precision": {}}))
        self.assertFalse(GC._case_profile_declared({"precision": None}))
        self.assertTrue(GC._case_profile_declared({"precision": {"case_profile": "legacy"}}))
        self.assertTrue(GC._case_profile_declared({"precision": {"case_profile": "torch_parity"}}))

    def test_declared_flag_also_validates(self):
        """单独问「声明了没」也要过词表校验——否则就多一条绕过 fail-closed 的旁路。"""
        with self.assertRaises(ValueError):
            GC._case_profile_declared({"precision": {"case_profile": "faithful"}})


class CaseProfilePlanMetaTest(unittest.TestCase):
    """`_plan` 入口读档位；legacy 保持原行为，torch_parity 进入完整矩阵。"""

    def setUp(self):
        self.spec = _load_spec()

    def test_meta_reports_legacy_when_undeclared(self):
        _entries, meta = _plan_of(self.spec)
        self.assertEqual(meta["case_profile"], "legacy")
        self.assertFalse(meta["case_profile_declared"])

    def test_meta_reports_declared_profile(self):
        # ⚠ torch_parity 自「批 B」起**必须**带 `precision.torch_parity_matrix`，缺矩阵当场
        # fail-closed（见同文件 test_torch_parity_without_matrix_fails_closed）。原来这里用
        # `_with_profile(spec, "torch_parity")` 只塞档位不塞矩阵，与那条用例对同一份输入的期望
        # **互相矛盾**——是本用例没跟上改动，不是代码错。两档各用其合法 spec。
        # ⚠ 两档的 case_target 必然不同（torch_parity 的矩阵大小锁死 12，legacy 无此约束），
        #   所以先断言「条数 == 该档的 target」把 target 这个变量摘干净，
        #   再断言档位元数据——某一档挂了能一眼看出是 target 还是 profile 的锅。
        for profile, spec, target in (
                ("legacy", _with_profile(self.spec, "legacy"), 20),
                ("torch_parity", _with_parity_matrix(self.spec), 12)):
            with self.subTest(profile=profile):
                entries, meta = _plan_of(spec, case_target=target)
                self.assertEqual(len(entries), target, "该档的 case_target 先不成立，档位断言无意义")
                self.assertEqual(meta["case_profile"], profile)
                self.assertTrue(meta["case_profile_declared"])

    def test_illegal_profile_fails_closed_in_plan(self):
        """非法档位在计划期就炸（gen_cases 与 _dry_run 两条路径都过 `_plan`，没有绕过口）。"""
        with self.assertRaises(ValueError):
            _plan_of(_with_profile(self.spec, "faithful"))

    def test_torch_parity_emits_complete_cartesian_matrix(self):
        parity = _with_parity_matrix(self.spec)
        entries, meta = _plan_of(parity, case_target=12)
        self.assertEqual(len(entries), 12)
        self.assertEqual(meta["pool_max"], 12)
        self.assertIn("complete_cartesian", meta["coverage_strength"])
        self.assertEqual(
            {tuple(e["shape"]) for e in entries},
            {(31,), (2047,), (262144,),
             (31, 1), (2047, 1), (262144, 1)})

    def test_torch_parity_without_matrix_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "torch_parity_matrix"):
            _plan_of(_with_profile(self.spec, "torch_parity"))

    def test_torch_parity_refuses_sampling_complete_matrix(self):
        parity = _with_parity_matrix(self.spec)
        with self.assertRaisesRegex(ValueError, "必须相等"):
            _plan_of(parity, case_target=11)

    def test_axis_classes_resolve_by_rank(self):
        attrs = [
            {"name": "first", "attrs": {"dim": {"axis_class": "first_axis"}}},
            {"name": "middle", "attrs": {"dim": {"axis_class": "middle_axis"}}},
            {"name": "last", "attrs": {"dim": {"axis_class": "last_axis"}}},
        ]
        self.assertEqual(
            [GC._resolve_axis_class(row["attrs"]["dim"], 8, "test") for row in attrs],
            [0, 3, 7])


class CaseProfileByteSafetyTest(unittest.TestCase):
    """**字节安全 pin**：真样例 spec 实跑 gen_cases，证「声明 legacy 只多一个账本键、别的一字不改」。

    golden 用 **numpy 顶替**（`np.sign`）而非 `samples/golden/Sign/golden.py`：后者按仓内约定延迟 import torch，
    真跑 golden_fn 要 torch，而本 pin 与后端无关（两次运行用的是同一份 golden）。同 `test_gen_cases_dtype_attr`
    里那条 sha256 字节 pin 的做法，保证本用例在没装 torch 的环境也照跑、不靠 skip 假绿。"""

    _FAKE_SIGN_GOLDEN = "def golden_fn(inputs, attrs):\n    return np.sign(inputs[0])\n"

    @classmethod
    def setUpClass(cls):
        cls.ops_root = os.path.realpath(tempfile.mkdtemp(prefix="cp_ops_root_"))
        cls.work_plain = cls.work_legacy = None
        cls.old_ops_dir = os.environ.get("OPRUNWAY_OPS_DIR")   # 模块级 fixture 设的那个 root，跑完要还回去
        try:
            spec = _load_spec()
            _gf.place_golden(cls.ops_root, spec["op"], body=cls._FAKE_SIGN_GOLDEN)
            os.environ["OPRUNWAY_OPS_DIR"] = cls.ops_root
            cls.work_plain = tempfile.mkdtemp(prefix="cp_plain_")
            cls.work_legacy = tempfile.mkdtemp(prefix="cp_legacy_")
            cls.cs_plain = GC.gen_cases(spec, cls.work_plain)                   # 未声明（= 现有 8 份 spec 的形态）
            cls.cs_legacy = GC.gen_cases(_with_profile(spec, "legacy"), cls.work_legacy)
        except BaseException:
            cls.tearDownClass()      # setUpClass 抛异常时 unittest **不会**调 tearDownClass，得自己收尾还原 env
            raise

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "old_ops_dir", "__unset__") != "__unset__":
            if cls.old_ops_dir is None:
                os.environ.pop("OPRUNWAY_OPS_DIR", None)
            else:
                os.environ["OPRUNWAY_OPS_DIR"] = cls.old_ops_dir
            cls.old_ops_dir = "__unset__"
        for attr in ("ops_root", "work_plain", "work_legacy"):
            d = getattr(cls, attr, None)
            if d:
                shutil.rmtree(d, ignore_errors=True)
                setattr(cls, attr, None)

    def test_key_absent_when_undeclared(self):
        """现有 8 份 sample spec 都没声明 → caseset 里**不许**多出这个键（多一个键就破 sha256 pin）。"""
        self.assertNotIn("case_profile", self.cs_plain)

    def test_key_present_and_correct_when_declared(self):
        self.assertEqual(self.cs_legacy.get("case_profile"), "legacy")

    def test_cases_identical_between_undeclared_and_legacy(self):
        """只多了记账、没改造例：`cases` 列表逐项相等（case id / 输入清单 / attrs / 判据契约全等）。"""
        self.assertEqual(self.cs_plain["cases"], self.cs_legacy["cases"])
        self.assertTrue(self.cs_plain["cases"], "样例 spec 应产出非空用例集，否则本 pin 是空转")

    def test_caseset_differs_only_by_that_one_key(self):
        """caseset 的**其余账本字段**（pool_max / emitted / coverage_strength / golden_cost …）全等，
        且新旧键集只差 `case_profile` 一个。
        （`cases` 由上一条单独比——放进来只会让失败时的 diff 长到没法看，覆盖并不增加；
          `work_dir` 是两个临时目录、天然不同，故排除。）"""
        drop = ("case_profile", "work_dir", "cases")
        self.assertEqual({k: v for k, v in self.cs_plain.items() if k not in drop},
                         {k: v for k, v in self.cs_legacy.items() if k not in drop})
        self.assertEqual(set(self.cs_legacy) - set(self.cs_plain), {"case_profile"})
        self.assertEqual(set(self.cs_plain) - set(self.cs_legacy), set())

    def test_on_disk_bytes_identical(self):
        """落盘产物（x*.npy / golden.npy）字节全等——per-case 种子只吃 case_id，声明档位不该扰动数据。"""
        self.assertEqual(_tree_digest(self.work_plain), _tree_digest(self.work_legacy))


class CaseProfileDryRunAndDocTest(unittest.TestCase):
    """dry-run 回显 + 空模板记载（防「实现改了、给 acc-spec 看的模板没跟上」）。"""

    def _dry_run_text(self, spec):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            GC._dry_run(spec)
        return buf.getvalue()

    def test_dry_run_says_undeclared_defaults_to_legacy(self):
        out = self._dry_run_text(_load_spec())
        self.assertIn("case_profile: 未声明（缺省 = legacy = 现行为）", out)

    def test_dry_run_echoes_declared_profile(self):
        out = self._dry_run_text(_with_parity_matrix(_load_spec()))
        self.assertIn("case_profile: torch_parity（spec 显式声明）", out)

    def test_schema_template_documents_the_field(self):
        with open(_SCHEMA_TMPL, encoding="utf-8") as fh:
            tmpl = fh.read()
        self.assertIn("case_profile", tmpl, "空模板未记载 precision.case_profile —— acc-spec 产 spec 只看模板")
        self.assertIn("torch_parity", tmpl)
        for word in GC._CASE_PROFILES:
            self.assertIn(word, tmpl, f"空模板缺受控词表值 {word!r}")


if __name__ == "__main__":
    unittest.main()
