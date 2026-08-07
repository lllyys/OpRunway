# worktree19 的改动与原因（供合并到 worktree21）

**分叉点** `96edacd` → **19 现在** `4c68ca6`（7 个 commit）。
21 侧独有 3 个 `docs:` commit，非 doc 只动了 `AGENTS.md` 与两份 `.cc-suite/audits/`。

---

## 1 · runner_form 准入收敛到 `cpp_extension`

**改**：`_RUNNER_FORM_TO_MODE` 只剩一条；缺省从 `cpp` 改为 `cpp_extension`；
`--allow-experimental-form` **删除**。

**为什么**：一次 aclnnRoll 试跑因为抽 spec 的 agent 写下一句**未经验证**的论断
（「`aclIntArray` 两条通路都不支持」），编排层采信后把 `runner_form` 改成非准入形态，
整轮物理上产不出裁决。而缺省 `cpp` 意味着「spec 漏写字段」必然撞准入门——
漏写时想要的恰恰是唯一准入那条通路。

⚠ 那句论断后来被实测证明**整句都错**，见 `dev-doc/oprunway-aclintarray-probe.md`。

## 2 · 删掉 `case_target` 的默认值 50

**改**：常量删除，读取点**缺席即 fail-fast**；样例 spec 里那些无依据的 50/20 也一并删掉，
测试预算改由夹具注入。

**为什么**：那个 50 是 extractor 照仓规「建议值」自己填的，**全程 0 次被改**，
于是「没人定过用例数」被一个默认值长期藏着。留着它等于把问题重新藏起来。

⚠ **后果：7 份样例 spec 现在有意不可跑**，等轴集推算规则定出来再由人写值。
**别顺手填个数进去。**

## 3 · `dtype_deferred` 不得干净 pass + 补硬校

**改**：`dtype_required` 里因 deferred 未测的 dtype，终态不得为干净 `pass`；
deferred 须声明 `capability_source` 并与三张**活能力表**交叉核验，
自报不支持而表里其实支持 → 拒该 gap。权威 dtype 集合取自 CP-E staged `spec.json`。

**为什么**：complex64 与 uint32 当初就是挂个 deferred 被算作已挂账、直接绕过覆盖门的。
后来实测发现两者在真机上载体往返无损——**挂 deferred 从此是伪造挂账**。

## 4 · `complex64` / `uint32` 能力闭环，复数口径统一到 float32

**改**：两个 dtype 四层接入（先真机实测载体往返无损才接）；复数比对**实部虚部各按 float32 标准判**，
删掉那条标着「外推」的独立 complex64 容差。

**为什么**：能力表说不支持而实际支持，会让 deferred 硬校拿错基准。
复数口径统一是用户 2026-08-06 定的。
⚠ **与 torch 有差异且有意保留**：`torch.isclose` 在复数上用模长，本仓用分量各判。已在 5 处标注。

## 5 · perf 无验收目标时 fail-closed

**改**：缺 `target_ratio` 一律 `invalid_config` + 全行 blocked，不再凭空套 0.95。

**为什么**：任务书写「性能要求：无」、spec 也如实省略了 `perf` 块，
流水线却自己套目标判了 19 条 fail——**凭空造的要求**。

## 6 · 新增 spec 变更门

**改**：spec 变更须有收据（sha 当场重算不读自报、`confirmed_by` 与 `change_reason` 非空），
门落两处（进 Task1 前 + 写 `acceptance.json` 前）；入口冻结 `entry_spec_sha256`，
原件/收据/staged 三格必须全部等于它。

**为什么**：那次试跑**跑不通就改 spec**，dtype 从任务书要求的 8 种砍到 3 种，
3 次字段收缩全无机制阻止。

⚠ **门的宣称已改准**：它证的是「内容完整性 + 有人显式声明过」，**不是「用户已确认」**。
收据无密钥，`confirmed_by` 证明不了真人身份；`previous_spec_sha256` 与被审计产物
**同处一个可写信任域**，证明不了不可伪造的跨轮历史。已挂账。

## 7 · shape 轴去退化

**改**：`torch_parity` 按**接口能力**派生布局（检测到 `axis_class` 才把可能被选中的轴提升到 ≥2），
无轴选择器的 elementwise 仍为 `(L,1,…,1)`。

