# 问题说明：spotri 直审门对小幅输出（scale/zero 类错误）失效——转研发确认

**编号**：HT-24（2026-10-07 更正版）
**性质**：口径裁定问题（非实现缺陷——判定脚本与任务书公式逐行一致，判定行为符合当前标准表语义）
**影响面**：spotri / cpotri（公私两域全部 potri 包）；spotrf / spotrs 族实测不受影响
**状态**：待研发确认口径后施工

---

## 1. 问题一句话

spotri 大 n 用例中，**被测输出全零（或 ×2 整体错误）仍被数值判定放行**——混合容差直审三道门（逐元素 atol+rtol、matched_ratio≥0.99、max_abs≤1e-2）对 O(1e-4) 量级的逆矩阵输出全部失明，且按判定次序（先生态标准直审、不过再 LAPACK 残差）残差复核层无机会执行，而残差层本可拦截（实测 scale ratio=2.59、zero 零分母异常）。

## 2. 发现经过

私集十包验证（2026-09-29）：spotri/cpotri 的 sim_dut 负例演练中，scale（golden×2）
与 zero（全零）两种扰动均呈 **121 PASS / 24 FAIL** 分布（两册一致，FAIL/PASS 按
n 单调分界：小 n 的 24 例挂、大 n 全放行）。初版怀疑判定实现缺陷，经逐 case
报告数据分析与最小复现（见 §4），更正为下述失效链。

## 3. 失效链条（每环有实测数据）

spotri 的 golden 是**逆矩阵 C**。对角占优 SPD 构造下 A ≈ n·I 量级，
故 C = A⁻¹ 元素幅度 ~1/n：n=4096 时 **max|C|=1.56e-4，99.98% 元素低于
atol 地板 1.22e-4，中位数 6.6e-7**。

对 scale（Δ=|golden|，相对误差 100%）与 zero（Δ=|golden|）：

| 环节 | 机制 | 实测（n=4096） |
| --- | --- | --- |
| ① 逐元素相对项被 atol 地板短路 | 判据是混合容差 `\|Δ\| ≤ atol + rtol·\|g\|`；元素 \|g\| < ~1.22e-4 时即使相对误差 100% 也判「匹配」 | 99.9756% 元素在地板下 → 全匹配 |
| ② matched_ratio 0.99 门过 | 超门元素恰好是对角线（占比 1/n） | matched_ratio = 0.999756 = 1 − 1/4096 |
| ③ max_abs 1e-2 门过 | 扰动绝对差 = max\|golden\| ≈ 1.56e-4 | 距 1e-2 门 65 倍宽容 |
| ④ 判定次序使残差层不跑 | 直审过 = 终局 PASS（HT-7 裁定「先生态标准、不过再 LAPACK」） | 报告实证 `fallback.ran=false` |
| ⑤ 残差层本可拦（没机会） | DPOT03 `ratio = ‖I−A·C‖₁/(n·‖A‖₁·‖C‖₁·ε)` | scale ratio=2.59 > 0.25 拦；zero 分母零 → 异常拦 |

scale 与 zero 的直审指标**完全相同**（0.999756 / 1.559e-4）——直审无法区分
「×2 整体错误」与「全零输出」。

## 4. 最小复现（n=4096，约 30 秒）

