# role 投影矩阵 fixture（ProjectionIR 重构钉板）

本目录是 `dev-doc/sparse-r1-projection-matrix.md`（钉板蓝图）§D 六组合成用例的落地：
一组合成 FACTS（完整 gen_csv.py）+ 录制脚本 + 录下的现状输出 `fixture.json`。
它是后续 ProjectionIR 重构「无行为变化」的证明面：重构前后各录一次，
`fixture.json` 的 `results` 子树逐字节一致即无行为变化。

## 目录结构

| 路径 | 内容 |
| --- | --- |
| `facts/g1_profile.py` … `g5_scalar.py` | 五个正向合成 gen_csv.py（check=0 且 render 成功） |
| `facts/g6_trap_*.py` | 六个负例（蓝图陷阱各一个最小 FACTS，如实录制现状） |
| `record_fixture.py` | 录制脚本，幂等（两次运行 fixture.json 逐字节一致） |
| `fixture.json` | 钉板本体：check/render 退出码与全文输出、CSV 表头、五件派生物 SHA-256 |
| `out/<name>/` | 每次录制重建的渲染工作区（gen_csv.py 副本 + 渲染产物） |

每个 facts 文件 = FACTS 字面量 + 模板通用代码区逐字节照抄（check 的哈希门要求）。
FACTS 是 AST 白名单纯字面量，长列表（200 行 perf）由脚本生成后写入。

## 怎么重跑录制

```bash
python3 record_fixture.py                  # 录全部 11 个，重写 fixture.json
python3 record_fixture.py g1_profile       # 只重录指定项
python3 record_fixture.py --refresh-common # 模板重构后先刷新 facts/*.py 的通用代码区再录
```

录制对每个 FACTS 依次做三件事：`package.py check --facts ... --print-header`（记退出码与
stdout/stderr 全文）；拷入 `out/<name>/gen_csv.py` 后 `package.py render`（记退出码、CSV 表头行
原文、CSV/README/gpu_baseline/两个 verify 的 SHA-256）；再在进程内直接调模板 `generate(FACTS)`
（记 axes/blocks/pairs 摘要，或异常类型 + cause 类型 + 消息首行）。第三条通路能把
「校验拒但生成器接受」这类口径分叉显式钉住（见陷阱 5）。

幂等性约定：fixture.json 不含绝对路径与时间戳（fixture 目录替换成 `<FIXTURE>`，
解释器行替换成 `<PYTHON>`）；`_meta.recorded_against` 里的代码/输入哈希是录制指纹，
重构后允许变化，比对只看 `results`。

## 正向组覆盖（对照蓝图 §D 三张表的 ✗ 条目）

新规提醒：`perf` 节存在时 `rows` 必须恰 200 行（check 机械强制）。g1、g5 与陷阱 2 各带
200 行 perf；g2/g3/g4 不需要 perf 覆盖，直接省略 perf 节（同时钉住无 perf 的渲染面）。

### role 缺口（3 项）

| §D 条目 | 落点 |
| --- | --- |
| inout_scalar | g5 `c`（float32，单列 + REAL_SCALAR_TIERS 轴） |
| fixed_vector | g3 `pv`（dir=in，len=3 与 samples 2 条故意不等长）+ `outv`（dir=out 无 samples，完全退出投影） |
| int_array | g3 `ipiv`（dir=out）+ `ib`（dir=inout + producer + dtype=int64） |

### 属性条件缺口（17 项）

| §D 条目 | 落点 |
| --- | --- |
| enum_kind dtype | g1 `dtypeA`、g5 `dt`（列值全部来自 profile.assign） |
| enum_kind compute | g1 `computeType` |
| enum_kind algo | g1 `algo`（自定 ctype，验证 algo 走 op_enum 轴、不被 profile 接管） |
| layout.kind stride | g2 `strideA`（第二趟物化：min=基元素数 256，pad=+7） |
| layout.kind batch | g2 `bcB`（带 of）+ `bcC`（不带 of，蓝图要求两条各一） |
| layout.of 为列表 | g4 `ldshared`（of=["M","R"]，min/pad 只按 of[0]=M 计算） |
| 标量 dtype 复数 | g5 `beta`（complex64 → beta_re/beta_im 列 + COMPLEX_SCALAR_TIERS 轴） |
| dtype_from 标量（无 values） | g1 `alpha`（scalar_tier 索引轴；复 profile 出 (0.5,-1.5)，实 profile 补 (v,0.0)） |
| dtype_from buffer | g1 `A`、`C`（footprint dtype 走 assign 的 FP16/FP32 记号） |
| dtype_from + values 并存 | g5 `s`（同质复数 profile 下 [[re,im],…]，轴 kind=scalar_value，跳过 tier 反解） |
| 标量 values（配 dtype） | g5 `alpha`（[1.0, 0.5, -1.5] 显式轴） |
| dir=out buffer | g4 `v`（vector）+ g3 `outv`（fixed_vector）+ 陷阱 6 `A`（matrix）：列/轴/state 三面同时静默 |
| producer | g4 `R`（matrix，fill 列与轴同时消失）+ g3 `ib`（int_array） |
| conditioning | g4 `M`（m_fill 钉死 fill_tiers[0]，M_matrix_type 成轴，CSV 可直接看到） |
| batch 三 model | g2 `A`（strided）、`B`（ptr_array+table_mem）、`C`（contiguous_implicit） |
| inc 字面量 1 | g5 `x`（无 inc 轴） |
| storage packed | g4 `P`（无 ld：少一根轴少一列，footprint 走 n(n+1)/2） |
| samples | g3 `pv`（轴基数 = len(samples) = 2，列宽 = len = 3） |
| 同名不同 case 列冲突 | 陷阱 6（见下） |

