#!/usr/bin/env python3
"""`source_provenance` 的门禁测试。

这个模块决定「哪条取源路由能放行」，是一道**安全门**，所以本文件的重点不是覆盖率，
而是那几条一旦失守就会让验收结论失真的红线：

1. `git_pr` 档仍必须两侧都有 40 位 head_sha 且逐字相等——别的形态的引入不得顺手放松它；
2. **声明 `local_source`（本地源码）如愿实得 → 正常放行，不需要任何环境变量**：
   `head_sha=null` 是这条形态的正确值，不是缺陷，也不挂降级账；
3. **声明 `git_pr` 却只实得本地快照 → 仍是降级**：默认拒，必须编排层指名道姓授权；
   泛真值（`1`/`true`/`yes`）不算授权。老事实包（未声明形态）走的就是这一条；
4. 授权只解除「必须绑 PR head」这一条：谁在 head_sha 里填了合成的 40 位 hex，**当场报错**
   （律令 5.8——merkle 只证字节，不证 commit）；
5. 声明 `local_source` 却带着上游 commit → 一律 fail-closed（声明与实得不是同一件事）；
6. 两个 merkle 只有在**同 scope** 时才可比，scope 对不上一律 fail-closed。
"""
import unittest

import source_provenance as SP


def _complete_source(head="a" * 40, **extra):
    pr = {"provenance_kind": SP.PROVENANCE_GIT_PR, "head_sha": head}
    pr.update(extra)
    return {"completeness": {"status": SP.TIER_COMPLETE}, "pr": pr}


def _complete_facts(head="a" * 40, **extra):
    """`fetch_pr` 产的 pr_facts 形态（provenance_kind 恒在）。"""
    facts = {"provenance_kind": SP.PROVENANCE_GIT_PR, "head_sha": head}
    facts.update(extra)
    return facts


def _snapshot_source(merkle="b" * 64, scope="gaussian_blur", head=None, **extra):
    """**未声明形态**的老事实包 + 本地快照 → 降级路由（按 `git_pr` 声明对待）。"""
    pr = {"provenance_kind": SP.PROVENANCE_LOCAL_SNAPSHOT, "head_sha": head,
          "snapshot_merkle_sha256": merkle, "snapshot_scope": scope}
    pr.update(extra)
    return {"completeness": {"status": SP.TIER_SNAPSHOT_ONLY}, "pr": pr}


def _snapshot_facts(merkle="b" * 64, scope="gaussian_blur", head=None, **extra):
    """`scan_pr_snapshot` 产的 pr_facts 形态（kind/head/merkle/scope 四项恒在）。"""
    facts = {"provenance_kind": SP.PROVENANCE_LOCAL_SNAPSHOT, "head_sha": head,
             "snapshot_merkle_sha256": merkle, "snapshot_scope": scope}
    facts.update(extra)
    return facts


def _local_source(**over):
    """**声明 local_source** 且如愿实得的事实包（今天 `--pr-snapshot` 产的形态）。"""
    src = _snapshot_source(**over)
    src["completeness"]["status"] = SP.TIER_COMPLETE
    src[SP.DECLARED_FORM_KEY] = SP.FORM_LOCAL_SOURCE
    return src


def _local_source_facts(**over):
    facts = _snapshot_facts(**over)
    facts[SP.DECLARED_FORM_KEY] = SP.FORM_LOCAL_SOURCE
    return facts


def _env(value=None):
    """只喂授权开关的假 getenv；其余键一律 None（免得测试依赖真实环境）。"""
    return lambda k, default=None: value if k == SP.AUTHORIZE_ENV else default


