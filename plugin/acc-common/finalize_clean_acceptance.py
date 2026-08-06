#!/usr/bin/env python3
"""从已落盘且已过三级门的验收证据生成干净 PASS acceptance.json。

用法：

    python3 finalize_clean_acceptance.py --dir <报告根> \\
        --spec <spec.json 原件> --source-facts <source_facts.json>

本入口只处理最窄的 clean-pass 情形；风险、挂起、无性能任务或任何不完整状态均 fail-closed，
不得借此绕过 run_workflow 的完整状态机。

---

## 🔴 这是**第二个**写验收裁决的入口，所以它必须扛住主入口扛的每一道门

病历（2026-08-06 审修门 · 两条 Critical，同一个文件）：

| # | 症状 | 现在怎么堵 |
|---|---|---|
| ① **产物层 fail-open** | 目录里有上轮 `acceptance.json=PASS`，本轮换了 spec / evidence / verdict，`finalize_directory` 在 `_load`、三级门或 `build_clean_acceptance` 处拒绝——**旧 PASS 原样留任**。下游按固定文件名读，就把它当成本轮裁决 | 进函数后**第一件事**（早于任何读取和校验）作废旧裁决及其人读渲染；**清不掉就拒绝继续**。与主入口 `run_workflow._invalidate_stale_results` 共用同一个原语 `run_workflow.invalidate_results` |
| ② **绕过 spec 门与强制 source-facts 门** | 旧版读 `<out>/ops/<op>/<op>.spec.json`（**不是** CP-E staging 的 `<out>/spec.json`），调三级门时也**不传** source facts；PR 通路的收据在「找不到 source facts」那条分支直接返回不报错 → 一套旧式、表面干净的工件就能写出 PASS。**主入口新增的两道硬门都可被这个裁决写入口绕过** | `--spec` / `--source-facts` **必填**；执行**同一套** spec 变更收据校验（入口 + 出口两处，与主入口逐字同口径）；`--spec` 原件须与 `<out>/spec.json` 那份 CP-E staging 副本**逐字节相等**；source facts 先按三级门自己那份判据验可信，再**显式**传给每一级门 |

⚠ **为什么不是直接删掉这条旁路的裁决写能力**（另一条备选，也确实更彻底）：删能力是**减一项对外能力**，
按仓规 §5.2 属于要先跟用户确认的范围；而这两条 Critical 是**当下就在漏的门**，不该等确认。
所以本轮先把旁路收紧到与主入口同门——它现在物理上不可能比主入口松。
如实记账：查过全仓，**没有任何编排层调用它**（`plugin/skills`、`plugin/commands`、`plugin/agents`、
其余 `acc-common/*.py` 均无引用；CP-F 的 `precision_retest_runner` 也另写了自己的原子写，
设计文档里那句「复用 finalize 的 gate 与原子写机制」并未落成 import）。它今天只有人手敲 CLI 这一个消费面。
**下一轮建议直接删掉裁决写能力**（保留纯函数 `build_clean_acceptance` 当只读自检），
理由是「第二个写裁决的入口」天然是一台假门制造机：主入口以后每加一道门，这里都得有人记得同步。

⚠ 收紧的代价要认账：**旧式报告目录会 finalize 不动**（没有 `<out>/spec.json`、没有
`work/spec_change_receipt.json`）。这是有意的——那些目录本来就没经过封死后的编排链，
给它们补一份 PASS 正是要挡的事。
"""

import argparse
import json
import os
import tempfile

import repo_adapter
import run_workflow
import source_facts_lookup
import spec_change_gate
import validate_acceptance_state as gate

#: spec 变更门在本模块的两处落点。⚠ **两处、缺一不可**，口径照抄 `run_workflow`：
#: 只拦入口拦不住（入口过了之后 spec 原件仍可被换掉，而出口才是真正写盘的那一刻）。
_SPEC_GATE_ENTRY = "① 入口门（读证据之前）"
_SPEC_GATE_EXIT = "② 出口门（写 acceptance.json 之前）"


class FinalizeError(RuntimeError):
    pass


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
    except Exception as ex:
        raise FinalizeError(f"缺/坏 JSON {path}: {type(ex).__name__}: {ex}") from ex
    if not isinstance(value, dict):
        raise FinalizeError(f"{path} 顶层须为对象")
    return value


