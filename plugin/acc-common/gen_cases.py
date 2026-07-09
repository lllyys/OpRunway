"""Task 1 · gen_cases — spec.json -> caseset.json (+ per-case input/golden .npy).

Layer 1 确定性脚本（工具中立、op 驱动）。据 spec（参数 arity/attrs、verify_mode）× dtype × shape × 泛化
生成用例，用参考实现算 golden（逐算子分发；golden_source 记来源，不设全局假设）。
支持 IsClose（二元、bool、exact）、Sign（一元、同 dtype、numerical）。加算子 = 注册 GOLDEN[op]。
确定性：固定种子 SEED，无时间/系统随机。
"""
import json, os, sys
import numpy as np
import precision_policy

SEED = 2026
_DTYPES = {"float32": np.float32, "float16": np.float16, "int32": np.int32}


def _resolve_acceptance(spec, standard, dtype):
    """任务书验收目标口径（可选、独立于平台 standard）：spec.precision.acceptance_policy。

    形如 {"standard": "ascendoptest_default", "error_rate": 0.1}：以某标准为底 + 覆盖判据字段。
    返回 (policy, tolerance_policy_id)；无声明 → None（validator 时 acceptance 继承 standard）。
    """
    ap = (spec.get("precision") or {}).get("acceptance_policy")
    if not ap:
        return None
    ap_std = ap.get("standard", standard)
    pol = precision_policy.threshold_for(ap_std, dtype)
    for k in ("tolerance", "error_rate", "threshold", "max_ratio", "eps"):
        if k in ap:
            pol[k] = ap[k]
    return pol, precision_policy.tolerance_policy_id(ap_std, dtype)


def _np_dtype(name):
    if name not in _DTYPES:
        raise ValueError(f"unsupported dtype {name!r}, supported={list(_DTYPES)}")
    return _DTYPES[name]


# ---- golden 参考实现（逐算子；inputs=按 spec 顺序的输入数组，attrs=属性字典） ----
def golden_isclose(inputs, attrs):
    return np.isclose(inputs[0], inputs[1], rtol=attrs["rtol"], atol=attrs["atol"],
                      equal_nan=attrs["equal_nan"])


def golden_sign(inputs, attrs):
    return np.sign(inputs[0])


def golden_equal(inputs, attrs):
    return np.equal(inputs[0], inputs[1])


def golden_neg(inputs, attrs):
    return np.negative(inputs[0])


GOLDEN = {"IsClose": ("numpy np.isclose", golden_isclose),
          "Sign": ("numpy np.sign", golden_sign),
          "Equal": ("numpy np.equal", golden_equal),
          "Neg": ("numpy np.negative", golden_neg)}