class BindCompleteTest(unittest.TestCase):
    def test_happy_path_returns_head_and_no_degradation(self):
        b, deg = SP.bind(_complete_source(), _complete_facts(), getenv=_env())
        self.assertEqual(b["pr_head_sha"], "a" * 40)
        self.assertEqual(b["provenance_kind"], SP.PROVENANCE_GIT_PR)
        self.assertEqual(deg, [])
        # 三个键恒在：省略会让人分不清「没这回事」和「工具忘了记」
        self.assertIn("snapshot_merkle_sha256", b)

    def test_head_mismatch_between_two_fact_packs_is_rejected(self):
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(_complete_source(), _complete_facts(head="c" * 40), getenv=_env())

    def test_non_hex40_head_is_rejected(self):
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(_complete_source(head="not-a-sha"), _complete_facts(head="not-a-sha"),
                    getenv=_env())

    def test_complete_tier_never_needs_authorization(self):
        """降级开关不该反过来影响正常档——没设开关也必须照常放行。"""
        b, deg = SP.bind(_complete_source(), _complete_facts(), getenv=_env(None))
        self.assertEqual(deg, [])
        self.assertEqual(b["pr_head_sha"], "a" * 40)

    # —— 以下是 fail-open 负例（审计 C4 / #38 / #39 / #40）——

    def test_both_sides_missing_head_key_is_not_a_pass(self):
        """`None == None` 曾让「两边都没写 head_sha」等价于「校验通过」。"""
        src = _complete_source()
        del src["pr"]["head_sha"]
        facts = _complete_facts()
        del facts["head_sha"]
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(src, facts, getenv=_env())

    def test_explicit_null_head_is_not_a_complete_binding(self):
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(_complete_source(head=None), _complete_facts(head=None), getenv=_env())

    def test_stringifiable_non_string_head_is_rejected(self):
        """40 位十进制整数不是 commit SHA——旧实现 `str(source_head)` 会把它放过。"""
        fake = int("1" * 40)
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(_complete_source(head=fake), _complete_facts(head=fake), getenv=_env())

    def test_missing_provenance_kind_is_not_defaulted_to_git_pr(self):
        src = _complete_source()
        del src["pr"]["provenance_kind"]
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(src, _complete_facts(), getenv=_env())

    def test_pr_facts_must_declare_the_same_provenance_kind(self):
        facts = _complete_facts()
        del facts["provenance_kind"]
        with self.assertRaises(SP.ProvenanceError):        # 缺
            SP.bind(_complete_source(), facts, getenv=_env())
        with self.assertRaises(SP.ProvenanceError):        # 有但不同
            SP.bind(_complete_source(),
                    _complete_facts(provenance_kind=SP.PROVENANCE_LOCAL_SNAPSHOT),
                    getenv=_env())

    def test_tier_and_kind_must_agree(self):
        """`complete` 档不接受 local_snapshot 形态，反之亦然。"""
        src = _complete_source()
        src["pr"]["provenance_kind"] = SP.PROVENANCE_LOCAL_SNAPSHOT
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(src, _complete_facts(provenance_kind=SP.PROVENANCE_LOCAL_SNAPSHOT),
                    getenv=_env())


