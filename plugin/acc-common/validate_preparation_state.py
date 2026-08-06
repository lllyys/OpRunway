#!/usr/bin/env python3
"""校验非真机准备工件能否安全复用。

本脚本只复核 CP-A/CP-B 的来源、对应关系、spec、golden 与 dry-run 计划绑定。
任务书授权锚还须在 source facts、spec golden、GOLDEN_CONTRACT 与 golden 生效目录
四处摘要一致；生效目录快照必须是普通非符号链接文件。
它不读取 caseset/evidence/verdict，不运行 golden，不产生任何验收 PASS。

⚠ 「逻辑摘要一致」不蕴含「用例数据字节一致」：`gen_cases` 的随机流由 numpy 提供，
换 numpy 大版本就可能让同一 case_id 落出不同 `.npy`，而 spec/planner/golden 的摘要
全都不动。故另有 `case_data_stream` 一项，按 `主.次` pin 对账随机流身份。
"""

import argparse
import ast
import hashlib
import json
import os
import sys

import content_address
# 取源形态词表（实得 `provenance_kind` / 声明 `declared_source_form`）与形态中性事实的
# **唯一真源**——不在此另抄一份字面量，抄一次就是两套词表，迟早对同一份事实包给出两种归类。
import source_provenance


#: 「键根本没写」与「显式写了某个值」必须分得开：`.get()` 会把两者压成同一个 `None`，
#: 而 `provenance_kind` 的兼容规则恰恰是「**只有整键缺席**才按老事实包处理」。
_KIND_ABSENT = object()
_PROVENANCE_KINDS = (source_provenance.PROVENANCE_GIT_PR,
                     source_provenance.PROVENANCE_LOCAL_SNAPSHOT)
_SOURCE_DOMAIN = "oprunway/source-facts/v1"
_RECEIPT_DOMAIN = "oprunway/preparation-receipt/v1"
_LEDGER_SCHEMA = "oprunway.gen_cases.dry_run_ledger"
_CASE_PLAN_DOMAIN = "oprunway/case-plan/v1"
_PLANNER_DEPENDENCIES = (
    "gen_cases.py",
    "repo_adapter.py",
    "precision_policy.py",
)


def _is_sha(value, length=64):
    return (isinstance(value, str) and len(value) == length
            and all(c in "0123456789abcdef" for c in value))


