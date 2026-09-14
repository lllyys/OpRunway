#!/usr/bin/env python3
"""性能量测骨架（Profiler Kernel 总耗时口径）：任务书给绝对指标、自带件覆盖不到时照抄改。

与同目录的 `perf_harness.py` 分工按**口径**，不按算子：

| 骨架 | 主口径 | 什么时候用 |
| --- | --- | --- |
| `perf_harness.py` | 一次 `invoke()` 两端的墙钟 | 任务书按调用耗时给指标 |
| 本件 | Profiler 逐次调用的 Kernel 总耗时 | 任务书拿 GPU 的 NCU Kernel 总耗时当标杆，要同量纲 |

**只改文件顶部那一段扩展点，别动下面的。** 采样循环、产物解析、统计量、输出格式
四件事一旦各写各的，两轮跑出来的数就没法比。三个扩展点：

| 扩展点 | 改什么 | 不改会怎样 |
| --- | --- | --- |
| `DTYPES` | 补任务书判据表里有、而这里没有的 dtype | 那些场景开跑就报 `Unsupported dtype` |
| `build_sparse` | 换稀疏格式或分布（默认给 CSR 精确 nnz） | 造不出判据表要的 nnz 与行长分布 |
| `build_call` | 换被测算子与它的入参 | 量的是别的算子 |

用例文件是 `{"cases": [...]}` 或裸列表，每条至少要 `id`、`dtype`、造数要用到的
形状字段；`baseline_us` 在条目里给，或用 `--baseline` 给一张表。产出直接是
`collect_perf.py` 的交换格式：

    python3 <skill>/scripts/perf_lock.py --stage stage --who "判据表 8 场景" --devices 0 -- \\
        python3 perf_harness_kernel.py --cases perf_cases_p_table.json \\
        --device 0 --warmup 10 --samples 30 -o /tmp/ext.json
    python3 <skill>/scripts/collect_perf.py --from /tmp/ext.json \\
        -c ../input/cases.json --facts ../input/facts.json \\
        -o stage/performance_external.json

**造完的稀疏输入当场自检**（`check_sparse`）：nnz、crow 单调、行内列严格递增
无重复、列域。不合法退 3 整轮作废——算子侧的结论只有建立在合法输入上才成立。
换成 CSR 以外的 layout 时把对应检查补进 `check_sparse`，别让这一步静默失效。

**走 torch 算子注册的被测实现要给 `--preload`**（如 `--preload ops_xxx_torch`）：
不 import 注册模块时 dispatch 落到别的实现，量到的不是待验收的那一份。
配 `--expect-lib <so 名>` 开跑前核一次 `/proc/self/maps`，核不到退 3。

**外层那把锁不要省。** 性能采集期间本现场并发第二个 NPU 任务时，两轮的数互相
污染而退出码都是 0，判据见 references/external-perf.md「现场级排他锁」。

**预热与采样次数是任务书规定的验收口径**，通常写成硬性下限（实测一例「NPU 侧
每个 case 至少预热 10 次、正式采样 30 次」），低于它的数不能当验收依据。
"""
from __future__ import annotations

import argparse
import csv
import importlib
import json
import sys
from pathlib import Path

import torch
import torch_npu  # noqa: F401 - 注册 npu 后端与 profiler

# ================================ 扩展点 ================================
# 下面这一段按被测算子改，别的都不用动。

DTYPES = {
    "float16": torch.float16,
    "bfloat16": torch.bfloat16,
    "float32": torch.float32,
    "complex64": torch.complex64,
}


def build_sparse(rows, cols, nnz, dtype, device, seed):
    """精确 nnz 的 CSR：行长为 base 或 base+1，行内列严格递增、无重复坐标。

    **判据表的 nnz 除以行数通常不是整数**（实测一例 10556 / 2708 = 3.898），
    等长行造不出来，所以前 `rem` 行长 `base+1`、其余 `base`。列取
    `(i*base + j) mod cols`，同行内 j 连续且行长不超过 cols，故互不相同；
    再按 (行, 列) 全局排序，满足任务书对「已按列索引排序、已合并重复坐标」的要求。

    换稀疏格式（COO、BSR）或换分布（等长行、空行、长尾）时改这里。
    """
    base, rem = divmod(nnz, rows)
    lengths = torch.full((rows,), base, dtype=torch.int64, device=device)
    lengths[:rem] += 1
    crow = torch.cat([
        torch.zeros(1, dtype=torch.int32, device=device),
        lengths.cumsum(0).to(torch.int32),
    ])
    row_ids = torch.repeat_interleave(
        torch.arange(rows, dtype=torch.int64, device=device), lengths)
    row_starts = torch.repeat_interleave(lengths.cumsum(0) - lengths, lengths)
    within = torch.arange(nnz, dtype=torch.int64, device=device) - row_starts
    starts = (torch.arange(rows, dtype=torch.int64, device=device) * base) % cols
    col = (torch.repeat_interleave(starts, lengths) + within) % cols
    key = torch.sort(row_ids * cols + col).values
    values = rand_values(nnz, dtype, device, seed)
    return torch.sparse_csr_tensor(crow, (key % cols).to(torch.int32), values,
                                   size=(rows, cols), device=device)