class BindLocalSourceTest(unittest.TestCase):
    """红线 2/5：本地源码是**一等输入形态**，声明即所得就正常放行；声明与实得不符则拒。

    这一组是本次重构的核心：改动前「正常的本地代码验收」每次都要人为设一个环境变量解锁，
    于是「降级」这个信号被日常噪音淹没。现在只有「本该绑 PR head 却没绑」才叫降级。
    """

    def test_declared_local_source_passes_without_any_authorization(self):
        b, deg = SP.bind(_local_source(), _local_source_facts(), getenv=_env(None))
        self.assertEqual(deg, [], "声明即所得不是降级，不得挂降级账")
        self.assertIsNone(b["pr_head_sha"], "null 是这条形态的正确值")
        self.assertEqual(b[SP.DECLARED_FORM_KEY], SP.FORM_LOCAL_SOURCE)
        self.assertEqual(b["snapshot_merkle_sha256"], "b" * 64)
        self.assertEqual(b["snapshot_scope"], "gaussian_blur")

    def test_neutral_form_facts_are_carried_but_are_not_degradations(self):
        """「没有上游 commit」必须原样带给报告，但它落的是中性事实那一栏。"""
        b, deg = SP.bind(_local_source(), _local_source_facts(), getenv=_env(None))
        self.assertEqual(SP.form_facts(b), list(SP.LOCAL_SOURCE_FORM_FACTS))
        self.assertNotIn(SP.DEGRADATION_PR_HEAD_UNBOUND, SP.form_facts(b))
        self.assertEqual(deg, [])

    def test_local_source_still_requires_the_full_byte_identity(self):
        """放行的是「形态」，不是「事实」：merkle/scope 一条不放松。"""
        for over in ({"merkle": "not-hex"}, {"merkle": "b" * 63}):
            with self.subTest(**over):
                with self.assertRaises(SP.ProvenanceError):
                    SP.bind(_local_source(**over), _local_source_facts(**over),
                            getenv=_env(None))
        src = _local_source()
        del src["pr"]["snapshot_scope"]
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(src, _local_source_facts(), getenv=_env(None))

    def test_local_source_may_not_carry_a_synthesized_head(self):
        """律令 5.8 在新档位下同样成立：本地源码没有 commit，编一个就当场拒。"""
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(_local_source(head="d" * 40), _local_source_facts(),
                    getenv=_env(None))

    def test_declared_local_source_with_a_real_pr_is_rejected(self):
        """声明本地源码、实得却是绑定上游 commit 的取材 → 声明与实得不是同一件事。"""
        src = _complete_source()
        src[SP.DECLARED_FORM_KEY] = SP.FORM_LOCAL_SOURCE
        facts = _complete_facts(**{SP.DECLARED_FORM_KEY: SP.FORM_LOCAL_SOURCE})
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(src, facts, getenv=_env(None))

    def test_declared_git_pr_pair_is_unchanged(self):
        src = _complete_source()
        src[SP.DECLARED_FORM_KEY] = SP.FORM_GIT_PR
        b, deg = SP.bind(src, _complete_facts(**{SP.DECLARED_FORM_KEY: SP.FORM_GIT_PR}),
                         getenv=_env(None))
        self.assertEqual(deg, [])
        self.assertEqual(b["pr_head_sha"], "a" * 40)
        self.assertEqual(SP.form_facts(b), [])

    def test_one_sided_declaration_fails_closed(self):
        """两份事实包必须出自同一次取材：只有一侧声明形态 → 拒。"""
        src = _local_source()
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(src, _snapshot_facts(), getenv=_env(None))   # pr_facts 没声明
        facts = _local_source_facts()
        plain = _snapshot_source()
        plain["completeness"]["status"] = SP.TIER_COMPLETE
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(plain, facts, getenv=_env(None))             # source 没声明

    def test_declarations_must_agree(self):
        facts = _snapshot_facts()
        facts[SP.DECLARED_FORM_KEY] = SP.FORM_GIT_PR     # 两侧声明不同 → 拒
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(_local_source(), facts, getenv=_env(None))

    def test_out_of_vocabulary_declaration_is_rejected(self):
        """词表外的声明**两侧各自**都要挡住，不能只查一侧。"""
        bad = "snapshot_only"
        src = _local_source()
        src[SP.DECLARED_FORM_KEY] = bad
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(src, _local_source_facts(), getenv=_env(None))
        facts = _snapshot_facts()
        facts[SP.DECLARED_FORM_KEY] = bad
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(_local_source(), facts, getenv=_env(None))

    def test_local_source_tier_must_be_complete(self):
        """声明即所得的那一档就是 `complete`；事实包自称 snapshot_only 就说明它认为自己降级了。"""
        src = _local_source()
        src["completeness"]["status"] = SP.TIER_SNAPSHOT_ONLY
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(src, _local_source_facts(), getenv=_env(None))


