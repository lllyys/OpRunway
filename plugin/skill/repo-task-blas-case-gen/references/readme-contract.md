# README 投影与六个交付文件

## 目录

- [章节来源](#章节来源)
- [开发者约束](#开发者约束)
- [同步修改规则](#同步修改规则)

README 是事实表 FACTS 供人阅读的投影，不是独立的事实来源。
`package.py render` 每次都从模板
重建它，`package.py check` 机械核对表头、列数和六件的一致性。

## 章节来源

| README 章节 | FACTS 或生成结果 |
| --- | --- |
| 来源 | `sources`、`symbol`、`returns`、`family`、两个版本号 |
| 接口签名 | `returns`、`symbol`、按顺序排列的 `params[].ctype/name` |
| 六件清单 | 固定文件名与 `<op>_test.csv` |
| 列契约 | params 角色、属性、profiles 与 CSV 表头投影 |
| param.h 读列要求 | 固定开发契约与完整投影表头 |
| Golden 要求 | `golden`、profile dtype、inout 与 `producer` |
| 校验形态与阈值 | `verify` token 与固定 dtype 阈值表 |
| 用例块与覆盖 | `generate()` 的 blocks 与 pairwise report |
| 精度验收 | 固定命令、结果路径和退出码 |
| 性能验收 | `perf.key/threshold`、固定 msprof kernel 协议与单次调用条款 |
| GPU 基线 | `perf.key/rows/meta`，无 perf 时写不评判 |

## 开发者约束

- `param.h`：
  - 每个投影列都显式调用 `ReadMap`，缺列或空值时抛错。
  - `ReadMap` 的默认值行为不能替代列契约。
- Golden：
  - 使用 README 列出的来源、dtype 和 producer 链。
  - in-place 输入先快照，再调用被测接口和 golden。
- Verify：
  - 每个 token 都实现 README 指定的对象、方法和阈值。
  - 不用单一全矩阵误差检查替代结构性或残差检查。
- 被测调用：
  - 一条 gtest 用例只调用被测接口一次；预热与重复由验收量具 `verify_performance.py` 负责。
- CSV：
  - 逐字节部署到 `test/<family>/<op>/<arch>/<op>_test.csv`。
  - 不在测试源码旁保留另一份手写 CSV。

## 同步修改规则

列、golden、校验形态、阈值或部署位置任一契约变更时，必须同时修改事实表投影侧与
C++ 消费侧，并在目标 SoC 真机重新构建和运行。只改 README、只改 CSV 或只跑静态
`check` 都不能证明新契约已落地。
