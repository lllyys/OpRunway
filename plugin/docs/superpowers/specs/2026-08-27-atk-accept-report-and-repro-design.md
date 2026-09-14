# 跑测侧的报告分层、最小复现包与现场归档

**范围：** `skill/repo-task-atk-accept`。生成侧不动，用例包九项契约不动。

## 要解决的三件事

1. 报告只有一份 `report.md`，精度不按 dtype 出通过情况，没有给人看的极简版
2. 失败用例只有一串 id，算子作者拿不到能直接跑的东西
3. 验收现场十几个文件平铺，人看的、agent 读的、排查用的混在一层

## 现场按读者分四区

```text
<输出目录>/<op>-verify/
├── report/    index.html · report.md · verdict.json
├── repro/     rerun.sh · README.md · env.sh · failed/cases.json
├── input/     用例包副本，只读语义
└── work/      CWD：stage/ evidence/ atk_output/ opp/ isolate/ subset/
```

与生成侧用例包 `<输出目录>/<op>/` 同根不同名，`-verify` 后缀避免撞名。
入口参数新增 `输出目录`，与生成侧对称：用户给，没给就问。

**CWD 是 `work/` 而不是现场根。** `atk_output/` 由 ATK 在 CWD 下建，落点改不了；
把 CWD 放进 `work/`，过程数据一并圈住，现场顶层永远只有四个目录零个文件。
代价是命令里的用例包写成 `../input/…`，产物写成 `stage/…`。

连带改动两处，漏一处的表现都是静默出错：

| 改动 | 不改的后果 |
| --- | --- |
| `run_atk.py` 找 `function_*.py` 从 CWD 改到 golden 的父目录 | CPU 标杆执行器静默挂不上 |
| `--builtin-out` 默认值改成跟着 `-o` 走 | 基线轮写到老路径，报成「基线轮无数据」 |

## 报告只算一次，渲染两遍

`verdict.py` 算数并写 `verdict.json`；`render.py` **只读 verdict.json**，
出 `report.md` 与 `index.html`。两个渲染器共用列定义与说明文本，
避免「HTML 说通过、md 说不通过」。

### 精度按 dtype

分母是**用例包里的用例数**，不是 ATK 报告的 `total`；两者不等时报告点出来。
「未通过」拆三列——精度不符 / 执行失败 / 未判定，归因不同不能揉成一列；
**全过时不出这三列**，一排 0 是噪声。连带失败与未复验不进分母，与总体口径一致。

### 性能按规模档

沿用已有的 `_band_rows`（逐样本先算比值再取中位数，某档少于 3 条写 unknown）。
新增：`kind=none` 时若有 `perf/manifest.json` 的 `bands`，出各档**绝对耗时**表——
没有基线不等于没有形态，切分路径在大张量上崩掉照样看得见。

### index.html

单文件、内联 CSS、零 JS、零外部资源（远程机无网），`prefers-color-scheme` 两套色。
四块一屏：总结论横幅、精度 dtype 表、性能规模档表、失败用例与复现入口。
部署信息、推断项、证据链只进 `report.md`。

## 最小复现包

`repro/` 只回答一个问题：**改完 kernel 之后，那几条挂掉的用例过了没。**
不重现裁决逻辑，不出 `verdict.json`，结果直接看 ATK 自己产的 xlsx。
读者是算子作者，不装本 skill。

三条约束：

- **不拷 golden**（几百 MB），`PKG=$HERE/../input` 引过去；整包拷走时改这一个变量
- **atk 命令由 `run_atk.py` 的 `_node_command` 现拼**，不抄一份。为此
  `_node_command` 不再自己 `resolve()` golden，并接受传入的 `load_node`
- **失败用例子集仍叫 `cases.json`**，只换目录。基名是 golden 子目录名的来源

子命令按本轮形态生成：`failed`（有真实失败才有）、`accuracy`、`perf`，
`kind=builtin` 时多一个 `perf-baseline`。跑不了的形态不生成。

## 顺带修掉的

`SKILL.md` 里「生成侧报过抽不够的规模档，要在验收报告的『不覆盖的范围』里写明」
这条指令**在真机上无法执行**——`report.md` 由 `verdict.py` 生成、没有那一节，
而且它撞红线 3「报告作者不写结论」。改成由 `verdict.py` 逐档出表、
样本不足自己写 `unknown（样本不足）`。

## 不做

- 不把抽样、档位阈值搬回跑测侧（两套阈值真机上出过事）
- 不在 `repro/` 里放量具副本（787 行 Python 不是给人读的）
- 不给 `render.py` 任何计算职责