class BindSnapshotAuthorizationTest(unittest.TestCase):
    """红线 3/4：**降级**路由（声明要测 PR、只拿到本地快照，含未声明的老事实包）——
    默认拒、指名道姓才放行、授权不等于可以编 commit id。"""

    def test_unauthorized_snapshot_is_rejected(self):
        with self.assertRaises(SP.ProvenanceError) as cm:
            SP.bind(_snapshot_source(), _snapshot_facts(), getenv=_env(None))
        self.assertIn(SP.AUTHORIZE_ENV, str(cm.exception))

    def test_truthy_value_is_not_authorization(self):
        """`1` / `true` / `yes` 都**不算**授权：授权必须逐字等于被授权的 kind，
        否则将来新增别的降级档会被同一个泛真值一起放行。"""
        for truthy in ("1", "true", "TRUE", "yes", "on"):
            with self.subTest(value=truthy):
                with self.assertRaises(SP.ProvenanceError):
                    SP.bind(_snapshot_source(), _snapshot_facts(), getenv=_env(truthy))

    def test_authorizing_a_different_kind_does_not_unlock_this_one(self):
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(_snapshot_source(), _snapshot_facts(), getenv=_env("some_other_kind"))

    def test_authorized_snapshot_passes_and_books_degradations(self):
        b, deg = SP.bind(_snapshot_source(), _snapshot_facts(),
                         getenv=_env(SP.PROVENANCE_LOCAL_SNAPSHOT))
        self.assertIsNone(b["pr_head_sha"])
        self.assertEqual(b["snapshot_merkle_sha256"], "b" * 64)
        self.assertEqual(b["snapshot_scope"], "gaussian_blur")
        self.assertIn(SP.DEGRADATION_PR_HEAD_UNBOUND, deg)

    def test_synthesized_head_sha_is_rejected_even_when_authorized(self):
        """**律令 5.8 的落点**：授权只解除「必须绑 PR head」，不解除「不许编 commit id」。
        两侧任一处填了 40 位 hex 都必须当场报错——否则合成值会一路流进验收结论。"""
        fake = "d" * 40
        with self.assertRaises(SP.ProvenanceError):        # source 侧编
            SP.bind(_snapshot_source(head=fake), _snapshot_facts(),
                    getenv=_env(SP.PROVENANCE_LOCAL_SNAPSHOT))
        with self.assertRaises(SP.ProvenanceError):        # pr_facts 侧编
            SP.bind(_snapshot_source(), _snapshot_facts(head=fake),
                    getenv=_env(SP.PROVENANCE_LOCAL_SNAPSHOT))

    def test_missing_head_key_is_not_an_explicit_null(self):
        """审计 C5：缺字段被当成显式 null，等于让「事实包没写」自动过门。"""
        auth = _env(SP.PROVENANCE_LOCAL_SNAPSHOT)
        src = _snapshot_source()
        del src["pr"]["head_sha"]
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(src, _snapshot_facts(), getenv=auth)
        facts = _snapshot_facts()
        del facts["head_sha"]
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(_snapshot_source(), facts, getenv=auth)


class BindSnapshotFactsTest(unittest.TestCase):
    """授权之后，事实本身一条都不放松。"""

    AUTH = None

    def setUp(self):
        self.AUTH = _env(SP.PROVENANCE_LOCAL_SNAPSHOT)

    def test_merkle_must_be_hex64(self):
        for bad in (None, "", "xyz", "b" * 63, 12345):
            with self.subTest(merkle=bad):
                with self.assertRaises(SP.ProvenanceError):
                    SP.bind(_snapshot_source(merkle=bad), _snapshot_facts(merkle=bad),
                            getenv=self.AUTH)

    def test_merkle_must_agree_across_fact_packs(self):
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(_snapshot_source(merkle="b" * 64), _snapshot_facts(merkle="e" * 64),
                    getenv=self.AUTH)

    def test_missing_merkle_key_is_rejected(self):
        facts = _snapshot_facts()
        del facts["snapshot_merkle_sha256"]
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(_snapshot_source(), facts, getenv=self.AUTH)

    def test_missing_scope_is_rejected(self):
        src = _snapshot_source()
        del src["pr"]["snapshot_scope"]
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(src, _snapshot_facts(), getenv=self.AUTH)

    def test_scope_must_agree_across_fact_packs(self):
        """两个 merkle 的覆盖范围对不上就不可比——只读 source 侧 scope 是漏了一半对账。"""
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(_snapshot_source(scope="gaussian_blur"), _snapshot_facts(scope=""),
                    getenv=self.AUTH)
        facts = _snapshot_facts()
        del facts["snapshot_scope"]
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(_snapshot_source(), facts, getenv=self.AUTH)

    def test_blocked_tier_is_never_authorizable(self):
        src = _snapshot_source()
        src["completeness"]["status"] = "blocked"
        with self.assertRaises(SP.ProvenanceError):
            SP.bind(src, _snapshot_facts(), getenv=self.AUTH)

    def test_malformed_fact_packs_fail_closed(self):
        for src, facts in (("not-a-dict", {}), ({}, "not-a-dict"),
                           ({"completeness": {"status": "complete"}}, {}),   # 缺 pr
                           ({"pr": {}}, {})):                                # 缺 completeness
            with self.subTest(src=src):
                with self.assertRaises(SP.ProvenanceError):
                    SP.bind(src, facts, getenv=self.AUTH)