def _gen_input(rng, shape, dt, kind, atol, rtol, ref=None):
    """造一个输入。kind='pair_far'：与 ref 前半 near(→True)后半 far(→False)；'varied'：含负/零/正。"""
    if kind == "pair_far":
        near = (ref * (1.0 + rng.uniform(-rtol, rtol, size=shape))
                + rng.uniform(-atol, atol, size=shape)).astype(dt)
        far = (ref + 0.1 + rng.uniform(0.05, 0.2, size=shape)).astype(dt)
        x = far.copy().reshape(-1)
        x[: x.size // 2] = near.reshape(-1)[: x.size // 2]  # 前半 near、后半 far → golden 混合
        return x.reshape(shape)
    if kind == "pair_half":  # 前半严格相等(→True)、后半+1(→False)：exact-equal 类(Equal)混合覆盖
        x = ref.astype(dt).copy().reshape(-1)
        x[x.size // 2:] = (x[x.size // 2:] + dt(1)).astype(dt)
        return x.reshape(shape)
    x = rng.uniform(-5.0, 5.0, size=shape).astype(dt)
    if kind == "varied" and x.size >= 3:  # 保证含负/零/正（Sign 全分支覆盖）
        f = x.reshape(-1)
        f[0], f[1], f[2] = dt(-2.0), dt(0.0), dt(3.0)
    return x


def gen_cases(spec, work_dir):
    op = spec["op"]
    if op not in GOLDEN:
        raise ValueError(f"unsupported op {op!r}, supported={list(GOLDEN)}")
    src_name, golden_fn = GOLDEN[op]
    rng = np.random.default_rng(SEED)
    in_params = [p for p in spec["params"] if p["io"] == "in"]
    attrs = {p["name"]: p.get("default") for p in spec["params"] if p["io"] == "attr"}
    self_param = next((p for p in in_params if p["name"] == "self"), in_params[0])
    dtypes = self_param["dtype"]
    standard = precision_policy.select_standard(spec)   # 平台层标准（显式或按 oracle+verify_mode 映射）
    vmode = spec["verify_mode"]
    exact = vmode == "exact"
    os.makedirs(work_dir, exist_ok=True)

    plan = []  # (dims, shape, dtype, tags)
    for dt in dtypes:
        for shp in [(16,), (4, 4)]:
            plan.append((["功能", "精度"], shp, dt, ["常规"]))
    if len(in_params) == 2:  # 二元才有广播用例
        plan.append((["功能", "精度"], "broadcast", "float32", ["泛化", "广播"]))
    plan.append((["性能"], (1024, 1024), "float32", ["性能", "大shape"]))
    # T6：spec 声明小 shape 例外 → 追加 ≥2 个小 shape 性能用例（dtype 从 spec 取；shape 为当前
    # elementwise 算子族(Sign/IsClose/Equal/Neg) 的 fixture 默认，非通用规则——带 dtype_combinations/
    # 特殊 shape 的算子需各自派生规则，见 doc TODO follow-up）。无此声明的 spec 行为完全不变。
    if (spec.get("perf") or {}).get("small_shape_exception"):
        sdt = dtypes[0]
        for shp in [(64,), (256,)]:
            plan.append((["性能"], shp, sdt, ["性能", "小shape"]))

    cases = []
    for i, (dims, shp, dtn, tags) in enumerate(plan):
        cid = f"{op.lower()}_{i:03d}"
        cdir = os.path.join(work_dir, cid)
        os.makedirs(cdir, exist_ok=True)
        dt = _np_dtype(dtn)
        inputs, ishapes = [], []
        for j, p in enumerate(in_params):
            if shp == "broadcast":  # 仅二元：self (4,1) vs other (1,5)
                s = (4, 1) if j == 0 else (1, 5)
                x = _gen_input(rng, s, dt, "varied", attrs.get("atol", 0), attrs.get("rtol", 0))
            elif j == 1:  # 二元第二输入
                if "rtol" in attrs:  # close 类(IsClose)：跨 tol 边界（near/far）
                    x = _gen_input(rng, shp, dt, "pair_far", attrs["atol"], attrs["rtol"], ref=inputs[0])
                else:  # exact-equal 类(Equal)：前半严格相等、后半不等
                    x = _gen_input(rng, shp, dt, "pair_half", 0, 0, ref=inputs[0])
            else:
                x = _gen_input(rng, shp, dt, "varied", attrs.get("atol", 0), attrs.get("rtol", 0))
            inputs.append(x)
            ishapes.append(list(x.shape))
        golden = golden_fn(inputs, attrs)
        if not exact:
            golden = golden.astype(dt)  # numerical：输出同 dtype
        if exact and golden.dtype == bool and golden.size > 1:
            assert golden.any() and (~golden).any(), f"{cid}: golden 未覆盖 True/False 边界"
        for j, x in enumerate(inputs):
            np.save(os.path.join(cdir, f"x{j + 1}.npy"), x)
        np.save(os.path.join(cdir, "golden.npy"), golden)
        # 精度口径 per-case：按 golden/输出 dtype 解析 policy（非输入 dtype）；未支持 dtype fail-fast
        cdtype = precision_policy.compare_dtype(golden)
        policy = precision_policy.threshold_for(standard, cdtype)
        tpid = precision_policy.tolerance_policy_id(standard, cdtype)
        expected = {"golden_source": src_name, "golden_path": f"{cid}/golden.npy",
                    "verify_mode": vmode, "standard": standard, "compare_dtype": cdtype,
                    "tolerance_policy_id": tpid, "policy": policy,
                    "threshold": precision_policy.threshold_digest(policy)}  # digest：向后兼容
        acc = _resolve_acceptance(spec, standard, cdtype)
        if acc:
            expected["acceptance_policy"], expected["acceptance_tolerance_policy_id"] = acc
        cases.append({
            "id": cid, "dims": dims, "tags": tags,
            "inputs": [{"name": in_params[j]["name"], "shape": ishapes[j], "dtype": dtn,
                        "path": f"{cid}/x{j + 1}.npy"} for j in range(len(inputs))],
            "attrs": attrs,
            "expected": expected,
        })
    attr_order = [p["name"] for p in spec["params"] if p["io"] == "attr"]
    return {"op": op, "spec_ref": spec.get("op"), "work_dir": work_dir,
            "attr_order": attr_order, "cases": cases}


def main(argv):
    spec_path, work_dir, out_path = argv[0], argv[1], argv[2]
    spec = json.load(open(spec_path, encoding="utf-8"))
    caseset = gen_cases(spec, work_dir)
    json.dump(caseset, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[gen_cases] {caseset['op']}: {len(caseset['cases'])} cases -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1:])
