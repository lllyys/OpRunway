# 并发拓扑与入口命令

P0 参数收齐、准备起跑时读这份。

「示例跑测（examples）」= build → install → 真机逐算子跑 `examples/test_aclnn_*.cpp`，
即历史命名的 phase1（`run_state.json` 里仍用 `phase1` 键，保持兼容）。

跑测范围当前**仅示例跑测**。kernel UT / pytest / msprof 未开放（脚本暂存 `scripts/_attic/`，
勿调用），相关请求如实告知用户暂不受理。

## 三种拓扑

仓路径来自 P0 的 `--repo-mapping repo1=path1,repo2=path2,…`；SOC 用 `--soc <soc>`；
算子用 `--ops` / `--ops-file`，或走 fallback。

| 场景 | 仓间并发 | 仓内并发 | 加速比 |
| --- | --- | --- | :---: |
| **A. 多仓全量** | N worker ProcessPool | 合并 build（`--ops=op1,…,opN`）+ 串行 install + 逐个 run_example | 6–10× |
| **B. 单仓全量** | — | 同上合并 build | 3–5× |
| **C. 单算子** | — | 单算子三步 | 1× |

```bash
# A / B —— mapping 填一项就是 B
<python> <skill>/scripts/run_phase1_batched.py \
  --repo-mapping <r1>=<p1>,<r2>=<p2> --soc <soc> [--ops <csv> | --ops-file <path>]

# C
<python> <skill>/scripts/phase_examples.py \
  --repo <name> --repo-path <path> --soc <soc> --op <op>

# 合并 build 连坐兜底 —— 逐算子重跑，还原每个算子的真实状态
<python> <skill>/scripts/run_phase1_fallback.py \
  --repo-mapping <r1>=<p1>,… --soc <soc> [--statuses BUILD_FAIL,INSTALL_FAIL]
```

**为什么要兜底**：A/B 把整仓算子合并成一次 build 以省时间，一个算子编不过会把同批其余算子
一起判成 `BUILD_FAIL`。兜底轮逐算子重跑，把被连坐的还原成真实状态。

**禁止用 `phase_examples.py --op` 起多进程跑同仓多算子**——同仓共用 `CMakeCache.txt`，
并发写会互相踩，结果不可信。要并发就用 A/B 的合并 build。

## 退出码

| 退出码 | 含义 | 怎么办 |
| --- | --- | --- |
| `0` | `COMPLETE`，三队列全空 | 汇报 PASS/FAIL 汇总 |
| `2` | usage 错误 / 算子清单解析失败 | 修参数重跑 |
| `3` | `ACTION_REQUIRED`，收尾闸门有未处理项 | **不得声称跑测完成**，读 `postrun_actions.json` 后按 `failure-followup.md` 执行 |
| `4` | 命令模板解析不到 | 回 P2，见 `run-params.md`。`run_phase1_batched.py` 会跳过该仓、其余仓跑完后整体也退 4 |

## 跑测期间不要轮询日志

跑测是小时级的，每次 `tail` 或读日志都要重放整个上下文，代价远超它带来的信息。
起完后台任务就**等它退出**——退出码与 `postrun_actions.json` 的三队列才是权威收尾信号。

只在这三种情况下读日志：① 任务已退出后收尾；② 用户主动问进度（此时 tail 一次，别循环）；
③ 诊断某个具体失败算子。

`/tmp/phase1_*.log` 里的实时进度（per repo `[{repo}] {N_pass}/{N_total} PASS, build={s}s, …`；
per op `[{repo}] [i/N] {symbol} {op}: {status}`）是给人看的，不是让 agent 盯的。

## 续跑

已 PASS 的算子跳过；`BUILD_FAIL` / `INSTALL_FAIL` 自动重试，`attempts` 累加。
