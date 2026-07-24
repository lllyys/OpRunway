# golden.py 产出手册（`gen_golden` 用）

`acc-runner-dev` 的 `gen_golden` 模式据**任务书**为一个算子产出 `<ops_root>/<op>/golden.py`。

**为什么这个 mode 存在**：`gen_cases.load_golden` 缺 golden.py 就 fail-closed，而在批 6 之前
**全流程没有任何一环产它**——`gen_cases` 的报错文本、`acc-casegen` skill 都写着「由 acc-spec/acc-runner-dev 产」，
但那两个 agent 的 dispatch 表里都没有这件事。Pdist 首跑撞的就是这个洞（人肉补 golden.py 才走得下去）。
批 6 把产出者钉在本 agent：golden.py **是 Python 代码**、性质同 runner.cpp（会被 import 执行、同信任级，
ADR 0011 决策 6），归产代码的 agent；`acc-spec-extractor` 产的是 JSON 数据、且带禁读纪律，不承担这件事。

---

## 0. 前置：任务书快照必须先入库

授权引文要能被机器复核（R12），任务书全文快照必须与 golden.py **同处算子目录**：

```bash
cd ${CLAUDE_PLUGIN_ROOT}/acc-common          # ← 必须先 cd：repo_adapter 靠 cwd 才 import 得到
OPDIR=$(python3 -c 'import repo_adapter,sys; print(repo_adapter.op_dir(sys.argv[1]))' <Op>)
python3 fetch_source.py --taskdoc <路径|链接> --pr <PR链接> --out <work> --snapshot-into "$OPDIR"
```

它落 `<ops_root>/<op>/task_doc.snapshot.md` 并打印 sha256——那个 sha256 就是要填进 `GOLDEN_CONTRACT` 的。

- 快照是**逐字节副本**，不许手改、不许摘录。改一个字符，`verify_authorization` 立刻核不过（这正是它的作用）。
- 快照已存在但内容不同 → `fetch_source` **fail-loud**（打印新旧两个 digest），不静默保留旧的。
  撞上就停下问用户：上游任务书变了，先确认以哪份为准。
- **没有快照就别声称有任务书授权**。`kind` 只能写 `impl_reference` / `none`，老老实实落第二档。
  写了 `oracle_method` 却核不出来 → `derive_golden_tier` 判 **tier 4 · unverifiable_authorization**（假授权不降级、直接 blocked）。

---

## 1. 定真值口径（R3 两档链 · 唯一决策树）

**只走这条链，不发明第三档。** 通读任务书全文，按序判：

| 判断 | 落点 | `authorization.kind` |
|---|---|---|
| 任务书**就真值该怎么算**作了指定（「与 cpu 一致的逻辑值比较」「按 IEEE754 就近舍入」…），且该方法在本机 CPU 跑得起来 | **第一档 · tier 1** | `oracle_method` |
| 任务书指定了口径，但方法**本环境跑不起来**（内置 TBE / cuSPARSE / OpenCV-GPU / 需要 NPU 才有的算子）| **tier 4 · blocked**（R4：**不自动回落**第二档）| `oracle_method` |
| 任务书只给了**算子公式**（LaTeX / 数学定义），需要自拼多步实现 | **tier 3 · 必须人核**（R5 末位档）| `formula` |
| 任务书只说「参考内置 TBE 实现重写」「对标 XX 实现」 | **第二档 · tier 2**——`impl_reference` **不构成 golden 授权**（它说「照着谁重写」、不是「真值该怎么算」）| `impl_reference` |
| 任务书对真值口径只字未提 | **第二档 · tier 2** | `none` |

第二档 = **CPU 上的 torch/numpy 现成 API 单调**（`source: "single_api"`）。
找不到能一调直出的现成 API、必须自拼多步 → `source: "multistep"` → 走 tier 3 人核路径，**别硬凑成 single_api**。

> ⚠ 判档的**唯一实现**是 `precision_policy.derive_golden_tier`。本页只讲怎么填契约块，
> **不要在 golden.py 的注释里复述判档逻辑**（复述会漂）——只抄录本算子的判定结果。

### R2：PR / 仓里的参考实现，一律不得作 golden 源

这条不是靠禁令守的，是靠**受控词表里根本没有那个格子**守的：
`GOLDEN_SOURCE_KIND` 四枚举（`single_api` / `multistep` / `external_method` / `needs_user`）没有「仓内参考」，
`cite` 只认 `task_doc.snapshot.md:<行>`（指向 `pr_facts.json` / 仓内任何文件都非法）。
**别试图表达它**——被测实现算出来的东西不能拿来验被测实现，这是自证循环。

---

## 2. R6：后端在**生成期**选定并写死

golden 恒跑 CPU。torch 优先、numpy 兜底，但选择**发生在生成这一刻**、结果写死进文件：

```python
def _require_torch():             # ✅ 生成期已定：本文件只提 torch，没有第二个后端可换
    import torch                  #    （延迟 import，理由见 §3 骨架）
    return torch
```