class ConfigAgainstPreflightTest(unittest.TestCase):
    def test_kind_mismatch_is_rejected(self):
        with self.assertRaises(SP.ProvenanceError):
            SP.check_config_against_preflight(
                {"source_mode": "local_snapshot"},
                {"provenance_kind": SP.PROVENANCE_GIT_PR, "pr_head_sha": "a" * 40})

    def test_unknown_source_mode_is_rejected(self):
        with self.assertRaises(SP.ProvenanceError):
            SP.check_config_against_preflight({"source_mode": "carrier-pigeon"}, {})

    def test_missing_source_mode_defaults_to_git_fetch(self):
        """既有 spec 不带 source_mode——缺省必须仍是 git 通路，否则是静默行为变更。"""
        SP.check_config_against_preflight(
            {"head_sha": "a" * 40},
            {"provenance_kind": SP.PROVENANCE_GIT_PR, "pr_head_sha": "a" * 40})

    def test_git_head_mismatch_is_rejected(self):
        with self.assertRaises(SP.ProvenanceError):
            SP.check_config_against_preflight(
                {"source_mode": "git_fetch", "head_sha": "a" * 40},
                {"provenance_kind": SP.PROVENANCE_GIT_PR, "pr_head_sha": "c" * 40})

    def test_snapshot_path_must_not_carry_a_bound_head(self):
        with self.assertRaises(SP.ProvenanceError):
            SP.check_config_against_preflight(
                {"source_mode": "local_snapshot"},
                {"provenance_kind": SP.PROVENANCE_LOCAL_SNAPSHOT, "pr_head_sha": "a" * 40})

    def test_snapshot_happy_path(self):
        SP.check_config_against_preflight(
            {"source_mode": "local_snapshot"},
            {"provenance_kind": SP.PROVENANCE_LOCAL_SNAPSHOT, "pr_head_sha": None})

    # —— fail-open 负例（审计 #43）：畸形 bindings 不得靠缺键过门 ——

    def test_git_path_rejects_bindings_without_pr_head_key(self):
        with self.assertRaises(SP.ProvenanceError):
            SP.check_config_against_preflight(
                {"source_mode": "git_fetch", "head_sha": "a" * 40},
                {"provenance_kind": SP.PROVENANCE_GIT_PR})

    def test_git_path_rejects_two_missing_heads_comparing_equal(self):
        """cfg 与 bindings 都没有 head 时，旧实现靠 `None == None` 判「一致」。"""
        with self.assertRaises(SP.ProvenanceError):
            SP.check_config_against_preflight(
                {"source_mode": "git_fetch"}, {"provenance_kind": SP.PROVENANCE_GIT_PR})

    def test_git_path_rejects_non_hex40_head(self):
        with self.assertRaises(SP.ProvenanceError):
            SP.check_config_against_preflight(
                {"source_mode": "git_fetch", "head_sha": "HEAD"},
                {"provenance_kind": SP.PROVENANCE_GIT_PR, "pr_head_sha": "HEAD"})

    def test_snapshot_path_requires_explicit_null_pr_head(self):
        with self.assertRaises(SP.ProvenanceError):
            SP.check_config_against_preflight(
                {"source_mode": "local_snapshot"},
                {"provenance_kind": SP.PROVENANCE_LOCAL_SNAPSHOT})

    def test_non_dict_bindings_fail_closed(self):
        with self.assertRaises(SP.ProvenanceError):
            SP.check_config_against_preflight({"source_mode": "local_snapshot"}, None)

    def test_declared_local_source_bindings_pass(self):
        SP.check_config_against_preflight(
            {"source_mode": "local_snapshot"},
            {"provenance_kind": SP.PROVENANCE_LOCAL_SNAPSHOT, "pr_head_sha": None,
             SP.DECLARED_FORM_KEY: SP.FORM_LOCAL_SOURCE})

    def test_declared_local_source_against_git_bindings_is_rejected(self):
        """CP-C0 之后每道门都重判一次「声明 × 实得」，防中途有人换掉其中一半。"""
        with self.assertRaises(SP.ProvenanceError):
            SP.check_config_against_preflight(
                {"source_mode": "git_fetch", "head_sha": "a" * 40},
                {"provenance_kind": SP.PROVENANCE_GIT_PR, "pr_head_sha": "a" * 40,
                 SP.DECLARED_FORM_KEY: SP.FORM_LOCAL_SOURCE})

    def test_out_of_vocabulary_declared_form_in_bindings_is_rejected(self):
        with self.assertRaises(SP.ProvenanceError):
            SP.check_config_against_preflight(
                {"source_mode": "local_snapshot"},
                {"provenance_kind": SP.PROVENANCE_LOCAL_SNAPSHOT, "pr_head_sha": None,
                 SP.DECLARED_FORM_KEY: "whatever"})


