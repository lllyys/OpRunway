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
  · **torch_parity 拒收 legacy 造例键**（`TorchParityUnconsumedKeysTest`）：`attr_matrix` /
    `attr_axis_lengths` / `allow_empty_tensor` / `empty_axis` / `precision.value_profiles`
    在本档一行都不消费，声明即 fail-closed；legacy 侧照旧消费（两侧都立了见证）；
  · **三重记账**（`TorchParityCaseTargetAccountingTest`）：`矩阵大小 − |有证据的排除| ==
    case_target == 实产数`，`excluded` 的受控词表 / reason+evidence / 重叠 / 排空全部 fail-closed；
  · **coverage_strength 如实措辞**（`TorchParityCoverageStrengthTest`）：不许再写「全覆盖」，
    重复覆盖组合按实数落账（median 实测 1344 例 / 1200 个不同组合 / 144 条重复）；
  · dry-run 回显 + 空模板 `spec_schema_template.jsonc` 已记载该字段（防实现与文档漂移）。
"""
import contextlib, copy, hashlib, io, json, os, shutil, tempfile, unittest
from unittest import mock

import gen_cases as GC
import _golden_fixture as _gf
import _spec_fixture as SF

_HERE = os.path.dirname(os.path.abspath(__file__))
# 用**真样例 spec**做字节安全 pin（任务要求：别自己编算子）。Sign 的 golden 由共享 fixture 装进临时 ops_root。
_SIGN_SPEC = os.path.join(_HERE, "..", "samples", "specs", "sign.spec.json")
# Median 是仓内唯一的真 torch_parity 样例，且它的 attr 轴带 `axis_class`——低 rank 下轴类塌缩、
# 因而是「重复覆盖组合」这条记账**唯一有真数据**的见证（`_plan` 不需要 golden，故这里能直接跑）。
_MEDIAN_SPEC = os.path.join(_HERE, "..", "samples", "specs", "median.spec.json")
_SCHEMA_TMPL = os.path.join(_HERE, "spec_schema_template.jsonc")


def setUpModule():
    # 共享 fixture：建临时 ops_root + 拷 4 份样例 golden + 设 OPRUNWAY_OPS_DIR。
    # 本文件只有 dry-run 那组真需要它（`_dry_run` 要 load_golden("Sign")）；字节 pin 那组自带更小的 root。
    _gf.install()


def tearDownModule():
    _gf.uninstall()


def _load_spec(path=_SIGN_SPEC):
    """读样例 spec，并补上**测试侧**用例预算（`_spec_fixture`，仅当 spec 未声明时）。

    ⚠ `sign.spec.json` 已于 2026-08-06 删掉历史沿用的 `case_target: 50`（缺省值的化石、无覆盖矩阵
    依据，见该文件的 `_case_target_note`），对 gen_cases 而言不可跑。本文件的字节安全 pin 比的是
    「声明 legacy 前后 caseset 是否逐字节相同」，两侧同预算即可，预算取多少不影响该断言。
    """
    return SF.load(path)


def _load_median():
    """真 median spec 深拷贝（它自带 `case_target: 1344`，不需要测试侧夹具预算）。"""
    with open(_MEDIAN_SPEC, encoding="utf-8") as fh:
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


class TorchParityUnconsumedKeysTest(unittest.TestCase):
    """§5.1 · `torch_parity` 档「声明了却没人消费」的 legacy 造例键 —— **声明即 fail-closed**。

    为什么这组用例值钱：这些键写在 spec 里**一条用例都不会产**，读起来却像「我已经声明了所以已经覆盖了」。
    今天不出事只是因为 median 恰好不依赖它们；这正是本仓最忌的形状（账面声明覆盖、实际零覆盖）。
    """

    def setUp(self):
        self.spec = _load_spec()

    def test_key_table_is_pinned(self):
        """表漂移守卫：本档哪天真的开始消费某个键（如拍板要保留边界叠加），
        **把它从表里删掉是那次改动的一部分**——这条断言逼那次改动的人显式改一行，别留反向坑。"""
        self.assertEqual(
            [(where, key) for where, key, _why in GC._TORCH_PARITY_UNCONSUMED_KEYS],
            [("spec", "attr_matrix"), ("spec", "attr_axis_lengths"),
             ("spec", "allow_empty_tensor"), ("spec", "empty_axis"),
             ("precision", "value_profiles")])
        for _where, _key, why in GC._TORCH_PARITY_UNCONSUMED_KEYS:
            self.assertTrue(why.strip(), "每个键都得说清『本档为什么不消费它』")

    def test_each_key_fails_closed_under_torch_parity(self):
        """逐个键：写进 torch_parity spec → 当场 ValueError，且报错点名该键。

        ⚠ 用**真 median spec**（仓内唯一真 torch_parity 样例）而非 Sign 夹具：这五个键的取值要写得
        「合法且真实」才有意义——写个语法就不合法的值，炸的可能是别的门，证不出本门存在。
        `attr_matrix` 这条尤其：它的类型闸在 `_plan` 里比本门更早，喂非法 attr 名会被那道闸先炸掉。
        这里给的正是 median.spec.json 2026-08-06 之前真写着的那 4 组。
        """
        cases = {
            "attr_matrix": ("spec", [{"dim": None, "keepDim": False}, {"dim": 0, "keepDim": True}]),
            "attr_axis_lengths": ("spec", [{"attr": "dim", "lengths": [1]}]),
            "allow_empty_tensor": ("spec", False),
            "empty_axis": ("spec", 0),
            "value_profiles": ("precision", ["tie"]),
        }
        for key, (where, value) in cases.items():
            with self.subTest(key=key):
                parity = _load_median()
                (parity["precision"] if where == "precision" else parity)[key] = value
                with self.assertRaises(ValueError) as cm:
                    _plan_of(parity, case_target=1344)
                msg = str(cm.exception)
                self.assertIn(key, msg)
                self.assertIn("没有任何代码消费", msg)

    def test_reports_every_hit_at_once(self):
        """一次报全部命中项，不是撞一个报一个——spec 作者一趟改干净。"""
        parity = _load_median()
        parity["attr_matrix"] = [{"dim": None, "keepDim": False}]
        parity["allow_empty_tensor"] = False
        parity["precision"]["value_profiles"] = ["tie"]
        with self.assertRaises(ValueError) as cm:
            _plan_of(parity, case_target=1344)
        msg = str(cm.exception)
        for key in ("attr_matrix", "allow_empty_tensor", "value_profiles"):
            self.assertIn(key, msg)
        self.assertIn("`_` 前缀", msg, "报错要给出替代写法（`_` 前缀的纯注释键）")

    def test_legacy_still_consumes_them(self):
        """**只封 torch_parity 这一档**：legacy 侧照旧接受并消费，字节安全那条护栏不受影响。

        用 `allow_empty_tensor` 作见证（Sign 无 attr，`attr_matrix` 在这份 spec 上没法立见证）：
        legacy 下它是真被读的——`false` 会把空 Tensor 特殊场景整条关掉，用例数因此变少。
        """
        legacy_on = _plan_of(_with_profile(self.spec, "legacy"), case_target=20)[0]
        off = _with_profile(self.spec, "legacy")
        off["allow_empty_tensor"] = False
        legacy_off = _plan_of(off, case_target=20)[0]
        self.assertTrue(any(e["id_kind"] == "empty" for e in legacy_on),
                        "legacy 缺省本应铺空 Tensor 特殊场景，否则本见证是空转")
        self.assertFalse(any(e["id_kind"] == "empty" for e in legacy_off),
                         "legacy 下 allow_empty_tensor=false 必须真把空用例关掉（= 该键真被消费）")

    def test_median_sample_spec_carries_none_of_them(self):
        """真样例 spec 得是干净的——否则它一跑就炸，等于把这道门自己证伪。"""
        spec = _load_median()
        for where, key, _why in GC._TORCH_PARITY_UNCONSUMED_KEYS:
            holder = spec.get("precision", {}) if where == "precision" else spec
            self.assertNotIn(key, holder, f"median.spec.json 仍带本档不消费的 {key}")

    def test_schema_template_documents_the_rejection(self):
        """空模板必须写清（acc-spec 产 spec 只看模板；模板不写就等着 extractor 一遍遍写进去再被拒）。"""
        with open(_SCHEMA_TMPL, encoding="utf-8") as fh:
            tmpl = fh.read()
        for _where, key, _why in GC._TORCH_PARITY_UNCONSUMED_KEYS:
            self.assertIn(key, tmpl)
        self.assertIn("仅 legacy 档可写", tmpl)


class TorchParityCaseTargetAccountingTest(unittest.TestCase):
    """§6.3 · 三重记账：`矩阵大小 − |有证据的排除| == case_target == 实产数`。

    ⚠ 这**不是**把 case_target 的缺省值加回来：它仍必填、仍无缺省（`_require_case_target`），
    这里加的是「它必须与矩阵对得上」的第二重、以及「账面必须等于实产」的第三重。
    """

    def setUp(self):
        self.spec = _load_spec()

    def _excluded(self, spec, rows):
        spec["precision"]["torch_parity_matrix"]["excluded"] = rows
        return spec

    def test_ledger_reports_all_three_numbers_when_nothing_excluded(self):
        entries, meta = _plan_of(_with_parity_matrix(self.spec), case_target=12)
        led = meta["case_matrix_ledger"]
        self.assertEqual(led["full_cartesian"], 12)
        self.assertEqual(led["excluded_total"], 0)
        self.assertEqual(led["excluded"], [])
        self.assertEqual((led["expected"], led["case_target"], led["emitted"]), (12, 12, 12))
        self.assertEqual(led["emitted"], len(entries))
        # 轴是**列取值**不是列基数：`8` 说不出哪 8 个 dtype 被测了。
        self.assertEqual([ax["name"] for ax in led["axes"]],
                         ["dtype", "rank", "shape_profile", "attribute_profile"])
        self.assertEqual([ax["values"] for ax in led["axes"]][1], [1, 2])

    def test_excluded_shrinks_matrix_and_case_target_follows(self):
        """有证据的排除 → 等式右边变成 `∏ − |excluded|`，`case_target` 必须跟着改。"""
        spec = self._excluded(_with_parity_matrix(self.spec), [
            {"combo": {"shape_profile": "large"},
             "reason": "测试夹具：假装该规模档超出本轮 golden 预算",
             "evidence": "test fixture, not a real acceptance claim"},
        ])
        spec["precision"]["case_target"] = 8            # 12 − (2 dtype × 2 rank × 1 shape × 1 attr)=4
        entries, meta = _plan_of(spec, case_target=8)
        self.assertEqual(len(entries), 8)
        self.assertNotIn((262144,), {tuple(e["shape"]) for e in entries})
        led = meta["case_matrix_ledger"]
        self.assertEqual((led["full_cartesian"], led["excluded_total"], led["expected"]), (12, 4, 8))
        self.assertEqual(led["excluded"][0]["combos_excluded"], 4)

    def test_old_case_target_now_fails_closed(self):
        """排除项落地后还照抄旧的 `∏`（12）→ 当场炸。等式右边变了，账面数必须跟着变。"""
        spec = self._excluded(_with_parity_matrix(self.spec), [
            {"combo": {"shape_profile": "large"}, "reason": "r", "evidence": "e"}])
        with self.assertRaisesRegex(ValueError, "必须相等"):
            _plan_of(spec, case_target=12)

    def test_excluded_schema_fail_closed(self):
        """受控词表 + 缩水必须留痕：轴名、取值、reason/evidence、重叠、空表一律炸。"""
        bad_rows = {
            "空表": [],
            "非列表": {},
            "缺 evidence": [{"combo": {"rank": 1}, "reason": "r"}],
            "空 reason": [{"combo": {"rank": 1}, "reason": "  ", "evidence": "e"}],
            "空 combo": [{"combo": {}, "reason": "r", "evidence": "e"}],
            "未知轴名": [{"combo": {"layout": "nchw"}, "reason": "r", "evidence": "e"}],
            "取值不在矩阵里": [{"combo": {"rank": 7}, "reason": "r", "evidence": "e"}],
            "shape 档名不存在": [{"combo": {"shape_profile": "huge"}, "reason": "r", "evidence": "e"}],
        }
        for label, rows in bad_rows.items():
            with self.subTest(case=label):
                spec = self._excluded(_with_parity_matrix(self.spec), rows)
                with self.assertRaises(ValueError):
                    _plan_of(spec, case_target=12)

    def test_overlapping_exclusions_fail_closed(self):
        """两条排除项相交 → 当场炸，**且必须点名「重叠」**。

        ⚠ 这条一定要按 union 后的 `case_target` 来测（12 − |{rank1} ∪ {large}| = 12 − 8 = 4），
        否则测不到重叠检测本身：`combos` 是 set，重叠会被 `|=` 悄悄吞掉，`expected` 照样算对，
        于是 `case_target` 那道门会替它炸——**测试绿了，检测其实没了**（实测：把重叠检测删掉，
        用 case_target=12 的写法照样全绿）。真正被伪造的是**账本**：
        `excluded` 逐条的 `combos_excluded` 之和 = 6+4 = 10，而实际只排除了 8 个组合。
        """
        spec = self._excluded(_with_parity_matrix(self.spec), [
            {"combo": {"rank": 1}, "reason": "r", "evidence": "e"},
            {"combo": {"shape_profile": "large"}, "reason": "r", "evidence": "e"}])
        with self.assertRaisesRegex(ValueError, "重叠"):
            _plan_of(spec, case_target=4)

    def test_multiple_disjoint_exclusions_keep_the_books_consistent(self):
        """互不相交的多条排除：逐条 `combos_excluded` 之和必须等于 `excluded_total`（账本自洽）。"""
        spec = self._excluded(_with_parity_matrix(self.spec), [
            {"combo": {"rank": 1, "shape_profile": "large"}, "reason": "r1", "evidence": "e1"},
            {"combo": {"rank": 2, "shape_profile": "large"}, "reason": "r2", "evidence": "e2"}])
        entries, meta = _plan_of(spec, case_target=8)
        led = meta["case_matrix_ledger"]
        self.assertEqual(sum(r["combos_excluded"] for r in led["excluded"]), led["excluded_total"])
        self.assertEqual((led["excluded_total"], led["expected"], len(entries)), (4, 8, 8))
        self.assertEqual([r["reason"] for r in led["excluded"]], ["r1", "r2"])

    def test_excluding_everything_fails_closed(self):
        """把整个矩阵逐 dtype 排空 → 零用例空跑不能冒充验收。"""
        spec = _with_parity_matrix(self.spec)
        dtypes = next(p for p in spec["params"] if p["name"] == "self")["dtype"]
        self._excluded(spec, [{"combo": {"dtype": d}, "reason": "r", "evidence": "e"}
                              for d in dtypes])
        with self.assertRaisesRegex(ValueError, "一条用例都不剩"):
            _plan_of(spec, case_target=12)

    def test_unknown_matrix_key_still_fails_closed(self):
        """`excluded` 进白名单了，别顺手把别的错别字也放进来。"""
        spec = _with_parity_matrix(self.spec)
        spec["precision"]["torch_parity_matrix"]["exclude"] = []    # 少个 d
        with self.assertRaisesRegex(ValueError, "未知字段"):
            _plan_of(spec, case_target=12)

    def test_emitted_must_equal_the_books(self):
        """第三重（**实产数**）真的是一道门，不是注释。

        造法：把 `_torch_parity_excluded` 换成返回一个**匹配不到任何真实组合**的幽灵排除项——
        账面因此记 `12 − 1 = 11`，而生成循环一条都跳不过、照产 12。这正是第三重要逮的那种漂移
        （账面与实产靠「完整笛卡尔不采样」隐式对齐，一旦有排除项那条隐式保证就断了）。
        ⚠ 没有这道门时本用例会静静地通过、产出一份 emitted=12 却自称 11 的 caseset。"""
        phantom = frozenset({("no_such_dtype", 1, "small", "attr_00")})

        def ghost(_cfg, _axes):
            return phantom, [{"combo": {"dtype": "no_such_dtype"}, "reason": "r",
                              "evidence": "e", "combos_excluded": 1}]

        with mock.patch.object(GC, "_torch_parity_excluded", ghost):
            with self.assertRaisesRegex(ValueError, "实产"):
                _plan_of(_with_parity_matrix(self.spec), case_target=11)


class TorchParityCoverageStrengthTest(unittest.TestCase):
    """§7.4 · `coverage_strength` 必须如实：完整笛卡尔 ≠ N 个**不同**覆盖组合。

    报告是**逐字引**这句话的，所以这句话本身就是账。
    """

    def test_no_overclaim_wording(self):
        _entries, meta = _plan_of(_with_parity_matrix(_load_spec()), case_target=12)
        strength = meta["coverage_strength"]
        self.assertIn("complete_cartesian", strength)
        self.assertNotIn("全覆盖", strength, "「全覆盖」是过强表述——低 rank 下会有重复覆盖组合")
        self.assertIn("12", strength)

    def test_wording_switches_when_excluded(self):
        spec = _with_parity_matrix(_load_spec())
        spec["precision"]["torch_parity_matrix"]["excluded"] = [
            {"combo": {"shape_profile": "large"}, "reason": "r", "evidence": "e"}]
        _entries, meta = _plan_of(spec, case_target=8)
        strength = meta["coverage_strength"]
        self.assertIn("cartesian_minus_excluded", strength)
        self.assertNotIn("complete_cartesian", strength, "有排除还叫 complete_cartesian 就是假话")

    def test_median_duplicate_combinations_are_accounted(self):
        """真样例实数：1344 例里 144 例是**同一 (dtype, 实际 shape, 解析后 attrs) 组合的额外样本**。

        算术（逐 rank 解 `_resolve_axis_class`）：rank1 first=middle=last=0 → 6 个 by-dim profile
        只剩 2 个不同 → 4 条重复；rank2 first=middle=0 → 再 2 条；rank≥3 起互不相同。
        每个 (dtype, 规模档) 单元格 6 条 × 8 dtype × 3 档 = 144，故不同组合数 = 1200。
        ⚠ 本轮**不删**这 144 条（删了三重记账的等式当场破，属待拍板项），只如实记账。"""
        entries, meta = _plan_of(_load_median(), case_target=1344)
        self.assertEqual(len(entries), 1344)
        led = meta["case_matrix_ledger"]
        self.assertEqual(led["full_cartesian"], 1344)
        self.assertEqual(led["distinct_combinations"], 1200)
        self.assertEqual(led["duplicate_cases"], 144)
        strength = meta["coverage_strength"]
        self.assertIn("1200", strength)
        self.assertIn("144", strength)
        self.assertNotIn("全覆盖", strength)

    def test_ledger_only_exists_on_torch_parity(self):
        """账本只在本档出现 → legacy 侧 caseset / dry-run 一个键都不多（字节 pin 不破）。"""
        _entries, legacy_meta = _plan_of(_load_spec(), case_target=20)
        self.assertNotIn("case_matrix_ledger", legacy_meta)


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
            cls.work_parity = tempfile.mkdtemp(prefix="cp_parity_")
            cls.cs_plain = GC.gen_cases(spec, cls.work_plain)                   # 未声明（= 现有 8 份 spec 的形态）
            cls.cs_legacy = GC.gen_cases(_with_profile(spec, "legacy"), cls.work_legacy)
            # 三重记账账本得真的落到 **caseset**（报告读的是产物，不是 `_plan` 的返回值）。
            cls.cs_parity = GC.gen_cases(_with_parity_matrix(spec), cls.work_parity)
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
        for attr in ("ops_root", "work_plain", "work_legacy", "work_parity"):
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

    def test_case_matrix_ledger_lands_in_the_caseset(self):
        """账本必须落进 **caseset**（报告与门读产物，不读 `_plan` 的返回值）；
        非 torch_parity 档一个键都不多——`ExistingOpsByteIdenticalTest` 的 sha256 pin 靠这条。"""
        self.assertNotIn("case_matrix_ledger", self.cs_plain)
        self.assertNotIn("case_matrix_ledger", self.cs_legacy)
        led = self.cs_parity["case_matrix_ledger"]
        self.assertEqual((led["full_cartesian"], led["excluded_total"]), (12, 0))
        self.assertEqual({led["expected"], led["case_target"], led["emitted"],
                          len(self.cs_parity["cases"])}, {12})
        self.assertIn("complete_cartesian", self.cs_parity["coverage_strength"])

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
