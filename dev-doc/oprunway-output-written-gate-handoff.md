# 交接给 codex · 实施「DUT 输出未被写入」检测门

**日期**：2026-08-07
**方案原稿**：`doc/oprunway-output-written-gate.md`（在**主仓**，不在本 worktree）
**代码基线**：`.claude/worktrees/oprunway19`，HEAD `b358232`
**范围**：**只改 `plugin/acc-common/` 下的脚本。不改任何 agent 与 skill。**

> 本文是给实施者（codex）的执行交接。方案原稿讲「为什么」，本文讲「照着这个做，
> 以及有哪些坑」。**两份都要读**，冲突时以方案原稿的意图为准、以本文的 file:line 为准
> （本文的行号已逐条对当前 HEAD 核过）。

---

## 0 · 一句话

`cpp_extension` 通路用 `torch.empty` 分配输出缓冲，**脏数据与 DUT 真实输出在字节上无法区分**，
于是「DUT 没写」被原样传成「算子精度失败」。用哨兵填充让它现形，
并落成 **harness 侧失败**（`output_not_written`），**不进精度裁决**。

🔴 **最严重的后果不是误判 fail，是误判 pass。** 实证：`roll_uint8_1_scalar_a0`（x=22）
与 `roll_int8_1_scalar_a0`（x=83）的输出**同为 `0x16`**——前者 golden 恰好是 22，于是判 pass。
**在这道门补上之前，`cpp_extension` 通路判 pass 的裁决同样不可信。**

---

## 1 · 已核对的现场（行号对当前 HEAD `b358232`）

| 位置 | 现状 |
|---|---|
| `cpp_extension_driver.py:33-45` | `_TORCH_DTYPES`，**恰好 11 种**：`float32/float16/bfloat16/int64/int32/int16/int8/uint8/uint32/complex64/bool`（`uint32`、`complex64` 是 2026-08-06 真机实测往返后新加的，带 provenance 注） |
| `:266-279` | `_empty_output`，最后一行即病灶：`return torch.empty(...)` |
| `:258-262` | 上方注释正好在讲同类失败模式（「一条本该停在本地的 harness 缺陷，被写成了 DUT 的拒绝理由」），但防的是 `out_shape` 缺失，**没防「输出未被写入」** |
| `:323-325` | `FAILED_MATERIALIZE` / `FAILED_EXECUTE` / `FAILED_READBACK` 三个常量 |
| `:326-330` | `_FAILED_KIND_BY_PHASE`，**phase → kind 单键映射** |
| `:408` | `phase = "readback"` 起点；`:411-423` 是逐输出 `_dump_output` + `out_rows.append` 的循环 |
| `:424-441` | `except Exception` 兜底：`shutil.rmtree(cdir)` + `failed.append({... "error_kind": _FAILED_KIND_BY_PHASE[phase] ...})` |
| `validator.py:860` | `_EXECUTED_EV_STATUSES = ("ok", "skipped_empty")`，消费点在 `:1159` |

---

## 2 · 要做的三处改动

### A · `_empty_output` 改哨兵填充（`:279`）

新增一张覆盖**全部 11 种** dtype 的哨兵常量表：

| dtype | 哨兵 |
|---|---|
| `float32` / `float16` / `bfloat16` | `nan` |
| `complex64` | `complex(nan, nan)` |
| `int64` / `int32` / `int16` / `uint32` | `0x5A5A5A5A` 截断到位宽 |
| `int8` / `uint8` | `0x5A`（= 90，在两者值域内） |
| `bool` | 见 §3① —— **无法用哨兵区分** |

🔴 **缺任何一种 dtype 就 `raise DriverError`（fail-closed）。**
**不要**留「不认识的 dtype 就退回 `torch.empty`」的兜底——那等于这道门对新 dtype 自动失效。
⚠ 本仓刚刚才发生过一次同类事故：一道门因为配套表没跟着迁而**静默失效数轮**。别再造一个。

### B · 回读后检查是否仍是哨兵（`:411-423` 循环内）

判据是「**全部元素都等于哨兵**」，不是「存在哨兵」（理由见 §3②）。

命中就抛，让它走既有 `except` 分支（自动 `rmtree` 半截产物 + 落 `failed[]`）。

### C · 新增独立 `error_kind`（`:323-330`）

```python
FAILED_NOT_WRITTEN = "output_not_written"
```

🔴 **不要复用 `output_readback_failed`**——那个语义是「读回/落盘这一步出错」，
本问题是「**读回成功，但内容证明 DUT 没写**」。归因方向完全不同：
前者指向 harness IO，后者指向调用链或 DUT。