def _read_bytes(path, what):
    if os.path.islink(path):
        # 与 CP-E staging / spec 变更门同一条口径：判定依据不跟随软链（防换靶）。
        raise FinalizeError(f"{what} 是符号链接，拒绝跟随（防换靶）：{path!r}")
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as ex:
        raise FinalizeError(f"读不到{what}：{path!r}：{ex}") from ex


def _assert_spec_change_confirmed(spec_path, out_dir, stage):
    """与主入口**同一套**判据：直接调 `spec_change_gate.check` / `blocked_message`。

    ⚠ 刻意不调 `assert_confirmed`：那个抛 `SystemExit`，而本模块对外只承诺 `FinalizeError`
    （`main` 按它统一打 REFUSED）。换的只是异常壳子——判据与人读消息一个字没动，
    **绝不能**在这里另写一套「宽松版」检查。
    """
    problems = spec_change_gate.check(spec_path, out_dir)
    if problems:
        raise FinalizeError(
            spec_change_gate.blocked_message(spec_path, out_dir, stage, problems))


def _assert_source_facts_trusted(source_facts_path):
    """`--source-facts` 必须指向一份**可信**的 `source_facts.json`（判据复用三级门那一份）。

    ⚠ 这道校验不能省成「反正会传给门」：三级门对 `gitcode_pr` 通路在「找不到 source facts」
    时是**直接返回、不报错**的（历史 PR 通路的报告目录里确实没有这份文件）。于是不验就传，
    一个指不到的路径在 PR 通路上等价于「没这道门」——那正是 Critical ② 的物理入口。
    """
    if (source_facts_lookup.find_source_facts(None, source_facts_path)
            == source_facts_lookup.SOURCE_FACTS_UNTRUSTED):
        raise FinalizeError(
            f"--source-facts 指向的文件不是可信的 source_facts.json：{source_facts_path!r}\n"
            f"  → 它必须是 fetch_source.py 落的内容寻址 envelope，且 "
            f"completeness.status=complete、reasons=[]。\n"
            f"  → blocked/半成品的取材事实只供诊断，不能当验收的来源锚（fail-closed）。")


def _load_verified_spec(out_dir, spec_path):
    """取本轮 spec：`--spec` 原件，且必须与 `<out>/spec.json` 那份 CP-E staging 副本逐字节相等。

    ⚠ 旧版读的是 `<out>/ops/<op>/<op>.spec.json`——那既不是 CP-E staging 的落点，也不是
    spec 变更门校的那份原件。于是「门校 A、裁决按 B 拼」在这条旁路上是可达状态。
    现在三者钉成同一份：门校原件 → 原件 == staging 副本 → 裁决按原件拼。

    ⚠ 比的是**字节**而非解析结果（与 `run_workflow._read_acceptance_inputs` 里那处「比解析结果」
    不同，且是有意的）：那边两侧同源、只防重排版误伤；这边两侧是**不同的文件**，
    staging 副本本就是原件的字节副本，放宽到「语义相等」等于承认副本可以被重写过。
    """
    staged = os.path.join(out_dir, run_workflow._STAGED_SPEC_FILE)
    original = _read_bytes(spec_path, "--spec 指的 spec 原件")
    if not os.path.isfile(staged) and not os.path.islink(staged):
        raise FinalizeError(
            f"报告目录里没有 CP-E staging 的 {run_workflow._STAGED_SPEC_FILE}：{staged!r}\n"
            f"  → 说明这个目录不是封死后的编排链（run_workflow 验收通路）产出的。\n"
            f"  → 本入口不给这类目录补裁决：它缺的正是「这一轮到底验的是哪份 spec」的自证材料。")
    if original != _read_bytes(staged, f"报告目录里的 {run_workflow._STAGED_SPEC_FILE}"):
        raise FinalizeError(
            f"--spec 原件与报告目录里那份 CP-E staging 副本不是同一份字节：\n"
            f"     原件      {spec_path}\n"
            f"     staging   {staged}\n"
            f"  → spec 变更门校的是原件、而这一轮证据是按 staging 那份跑出来的，"
            f"两边对不上就说不清裁决在给哪份 spec 背书 —— fail-closed。")
    try:
        spec = json.loads(original)
    except (json.JSONDecodeError, UnicodeDecodeError) as ex:
        raise FinalizeError(f"spec 不是合法 JSON：{spec_path!r}：{ex}") from ex
    if not isinstance(spec, dict):
        raise FinalizeError(f"{spec_path} 顶层须为对象")
    return spec