class BuildIdentityTest(unittest.TestCase):
    GIT_CFG = {"source_mode": "git_fetch", "head_sha": "a" * 40}
    GIT_BIND = {"provenance_kind": SP.PROVENANCE_GIT_PR, "pr_head_sha": "a" * 40}

    SNAP_CFG = {"source_mode": "local_snapshot", "snapshot_sha256": "b" * 64}
    SNAP_BIND = {"provenance_kind": SP.PROVENANCE_LOCAL_SNAPSHOT, "pr_head_sha": None,
                 "snapshot_merkle_sha256": "f" * 64, "snapshot_scope": "gaussian_blur"}

    def _snap_prov(self, **over):
        p = {"provenance_kind": "local_snapshot", "head_sha": None,
             "snapshot_sha256": "b" * 64, "snapshot_subtree_sha256": "f" * 64,
             "snapshot_subtree_scope": "gaussian_blur"}
        p.update(over)
        return p

    def test_git_happy_path_unchanged(self):
        self.assertEqual([], SP.check_build_identity(
            {"provenance_kind": "git_fetch", "head_sha": "a" * 40}, self.GIT_CFG, self.GIT_BIND))

    def test_git_build_head_must_match_config_and_preflight(self):
        with self.assertRaises(SP.ProvenanceError):
            SP.check_build_identity({"provenance_kind": "git_fetch", "head_sha": "c" * 40},
                                    self.GIT_CFG, self.GIT_BIND)

    def test_snapshot_happy_path_books_head_unbound(self):
        """**未声明形态**的老 bindings：仍按「本该绑却没绑」挂账，与改动前逐字相同。"""
        deg = SP.check_build_identity(self._snap_prov(), self.SNAP_CFG, self.SNAP_BIND)
        self.assertEqual([SP.DEGRADATION_PR_HEAD_UNBOUND], deg)

    def test_declared_local_source_books_no_degradation(self):
        """声明即所得 → 一条降级都不挂；字节对账仍是同一套硬校。"""
        bind = dict(self.SNAP_BIND, **{SP.DECLARED_FORM_KEY: SP.FORM_LOCAL_SOURCE})
        self.assertEqual([], SP.check_build_identity(
            self._snap_prov(), self.SNAP_CFG, bind))

    def test_declared_local_source_still_checks_subtree_merkle(self):
        bind = dict(self.SNAP_BIND, **{SP.DECLARED_FORM_KEY: SP.FORM_LOCAL_SOURCE})
        with self.assertRaises(SP.ProvenanceError):
            SP.check_build_identity(self._snap_prov(snapshot_subtree_sha256="9" * 64),
                                    self.SNAP_CFG, bind)

    def test_declared_git_pr_over_snapshot_still_books_degradation(self):
        bind = dict(self.SNAP_BIND, **{SP.DECLARED_FORM_KEY: SP.FORM_GIT_PR})
        self.assertEqual([SP.DEGRADATION_PR_HEAD_UNBOUND], SP.check_build_identity(
            self._snap_prov(), self.SNAP_CFG, bind))

    def test_snapshot_scope_mismatch_fails_closed(self):
        """两个 merkle 只有同 scope 才可比——整仓摘要与算子子树摘要不是一回事，
        不比对 scope 就会拿两个必然不等的值互相"验证"，然后永远红或永远绿。"""
        with self.assertRaises(SP.ProvenanceError) as cm:
            SP.check_build_identity(self._snap_prov(snapshot_subtree_scope=""),
                                    self.SNAP_CFG, self.SNAP_BIND)
        self.assertIn("范围", str(cm.exception))

    def test_snapshot_subtree_merkle_mismatch_is_rejected(self):
        with self.assertRaises(SP.ProvenanceError):
            SP.check_build_identity(self._snap_prov(snapshot_subtree_sha256="9" * 64),
                                    self.SNAP_CFG, self.SNAP_BIND)

    def test_snapshot_whole_repo_merkle_must_match_config(self):
        with self.assertRaises(SP.ProvenanceError):
            SP.check_build_identity(self._snap_prov(snapshot_sha256="9" * 64),
                                    self.SNAP_CFG, self.SNAP_BIND)

    def test_snapshot_non_null_head_is_rejected(self):
        with self.assertRaises(SP.ProvenanceError):
            SP.check_build_identity(self._snap_prov(head_sha="d" * 40),
                                    self.SNAP_CFG, self.SNAP_BIND)

    def test_build_kind_must_match_config(self):
        with self.assertRaises(SP.ProvenanceError):
            SP.check_build_identity(self._snap_prov(provenance_kind="git_fetch"),
                                    self.SNAP_CFG, self.SNAP_BIND)

    def test_missing_subtree_scope_is_rejected(self):
        p = self._snap_prov()
        del p["snapshot_subtree_scope"]
        with self.assertRaises(SP.ProvenanceError):
            SP.check_build_identity(p, self.SNAP_CFG, self.SNAP_BIND)

    def test_non_dict_provenance_fails_closed(self):
        with self.assertRaises(SP.ProvenanceError):
            SP.check_build_identity(None, self.SNAP_CFG, self.SNAP_BIND)

    # —— fail-open 负例（审计 C1 对应的 #36 / #37）——

    def test_git_all_three_heads_missing_is_not_a_match(self):
        """三方都没有 head_sha 时，`None == None == None` 曾被判成「身份一致」。"""
        with self.assertRaises(SP.ProvenanceError):
            SP.check_build_identity({"provenance_kind": "git_fetch"},
                                    {"source_mode": "git_fetch"},
                                    {"provenance_kind": SP.PROVENANCE_GIT_PR})

    def test_git_non_hex40_head_is_rejected_even_when_all_three_agree(self):
        with self.assertRaises(SP.ProvenanceError):
            SP.check_build_identity({"provenance_kind": "git_fetch", "head_sha": "HEAD"},
                                    {"source_mode": "git_fetch", "head_sha": "HEAD"},
                                    {"provenance_kind": SP.PROVENANCE_GIT_PR,
                                     "pr_head_sha": "HEAD"})

    def test_snapshot_missing_head_key_is_not_explicit_null(self):
        p = self._snap_prov()
        del p["head_sha"]
        with self.assertRaises(SP.ProvenanceError):
            SP.check_build_identity(p, self.SNAP_CFG, self.SNAP_BIND)

    def test_snapshot_digests_must_be_present_and_hex64(self):
        for key in ("snapshot_sha256", "snapshot_subtree_sha256"):
            with self.subTest(missing=key):
                p = self._snap_prov()
                del p[key]
                with self.assertRaises(SP.ProvenanceError):
                    SP.check_build_identity(p, self.SNAP_CFG, self.SNAP_BIND)
        # 两侧都缺 whole-tree 摘要 → 旧实现靠 None == None 放行
        with self.assertRaises(SP.ProvenanceError):
            SP.check_build_identity(self._snap_prov(snapshot_sha256=None),
                                    {"source_mode": "local_snapshot", "snapshot_sha256": None},
                                    self.SNAP_BIND)

    def test_snapshot_bindings_missing_merkle_is_rejected(self):
        bind = dict(self.SNAP_BIND)
        del bind["snapshot_merkle_sha256"]
        with self.assertRaises(SP.ProvenanceError):
            SP.check_build_identity(self._snap_prov(), self.SNAP_CFG, bind)

    def test_snapshot_bindings_missing_scope_is_rejected(self):
        bind = dict(self.SNAP_BIND)
        del bind["snapshot_scope"]
        with self.assertRaises(SP.ProvenanceError):
            SP.check_build_identity(self._snap_prov(), self.SNAP_CFG, bind)


if __name__ == "__main__":
    unittest.main()