⚠ `readback` 阶段现在有**两种**失败，而 `_FAILED_KIND_BY_PHASE` 是单键映射（`:326-330`）——
**不够用了**。需要让抛出方能指定 kind（自定义异常子类，或在 `DriverError` 上带属性）。
这是本次唯一需要动结构的地方，别绕过它去硬塞。

---

## 3 · 三个必须处理的边界

### ① `bool` 输出无法用哨兵区分

bool 只有两个值，任何哨兵都是合法输出。

**跳过本检查**，并在 evidence 里显式记 `output_written_check: "skipped_bool"`。
🔴 **不要静默跳过**——要让「这条没被这道门保护」在产物里**可见**。

### ② 算子本身合法输出 NaN

判据用「全部元素都等于哨兵」而非「存在哨兵」，可覆盖绝大多数情况。
`numel == 1` 的 case 仍有误报可能，但**方向安全**：宁可误报 harness 故障，
也不要把没写的输出当精度失败。

若确有算子需要豁免，可加 `output_may_be_all_nan: true`，
**但必须显式声明、不给缺省**。

### ③ 空 tensor（`numel == 0`）

没有元素可检查。跳过，记 `output_written_check: "skipped_empty"`。
⚠ 与现有的 case 级 `skipped_empty` 状态不冲突（那是 case 级，这是 **output 级**）。

---

## 4 · 方案原稿没写、但你会撞到的四件事

### ⚠ (a) `rmtree` 会把「哨兵证据」一起删掉

`:425-428` 在任何失败时 `shutil.rmtree(cdir)`，理由正当（半截产物会制造「看起来有产物」的假象）。
但对 `output_not_written` 而言，那些 `.bin` **正是「DUT 没写」的物证**——删了就没法事后复核。

**自己判断并说明**：是保留一份诊断快照（比如落到单独的 `diagnostics/` 而非 `cdir`），
还是接受删除、只在 `failed[]` 里记足够的元数据（哨兵值、numel、命中比例）。
🔴 别默默沿用 `rmtree` 就当没这回事——那会让这道门抓到问题却留不下证据。

### ⚠ (b) `_dump_output` 会做 dtype 转换，检查要在转换**之前**

`:281-295` 的 `_dump_output`：`bfloat16` → 落盘转 `float32`，`bool` → 转 `uint8`。
所以哨兵检查应当作用在**转换前的 `tensor`**（`:411` 拿到的那个），不是落盘后的字节。
方案原稿说「在 `_dump_output` 之后」——那样也能拿到 `tensor`，但**别去读回落盘文件**。

### ⚠ (c) 这道门只保护 `cpp_extension`，别顺手动 catlass

catlass 通路早已有等价机制（`catlass_parse.py` 的 runner 收尾哨兵 + 运行随机数 nonce + 崩溃信号）。
**不动它。** 本次是把 `cpp_extension` 补齐到同一水平，不是重构两条通路。

### ⚠ (d) 不会影响 legacy caseset 字节

改的是 driver 侧的**运行期分配**，不是 `gen_cases` 的用例生成。
所以 `ExistingOpsByteIdenticalTest` 的 6 份 sha256 基线**不该有任何变化**——
**如果它红了，说明你改到了不该改的地方**，回头查，别去重取基线。

---

## 5 · 验收标准

🔴 **全部在服务器容器里做，不在本地**（仓规 §5.3）。

### 验收 1 · 判别实验（**先做，别跳过**）

只改完 A 之后，先跑一条 case（如 `roll_int8_1_scalar_a0`）：

| 输出 | 结论 | 下一步 |
|---|---|---|
| **全等哨兵** | DUT 完全没写这块内存 → 问题在调用侧 | 继续 B、C，**另开问题查调用侧** |
| 正确 roll 结果 | 原来只是脏数据没被覆盖 | **说明有别的路径覆盖，重新定位** |
| 其它垃圾 | 写到了别的地方 | 继续 B、C |

⚠ **这一步的结果决定后续方向。** 如果是第二种，说明整个问题诊断需要重做——如实说，别硬着头皮往下走。

### 验收 2 · 门生效

跑完整 288 例（或同等规模）：
- 原 287 条 fail 中未被写入的那些**落 `output_not_written`**，不是 `exact_mismatch`；
- `acceptance.json` / `verdict.json` 里这些 case **不出现在精度失败统计里**；
- 验收报告措辞**不得**是「算子精度失败」。

### 验收 3 · 下游链确认（**验证，不要假定**）

`validator.py:860` 的 `_EXECUTED_EV_STATUSES = ("ok", "skipped_empty")` 决定了
任何非 `ok` 状态都当「没有可比结果」。断言：
- `output_not_written` 的 case 在 evidence 侧 `status != "ok"`；
- 精度归 errored 桶，**不计入 pass 也不计入 precision fail**。

方案判断 validator 不用改。⚠ **实测确认这条链成立**，别假定。