```python
try:                              # ⛔ 禁止：运行时兜底
    import torch
except ImportError:
    import numpy as torch         # 换个后端 = 换了数值语义，静默改判
```

⚠ **别把这条误读成「必须顶层 import」**：禁的是**换后端**，不是**延迟 import**。
`_require_torch()` 那种「函数内 import、缺了就抛」是本仓约定写法（§3 骨架），它没有第二个后端可换。

理由：torch 与 numpy 在舍入、bf16、subnormal、`nan` 传播上并不逐位等价。运行时切后端 =
**同一份 golden 在不同机器上给出不同真值**，而验收裁决拿它当基准。torch 缺失就 fail-closed（红），不降级。

---

## 3. 文件骨架

```python
"""<Op> 精度 golden —— 由 acc-runner-dev:gen_golden 据任务书生成。

档位：<抄录 derive_golden_tier 的判定结果，一句话>
后端（R6）：生成期选定 <torch|numpy>，运行时不兜底——缺失即 fail-closed。
"""
import numpy as np                             # 顶层只放 numpy


def _require_torch():
    """torch **延迟 import**（本仓四份样例一律如此，别改成顶层 import）。

    理由：`golden.py` 会被 `load_golden` 整个执行，而 `--dry-run` / `check_golden.py` 只想拿
    `out_shape` 与契约块——顶层 import torch 会让这两条本可纯 stdlib 的路径平白依赖 torch。
    ⚠ 这**不是**运行时兜底：选型已在生成期定死（本文件只提 torch，不存在 numpy 分支），
    缺 torch 一律 fail-closed，绝不换后端。"""
    try:
        import torch
        return torch
    except Exception as e:                     # noqa: BLE001 —— 缺失/损坏一律要求安装、不静默兜底
        raise RuntimeError(
            "golden 需 torch(CPU) 作 CPU 标杆参考、但未安装/不可用。请安装 CPU 版："
            "pip install torch --index-url https://download.pytorch.org/whl/cpu。"
            "不静默回退——确定性红线（ADR 0011 决策 4）。") from e

GOLDEN_SOURCE = "torch torch.<api>"            # 首 token 供 oracle_source 映射（torch→torch_ref）

GOLDEN_CONTRACT = {
    "source": "single_api",                    # single_api | multistep | external_method | needs_user
    "method_kind": "torch_cpu",                # torch_cpu | numpy_cpu | builtin_tbe | gpu_lib | other_external | needs_user
    "method": "torch.<api>",                   # 人读：到底调的哪个 API
    "authorization": {
        "kind": "oracle_method",               # oracle_method | formula | impl_reference | none
        "cite": "task_doc.snapshot.md:<行号>",  # 只认这一个文件名；`:<起>-<止>` 亦可
        "quote": "<逐字摘自快照该行区间，一个字都不能改>",
    },
    "taskdoc_snapshot": {"sha256": "<fetch_source --snapshot-into 打印的那串>"},
}
# kind 为 impl_reference / none 时：cite / quote / taskdoc_snapshot 都不必填。

GOLDEN_PROVENANCE = (                          # 统一句式，见 §4
    "第二档（tier 2）·任务书未指定真值口径（仅「参考内置 TBE 重写」）→ 回落 CPU 现成 API torch.<api>"
)


def golden_fn(inputs, attrs):
    """inputs: list[np.ndarray]（按 spec 里 io=="in" 的出现序）；attrs: dict → 返回 np.ndarray。"""
    t = _require_torch()
    x = t.from_numpy(np.ascontiguousarray(inputs[0]))
    return np.ascontiguousarray(t.<api>(x, ...).numpy())


def out_shape(in_shapes, attrs):               # 仅非 elementwise 才导出；见 runner-skeleton §6.1
    ...
```

**必需三件套**：`golden_fn` + `GOLDEN_SOURCE` + `GOLDEN_PROVENANCE`（`load_golden` fail-closed 校）。
`GOLDEN_CONTRACT` 缺失不阻塞加载，但**没有它就派生不出档位**——正式验收一律要写。

### 输出形状（C1）

elementwise（输出同输入形状）→ **不导出** `out_shape`，缺省语义即同形。
非 elementwise → 导出 `out_shape(in_shapes, attrs)`，写法 / 诚实边界 / 两个具体例子见
**`runner-skeleton.md` §6.1**（不在本页重复）。要点只有两条：**只据任务书原文或算子
`*_infershape.cpp` 的公式写，不猜；写不准就别导出**，把「输出形状规则未知」记进 `task_pr_gaps` 并停下。

### 空 Tensor / 非法输入：闸门必须在 `out_shape` 里，不能委托给 torch

