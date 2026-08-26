# repo-task-case-gen 上手

拿一份社区任务书，产出带冻结 golden 的 ATK 用例包。**不需要 NPU**。

设计与红线在 `skill/repo-task-case-gen/CLAUDE.md`，运行时规则在同目录 `SKILL.md`。
本文只给一条能照着敲完的真实流程。

## 例子：aclnnRoll

任务书要求「在已有 aclnnRoll 基础上扩展支持 complex64」，工程在
`ops-test/roll/`，母仓是 ops-math。

### 1. 建工作目录并探环境

```bash
mkdir -p atk-case-Roll && cd atk-case-Roll
python <skill>/scripts/probe_env.py -o env.json
```

要看到 `atk` 与 `torch` 都有版本号。退出码 2 就是缺件，先装。

### 2. 写事实表

读任务书，再读 `ops-test/roll/docs/aclnnRoll.md`，按
`references/interface-facts.md` 的字段清单填 `facts.json`。每项标来源：

- `x` 的 dtype 列在任务书的参数说明表里 → `taskdoc`
- 函数原型与「dims 与 shifts 必须等长」在工程文档里 → `opdoc`

```bash
python <skill>/scripts/check_facts.py facts.json
```

它会拦住三类错：dtype 没从 `FLOAT16` 翻成 `fp16`、把 `workspaceSize` 也列进
`params`、某项漏了 `source`。

### 3. 写 YAML

照 `assets/example/Roll.yaml` 改。三处容易错：

- `inputs` **只列输入参数**，`out` 不写进去
- `aclnn_api_type` 填 `aclnn_function`，不是 `pyaclnn`
- `dim_values` 必须是离散列表，写成 range 会让 `atk case` 卡死

### 4. 判断要不要写约束器

判据表在 `references/plugin-authoring.md`。roll 命中「参数之间有依赖」
（`shifts` 与 `dims` 必须等长），所以写 `Roll_constraint.py`，
照 `assets/example/Roll_constraint.py` 改。

```bash
python <skill>/scripts/gen_cases.py --dry-run
```

dry-run 用 `dtype_numbers=1` 跑，只验证 YAML 与约束器能组合出合法用例。

### 5. 正式生成

回填 `dtype_numbers`（算法见 `references/case-strategy.md`，9 个 dtype 填 20）：

```bash
python <skill>/scripts/gen_cases.py
```

roll 这套配置出 180 条。少于 100 条脚本会拦，那是 YAML 写窄了。

### 6. 冻结 golden

```bash
python <skill>/scripts/freeze_golden.py
```

要看到 `180/180 条（100.0%）`。不到 100% 说明有用例 CPU 标杆跑不出来，
它们在 NPU 上也无法比对，回第 3 步修，**不要剔掉了事**。

## 产出

```text
atk-case-Roll/
├── facts.json          事实表，每项带 source
├── env.json            生成侧环境指纹
├── Roll.yaml           用例设计
├── Roll_constraint.py  约束器
├── cases.json          180 条用例
└── golden/             CPU 标杆输出
```

整个目录交给 `repo-task-atk-accept` 即可，不需要打包也不需要校验和。

## 常见卡点

| 现象 | 原因 |
| --- | --- |
| `atk case` 一直不结束 | `dim_values` 写成了 `range` |
| `KeyError: 'FLOAT16'` | dtype 没翻译成 ATK 词表写法 |
| golden 只出来一部分 | 约束器让某些用例参数不合法，看那批用例的共同特征 |
| golden 一条都没有 | `name` 不是可 eval 的 torch 接口名，或执行器注册名对不上 |