def _validate_source_payload(source):
    """校验 source facts 的载重字段；摘要自洽不等于 producer 输出完整。

    按 `pr.provenance_kind` 分支（**键缺席 = `gitcode_pr`**，老事实包照旧走 PR 分支，
    与改动前逐字同规矩）：

      · `gitcode_pr`     ：`pr` 必填集**一行不改**，关键文件锚 = `pr.head_sha`；
      · `local_snapshot` ：`head_sha` 须**显式 null**（本形态没有上游 commit，合成一个
        40 位 hex 就是捏造 PR head，AGENTS.md 5.8）、`snapshot_merkle_sha256` 与
        `snapshot_scope` **成对**必填，关键文件锚 = 字面量 `"local_snapshot"`
        （`fetch_source.scan_pr_snapshot` 就是这么写的，此处不另立第二套口径）。

    另核「**声明**形态 × **实得**形态」仍在 `source_provenance` 的 allowlist 内：
    声明 `local_source` 却实得绑着上游 commit 的 `gitcode_pr`，一律拒。

    ⚠ 「本该测 PR、只拿到一份快照」那条**降级**路由在这里天然过不去：`fetch_source`
    给它落的 `completeness.status` 是 `snapshot_only`，而本函数只收 `complete`。
    这不是巧合，别在后续改动里把 `complete` 放宽成「complete 或 snapshot_only」。
    """
    if not isinstance(source, dict):
        raise content_address.ContentAddressError(
            "source_facts payload 须为 JSON object")
    required = {
        "contract_version", "taskdoc", "pr", "changed_files", "key_files",
        "derived", "completeness", "producer",
    }
    missing = sorted(required.difference(source))
    if missing:
        raise content_address.ContentAddressError(
            f"source_facts 缺必要字段: {missing}")
    if source["contract_version"] != 1:
        raise content_address.ContentAddressError(
            "source_facts.contract_version 不受支持")
    taskdoc = source["taskdoc"]
    if not isinstance(taskdoc, dict):
        raise content_address.ContentAddressError(
            "source_facts.taskdoc 须为 JSON object")
    if (not _is_sha(taskdoc.get("bytes_sha256"))
            or taskdoc.get("snapshot_sha256") != taskdoc.get("bytes_sha256")
            or not isinstance(taskdoc.get("size"), int)
            or isinstance(taskdoc.get("size"), bool)
            or taskdoc["size"] < 0
            or not isinstance(taskdoc.get("source_locator"), str)):
        raise content_address.ContentAddressError(
            "source_facts.taskdoc 的 bytes/snapshot/size/source_locator 契约不完整")
    pr = source["pr"]
    if not isinstance(pr, dict):
        raise content_address.ContentAddressError(
            "source_facts.pr 须为 JSON object")
    # 实得取源形态：**键缺席**才表示「本字段引入之前产的老事实包」（按 gitcode_pr 处理）；
    # 显式写了一个词表外的值一律拒——静默归类等于让未知档位自己挑一条分支走。
    kind = pr.get("provenance_kind", _KIND_ABSENT)
    if kind is _KIND_ABSENT:
        kind = source_provenance.PROVENANCE_GIT_PR
    elif kind not in _PROVENANCE_KINDS:
        raise content_address.ContentAddressError(
            f"source_facts.pr.provenance_kind={kind!r} 非受控值，"
            f"须属 {list(_PROVENANCE_KINDS)}（fail-closed，不猜、不归类）")
    is_snapshot = kind == source_provenance.PROVENANCE_LOCAL_SNAPSHOT
    # 声明形态：入口就定的一等事实。缺席/null = 老事实包（按最严的 git_pr 一档对待）。
    declared = source.get(source_provenance.DECLARED_FORM_KEY)
    if declared is not None and declared not in source_provenance.DECLARED_SOURCE_FORMS:
        raise content_address.ContentAddressError(
            f"source_facts.{source_provenance.DECLARED_FORM_KEY}={declared!r} 非受控值，"
            f"须属 {list(source_provenance.DECLARED_SOURCE_FORMS)}")
    if declared == source_provenance.FORM_LOCAL_SOURCE and not is_snapshot:
        raise content_address.ContentAddressError(
            f"source_facts 声明 {source_provenance.DECLARED_FORM_KEY}="
            f"{source_provenance.FORM_LOCAL_SOURCE}（本地源码），实得却是绑定上游 commit 的 "
            f"{kind!r}——声明与实得不是同一件事，fail-closed")
    if is_snapshot:
        # ⚠ `head_sha` 必须**显式 null**：`.get()` 会把「键根本没写」与「写了 null」压成
        #   同一个 `None`，而这一档的判据恰恰是「显式声明没有上游 commit」。
        if "head_sha" not in pr:
            raise content_address.ContentAddressError(
                "source_facts.pr.head_sha 键缺失——本地快照档须**显式**写 null，"
                "「没写这个字段」与「显式写 null」不是一回事")
        if pr["head_sha"] is not None:
            raise content_address.ContentAddressError(
                f"source_facts.pr.head_sha 在 {kind} 档须为 null（实得 {pr['head_sha']!r}）——"
                "本地快照没有上游 commit，合成一个 40 位 hex 就是捏造 PR head")
        # merkle 与 scope **成对**：没有范围的 merkle 与真机 build 侧不可比
        # （对上了是巧合，对不上也说不清是改了字节还是换了范围）。
        if not _is_sha(pr.get("snapshot_merkle_sha256")):
            raise content_address.ContentAddressError(
                "source_facts.pr.snapshot_merkle_sha256 须为 64 位小写 sha"
                f"（实得 {pr.get('snapshot_merkle_sha256')!r}）——本地快照的字节身份全靠它")
        if not isinstance(pr.get("snapshot_scope"), str):
            raise content_address.ContentAddressError(
                "source_facts.pr.snapshot_scope 须为字符串（空串= 快照根，属合法显式值）——"
                "merkle 没有范围就无法与真机 build 侧对账")
        # 关键文件的锚：本形态没有 commit id 可绑，`fetch_source` 落的是这个字面量。
        anchor = source_provenance.PROVENANCE_LOCAL_SNAPSHOT
        anchor_desc = "本地快照锚（key_files[].ref 应为 'local_snapshot'）"
    else:
        if (not isinstance(pr.get("canonical_url"), str)
                or not isinstance(pr.get("source_repo"), str)
                or not isinstance(pr.get("number"), int)
                or isinstance(pr.get("number"), bool)
                or pr["number"] <= 0
                or not _is_sha(pr.get("head_sha"), length=40)
                or not isinstance(pr.get("head_repo"), str)
                or not isinstance(pr.get("is_fork"), bool)
                or not isinstance(pr.get("state"), str)):
            raise content_address.ContentAddressError(
                "source_facts.pr 的 URL/repo/number/head/fork/state 契约不完整")
        # 反向排他：PR 档不得带任何本地快照锚（值为 None 才算「没有」——`fetch_source`
        # 恒带这两个键、PR 通路值为 None，要求「键不存在」会把所有 PR 通路当场打死）。
        for stray in ("snapshot_merkle_sha256", "snapshot_scope"):
            if pr.get(stray) is not None:
                raise content_address.ContentAddressError(
                    f"source_facts.pr 声明 provenance_kind={kind!r} 却带着 "
                    f"{stray}={pr.get(stray)!r}——PR 通路混装本地快照锚，fail-closed")
        anchor = pr["head_sha"]
        anchor_desc = "PR head"
    changed_files = source["changed_files"]
    if (not isinstance(changed_files, list) or not changed_files
            or any(not isinstance(path, str) or not path
                   for path in changed_files)):
        raise content_address.ContentAddressError(
            "source_facts.changed_files 须为非空字符串数组")
    key_files = source["key_files"]
    if not isinstance(key_files, list) or not key_files:
        raise content_address.ContentAddressError(
            "source_facts.key_files 须为非空数组")
    for index, item in enumerate(key_files):
        if (not isinstance(item, dict)
                or not isinstance(item.get("path"), str)
                or item.get("ref") != anchor
                or not _is_sha(item.get("bytes_sha256"))
                or not isinstance(item.get("size"), int)
                or isinstance(item.get("size"), bool)
                or item["size"] < 0):
            raise content_address.ContentAddressError(
                f"source_facts.key_files[{index}] 契约不完整或未绑定{anchor_desc}")
    derived = source["derived"]
    if (not isinstance(derived, dict)
            or not isinstance(derived.get("op"), str)
            or not isinstance(derived.get("target_dir"), str)
            or not isinstance(derived.get("aclnn_headers"), list)
            or any(not isinstance(path, str)
                   for path in derived.get("aclnn_headers", []))):
        raise content_address.ContentAddressError(
            "source_facts.derived 的 op/target_dir/aclnn_headers 契约不完整")
    completeness = source["completeness"]
    if (not isinstance(completeness, dict)
            or completeness.get("status") != "complete"
            or completeness.get("reasons") != []):
        raise content_address.ContentAddressError(
            "source_facts.completeness 必须是 complete 且 reasons 为空")
    # `form_facts` = 该输入形态本来就成立的**中性事实**（不是降级，见 `source_provenance`）。
    # ⚠ 必须是**受控词表**且与形态**双向一致**：少写 = 形态弱点没留痕，多写 = 编造事实。
    #   一个「随便什么字符串都收下」的 form_facts 等于给 provenance 强度声明开了后门。
    facts_listed = completeness.get("form_facts", [])
    if not isinstance(facts_listed, list) or any(
            not isinstance(item, str) for item in facts_listed):
        raise content_address.ContentAddressError(
            "source_facts.completeness.form_facts 若出现则须为字符串数组")
    expected_form_facts = (
        list(source_provenance.LOCAL_SOURCE_FORM_FACTS)
        if declared == source_provenance.FORM_LOCAL_SOURCE else [])
    if sorted(facts_listed) != sorted(expected_form_facts):
        raise content_address.ContentAddressError(
            f"source_facts.completeness.form_facts={sorted(facts_listed)} 与声明形态 "
            f"{declared!r} 派生的 {sorted(expected_form_facts)} 不一致"
            f"（少写 = 形态弱点没留痕；多写 = 编造事实）")
    producer = source["producer"]
    if (not isinstance(producer, dict)
            or producer.get("tool") != "fetch_source.py"
            or not _is_sha(producer.get("logic_sha256"))):
        raise content_address.ContentAddressError(
            "source_facts.producer 契约不完整")