def build_clean_acceptance(spec, evidence, verdict, perf_report, gate_errors):
    if gate_errors:
        raise FinalizeError(f"验收门未过：{gate_errors}")
    if evidence.get("evidence_grade") != "acceptance_candidate":
        raise FinalizeError("evidence_grade 不是 acceptance_candidate")
    # 缺省口径经全仓唯一真源（P5）。曾写 `or "cpp"`：spec 省略该键时这里算出 `cpp`、
    # 而 evidence 侧是真机实际跑出来的 `cpp_extension` → 一致性检查在**缺省值分裂**上误报。
    runner_form = repo_adapter.spec_runner_form(spec)
    if evidence.get("runner_form") != runner_form:
        raise FinalizeError(
            f"evidence.runner_form={evidence.get('runner_form')!r} 与 spec={runner_form!r} 不一致")
    # ⚠ 两种拒绝分开说（2026-08-06 通路收敛后必须分）：派生表现在只剩 `cpp_extension`，
    #   退役形态在这里拿到的是 `None`。若还混在同一句里，一份 `runner_form=aclnn_py` 的产物会被
    #   报成「runner_source 不匹配」——人会去改 runner_source，而真正的问题是这条通路已停止准入。
    mode = run_workflow._RUNNER_FORM_TO_MODE.get(runner_form)
    if mode is None:
        why = (run_workflow._retired_form_message(runner_form)
               if runner_form in run_workflow._KNOWN_RUNNER_FORMS
               else f"该值不在受控词表 {sorted(run_workflow._KNOWN_RUNNER_FORMS)} 内。")
        raise FinalizeError(
            f"runner_form={runner_form!r} 不产验收裁决，拒绝据此生成 acceptance.json。\n" + why)
    if not run_workflow._runner_source_allowed(mode, evidence.get("runner_source")):
        raise FinalizeError(
            f"runner_source={evidence.get('runner_source')!r} 与 runner_form={runner_form!r} 不匹配")

    overall = verdict.get("overall")
    if not isinstance(overall, dict) or overall.get("verdict") != "pass":
        raise FinalizeError("精度 verdict 不是干净 pass")
    counts = overall.get("counts")
    if not isinstance(counts, dict) or any(counts.get(k, 0) != 0 for k in (
            "fail", "uncertain", "risk", "gaps", "golden_blocked", "contract_problems")):
        raise FinalizeError(f"精度 counts 不是干净零风险状态：{counts!r}")

    summary = perf_report.get("summary")
    if not isinstance(summary, dict):
        raise FinalizeError("perf_report 缺 summary")
    perf_cases = summary.get("perf_cases")
    if (summary.get("status") != "ok" or summary.get("blocked") != 0
            or not isinstance(perf_cases, int) or isinstance(perf_cases, bool) or perf_cases <= 0
            or summary.get("达标") != perf_cases
            or summary.get("cases_scored") != perf_cases
            or summary.get("non_passing") != 0):
        raise FinalizeError(f"性能不是全部可比且全部达标：{summary!r}")

    clean = "PASS"
    state = run_workflow._canonical_state(clean, summary)
    exit_code = run_workflow._exit_code(clean)
    if state != "PASSED" or exit_code != 0:
        raise FinalizeError(f"状态映射异常：state={state!r}, exit_code={exit_code!r}")
    return {
        "op": spec.get("op"),
        "overall": clean,
        "state": state,
        "exit_code": exit_code,
        "requires_human_cp": False,
        "repo_mode": evidence.get("repo_mode"),
        "gate": {"passed": True, "errors": {}},
        "precision_verdict": "pass",
        "perf_status": "ok",
        "three_layer": {
            "catlass_compare_na": verdict.get("catlass_compare_na", []),
            "risk_cases": overall.get("risk", []),
            "uncertain_cases": overall.get("uncertain", []),
            "note": "放行只看 acceptance_precision_pass；risk=acceptance 过但 standard 不过 → 人工 CP",
        },
    }