### 验收 4 · 蒙对的那条现形

`roll_uint8_1_scalar_a0`（原判 pass，实为脏数据恰好等于 golden）
**应改判为 `output_not_written`**，不再是 pass。

⚠ **这条是本次修复价值的直接体现**，必须单独验。

### 验收 5 · 回归

- 11 种 dtype 都有哨兵定义，缺任一种 fail-closed；
- bool / 空 tensor 走 skip 分支且**在 evidence 里可见**；
- 一条**正常写入**的 case 仍判 pass（不误伤）；
- `ExistingOpsByteIdenticalTest` 仍绿且**不是 skip**（见 §6）。

---

## 6 · 本仓的门与跑测方式（照做，别自创）

### 跑测：本地不跑 pytest，在 a3 容器跑

```bash
COPYFILE_DISABLE=1 tar --no-xattrs --exclude='._*' -czf /tmp/owg.tgz plugin AGENTS.md
scp -q /tmp/owg.tgz ascend-a3:/home/liangyuansheng/oprunway_prov_work/owg.tgz
ssh ascend-a3 'docker exec oprunway_prov bash -lc "mkdir -p /work/run/owg && chown 1016:1016 /work/run/owg; cd /work/run/owg && rm -rf plugin AGENTS.md && tar xzf /work/run/owg.tgz && cd plugin/acc-common && set -o pipefail && python3 -m pytest -q -p no:randomly 2>&1 | tail -20"'
```

- 🔴 **必须带仓根 `AGENTS.md`**——漂移门有拿真文件跑的用例，不带它会红并报「读取失败」，
  那是**打包不全不是漂移**；
- 🔴 **必须 `set -o pipefail`**——`| tail` 会把 pytest 非零退出码掩成 shell 0。
  这个坑本仓刚踩过（一道门因此长期静默失效）；
- 容器内解包路径是 `/work/run/<D>.tgz`（宿主 `oprunway_prov_work` 挂在 `/work/run`）；
- 基线 **2533 passed / 10 skipped / 0 failed**，只许增不许减。

### skip 不是绿

本仓刚补了「关键测试不得静默 skip」的机器门（`CasesetBaselineAvailabilityTest`）。
⚠ **如果你的改动让某个测试从 pass 变 skip，那不是「没影响」，是门关了。**

### 真机跑测是副作用

按仓规 §5.2，clone / build / 真机跑测前**先确认**。验收 1/2/4 都要真机，
起跑前跟用户确认一次。

---

## 7 · 不做什么

- ❌ **不改任何 agent 与 skill**（用户明确要求）
- ❌ 不改 `validator.py`——现有 `_EXECUTED_EV_STATUSES` 已能正确接住
- ❌ **不把 `output_not_written` 判成精度 fail**——那仍是把 harness 故障算到被测方头上
- ❌ 不给不认识的 dtype 留 `torch.empty` 兜底
- ❌ 不动 catlass 通路
- ❌ 不在本地跑实验
- ❌ **不 commit、不 push**（由统筹方统一做）

---

## 8 · 已排除的假设（不用重查）

| 假设 | 排除依据 |
|---|---|
| `aclIntArray` 没接对 | receipt schema `invoke_v0(Tensor x, int[] shifts, int[] dims, Tensor out)`；invocation_plan `shifts: {"ctype":"int_array","value":[1]}`；生成的 C++ 用 `at::IntArrayRef` + 官方 `EXEC_NPU_CMD_EXT` |
| attrs 传错 | 唯一 pass 那条与 103 条 fail 的 attrs **完全相同** |
| 调用链整体失效 | `invocation.produced: 287/288`，只有 1 条真没跑出来 |
| 算子算错 | 单元素张量 roll 结果必等于输入；DUT 却吐出与输入无关的值（x=83 → out=22） |
| dtype 位宽误解释 | int8 的 83 = `0x53`，位重解释应为 `0x53`，实测是 `0x16` |

---

## 9 · 交付要求

报告里要有：

1. **验收 1 的实测结果**（三种可能哪一种）——这决定了问题是否如诊断所述；
2. 三处改动各自的 file:符号；
3. §4 那四件事各自怎么处置的（尤其 (a) 证据保留）；
4. **mutation 校验红绿**：至少证明「摘掉哨兵检查 → 蒙对的那条重新判 pass」会变红；
5. 验收 2/3/4/5 的实测数据；
6. **如实说没做到的**——包括「验收 1 结果不符合预期所以停下」这种结论。

🔴 本仓最贵的缺陷类型是 **fail-open** 与**假门**（删掉被测代码测试仍绿）。
一个宣称过强的门比没有门更危险。若某件事静态上证不了，
把宣称改成它**实际能证**的那句话，并把证不了的部分显式挂账。