def _validate_plan_payload(plan):
    """校验 dry-run 账本载重；防止只复制几项摘要的空壳命中。"""
    if not isinstance(plan, dict):
        raise content_address.ContentAddressError(
            "case plan 须为 JSON object")
    required = {
        "schema", "schema_version", "spec_binding", "preparation_inputs",
        "planner_binding", "planning", "golden_dependency", "summary",
        "coverage", "determinism", "ledger_digest",
    }
    missing = sorted(required.difference(plan))
    if missing:
        raise content_address.ContentAddressError(
            f"case plan 缺必要字段: {missing}")
    if (plan.get("schema") != _LEDGER_SCHEMA
            or plan.get("schema_version") != 1):
        raise content_address.ContentAddressError(
            "case plan schema/schema_version 不受支持")
    planning = plan["planning"]
    if (not isinstance(planning, dict)
            or not isinstance(planning.get("case_target"), int)
            or isinstance(planning.get("case_target"), bool)
            or planning["case_target"] < 1
            or not isinstance(planning.get("runner_form"), str)):
        raise content_address.ContentAddressError(
            "case plan planning.case_target/runner_form 契约不完整")
    summary = plan["summary"]
    count_keys = ("emitted", "pool_max", "forced_total", "forced_special")
    if (not isinstance(summary, dict)
            or any(not isinstance(summary.get(key), int)
                   or isinstance(summary.get(key), bool)
                   or summary[key] < 0 for key in count_keys)
            or not isinstance(summary.get("by_dtype"), dict)
            or not isinstance(summary.get("shapes"), list)
            or not isinstance(summary.get("id_kinds"), dict)):
        raise content_address.ContentAddressError(
            "case plan summary 载重字段不完整")
    coverage = plan["coverage"]
    if (not isinstance(coverage, dict)
            or not isinstance(coverage.get("strength"), str)
            or not isinstance(coverage.get("golden_cost"), dict)
            or not isinstance(coverage.get("dropped_combo_classes"), list)
            or not isinstance(coverage.get("unpaired_combo_classes"), dict)):
        raise content_address.ContentAddressError(
            "case plan coverage 载重字段不完整")
    determinism = plan["determinism"]
    if (determinism is not None
            and (not isinstance(determinism, dict)
                 or not isinstance(determinism.get("case_id"), str)
                 or not isinstance(determinism.get("equal"), bool))):
        raise content_address.ContentAddressError(
            "case plan determinism 契约不完整")