**为什么**：原布局 `(L,1,…,1)` 导致 1344 例里 46.4% 在归约一根长度为 1 的轴；
更严重的是**结构上取不到「归约长度>24576 ∧ batch>1」这个格子**，
而 `batch==1` 正是已知缺陷三条件之一——**那条从没被验证过**。

⚠ **代价**：`torch_parity` caseset 字节改变，**Median 的 1344/58 与 1152/51 两组历史真机基线均作废**，
不能再用于回归对照。

## 8 · finalize 旁路收紧到与主入口同门

**改**：`finalize_directory()` 进函数第一件事先作废旧裁决；加 spec 收据两道门、
staging 等值校验、三级门显式传 source facts。CLI 的 `--spec` / `--source-facts` 改必填。

**为什么**：它是**第二个能写验收裁决的入口**，却绕过主入口所有新门——
失败时不作废旧 PASS（下游按文件名读成本轮结果）、读旧 spec 落点、调三级门不传 facts。

⚠ **破坏性**：`--dir` 单参数调用**从此 exit 2**；旧式报告目录 finalize 不动。都是有意的。
⚠ 根因未除：第二个写裁决的入口天然是假门制造机，建议删掉其裁决写能力（待用户拍板）。

## 9 · `dut_source` 整条删除，本地来源统一到 `local_snapshot`

**改**：删 `dut_source.py` / `dut_source_kind.py`，CP-F 迁到 `source_provenance`；
锚从 `local_root_digest`（子树 merkle）换成 `snapshot_sha256` + `snapshot_subtree_sha256`（整树+子树）。

**为什么**：合并 main 时发现两边各自实现了同一件事，main 那套有 GaussianBlur 真机背书、
锚覆盖更宽。⚠ **两套 merkle 不是同一个算法**（帧格式/排除集合/rel 基准都不同），
必须整份二选一——拼一半会造出「取材端按 A 算、构建端按 B 核」的永久 `SNAPSHOT_MISMATCH`。

⚠ **连带**：CP-F 因未冻结 spec 字节**现在明确 BLOCKED**，恢复需把 base spec 字节纳入 attempt manifest。

## 10 · 修一批「宣称有门其实没门」

`measure_only` 授权锚允许 `null`（等于没锚）→ 改必填 + 当场重算快照摘要 + quote 逐字子串；
`fetch_source._walk_snapshot` 静默吞 `OSError` → 读不到当场报错；
`vendor_build_receipt` 的 returncode 曾是自报 → 真跑 build。

**最值钱的一条**：守 legacy caseset 字节的 `ExistingOpsByteIdenticalTest`
**因 numpy pin 粒度改成完整版本、而基线表键没跟着迁，一直在静默 skip**。

⚠ 三层叠加导致整轮无人报警：① 门 skip；② 跑门脚本 `| tail` 无 `pipefail`，
pytest 退出码被掩成 0；③ 报告只读「N passed / 0 failed」，而那数字不含这道门。

🔴 **门一恢复就抓到一条真实漂移**（isclose）。查清是**合理漂移**：
只有 gap 元数据多了 `capability_source` + `runner_form`，
50 个 case、case_id、全部 `.npy` 摘要**逐字未变**；而那两个字段正是第 3 条硬校所必需。
基线已重取并写明理由。另 5 份 legacy 基线全部未漂。

已补 `CasesetBaselineAvailabilityTest`：缺基线时 **FAIL 而不是 skip**。

---

## 合并时要注意的两处

**① `dev-doc/oprunway-changes-brief.md`**：两边都在顶部倒序追加，**都要保留、按日期排好，别二选一**。

**② `AGENTS.md`**：19 改 §4 / §5.2 / §5.3 / §5.4 / §8 / §9；21 新增 §5.12 并给 §5.10 / §5.11 补交叉引用。
区段不同、大概率自动合，但合完要核三节的交叉引用是否仍指得对。

## 待办

`dev-doc/oprunway-output-written-gate-handoff.md` —— 下一件要做的事（DUT 输出未被写入检测门），
方案原稿在主仓 `doc/oprunway-output-written-gate.md`。

**19 侧基线：a3 全量 2533 passed / 10 skipped / 0 failed，两道漂移门 SYNCED。**
