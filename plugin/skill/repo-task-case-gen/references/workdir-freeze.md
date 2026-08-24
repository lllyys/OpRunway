# 工作目录与输入冻结

## 工作目录

进入 S2 前建立阶段时间线，每进一个阶段打一次点：

```bash
<python> scripts/mark_step.py 2 用例生成 -o evidence/timeline.jsonl
<python> scripts/mark_step.py --summary -o evidence/timeline.jsonl
```

```text
work/<operator>/
├── *.yaml
├── *_constraint.py
├── cases/
├── evidence/
├── conclusion/
└── delivery/
```

命令逐条追加到 `evidence/repro.sh`。

接口分面各自固化 YAML、用例、报告和结论；部署产物可以共享。

## 冻结输入

S2 末尾把张量物化到磁盘，执行期各轮复用同一份输入：

```bash
<python> scripts/freeze_inputs.py -j <case-json> --atk-cli <atk-cli> \
  -p <function-plugin> -d frozen/ -o evidence/frozen_inputs.json
```

CPU 基线接口名与 torch 不一致时必须传 `-p`，否则物化在基线调用处失败。

执行期用 ATK 的 `--input_data frozen/` 消费该目录。

冻结时顺带核三件事，任一不过退出码同为 2：

- 整张只有一个取值的张量测不出错（错的置换与对的置换逐元素相等）——
  这属于取值范围的设计问题，改 decl 的 `range` 后重生成，不是待验收算子的缺陷
- 单条用例的输入是否超字节预算（默认 2GiB，`--budget-bytes` 可改）
- 基线插件是否真的跑通——冻结用的 CPU 单节点就是它，这里不核就会推迟到
  S3 冒烟甚至 S4 全量才发现，且已执行的部署成本连带作废

## 无效用例

单条用例的输入字节预算由 `freeze_inputs.py` 在冻结时顺带核（默认 2GiB，
`--budget-bytes` 可改），不再单独跑一个脚本——它本来就要把每条用例的输入
落盘，字节数它最清楚，原地核对比留到 S3/S4 才发现更省。