依赖：python3 + numpy + scipy。复现脚本（与包内 gen_data 构造族、
criteria/_dpot03 同款口径，独立实现、无内部依赖）：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spotri n=4096 直审三重门失效链最小复现。
构造对角占优 SPD（A = BᵀB + n·I），golden 走 float64 逆降型；
被测三型：f32 真实 / scale（×2）/ zero（全零）。
逐层输出 matched_ratio / max_abs 与 DPOT03 残差，验证失效链。
"""
import numpy as np
from scipy.linalg import inv

N = 4096
rng = np.random.default_rng(902309324)

# 构造对角占优 SPD（任务书用例族：随机对称 A = BᵀB + n·I）
B = rng.standard_normal((N, N)).astype(np.float64)
A = B @ B.T + N * np.eye(N)
A32 = A.astype(np.float32)

# golden：float64 逆 → 降型（golden 链路）
C64 = inv(A, check_finite=False)
C32 = C64.astype(np.float32)

RTOL = ATOL = 2.0 ** -13          # 生态标准表 FLOAT32（与判定脚本一致）
def layer1(out32, golden32):
    d = np.abs(out32.astype(np.float64) - golden32.astype(np.float64))
    tol = ATOL + RTOL * np.abs(golden32.astype(np.float64))
    return (float((d <= tol).mean()),      # matched_ratio
            float(d.max()))                # max_abs

def dpot03(a32, c32, eps=2.0**-24):        # _dpot03 同款（任务书残差公式）
    n = a32.shape[0]
    a = a32.astype(np.float64); c = c32.astype(np.float64)
    L  = np.tril(a);  Am = L  + L.T  - np.diag(np.diag(L))    # 半三角镜像
    Lc = np.tril(c);  Cm = Lc + Lc.T - np.diag(np.diag(Lc))
    anorm = np.max(np.sum(np.abs(Am), axis=0))
    cnorm = np.max(np.sum(np.abs(Cm), axis=0))
    w = np.eye(n) - Am @ Cm
    return float(np.max(np.sum(np.abs(w), axis=0)) / (n * anorm * cnorm * eps))

print(f"golden 幅度: max|C32|={np.abs(C32).max():.3e}  "
      f"低于 atol 地板 1.22e-4 占比={(np.abs(C32) < 1.22e-4).mean():.4%}")

print(f"{'被测':<10} {'matched_ratio':>14} {'max_abs':>12} {'直审双门':>8} "
      f"{'DPOT03 ratio':>14} {'结论':>22}")
for name, out in {
    "f32真实": inv(A32.astype(np.float64)).astype(np.float32),
    "scale(×2)": C32 * np.float32(2.0),
    "zero": np.zeros_like(C32),
}.items():
    mr, ma = layer1(out, C32)
    gate = mr >= 0.99 and ma <= 1e-2      # 直审双门（max_abs_limit 兜底 1e-2）
    try:
        ratio = dpot03(A32, out)
        rs = f"{ratio:.3e}" + ("（拦）" if ratio > 0.1 else "")
    except ZeroDivisionError:
        rs = "inf（零分母，拦）"
    print(f"{name:<10} {mr:>14.6f} {ma:>12.4e} {'PASS' if gate else 'FAIL':>8} "
          f"{rs:>14} {'直审放行' if gate else '直审拦截':>22}")
```

**实测输出**（2026-10-07）：

```
golden 幅度: max|C32|=1.559e-04  低于 atol 地板 1.22e-4 占比=99.9756%
被测          matched_ratio        max_abs     直审双门   DPOT03 ratio              结论
f32真实         1.000000   1.4552e-11        PASS       9.032e-06               直审放行
scale(×2)      0.999756   1.5588e-04        PASS       2.590e+00（拦）          直审放行
zero           0.999756   1.5588e-04        PASS   inf（零分母，拦）            直审放行
```

判读要点：
- scale/zero 的 matched_ratio = 0.999756 = **1 − 1/4096**——超门元素恰为对角线
  （对角 ~1/n 略超地板，非对角 ~1/n² 全在地板下）；
- scale 与 zero 指标完全相同——直审不区分两者；
- 残差层两路都能拦，但按当前判定次序没有执行机会。

## 5. 根因定位

**是「输出幅度极小」与「混合容差绝对地板」的叠加，不是判定实现缺陷**：

1. spotri 输出（逆矩阵）元素幅度 ~1/n，比混合容差门的设计量程（O(1) 输出）
   小 3~4 个数量级；
2. 混合容差 atol 地板（2⁻¹³≈1.22e-4）的设计本意是保护小 golden 元素的正常
   舍入误差比较（tiny 元素相对误差天然爆炸），副作用是对「整体错误但幅度
   小于地板」的输出失明；
3. 判定次序裁定（先生态标准直审、过即终局）使残差复核层在此场景无执行机会。

对照组：potrf 因子（对角 ~√n）、potrs 解（O(1)）输出幅度正常，同样的
scale/zero/conj 扰动 145/176 全抓——**问题为 potri 独有**。

任务书/判定脚本残差公式本身无问题（两者一致：
`ratio = ‖I−A·C‖₁/(n·‖A‖₁·‖C‖₁·ε)`，ε=2⁻²⁴）。

## 6. 附带发现（同批验证，顺手确认）

包内自测工具 sim_dut.py 文档声明「scale δ=1 使全部 case 兜底超阈」的定标
假设未计入 potri 输出幅度随 1/n 缩小，对 potri 不成立（文档级错误，需修
声明或改定标）；另 sim_dut 对 info 契约用例一律跳过，info 端到端在包内
演练不通。

## 7. 请研发确认的问题

1. **口径确认**：被测输出全零（或整体 ×2）但 status=ok，是否必须判 FAIL？
   - 若「是」→ 请在下列方案中裁定（或给出新方向）：
     - **甲**：potri 直审增设相对幅度门（如 max_abs ≤ max(1e-2, k·‖C‖∞)）——
       动判定脚本 + 测试重标定 + 全部 potri 包重渲重装；
     - **乙**：potri 判定次序例外——直审过后仍强制跑 DPOT03 残差复核——
       改动集中在 potri 卡，但破坏「先生态标准」的统一语义；
     - **丙**：接受现状（正式验收侧口径若含残差层复核则风险可控）——仅修
       sim_dut 文档声明，README 注明负例演练限制。
   - 若「否」（认为 max_abs=1e-2 语义下放行可接受）→ 仅修 sim_dut 文档与
     演练指引。
2. 若采甲：相对幅度门的系数 k 与基准范数（‖C‖∞ / ‖C‖₁ / max|golden32|）
   请裁定。
3. 正式验收侧对 potri 的实际判定序列（是否含残差复核）请同步确认——若验收
   侧本就双门串联，则开发者自测与验收的缝隙仅存在于包内自测工具演示层。

---
*附：私集验证台账 `frozen/private/VERIFICATION.md` §2.2、逐 case 报告数据
`frozen/private/smoke/spotri/report-scale.json`（PASS 组 matched_ratio
0.9911~0.9995、max_abs 2.4e-4~4.5e-3；FAIL 组前 24 个小 n case）。*
