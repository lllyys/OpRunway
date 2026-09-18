"""复测折叠核：把首轮与全部有效复测轮折成逐例有效状态（spec §6）。

纯逻辑、零 I/O：不读文件、不 import accept。输入由 A5 加载层构造，进来前已过
轮次有效性检查（spec §5）并按轮号升序；中断轮与无效轮不会出现在输入里，所以
「中断轮不撤销豁免」「无效轮结果不进历史」由构造保证，这里不再判。输入只含
算法必要字段（plan v4 §2 冻结接口）：展示、设备与预热字段不进折叠层，它们的
校验归加载层。本模块只校验该接口形状，违反即 raise ValueError——加载层的
缺陷在这里 fail-closed，不产出错裁决。
"""

import math

# 折叠规则版本，随 spec §6 的规则语义走，独立于轮次记录的 schema_version。
# verdict.json 的 fold_protocol_version 应引用这里，避免两处各记一份。
FOLD_PROTOCOL_VERSION = 1

# 测量记录合法状态集（spec §4.2）。首轮规范化记录同样受此约束：NO_REF 只出现在
# 可比期望集之外，进折叠的 case 不该有它，出现即加载层缺陷，按形状违规拒绝。
MEASURE_STATUSES = frozenset({"PASS", "FAIL", "NO_KERNEL", "CRASH", "TIMEOUT", "MISSING"})
# 证据缺口：测过但没测出可比数值的单例终态（spec §6 规则 4）。
GAP_STATUSES = frozenset({"NO_KERNEL", "CRASH", "TIMEOUT", "MISSING"})


def _is_int(value):
    # bool 是 int 的子类，轮号与计数字段收到 True/False 属加载层缺陷，不放行。
    return isinstance(value, int) and not isinstance(value, bool)


def _is_finite_number(value):
    # 规则 3 靠 ratio 最大值选代表轮，NaN 的比较恒 False、±Infinity 破坏全序，
    # 混进来会错选代表轮；json 解码器默认放行这三个值，不能赖上游挡住。
    return (_is_int(value) or isinstance(value, float)) and math.isfinite(value)


def _check_case(number, record, seen):
    if not isinstance(record, dict):
        raise ValueError(f"round {number}: 逐例记录须为 dict，得到 {type(record).__name__}")
    name = record.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"round {number}: 逐例记录缺少非空字符串 name")
    if name in seen:
        raise ValueError(f"round {number}: case {name!r} 重复（每个点名 case 恰好一条记录）")
    seen.add(name)
    status = record.get("status")
    if status not in MEASURE_STATUSES:
        raise ValueError(
            f"round {number} case {name!r}: 状态 {status!r} 不在合法集合 "
            f"{sorted(MEASURE_STATUSES)}"
        )
    ratio = record.get("ratio")
    if status in ("PASS", "FAIL"):
        if not _is_finite_number(ratio):
            raise ValueError(
                f"round {number} case {name!r}: PASS/FAIL 必须带有限数值 ratio"
            )
    elif ratio is not None:
        raise ValueError(f"round {number} case {name!r}: 证据缺口状态必须无 ratio")


def _check_measure(number, entry):
    if entry.get("waivers"):
        raise ValueError(f"测量轮 {number} 不得携带 waivers")
    cases = entry.get("cases")
    if not isinstance(cases, list):
        raise ValueError(f"round {number}: cases 须为 list")
    if number > 0 and not cases:
        # requested_cases 非空（spec §4.2），空测量复测轮只能是加载层拼装错误。
        raise ValueError(f"复测测量轮 {number} 的 cases 不得为空")
    seen = set()
    for record in cases:
        _check_case(number, record, seen)


def _check_waive(number, entry):
    if entry.get("cases"):
        raise ValueError(f"豁免轮 {number} 不得携带 cases")
    waivers = entry.get("waivers")
    if not isinstance(waivers, list):
        raise ValueError(f"round {number}: waivers 须为 list")
    seen = set()
    for item in waivers:
        if not isinstance(item, dict):
            raise ValueError(f"round {number}: waiver 项须为 dict，得到 {type(item).__name__}")
        case = item.get("case")
        if not isinstance(case, str) or not case:
            raise ValueError(f"round {number}: waiver 项缺少非空字符串 case")
        if case in seen:
            raise ValueError(f"round {number}: 豁免 case {case!r} 轮内重复")
        seen.add(case)
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason:
            raise ValueError(f"round {number}: 豁免 case {case!r} 的 reason 必填非空")


