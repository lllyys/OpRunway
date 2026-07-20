---
title: opp is provenance-bound to the op source with a fail-closed gate
updated: 2026-07-16
status: verified
---

# opp is provenance-bound to the op source with a fail-closed gate

真机 Task 2 用 route B（用户态 opp，`ASCEND_CUSTOM_OPP_PATH`）跑被测算子。opp 一旦建成会被后续跑测**复用**——
省编译，但引出一个验收级风险：**复用的 opp 可能来自异源或旧源**，「实测跑过且全过」未必等于「验收了本任务书
指定的那份 op 源码」。本页记 `run_on_npu.sh` 把 opp **绑到真实 op 源**、并以 fail-closed 门拦住 stale/异源复用的机制。

**绑源.** `OPRUNWAY_OP_SRC`（被测 op 源码相对 OPS 仓的子路径，如 `experimental/math/is_close`）**必填**。
`OPHASH = sha256(该源目录下所有文件内容)`；源目录不存在/无文件 → **fail-closed exit 3**（不吞 `find` 错误产恒定
空 hash）。opp 落一份独立 stamp `.oprunway_opp_provenance`，内容 `op_src|ophash|soc|vendor|build`（`build` 记
是否 `--experimental` 及 `--ops=<源目录名>`）。

**每次跑的门.** 缺 opp → 建；stamp 全字段与本次期望相符 → 复用不重建；**不符/缺失 → fail-closed exit 4 拒复用**
（打印期望 vs 实得），除非 `OPRUNWAY_OPP_REBUILD=1` 显式授权从源重建（含 `rm -rf` 旧 vendor 目录）。opp 重建时
级联刷新 runner stamp（`OPP_ID`），保证 opp 换了 runner 必重链。build 失败/无 `.run` → exit 5。

**op_src 安全校验（纵深防御，两层）.** `repo_adapter._ne_cfg` 与 `run_on_npu.sh` 各自校验 `OPRUNWAY_OP_SRC` 须为
**安全的 ≥2 段嵌套相对路径**：`posixpath.normpath` 强制 canonical，拒前导 `/`、`..`、`.`/`.` 段、尾斜杠、裸子树根
（如 `experimental`）、非 `[A-Za-z0-9_./-]` 字符。放行 `.` 或裸子树会让 OPHASH 绑**整仓**、跳 `--experimental`、
且 stamp **非算子专属**（同仓不同算子得同 stamp）→ 算子 B 复用算子 A 的 opp 假通过。二者是同类洞、走两扇门。

**教训（本页存在的理由）.** 「实测跑过」≠「验收了正确的东西」。一个真实案例：`run_on_npu.sh` 曾漏一行
`OP_SRC="$OPRUNWAY_OP_SRC"` 短名桥接 → `$OP_SRC` 恒空 → OPHASH 绑整仓、`--experimental` 没走 → **实际建/测的是
异源**（非 A2/A3 正源），却 Task2 全 pass。不绑源的 provenance 不仅可能 stale、还可能连源都错——**fail-closed 绑源
是防「测了个假东西还全绿」的唯一闸**。本机制是 [[Verification code provenance for runner and golden]] 在「被测 opp
构建产物」维的延伸；与 [[Evidence provenance binding proves metrics from files not files from NPU]] 互补（后者绑
metrics↔产物文件、本页绑 opp↔op 源）。

**诚实边界.** 本机制证「opp 由 `OPRUNWAY_OP_SRC` 指的那份源、按记录的构建参数建成」，**不**证「该源就是被测 PR 的
正确算子源」——选哪份源仍由编排层据任务书 + `op_def` 判定（[[Target hardware and dtype set are determined per operator from taskdoc and op_def]]），本机制只保证「建/复用的确是它、没被 stale 异源冒充」。

**Verified.** 2026-07-16 核，均存在：`plugin/acc-common/new_example/run_on_npu.sh`（`OPHASH` 绑 `$OPS/$OP_SRC`、
`.oprunway_opp_provenance` stamp、exit 3/4/5 fail-closed 门、`OP_SRC` 短名桥接 + 脚本侧 `OP_SRC` 守卫）、
`plugin/acc-common/repo_adapter.py`（`_ne_cfg` 的 `op_src` 必填 + `normpath` 嵌套路径校验、`opp_rebuild` 透传）。
真机坐实：a3 CANN 9.0.1 容器从 `experimental/math/is_close` 重建、ophash 与真源逐字节一致、fail-closed 三情形实测。

**Sources.** [[session 2488e031-5814-4c61-a723-56aeeb1e6029 · 2026-07-13]]（2026-07-16：provenance 机制 + 致命 OP_SRC bug 修 + a3 容器坐实）