class HarnessError(RuntimeError):
    """本侧造的数不合法。**整轮作废，不是某一条失败。**"""


def check_sparse(tensor, rows, cols, nnz):
    """造完当场核一遍，**不合法就别开跑**。

    这一步顶掉的是事后的「造数嫌疑」复验。算子在某个形态上失败时，只有先证明
    本侧造的数合法，「算子侧失败」才是个结论；放到事后去证，会变成一轮漫无
    边际的对照实验（实测一例花掉 8 分钟）。

    **判据不是本仓定的**，与 torch 自己的不变量检查一致，别放宽：

        torch.sparse.check_sparse_tensor_invariants.enable()   # 对拍用

    实测 torch 2.10 对「行内未排序」与「行内重复坐标」都拒绝构造；重复坐标那种
    尤其要拦，不拦的话 `to_dense()` 会静默少一个非零值。这里手写而不调官方检查器，
    是因为后者只在构造时生效，事后核要重新构造一遍张量——判据表的稀疏矩阵到
    六千万 nnz，重建一次的代价不值得。

    CSR 之外的 layout 跳过，返回 `False` 让调用方打印一行「未自检」——换成
    COO 或 BSR 时把对应的检查补进来，别让这一步静默失效。
    """
    if tensor.layout is not torch.sparse_csr:
        return False
    crow, col = tensor.crow_indices(), tensor.col_indices()
    if not (int(crow[-1]) == nnz == col.numel() == tensor.values().numel()):
        raise HarnessError(f"nnz 对不上：crow[-1]={int(crow[-1])} "
                           f"col={col.numel()} values={tensor.values().numel()} 要 {nnz}")
    if int(crow.numel()) != rows + 1:
        raise HarnessError(f"crow 长度 {int(crow.numel())}，要 {rows + 1}")
    lengths = (crow[1:] - crow[:-1]).to(torch.int64)
    if bool((lengths < 0).any()):
        raise HarnessError("crow 不是非降的")
    if bool((col < 0).any()) or bool((col.to(torch.int64) >= cols).any()):
        raise HarnessError(f"列号越界，要落在 [0, {cols})")
    # 行内严格递增（跨行的相邻差不算），同时排掉重复坐标
    step = col[1:].to(torch.int64) - col[:-1].to(torch.int64)
    at_row_start = torch.zeros(col.numel(), dtype=torch.bool, device=col.device)
    at_row_start[crow[:-1].to(torch.int64)] = True
    if col.numel() > 1 and not bool((step[~at_row_start[1:]] > 0).all()):
        raise HarnessError("行内列号不是严格递增的——没排序，或有重复坐标")
    return True


def build_call(case, device):
    """返回一个无参的 `invoke()`，调一次算子。**造数在这个函数外面做完**。

    循环外建好输入，`invoke()` 里只留被测调用本身——放进去的东西都会被计进耗时。
    """
    dtype = DTYPES[case["dtype"]]
    m, k, n = int(case["m"]), int(case["k"]), int(case["n"])
    seed = int(case["seed"])
    nnz = int(case["nnz"])
    mat1 = build_sparse(m, k, nnz, dtype, device, seed)
    if not check_sparse(mat1, m, k, nnz):
        print(f"    {case['id']} 的稀疏输入是 {mat1.layout}，未自检",
              file=sys.stderr)
    mat2 = rand_dense((k, n), dtype, device, seed + 1)
    dense_input = rand_dense((m, n), dtype, device, seed + 2)
    alpha, beta = float(case.get("alpha", 1.0)), float(case.get("beta", 1.0))

    def invoke():
        return torch.sparse.addmm(dense_input, mat1, mat2, beta=beta, alpha=alpha)

    return invoke


# ============================== 以下不用改 ==============================