def _check_shape(rounds):
    if not isinstance(rounds, list) or not rounds:
        raise ValueError("rounds 须为非空 list（首元素为首轮规范化记录）")
    previous = None
    for index, entry in enumerate(rounds):
        if not isinstance(entry, dict):
            raise ValueError(f"rounds[{index}] 须为 dict，得到 {type(entry).__name__}")
        number = entry.get("round")
        if not _is_int(number) or number < 0:
            raise ValueError(f"rounds[{index}]: round 须为非负整数，得到 {number!r}")
        kind = entry.get("kind")
        if kind not in ("measure", "waive"):
            raise ValueError(f"round {number}: kind 只接受 measure/waive，得到 {kind!r}")
        if index == 0:
            # 复测轮编号从 1 起（spec §2），round 0 只能是首轮，且首轮必为测量记录。
            if number != 0 or kind != "measure":
                raise ValueError("rounds[0] 必须是 round 0 的首轮规范化记录（kind=measure）")
        elif number <= previous:
            raise ValueError(f"rounds 须按 round 严格升序：{previous} 之后出现 {number}")
        previous = number
        if kind == "measure":
            _check_measure(number, entry)
        else:
            _check_waive(number, entry)


def _adjudicate(records):
    """spec §6 规则 2–4：最早 PASS → FAIL 取 ratio 最大 → 证据缺口取末次测量。

    records 按轮号升序，首轮在第 0 位。返回 (有效状态, 代表轮号)。
    """
    for number, record in records:
        if record["status"] == "PASS":
            return "PASS", number
    best = None
    for number, record in records:
        if record["status"] != "FAIL":
            continue
        # 升序遍历下只在严格更大时替换，同 ratio 自然留在轮号小的那轮（规则 3 平局裁定）。
        if best is None or record["ratio"] > best[1]["ratio"]:
            best = (number, record)
    if best is not None:
        return "FAIL", best[0]
    number, record = records[-1]
    return record["status"], number


def fold(rounds):
    """折叠首轮与全部有效复测轮，返回 FoldResult（plan v4 §2 冻结形状）。

    rounds：list[Round]，rounds[0] 为首轮规范化记录（round 0），其余为有效复测轮，
    已按轮号升序。逐例算两个独立量（spec §6）——豁免态取最后一个声明，测量历史
    永久保留全部记录。输入违反接口形状 raise ValueError。
    """
    _check_shape(rounds)

    order = []                # case 首次出现序，保证输出遍历顺序确定
    seen_names = set()
    history = {}              # name -> [(轮号, 记录)]，测量历史
    declarations = {}         # name -> [(轮号, kind, reason)]；首轮不是声明
    first_round_status = {}   # pass-on-retest 的「首轮非 PASS」判据来源
    retest_measures = {}      # name -> 有效复测测量次数（不含 round 0）

    for entry in rounds:
        number = entry["round"]
        if entry["kind"] == "measure":
            for record in entry["cases"]:
                name = record["name"]
                if name not in seen_names:
                    seen_names.add(name)
                    order.append(name)
                history.setdefault(name, []).append((number, record))
                if number == 0:
                    first_round_status[name] = record["status"]
                else:
                    retest_measures[name] = retest_measures.get(name, 0) + 1
                    # 点名即再测：MISSING 占位记录同样构成撤销豁免的声明——
                    # 测量轮对每个点名 case 恰好一条记录（spec §4.2），
                    # 「再测该 case 即撤销豁免」（spec §6）以点名为准，不看测没测出数值。
                    declarations.setdefault(name, []).append((number, "measure", None))
        else:
            for item in entry["waivers"]:
                name = item["case"]
                if name not in seen_names:
                    seen_names.add(name)
                    order.append(name)
                declarations.setdefault(name, []).append((number, "waive", item["reason"]))

    per_case = {}
    warnings = []
    pass_on_retest = 0
    for name in order:
        records = history.get(name, [])
        if name not in first_round_status:
            # 首轮闭合要求下不该发生（首轮 cases 集合 = 性能期望集）；出现说明
            # 加载层放进了期望集外的 case——照常折叠但醒目告警，不让整体翻车。
            warnings.append(
                f"{name}: 无首轮记录（首轮闭合缺口），pass-on-retest 按首轮非 PASS 计"
            )
        last_declaration = declarations.get(name, [None])[-1]
        if last_declaration is not None and last_declaration[1] == "waive":
            per_case[name] = {
                "effective_status": "WAIVED",
                # 代表轮 = 使豁免态成立的豁免轮；参考轮 = 最近一次测量（若有），
                # 两个独立字段不混用（spec §6 规则 1）。
                "representative_round": last_declaration[0],
                "reference_round": records[-1][0] if records else None,
                "waive_reason": last_declaration[2],
                "measure_count": retest_measures.get(name, 0),
            }
            continue
        status, representative = _adjudicate(records)
        per_case[name] = {
            "effective_status": status,
            "representative_round": representative,
            "reference_round": None,
            "waive_reason": None,
            "measure_count": retest_measures.get(name, 0),
        }
        if status == "PASS" and first_round_status.get(name) != "PASS":
            pass_on_retest += 1

    return {
        "per_case": per_case,
        "pass_on_retest": pass_on_retest,
        "warnings": warnings,
    }