def finalize_directory(out_dir, spec_path, source_facts_path):
    """对 `out_dir` 里已落盘的验收证据生成干净 PASS `acceptance.json`。

    步骤顺序**本身就是判据**，别重排：

      0. **作废上一轮的最终裁决**（早于任何读取与校验，清不掉即拒）——Critical ①；
      1. spec 变更门 · 入口；
      2. `--source-facts` 可信性（三级门同一份判据）；
      3. `--spec` 原件 ≡ `<out>/spec.json`（CP-E staging 等值），本轮 spec 只从这里取；
      4. 读证据 → 三级门（**显式**喂 source facts）→ `build_clean_acceptance`；
      5. spec 变更门 · 出口（写盘前再校一次）→ 原子写。
    """
    out_dir = os.path.realpath(out_dir)

    # ★ **第一件事**：让 `--dir` 里上一轮的最终裁决立刻不可消费。位置就是要在**所有**读取和
    #   校验之前——下面每一道门都会早退，而每一次早退都曾把旧 PASS 原样留在原地
    #   （Critical ①）。本步只删除、不产生任何新事实，失败方向是「少一份裁决」而非
    #   「多一份假裁决」，所以可以无条件前置：`finalize_directory` 一旦被调用，
    #   `--dir` 里上一轮的裁决就**必定**作废，不管本轮是写成还是被拒。
    #   ⚠ 用的是 `_FINAL_VERDICT_FILES` 而不是主入口那份 `_RESULT_FILES`：
    #     后者含 `verdict.json` / `perf_report.json`，而它们正是本函数的**输入**。
    run_workflow.invalidate_results(
        out_dir, run_workflow._FINAL_VERDICT_FILES, error_cls=FinalizeError)

    _assert_spec_change_confirmed(spec_path, out_dir, _SPEC_GATE_ENTRY)
    _assert_source_facts_trusted(source_facts_path)
    spec = _load_verified_spec(out_dir, spec_path)
    evidence = _load(os.path.join(out_dir, "evidence.json"))
    verdict = _load(os.path.join(out_dir, "verdict.json"))
    perf_report = _load(os.path.join(out_dir, "perf_report.json"))

    gate_errors = {}
    for stage in ("task1", "task2", "task3"):
        errors = []
        # ★ **每次都显式传 `source_facts_path`**，不让门退回自动发现（口径与 `run_workflow`
        #   逐字相同）。少了这个实参，PR 通路的收据在「找不到对照物」那条分支直接放行——
        #   门看着调了，实际什么都没核。
        gate._GATES[stage](out_dir, errors, source_facts_path=source_facts_path)
        if errors:
            gate_errors[stage] = errors
    acceptance = build_clean_acceptance(
        spec, evidence, verdict, perf_report, gate_errors)

    # ② 出口门：写验收产物**之前**再校一次 spec 原件（入口过了之后它仍可能被换掉）。
    _assert_spec_change_confirmed(spec_path, out_dir, _SPEC_GATE_EXIT)

    fd, tmp = tempfile.mkstemp(prefix=".acceptance.", suffix=".json", dir=out_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(acceptance, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, os.path.join(out_dir, "acceptance.json"))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return acceptance


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="仅对已落盘、三级门全过的 clean-pass 验收证据生成 acceptance.json。"
                    "⚠ 这是第二个写验收裁决的入口，因此与 run_workflow 同门："
                    "spec 变更收据（入口+出口两处）、--spec ≡ <dir>/spec.json、"
                    "--source-facts 必给且显式喂给每一级门；"
                    "进函数第一件事先作废 <dir> 里上一轮的裁决。")
    parser.add_argument("--dir", required=True, help="run_workflow 产物目录（报告根）")
    # ⚠ 两个都 required：**不给缺省、不自动去猜路径**。「自动发现」正是要被消灭的东西——
    #   旧版没有这两个参数，于是 spec 门与 source-facts 门在这条通路上物理上不存在。
    #   老命令行会因此直接失败（argparse exit 2），这是有意的：那些调用本来就绕过了两道门。
    parser.add_argument("--spec", required=True,
                        help="spec.json **原件**路径（不是报告目录里的 staging 副本）；"
                             "须与 <dir>/spec.json 逐字节相等，且过 spec 变更门")
    parser.add_argument("--source-facts", required=True, metavar="PATH",
                        help="fetch_source.py 产的 source_facts.json；"
                             "先验可信性，再显式传给三级门作来源对账的对照物")
    args = parser.parse_args(argv)
    try:
        acceptance = finalize_directory(args.dir, args.spec, args.source_facts)
    except FinalizeError as ex:
        print(f"[finalize_clean_acceptance] REFUSED: {ex}")
        return 1
    print(json.dumps(acceptance, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