WARMUP = 10
SAMPLES = 30
MIN_SAMPLES = 3            # 再少 p90 没有意义


def _skill_scripts(explicit):
    """`kernel_trace` 从 skill 的 scripts 目录来——认列规则不留第二份。"""
    if explicit:
        return Path(explicit)
    return (Path(__file__).resolve().parent.parent / "scripts")


def rand_values(nnz, dtype, device, seed):
    """复数走实虚部分别生成；半精度先按 fp32 生成再转，与自带件同构。"""
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed)
    if dtype.is_complex:
        real = torch.randn(nnz, generator=generator, dtype=torch.float32)
        imag = torch.randn(nnz, generator=generator, dtype=torch.float32)
        return torch.complex(real, imag).to(device)
    return torch.randn(nnz, generator=generator, dtype=torch.float32).to(dtype).to(device)


def rand_dense(shape, dtype, device, seed, chunk_elements=4_000_000):
    """**分块搬**：判据表的稠密矩阵到十亿元素级，一次性建完主机侧就爆内存。"""
    rows, cols = shape
    result = torch.empty(shape, dtype=dtype, device=device)
    generator = torch.Generator(device="cpu")
    generator.manual_seed(seed)
    rows_per_chunk = max(1, chunk_elements // max(1, cols))
    for start in range(0, rows, rows_per_chunk):
        end = min(rows, start + rows_per_chunk)
        if dtype.is_complex:
            real = torch.randn((end - start, cols), generator=generator, dtype=torch.float32)
            imag = torch.randn((end - start, cols), generator=generator, dtype=torch.float32)
            chunk = torch.complex(real, imag)
        else:
            chunk = torch.randn((end - start, cols), generator=generator, dtype=torch.float32)
        result[start:end].copy_(chunk.to(dtype).to(device))
    return result


def profile_case(invoke, trace_dir, warmup, samples):
    """按 torch_npu 的 schedule 采样：前 warmup 步不进产物，后 samples 步进。"""
    schedule = torch_npu.profiler.schedule(wait=0, warmup=warmup, active=samples,
                                           repeat=1)
    torch.npu.synchronize()
    with torch_npu.profiler.profile(
        activities=[torch_npu.profiler.ProfilerActivity.CPU,
                    torch_npu.profiler.ProfilerActivity.NPU],
        schedule=schedule,
        on_trace_ready=torch_npu.profiler.tensorboard_trace_handler(str(trace_dir)),
        record_shapes=False, profile_memory=False, with_stack=False,
    ) as profiler:
        for _ in range(warmup + samples):
            invoke()
            profiler.step()
    torch.npu.synchronize()


def _lib_loaded(name):
    """待验收实现在不在本进程里。

    **注册进 ATen 已有算子 NPU 键的那一档尤其要核**：so 没装进来时调用不抛异常，
    dispatcher 静默落到别的实现，报告一切正常而量的是别处的同名算子。
    """
    try:
        maps = Path("/proc/self/maps").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return True          # 读不到就不拦，把量测卡死在诊断步骤上更糟
    return name in maps


def load_baseline(path, metric):
    if not path:
        return {}
    path = Path(path)
    if path.suffix == ".json":
        return {str(k): float(v) for k, v in
                json.loads(path.read_text(encoding="utf-8")).items()}
    out = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                out[str(row["id"])] = float(str(row[metric]).replace(",", ""))
            except (ValueError, KeyError, TypeError):
                continue
    return out


def measure(case, device, warmup, samples, trace_root, trace):
    invoke = build_call(case, f"npu:{device}")   # 造数与自检先做完
    trace_dir = Path(trace_root) / str(case["id"])
    trace_dir.mkdir(parents=True, exist_ok=True)
    profile_case(invoke, trace_dir, warmup, samples)
    path = trace.newest(trace_dir)
    if path is None:
        raise FileNotFoundError(f"{trace_dir} 下没有 {trace.DETAILS}")
    values = trace.active_tail(trace.per_call_us(path), samples)
    if len(values) < MIN_SAMPLES:
        raise RuntimeError(f"有效采样只有 {len(values)} 次，p90 没有意义")
    # 统计量口径与认列规则一样不留第二份：中位数与 90%分位都由 `trace.summarize`
    # 出，判据（含任务书对 p90 的定义原话）在那个模块的「统计量」一节。
    got = trace.summarize(values)
    return {**got, "samples_used": got.pop("samples", len(values)),
            "warmup": warmup, "samples": samples}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cases", required=True)
    ap.add_argument("--baseline", default="",
                    help="标杆：id→us 的 json，或带 --metric-column 那一列的 csv")
    ap.add_argument("--metric-column", default="kernel_total_us")
    ap.add_argument("--device", default="0", help="卡号，一个算子占一张")
    ap.add_argument("--warmup", type=int, default=WARMUP)
    ap.add_argument("--samples", type=int, default=SAMPLES)
    ap.add_argument("--trace-root", default="stage/perf_traces")
    ap.add_argument("--preload", default="",
                    help="开跑前 import 的模块，逗号分隔。**走 torch 算子注册的"
                         "被测实现必须给**：不 import 注册模块时 dispatch 落到"
                         "别的实现，量到的不是待验收的那一份")
    ap.add_argument("--expect-lib", default="",
                    help="待验收实现的 so 名，开跑前核 /proc/self/maps 里在不在")
    ap.add_argument("--skill-scripts", default="",
                    help="本件拷出 assets/ 时给 skill 的 scripts 目录")
    ap.add_argument("--source", default="任务书性能判据表 · perf_harness_kernel.py")
    ap.add_argument("-o", "--out", required=True)
    ns = ap.parse_args()

    sys.path.insert(0, str(_skill_scripts(ns.skill_scripts)))
    try:
        import kernel_trace as trace
    except ImportError:
        print("找不到 kernel_trace.py。本件拷出 assets/ 之后要用 --skill-scripts "
              "指到 skill 的 scripts 目录——Profiler 产物的认列规则在那里，"
              "不在本件里复制一份。", file=sys.stderr)
        return 3

    for name in [m.strip() for m in ns.preload.split(",") if m.strip()]:
        try:
            importlib.import_module(name)
        except ImportError as exc:
            print(f"--preload 的 {name} import 不了：{exc}", file=sys.stderr)
            return 3
        print(f"preload {name}", file=sys.stderr)
    if ns.expect_lib and not _lib_loaded(ns.expect_lib):
        print(f"进程里没有 {ns.expect_lib}——待验收实现没装进来，"
              f"这一轮量到的是别处的同名实现。核 --preload 与 LD_LIBRARY_PATH。",
              file=sys.stderr)
        return 3

    payload = json.loads(Path(ns.cases).read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload
    baseline = load_baseline(ns.baseline, ns.metric_column)
    Path(ns.trace_root).mkdir(parents=True, exist_ok=True)
    torch.npu.set_device(int(ns.device))

    rows = []
    for index, case in enumerate(cases, 1):
        print(f"[{index}/{len(cases)}] {case['id']}", flush=True)
        try:
            got = measure(case, ns.device, ns.warmup, ns.samples, ns.trace_root, trace)
        except HarnessError as exc:
            print(f"本侧造的数不合法：{exc}\n"
                  f"整轮作废——先把 build_sparse 改对，算子侧的结论要建立在"
                  f"合法输入上。", file=sys.stderr, flush=True)
            return 3
        except Exception as exc:  # noqa: BLE001 - 一条挂了不拖整轮
            print(f"    FAILED {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            rows.append({"id": case["id"], "label": case.get("label", case["id"]),
                         "error": f"{type(exc).__name__}: {exc}"})
            continue
        row = {"id": case["id"], "label": case.get("label", case["id"])}
        row.update(got)
        base = case.get("baseline_us", baseline.get(str(case["id"])))
        if base is not None:
            row["baseline_us"] = float(base)
            row["perf_ratio"] = round(float(base) / got["under_test_us"], 6)
        rows.append(row)
        print(f"    kernel 中位数 {got['under_test_us']:.3f} us，p90 {got['p90_us']:.3f} us"
              + (f"，倍率 {row['perf_ratio']:.4f}" if "perf_ratio" in row else ""),
              flush=True)

    ok = [r for r in rows if "under_test_us" in r]
    out = {
        "source": ns.source, "unit": "us", "metric": "kernel_total_us",
        "warmup": ns.warmup, "samples": ns.samples,
        "cases": [{k: r[k] for k in ("id", "under_test_us", "baseline_us", "label")
                   if k in r} for r in ok],
        "detail": rows,
    }
    Path(ns.out).parent.mkdir(parents=True, exist_ok=True)
    Path(ns.out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    print(f"写入 {ns.out}：{len(ok)}/{len(rows)} 条跑出数")
    if not ok:
        return 2
    return 0 if len(ok) == len(rows) else 4


if __name__ == "__main__":
    sys.exit(main())