算子若声明「不支持空 Tensor」或只支持某种空形态，**`out_shape` 自己 `raise ValueError`**。
把拦截交给 torch 有两个真洞：① **换个 torch 版本结论就变**——fail-closed 的判据不该挂在第三方库的行为上；② 照 §3 骨架把 torch 延迟到 `_require_torch()` 后，`--dry-run` / `check_golden.py` 这两条**只取 `out_shape` 与契约块**的路径压根不会调到 torch，那层拦截自然走不到。
（Im2col 样例踩过：`GOLDEN_PROVENANCE` 白纸黑字写「不为 numel=0 编造输出」，而 `out_shape` 照样返回了形状。
`test_samples_golden_contract.py::test_provenance_claim_matches_behavior` 现在专盯这个形状的洞。）

---

## 4. `GOLDEN_PROVENANCE` 的统一句式

这段文本**会被后续 agent 逐字照抄**去产下一个算子的 golden——含糊一份，抄错一片。按本算子的档位选一种：

```
第一档（tier 1）·任务书指定真值口径（<原句摘要>）→ <backend>.<api>(CPU)
第二档（tier 2）·任务书未指定真值口径（仅 <impl_reference 内容>）→ 回落 CPU 现成 API <backend>.<api>
第三档（tier 3）·任务书只给公式（<公式摘要>）→ 按公式自拼多步实现（<backend> CPU）**必须人核**
```

声称什么就必须做到什么（诚实性对账）：写了「不为 numel=0 编造输出」，代码就得真拒。

---

## 5. 生成后自检（三步，全是确定性脚本）

```bash
cd ${CLAUDE_PLUGIN_ROOT}/acc-common

python3 check_golden.py <Op>            # 契约层：词表 → 授权真伪 → 档位，输出 JSON 账本
python3 check_golden.py <Op> --load     # 额外真跑 gen_cases.load_golden（会 import torch）
python3 gen_cases.py <spec> --dry-run   # 用例计划自检（plan-only，不算 golden、不落盘）
```

`check_golden.py` 把 `precision_policy` 的**三层**串起来跑，三层的分立语义原样保住
（`validate_golden_contract` 只看词表 / `verify_authorization` 只读快照 / `derive_golden_tier` 只按词表判档，
**谁也不核自己**）。别自己拼 `python -c` 复刻它——揉成一坨就退化成自证。

**按退出码判读**（账本里 `tier` / `blocked_reason` / `authorization_reason` 给具体原因）：

| 退出码 | 情形 | 处置 |
|---|---|---|
| **0** | `tier` 1/2 且**不需人核**、无 `blocked_reason` | ✅ 交回 orchestrator，可进 CP-B dry-run |
| **2** | **`needs_human_review=true`**（tier 3 必然如此；⚠ **tier 1 也可能**——`multistep + oracle_method` 判 `(tier 1, 需人核)`）| ⚠ 交回 orchestrator 并**显式要求人核**——自拼多步的实现没人核过不算数。**非失败**。⚠ **路由看 `needs_human_review`、不看档位数字**，按 tier 判会把那种 tier 1 静默放行 |
| **1** | `tier` 4 · `blocked_reason=method_unavailable` | ⛔ 任务书指定的方法本环境跑不起来（R4）→ **抛给用户**，不自动回落第二档 |
| **1** | `tier` 4 · `blocked_reason=unverifiable_authorization` | ⛔ 声称有授权但核不过——先查快照是否入库 / 引文是否逐字 / 行号是否错位（账本的 `authorization_reason` 会直说是哪一样）|
| **1** | 词表不合规（如 `source: "singleapi"`）| ⛔ **别猜着改**，回 §3 对表 |
| **1** | 缺 `GOLDEN_CONTRACT` / 缺必需三件套 / 文件不存在 | ⛔ 照 §3 骨架补齐 |

⚠ **两条命令都会 import 执行整个 `golden.py`**（`check_golden.py` 靠执行它才拿得到契约块）。照 §3 骨架把 torch 延迟到 `_require_torch()` 里，则**不带 `--load` 时不会 import torch**；`--load` 会真调 `gen_cases.load_golden`。本机没 torch 时 `--load` 必红，这不代表 golden 有问题，如实记进摘要。
⚠ 自检全过 ≠ golden 数值对。数值只有 CP-D 真机跑出来才知道，**别在摘要里声称「已验证」**。

---

## 6. 什么时候返回 BLOCKED（不硬产）

- 任务书对真值口径与公式**都没说**、且找不到 CPU 现成 API 能一调直出 → `needs_user`，抛用户。
- 输出形状规则从任务书与 `*_infershape.cpp` 都读不出来 → 记 gap、停下，**不猜一个公式写进去**。
- 输出形状**依赖输入的值**（bincount 那类）→ `out_shape(in_shapes, attrs)` 拿不到输入值，表达不了（不在 C1 覆盖内）→ BLOCKED。
- 任务书指定的方法要 NPU / GPU 才跑得起来 → tier 4，抛用户，**不偷偷换成 torch 近似**。

> 诚实返回 BLOCKED + 原因 + 建议，交回 orchestrator。**产一个来源可疑的 golden，比不产更坏**——
> 它会一路跑到 CP-D 出一个看起来正常的裁决，而那个裁决的基准是错的。