def _strict_json(path):
    try:
        with open(path, "r", encoding="utf-8") as src:
            value = json.load(
                src,
                parse_constant=lambda token: (_ for _ in ()).throw(
                    content_address.ContentAddressError(
                        f"非法 JSON 常量: {token}")),
            )
    except content_address.ContentAddressError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        raise content_address.ContentAddressError(
            f"无法读取 JSON {path!r}: {ex}") from ex
    content_address.canonical_json_bytes(value)
    return value


def _file_sha256(path):
    if os.path.islink(path):
        raise content_address.ContentAddressError(f"依赖文件不得是符号链接: {path!r}")
    try:
        with open(path, "rb") as src:
            return hashlib.sha256(src.read()).hexdigest()
    except OSError as ex:
        raise content_address.ContentAddressError(
            f"无法读取依赖文件 {path!r}: {ex}") from ex


def _golden_contract(path):
    """静态读取 golden.py 的 GOLDEN_CONTRACT；不 import、不执行被验收代码。"""
    if os.path.islink(path):
        raise content_address.ContentAddressError(
            f"golden.py 不得是符号链接: {path!r}")
    try:
        with open(path, "r", encoding="utf-8") as src:
            tree = ast.parse(src.read(), filename=path)
    except (OSError, UnicodeError, SyntaxError) as ex:
        raise content_address.ContentAddressError(
            f"无法静态读取 golden.py {path!r}: {ex}") from ex
    values = []
    for node in tree.body:
        if (isinstance(node, (ast.Assign, ast.AnnAssign))
                and ((isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name)
                              and target.id == "GOLDEN_CONTRACT"
                              for target in node.targets))
                     or (isinstance(node, ast.AnnAssign)
                         and isinstance(node.target, ast.Name)
                         and node.target.id == "GOLDEN_CONTRACT"))):
            try:
                values.append(ast.literal_eval(node.value))
            except (ValueError, TypeError) as ex:
                raise content_address.ContentAddressError(
                    "GOLDEN_CONTRACT 必须是可静态读取的字面量") from ex
    if len(values) != 1 or not isinstance(values[0], dict):
        raise content_address.ContentAddressError(
            "golden.py 必须且只能定义一个 object 型 GOLDEN_CONTRACT")
    return values[0]


def _check(checks, name, status, reason):
    checks.append({"name": name, "status": status, "reason": reason})


def _current_numpy_stream_pin():
    """当前进程的 numpy 随机流 pin；取不到就说取不到（**不返回占位值**）。

    刻意走 `gen_cases` 的函数而不是在这里另写一行 `split(".")[:2]`：pin 口径一旦有两份实现，
    两边迟早漂，而漂的表现是「门看着在跑、其实永远相等」。同 `source_provenance` 的词表
    在本文件里的用法——判定依赖只认产出方那一份真源。

    ⚠ 本函数是**懒导入**：`gen_cases` 会拉起 numpy，而本脚本其余部分是纯静态校验、
    不该为此在 import 期就绑上 numpy。
    """
    import gen_cases                                   # 懒导入：见上方注释
    return gen_cases.current_numpy_stream_pin()


def _is_wellformed_pin(value):
    """pin 的**形态**校验（不比大小、不判新旧，只判「像不像一个版本串」）。

    刻意不复用 `gen_cases.numpy_stream_pin`：那个函数是**产**pin 的，会 import numpy；
    这里要判的是账本里**已记录**的那串合不合法，不该为此拉起 numpy——
    一份坏账本的诊断不该依赖 numpy 装没装好。
    """
    parts = str(value).split(".")
    return len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit()