### 顶层条件缺口（8 项）

| §D 条目 | 落点 |
| --- | --- |
| dtype_profiles 全字段 | g1（fp32c/fp16r）+ g5（pc16/pc32），四个字段全给 |
| 复数 scalar_dtype | g1 `fp32c`（与实数 fp16r 并存，触发 re/im 拆列 + 补虚部两条路）+ g5 双复数 |
| cases.dim_tiers | g2（[2,4,8] 覆盖 mat_dim 轴） |
| cases.batch_tiers | g2（[1,3]；注意 L0/ED 仍按硬编码 batch=2 物化，CSV 里可见） |
| cases.max_footprint_bytes | g2（256 MiB） |
| edge_cases 的 enum 键 | g5 `enum_key`（trans=T） |
| edge_cases 的 layout 键 | g5 `layout_key`（incy=5，直写最终整数） |
| edge_cases 的 scalar 键 | g5 `scalar_key`（实数 2.5）+ `scalar_complex_key`（[re, im]） |
| edge_cases 的 `_batch_pattern` 键 | g2 `uniform_a`（UNIFORM）+ `null_element_b`（NULL_ELEMENT_3） |
| edge_cases 的 fixed_vector 元素键 | g3 `pin_pv0`（float）+ `pin_pv2`（int，末元素） |
| perf.key profile 虚拟键 | g1 key=["profile","m","n"] |
| perf.key layout 键 | g5 key=["n","incy"]（kind=inc 值被诚实采纳；ld 键的失真变体是陷阱 2） |
| perf.sweep:true / threshold | 都在 g1（sweep 只推 mat_dim 轴 → 7 条 pf sweep 行；threshold=0.9） |
| threshold 缺省路径 | g5 不写 threshold，钉默认 0.8 的渲染路径 |

覆盖不到 / 有意不放进正向组的条目：

- 蓝图组 3 提到的 fixed_vector 元素键「越界负例」没放进 g3：g1–g5 必须 check=0，而越界是
  校验器的普通拒绝（`P:984`），不是口径分叉，不值得占一个负例名额。
- 「dtype_from + values」无法在 g1 覆盖：g1 的 profile 混合实/复，schema 规定 values 必须同时
  匹配所有 profile 的 scalar_dtype，混合下必然校验失败（蓝图 B 表已注明「只能同质」），
  故落在 g5 的同质复数 profile 上。
- 除上述外，§D 标 ✗ 的条件全部有落点，没有需要改 plugin 才能触发的条目。

## 负例组（现状快照，不是正确性背书）

蓝图 §C 的陷阱 6（`_scalar_is_complex` 死代码）不是 FACTS 可表达的行为，按 §D 的清单以
「参数名 a 与 A 并存」顶替。下表的行为是录制时的实测现状，其中多数是蓝图预判的 bug；
重构修掉任何一条时，请同步更新此表并重录。

- **trap_1**（enum_kind=dtype 但无人 dtype_from，此时 schema 禁给 profiles）：
  check=2、render=2，报 `生成失败：L0: 'dt'`；schema 校验本身通过，崩在物化。
  进程内 generate：GeneratorError ← KeyError `'dt'`。
- **trap_2**（perf.key 含 kind=ld 的 lda；200 行里同 n 配 lda=16/32）：
  check=0、render=0，全绿。但 CSV 里 `pf n=1 lda=16` 与 `pf n=1 lda=32` 两行的
  lda 列都是 6（rows+5 重算），声明值被静默丢弃、两行塌成同一行为。
- **trap_3**（复数 profile 下 edge set dtype_from 标量，裸数字 2.0）：
  check=2、render=2，报 `生成失败：ED: cannot unpack non-iterable float object`；
  schema 校验通过。进程内 generate：GeneratorError ← TypeError。
- **trap_4**（conditioning=["SAME","SAME"]，校验不查重）：
  check=2、render=2，报 `轴 'M_matrix_type' 含重复取值`（轴唯一性不变量，无 cause）。
- **trap_5**（edge set 写派生列 `x_fill`）：check=2、render=2，报 `set 含未知键 'x_fill'`；
  但进程内 generate 成功（生成器经 `key in state` 接受该键并产出 ED 行）——
  校验比生成器严的口径分叉，fixture.json 里两条通路并排可见。
- **trap_6**（`a`(vector,in,nullable) 与 `A`(matrix,out,nullable) 并存）：
  check=0、render=0；表头出现两个同名 `nullA` 控制列（`…,nullA,nullA,…`），无处校验。

陷阱 5 的 `profile` 键变体与 x_fill 同机制，未单独立文件。陷阱 6 用 nullable 碰撞而不用
双 `a_fill` 碰撞，是因为后者让两根同名轴进入 pairwise：值不同的种子对永远无法被覆盖，
`_pairwise_rows` 不收敛（实测 >20s 不终止、行数无限增长），录不出快照；这本身也是一个
应由重构消灭的行为，先记录在此。

## 与蓝图的行号对应

蓝图行号基于 feature/sparse-r1 @ bbe29f4 的模板与 package.py；`fixture.json` 的
`_meta.recorded_against` 记录了录制时两份代码的 SHA-256，行号漂移以哈希为准。
