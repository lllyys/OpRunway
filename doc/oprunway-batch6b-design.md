# 批 6b 设计方案 —— 放宽 runner 的 scope gate 覆盖面

> 状态:**方案(未实施)**。据 2026-07-23 的 4 路并行调研 workflow(`wf_03b6a49b-8ce`)+ 直读核实产出。
> 这是「支持所有任务书里出现过的算子类型」那条用户指令的剩余大头。**先抛方案、点头才动手**(CLAUDE.md #1)。

## 0. 调研纠正的错误前提(先说,因为它改变了整批性质)

抛这批之前,我(和三份 runner 散文)都以为:批 6b 要**改真机路径 `run_on_npu.sh` 里硬编码的 `experimental/math/$OP`**。

**实读代码发现这是过时认知**——那些早已被生成化(commit `422ed52`「目录段软链洞」修的就是这个):

| 散文还在说的「硬编码」 | 代码实况 |
|---|---|
| `experimental/math/$OP` 目录字面 | 已删,改读 `OPRUNWAY_OP_SRC`(run_on_npu.sh:10) |
| `${VEN}_math` vendor 后缀 | 已删,改 `${VEN}_${VSUF}`,VSUF 由 env/正则生成(:24-38) |
| `--experimental` 旗标 | 只剩「`experimental/` 前缀→`--experimental`」一对半硬编码(:49) |

**所以真正锁死通路的不是引擎,是几张建在 stale 断言上的 gate 表**——散文甚至叫 agent 去扩一个**幽灵变量 `OPRUNWAY_TARGET_DIR`**(实读:runner 通路的 `.sh`/`.py` 里零命中,只有无关的 `init.sh` 读它)。这直接把批 6b 从「改真机大工程」变成「**文档对齐 + 微接线的省力第一刀**」。

## 1. 现状真相:真正把通路锁在 experimental/math aclnn 的三块

引擎已大幅泛化;剩下**三块真闸门**只对「换构建体系 / 换接口形态」的算子(catlass 等)是硬闸,对「单实现 aclnn 非-experimental」算子则**引擎已能跑、只被 stale 散文挡住**:

1. **build.sh CLI 方案**(run_on_npu.sh:77/50):锁死 ops-math/ops-nn 家族的 `build.sh --pkg --ops --soc --vendor_name`;catlass 是 `scripts/build.sh <example> -DCATLASS_ARCH` 完全不同。
2. **opp 自定义 vendor 布局**(:38/57/81/96/105-106):假定产物是 `build_out/*.run` 安装包 + `vendors/<vendor>/op_api/{include,lib}`;catlass 产 `output/bin/<exe>` 直接可执行,没这套。
3. **aclnn 两段式链接**(:97-100):`-lcust_opapi`/`-lopapi` 焊死 aclnn 接口;不暴露 aclnn opapi 符号的算子链不上。

### 一处「断头配置」(第一刀最省力的接线点)

`OPRUNWAY_VENDOR_SUFFIX`:shell 认(:28),但 `repo_adapter` 的 env-export(:806-809)**不导出它** → 编排通路里 vendor 后缀只能靠 `ops-<族>` 正则或 fail-closed,手工 env 才够得着显式配置。接回它是低垂果实。

## 2. 四个候选改法

| | 名称 | 一句话 | 覆盖 | 改动面 |
|---|---|---|---|---|
| **A** | doc-gate 对齐 + 接回断头配置 | 更正三份 stale 散文(删 `experimental/math`+`_math`+`--experimental` 断言、删幽灵 `OPRUNWAY_TARGET_DIR`,指向真实的 `OP_SRC`/`VENDOR_SUFFIX`)· scope gate 拦截键从「target_dir 前缀==experimental/math」换成**三条真闸**(接口是否 aclnn 两段式 / dtype∈{fp32,fp16} / 输出形状能否用 `out_shape` 表达)· 把 `OPRUNWAY_VENDOR_SUFFIX` 接进 `_ne_cfg`+env-export | 引擎已能跑却被过时散文挡住的「单实现 aclnn、单张量、输出=输入广播、fp32/fp16」非-experimental 算子(个位数,如 Relu、已成形的 FmodTensor)+ 解锁 shape-transform 批的目录/仓族轴 | **小**·纯散文+一处 cfg 字段+一行 export·0 新 adapter·可逆 |
| **B** | 接口形态成 spec 显式轴 + 从算子 example 探测 | 引入 `interface_kind` 轴,由 `fetch_source`/`pr_facts` 从 `test_aclnn_*.cpp`/op_def **探测**(接口形态任务书推不出、必须探 example);批 6b 只放 `aclnn_2stage` 过 runner 通路,其余按类记 gap BLOCKED | 不新增算子,但把「整族 BLOCKED」变成「按真实接口逐算子精准 BLOCKED/PASS」 | **中**·pr_facts 探测+spec 字段+gate 重写 |
| **C** | per-op `out_shape`(摘 shape-transform 果) | 利用已落地的 C1(`Golden.out_shape` 管路)+C2(list[int] attr),为 reduction/shape_transform 逐算子写 `out_shape(in_shapes,attrs)`+runner in/out buffer 分开 | shape_transform 3 个(2×Upsample+im2col,attr 已 C2 覆盖、只差 out_shape) | **中**·per-op 代码·非新 adapter(C1 管路已就位) |
| **D** | dtype 谱扩展 + per-input dtype + 修 arity≥3 静默截断 | 扩 `_NP`/`_NATIVE` 到 int/uint/double/complex+runner ACL 分支+逐算子真机验证;manifest 加 per-input dtype token | 绝大多数任务书的核心 dtype 增量(多为 int/uint) | **大**·真新代码·每 dtype 每算子要真机证据·最贵 |

## 3. 推荐 + 分期

**推荐:A + B-core 合并为本批第一刀,C 紧随第二刀,D 分期。**

理由:4 路调研 + 直读一致——真正把通路锁在 experimental/math 的**不是引擎**(已大幅泛化),**是几张建在 stale 断言上的散文 gate 表**。所以最大杠杆是**改文档对齐已泛化的脚本 + 接回断头的 `VENDOR_SUFFIX`**,引擎侧几乎不动。B-core 必须同批(否则 A 三闸里「接口」这条只能靠猜,违 fail-closed 最高律令——接口探不到就该 BLOCKED、不从任务书猜)。

| 期 | 内容 | = 候选 |
|---|---|---|
| **期0**(前置债,本批先还) | 修 `gen_cases._build_inputs` 的 **arity≥3 静默只造 2 输入**(:404-431,违 fail-closed)→ 按 arity 造满或 fail-closed。⚠ 不还这债就放宽 io_arity,会**静默测错东西** | (债) |
| **期1**(本批主体·省力可逆) | 三份散文对齐 + gate 换三真闸 + 接口从 example 探测 + 接回 `VENDOR_SUFFIX`;放行引擎已能跑的单实现 aclnn 非-experimental 算子。dtype 冻 {fp32,fp16}、bf16 deferred | A + B-core |
| **期2**(紧随·最高性价比) | per-op `out_shape` 摘 shape-transform 3(2×Upsample+im2col),管路 C1 已就位 | C |
| **期3**(更大一刀·逐项立项+真机预算) | dtype 谱扩展 + per-input dtype;「各输入独立形状」(matmul/conv/im2col,现 `broadcast_shapes` 强制可广播)与「多输出」各是比 C1 更大的 manifest 加轴;双实现 build-path selector | D 及以上 |

## 4. fail-closed 守卫:必须原样保住(放宽 ≠ 放软)

批 6b 是「多认几种目录/接口/形状形态」,**不是把 `raise` 改成静默兜底**。每加一条通路都配同强度的 provenance + 源绑定 + fail-closed:

- **OP_SRC ≥2 段 + canonical + 仅安全字符**(run_on_npu.sh:20-23 + repo_adapter:567-574)——防路径逃逸 + 防 OPHASH 绑整仓导致跨算子异源假通过。
- **OPHASH 源绑定**(:44-48)、**opp provenance 门**(:54-69,五字段逐字核、`OPRUNWAY_OPP_REBUILD=1` 才授权重建)、**vendor 后缀推不出即 exit 3**、**build 无 .run 即 exit 5**、**find_runner 缺 runner 不回退样例**(:515-520)。
- **新 dtype 未证 → deferred/BLOCKED 不静默强转**(`_NP` 白名单 `raise` 保留);**接口探不到 → BLOCKED**(不从任务书猜)。

## 5. 明确排除本批(保 fail-closed、归 P3 / 另立设计)

catlass(独立 `catlass_adapter`/`CATLASS_MODES`,两条通路别混)· bincount 类(值定形状,C1 结构表达不了)· 坏 attr(空/嵌套/dict/None)· GPU-baseline 性能算子(SPMV/TrsmBatched/Cheevj 属 Task 3、非 builtin-TBE 路径)· 容器类 dynamicMap(非算子)。

## 6. 需你拍板的 open questions

1. **第一刀范围**:期1 做 **A+B-core**(推荐),还是**只做 A**(更省力、但「接口」闸只能靠现有 aclnn 假设)?或**只留方案先不实施**?
2. **期0 前置债**(arity≥3 静默截断,违 fail-closed):本批一起还,还是单列?
3. **clone 那 4 仓**(ops-nn/ops-collections/ops-transformer/ops-solver 共 24 算子,op_def/接口一个没核):**clone 是副作用,需你同意**。不 clone 就无法据实分类接口/dtype、也就无法放行清单。
4. **dtype 本批冻不冻** {fp32,fp16}(bf16 deferred、int/uint/double/complex 仍 BLOCKED)?扩哪档要真机预算。
5. **非 `ops-<族>` 仓的 vendor 后缀**由谁给:spec 新增「仓族字段」还是继续手工 env?
6. **双源核验逐算子做**(不外推):放行的每个算子仍须「任务书 `适配硬件` ↔ op_def `AddConfig`」交叉核(目前仅 IsClose 已核)——放行清单不能外推。

---

> **一句话总结**:批 6b 不是「改真机大工程」,是「**把几张建在过时散文上的 gate 表对齐到已经泛化的引擎 + 接回一个断头配置**」。省力、可逆、引擎几乎不动。真正大的(dtype 谱、多输入独立形状、双实现、catlass)都显式分期到期3或排除本批,各配真机证据。