def evaluate(root, spec_rel, case_plan_rel, golden_path=None,
             source_rel="source_facts.json",
             correspondence_rel="correspondence.json",
             taskdoc_snapshot_rel=None):
    """返回 `REUSABLE | MISS | BLOCKED` 的准备阶段收据 payload。

    `MISS` 表示输入或逻辑正常漂移，应重做非真机准备；`BLOCKED` 表示工件损坏、
    schema 异常或不可信，不能静默重做来掩盖。
    """
    root = os.path.abspath(root)
    checks, bindings = [], {}
    if taskdoc_snapshot_rel is None:
        taskdoc_snapshot_rel = os.path.join(
            os.path.dirname(os.fspath(source_rel)), "task_doc.snapshot.md")
    snapshot_path = None

    try:
        snapshot_path = content_address.safe_path(root, taskdoc_snapshot_rel)
        source_path = content_address.safe_path(root, source_rel)
        if not os.path.exists(source_path):
            source = None
            source_digest = None
            _check(checks, "source_facts", "MISS", "source_facts.json 不存在")
        else:
            source = content_address.read_artifact(
                root, source_rel, _SOURCE_DOMAIN)
            _validate_source_payload(source)
            source_digest = content_address.content_digest(
                _SOURCE_DOMAIN, source)
            bindings["source_facts_digest"] = source_digest
            completeness = source.get("completeness")
            if not isinstance(completeness, dict):
                raise content_address.ContentAddressError(
                    "source_facts.completeness 须为 JSON object")
            complete = completeness.get("status")
            if complete == "complete":
                _check(checks, "source_facts", "PASS",
                       "内容摘要与完整性均有效")
            else:
                _check(checks, "source_facts", "MISS",
                       "source_facts completeness 不是 complete")
            fetch_source_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "fetch_source.py")
            producer_sha = _file_sha256(fetch_source_path)
            bindings["source_producer_sha256"] = producer_sha
            producer = source.get("producer")
            if not isinstance(producer, dict):
                raise content_address.ContentAddressError(
                    "source_facts.producer 须为 JSON object")
            recorded_producer = producer.get("logic_sha256")
            if recorded_producer != producer_sha:
                _check(checks, "source_producer", "MISS",
                       "fetch_source.py 事实生成逻辑已变化")
            else:
                _check(checks, "source_producer", "PASS",
                       "事实生成逻辑摘要一致")
            source_taskdoc = source.get("taskdoc")
            if not isinstance(source_taskdoc, dict):
                raise content_address.ContentAddressError(
                    "source_facts.taskdoc 须为 JSON object")
            recorded_snapshot_sha = source_taskdoc.get("snapshot_sha256")
            if not (isinstance(recorded_snapshot_sha, str)
                    and len(recorded_snapshot_sha) == 64
                    and all(c in "0123456789abcdef"
                            for c in recorded_snapshot_sha)):
                _check(checks, "taskdoc_snapshot", "MISS",
                       "source_facts 未绑定合法 taskdoc snapshot sha256")
            elif not os.path.exists(snapshot_path):
                _check(checks, "taskdoc_snapshot", "MISS",
                       f"{taskdoc_snapshot_rel} 不存在")
            else:
                current_snapshot_sha = _file_sha256(snapshot_path)
                bindings["taskdoc_snapshot_sha256"] = current_snapshot_sha
                if current_snapshot_sha != recorded_snapshot_sha:
                    _check(checks, "taskdoc_snapshot", "MISS",
                           "任务书快照内容已变化")
                else:
                    _check(checks, "taskdoc_snapshot", "PASS",
                           "任务书快照与 source_facts 摘要一致")
    except content_address.ContentAddressError as ex:
        source = None
        source_digest = None
        _check(checks, "source_facts", "BLOCKED", str(ex))

    correspondence_sha = None
    try:
        correspondence_path = content_address.safe_path(root, correspondence_rel)
        if not os.path.exists(correspondence_path):
            _check(checks, "correspondence", "MISS",
                   "correspondence.json 不存在")
        else:
            correspondence = _strict_json(correspondence_path)
            correspondence_sha = hashlib.sha256(
                content_address.canonical_json_bytes(correspondence)).hexdigest()
            bindings["correspondence_sha256"] = correspondence_sha
            if not isinstance(correspondence, dict):
                raise content_address.ContentAddressError(
                    "correspondence 须为 JSON object")
            if correspondence.get("status") != "confirmed":
                _check(checks, "correspondence", "MISS",
                       f"status={correspondence.get('status')!r}，不是 confirmed")
            elif not source_digest:
                _check(checks, "correspondence", "MISS",
                       "source_facts 尚不可绑定")
            elif correspondence.get("source_facts_digest") != source_digest:
                _check(checks, "correspondence", "MISS",
                       "缺少或不匹配 source_facts_digest，须重新确认对应关系")
            else:
                _check(checks, "correspondence", "PASS",
                       "confirmed 且绑定当前 source_facts")
    except content_address.ContentAddressError as ex:
        _check(checks, "correspondence", "BLOCKED", str(ex))

    try:
        spec_path = content_address.safe_path(root, spec_rel)
        if not os.path.exists(spec_path):
            spec = None
            spec_sha = None
            _check(checks, "spec", "MISS", f"{spec_rel} 不存在")
        else:
            spec = _strict_json(spec_path)
            if not isinstance(spec, dict):
                raise content_address.ContentAddressError(
                    "spec 须为 JSON object")
            spec_sha = hashlib.sha256(
                content_address.canonical_json_bytes(spec)).hexdigest()
            bindings["spec_sha256"] = spec_sha
            spec_golden = spec.get("golden")
            if spec_golden is not None and not isinstance(spec_golden, dict):
                raise content_address.ContentAddressError(
                    "spec.golden 须为 JSON object")
            authorization = (spec_golden or {}).get("authorization")
            if authorization is not None and not isinstance(
                    authorization, dict):
                raise content_address.ContentAddressError(
                    "spec.golden.authorization 须为 JSON object")
            authorization_kind = (authorization or {}).get("kind")
            if authorization_kind in {"oracle_method", "formula"}:
                spec_snapshot = (spec_golden or {}).get("taskdoc_snapshot")
                if not isinstance(spec_snapshot, dict):
                    raise content_address.ContentAddressError(
                        "spec.golden.taskdoc_snapshot 须为 JSON object")
                spec_snapshot_sha = spec_snapshot.get("sha256")
                source_snapshot_sha = (
                    ((source or {}).get("taskdoc") or {})
                    .get("snapshot_sha256"))
                if (not source_snapshot_sha
                        or spec_snapshot_sha != source_snapshot_sha):
                    _check(checks, "spec_taskdoc_anchor", "BLOCKED",
                           "spec golden 的任务书锚未绑定当前 source_facts 快照")
                else:
                    _check(checks, "spec_taskdoc_anchor", "PASS",
                           "spec golden 绑定当前任务书快照")
    except content_address.ContentAddressError as ex:
        spec = None
        spec_sha = None
        _check(checks, "spec", "BLOCKED", str(ex))

    try:
        plan_path = content_address.safe_path(root, case_plan_rel)
        if not os.path.exists(plan_path):
            _check(checks, "case_plan", "MISS", f"{case_plan_rel} 不存在")
            plan = None
        else:
            plan = _strict_json(plan_path)
        if plan is not None:
            _validate_plan_payload(plan)
            recorded_plan_digest = plan.get("ledger_digest")
            plan_payload = dict(plan)
            plan_payload.pop("ledger_digest", None)
            actual_plan_digest = content_address.content_digest(
                _CASE_PLAN_DOMAIN, plan_payload)
            if recorded_plan_digest != actual_plan_digest:
                raise content_address.ContentAddressError(
                    "case plan ledger_digest 缺失或不匹配")
            preparation_inputs = plan.get("preparation_inputs")
            if not isinstance(preparation_inputs, dict):
                _check(checks, "case_plan_inputs", "MISS",
                       "case plan 未绑定 source_facts/correspondence")
            elif (not source_digest or not correspondence_sha
                  or preparation_inputs.get("source_facts_digest")
                  != source_digest
                  or preparation_inputs.get("correspondence_sha256")
                  != correspondence_sha):
                _check(checks, "case_plan_inputs", "MISS",
                       "case plan 的来源事实或用户确认已变化")
            else:
                _check(checks, "case_plan_inputs", "PASS",
                       "case plan 绑定当前来源事实与用户确认")
            plan_spec_binding = plan.get("spec_binding")
            if not isinstance(plan_spec_binding, dict):
                raise content_address.ContentAddressError(
                    "case plan spec_binding 须为 JSON object")
            plan_spec_sha = plan_spec_binding.get("sha256")
            if not spec_sha:
                _check(checks, "case_plan_spec", "MISS", "spec 尚不可绑定")
            elif plan_spec_sha != spec_sha:
                _check(checks, "case_plan_spec", "MISS", "spec 内容已变化")
            else:
                _check(checks, "case_plan_spec", "PASS",
                       "case plan 绑定当前 spec")

            logic_root = os.path.dirname(os.path.abspath(__file__))
            current_logic_files = {
                filename: _file_sha256(os.path.join(logic_root, filename))
                for filename in _PLANNER_DEPENDENCIES
            }
            planner_sha = current_logic_files["gen_cases.py"]
            bindings["planner_sha256"] = planner_sha
            planner_binding = plan.get("planner_binding")
            if not isinstance(planner_binding, dict):
                raise content_address.ContentAddressError(
                    "case plan planner_binding 须为 JSON object")
            recorded_planner = planner_binding.get("gen_cases_py_sha256")
            recorded_logic_files = planner_binding.get("logic_files")
            if (recorded_planner != planner_sha
                    or recorded_logic_files != current_logic_files):
                _check(checks, "case_planner", "MISS",
                       "gen_cases.py 或其规划依赖逻辑已变化")
            else:
                _check(checks, "case_planner", "PASS", "规划逻辑摘要一致")

            # ⚠ 规划逻辑摘要相等**不蕴含**用例数据字节相等：`gen_cases` 一个字节没改，只要
            # numpy 换了版本，`_case_rng` 那条随机流就可能漂，于是同一 spec、同一 case_id
            # 落出不同的 `.npy`——而 spec / planner / golden 的摘要一个都不会动。
            # 所以随机流身份要单独对账，判定用**完整版本**（不做「主.次」收敛，理由见
            # `gen_cases.numpy_stream_pin` 的 ⚠：1.18.4 就在补丁版里改过 Generator.integers 的流）。
            #
            # ⚠ 三种「对不上」要分开判，别一锅炖成 MISS：
            #   · 取不到当前流身份       → BLOCKED（无从核对，重做准备也一样取不到）
            #   · 账本**没有**这个键     → MISS  （老工件正常过期，重跑一次即可）
            #   · 账本**有**但形态不合法 → BLOCKED（账本损坏/被改过，重跑救不了，要人看）
            recorded_pin = planner_binding.get("numpy_stream_pin")
            has_pin_key = "numpy_stream_pin" in planner_binding
            try:
                current_pin = _current_numpy_stream_pin()
            except Exception as ex:                    # noqa: BLE001 — 见下方 ⚠
                # ⚠ 这里必须宽捕：`_current_numpy_stream_pin` 是**懒导入**，会拉起 `gen_cases`
                #   及其全部顶层依赖（numpy 的二进制加载）。除 ImportError/ValueError 外，
                #   初始化失败还可能抛 RuntimeError / OSError / AttributeError——那些若裸穿，
                #   调用方拿不到机读的 BLOCKED 收据，只会看到 traceback。
                #   不捕 BaseException（KeyboardInterrupt / SystemExit 该照常传出去）。
                current_pin = None
                _check(checks, "case_data_stream", "BLOCKED",
                       f"无法确定当前 numpy 随机流 pin（{type(ex).__name__}）：{ex}")
            if current_pin is None:
                pass                                   # 上面已记 BLOCKED，不再重复判定
            elif not has_pin_key:
                # 老账本没有这个键 = 产它的那次没记随机流身份 → 无从证明数据可复现，重做准备。
                # 这是**正常漂移**（同 `case_planner` 的 MISS 口径），不是账本损坏。
                _check(checks, "case_data_stream", "MISS",
                       "case plan 未记录 numpy 随机流 pin（本字段之前产的账本），"
                       "无法证明用例数据可复现")
            elif not isinstance(recorded_pin, str) or not _is_wellformed_pin(recorded_pin):
                # 键在、值却不合法 → 这份账本被改过或写坏了。重跑准备救不了「账本不可信」，
                # 所以是 BLOCKED 不是 MISS；也**绝不**把它当成普通的版本失配蒙混过去。
                _check(checks, "case_data_stream", "BLOCKED",
                       f"case plan 的 numpy_stream_pin 形态非法：{recorded_pin!r}"
                       f"（应为至少 主.次 两段数字的版本串）——账本不可信，非重跑可解")
            elif recorded_pin != current_pin:
                _check(checks, "case_data_stream", "MISS",
                       f"numpy 随机流已变（账本 {recorded_pin} → 当前 {current_pin}）："
                       f"同一 case_id 会产出不同字节，请对齐 numpy 版本或重新生成并重新确认")
            else:
                _check(checks, "case_data_stream", "PASS",
                       f"numpy 随机流 pin 一致（{current_pin}）")
            if current_pin is not None:
                bindings["numpy_stream_pin"] = current_pin

            golden = plan.get("golden_dependency")
            if not isinstance(golden, dict) or golden.get("status") != "loaded":
                _check(checks, "golden", "MISS",
                       "dry-run 未成功绑定 golden.py")
            elif not golden_path:
                _check(checks, "golden", "MISS", "未提供 --golden 复核路径")
            elif not os.path.exists(os.path.abspath(golden_path)):
                _check(checks, "golden", "MISS", "golden.py 不存在")
            else:
                absolute_golden = os.path.abspath(golden_path)
                current_golden_sha = _file_sha256(absolute_golden)
                bindings["golden_sha256"] = current_golden_sha
                if current_golden_sha != golden.get("bytes_sha256"):
                    _check(checks, "golden", "MISS", "golden.py 内容已变化")
                else:
                    _check(checks, "golden", "PASS", "golden.py 摘要一致")
                contract = _golden_contract(absolute_golden)
                contract_sha = hashlib.sha256(
                    content_address.canonical_json_bytes(contract)).hexdigest()
                bindings["golden_contract_sha256"] = contract_sha
                if contract_sha != golden.get("contract_sha256"):
                    _check(checks, "golden_contract", "BLOCKED",
                           "GOLDEN_CONTRACT 与 case plan 记录摘要冲突")
                else:
                    _check(checks, "golden_contract", "PASS",
                           "GOLDEN_CONTRACT 摘要与 case plan 一致")

                contract_snapshot = contract.get("taskdoc_snapshot")
                contract_snapshot_sha = (
                    contract_snapshot.get("sha256")
                    if isinstance(contract_snapshot, dict) else None)
                bindings["golden_contract_taskdoc_snapshot_sha256"] = (
                    contract_snapshot_sha)
                ops_snapshot = os.path.join(
                    os.path.dirname(absolute_golden), "task_doc.snapshot.md")
                bindings["source_taskdoc_snapshot_path"] = snapshot_path
                bindings["ops_taskdoc_snapshot_path"] = ops_snapshot
                if not os.path.lexists(ops_snapshot):
                    _check(checks, "ops_taskdoc_snapshot", "MISS",
                           "golden 生效目录缺 task_doc.snapshot.md，须重做 gen_golden")
                elif os.path.islink(ops_snapshot):
                    _check(checks, "ops_taskdoc_snapshot", "BLOCKED",
                           "golden 生效目录 task_doc.snapshot.md 不得是符号链接")
                elif not os.path.isfile(ops_snapshot):
                    _check(checks, "ops_taskdoc_snapshot", "BLOCKED",
                           "golden 生效目录 task_doc.snapshot.md 须为普通文件")
                else:
                    ops_snapshot_sha = _file_sha256(ops_snapshot)
                    bindings["ops_taskdoc_snapshot_sha256"] = ops_snapshot_sha
                    source_snapshot_sha = (
                        ((source or {}).get("taskdoc") or {})
                        .get("snapshot_sha256"))
                    spec_snapshot_sha = None
                    spec_golden = (
                        spec.get("golden") if isinstance(spec, dict) else None)
                    if isinstance(spec_golden, dict):
                        spec_snapshot = spec_golden.get("taskdoc_snapshot")
                        if isinstance(spec_snapshot, dict):
                            spec_snapshot_sha = spec_snapshot.get("sha256")
                    bindings["source_taskdoc_snapshot_sha256"] = (
                        source_snapshot_sha)
                    bindings["spec_taskdoc_snapshot_sha256"] = (
                        spec_snapshot_sha)
                    anchors = {
                        "source_facts": source_snapshot_sha,
                        "spec.golden": spec_snapshot_sha,
                        "GOLDEN_CONTRACT": contract_snapshot_sha,
                        "ops_snapshot": ops_snapshot_sha,
                    }
                    if (not all(_is_sha(value) for value in anchors.values())
                            or len(set(anchors.values())) != 1):
                        _check(
                            checks, "ops_taskdoc_snapshot", "BLOCKED",
                            f"任务书授权锚四方摘要缺失或冲突: {anchors}")
                    else:
                        _check(
                            checks, "ops_taskdoc_snapshot", "PASS",
                            "source/spec/GOLDEN_CONTRACT/生效目录快照四方摘要一致")
            bindings["case_plan_sha256"] = hashlib.sha256(
                content_address.canonical_json_bytes(plan)).hexdigest()
    except content_address.ContentAddressError as ex:
        _check(checks, "case_plan", "BLOCKED", str(ex))

    statuses = {item["status"] for item in checks}
    status = ("BLOCKED" if "BLOCKED" in statuses
              else "MISS" if "MISS" in statuses else "REUSABLE")
    return {
        "schema": "oprunway.preparation_receipt",
        "schema_version": 1,
        "status": status,
        "reusable": status == "REUSABLE",
        "scope": "non-real-machine-preparation-only",
        "acceptance_verdict": None,
        "checks": checks,
        "bindings": bindings,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="复核非真机准备工件绑定；不产生验收裁决")
    ap.add_argument("--root", required=True, help="准备工件根目录")
    ap.add_argument("--spec", required=True, help="root 内 spec 相对路径")
    ap.add_argument("--case-plan", required=True, help="root 内 case plan 相对路径")
    ap.add_argument("--golden", default=None, help="golden.py 绝对或当前目录相对路径")
    ap.add_argument("--source", default="source_facts.json",
                    help="root 内 source facts 相对路径")
    ap.add_argument("--correspondence", default="correspondence.json",
                    help="root 内 correspondence 相对路径")
    ap.add_argument(
        "--taskdoc-snapshot", default=None,
        help=("root 内 CP-A 任务书快照相对路径；省略时自动取 --source 同目录下"
              "的 task_doc.snapshot.md"))
    ap.add_argument("--out", default=None, help="root 内收据相对路径（可选）")
    args = ap.parse_args(argv)
    receipt = evaluate(
        args.root, args.spec, args.case_plan, golden_path=args.golden,
        source_rel=args.source, correspondence_rel=args.correspondence,
        taskdoc_snapshot_rel=args.taskdoc_snapshot)
    if args.out:
        content_address.write_artifact(
            os.path.abspath(args.root), args.out, _RECEIPT_DOMAIN, receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0 if receipt["reusable"] else 2


if __name__ == "__main__":
    sys.exit(main())
