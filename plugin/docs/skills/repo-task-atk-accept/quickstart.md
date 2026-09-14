# repo-task-atk-accept 上手

拿一个用例包和一份算子工程，在 NPU 上编译部署、跑精度与性能、出结论。

设计与红线在 `skill/repo-task-atk-accept/CLAUDE.md`，运行时规则在同目录 `SKILL.md`。
本文只给一条能照着敲完的真实流程。

## 例子：aclnnRoll

用例包是上一步产出的 `atk-case-Roll/`，工程在 `ops-test/roll/`，
母仓是 `~/repo-task/ops/ops-math`。

### 1. 复制副本并探环境

```bash
cp -r atk-case-Roll atk-verify-Roll && cd atk-verify-Roll
python <skill>/scripts/probe_env.py --op Roll -o env.json --write-env-sh evidence/env.sh
```

**在副本里验收，不在原用例包里跑**——原包要留着复验和给别的 PR 复用。

输出里 `soc` 那行给出构建要用的 `--soc` 取值，A3 机器是 `ascend910_93`。

### 2. 编译安装

```bash
source evidence/env.sh
python <skill>/scripts/build_install.py --op Roll \
    --project ~/repo-task/repo-test/ops-test/roll \
    --parent-repo ~/repo-task/ops/ops-math \
    --soc ascend910_93 -o install.json
```

社区算子目录不能独立构建，脚本会把它合进母仓再跑 `build.sh`。
**每次都会清母仓的 `build/`**——换算子不清会拿到上一个算子的符号。

要看到 `符号 aclnnRollGetWorkspaceSize 可见`。

### 3. 冒烟

```bash
source evidence/env.sh
python <skill>/scripts/run_atk.py --mode smoke -c cases.json --golden golden -o smoke_result.json
```

**看的是执行成功数**：30/30 执行成功就进下一步，哪怕精度有几条没过。
执行失败才拦——那是部署问题，跑全量只会把它重复 180 遍。

### 4. 精度

```bash
python <skill>/scripts/run_atk.py --mode accuracy -c cases.json --golden golden -o accuracy.json
```

有执行失败就必须再跑一轮隔离复验：

```bash
python <skill>/scripts/run_atk.py --mode isolate -c cases.json --golden golden \
    --ids-from accuracy.json -o isolate.json
```

aicore 异常会把设备打到异常状态，同批次后面的用例跟着全挂。
IndexFillTensor 真机上就是这样：47 条执行失败，逐条重跑后只有 1 条是真的。

### 5. 性能

先抽样再跑，全量太慢：

```bash
python <skill>/scripts/run_atk.py --mode performance -c perf/cases.json \
    --golden golden --facts facts.json -o performance.json
```

跑什么由 `facts.json` 的 `performance.kind` 决定，不是选择题。

### 6. 结论

```bash
python <skill>/scripts/verdict.py -o verdict.json --report report.md
```

结论由脚本从数据推导。`report.md` 是可直接交付的验收报告。

## 三个环境变量

`evidence/env.sh` 里这三个缺一不可，每条命令都要先 source：

| 变量 | 不设会怎样 |
| --- | --- |
| `ASCEND_CUSTOM_OPP_PATH` | 算子未注册 |
| `ATK_CUSTOM_OPP_PATH` | 绑到 CANN 内置的同名接口，测的不是待验收实现 |
| `LD_LIBRARY_PATH` | `libcust_opapi.so` 加载失败 |

跑测日志里要出现这一行才说明绑对了：

```text
import aclnnRollGetWorkspaceSize from <...>/roll_atk_math/op_api/lib/libcust_opapi.so success!
```

## 常见卡点

| 现象 | 原因 |
| --- | --- |
| 装完符号找不到 | `--ops` 要用蛇形目录名，`--op` 要与 aclnn 接口名同大小写 |
| 包里是别的算子的符号 | 母仓 `build/` 没清，用默认行为不要加 `--no-clean` |
| `标杆输出为空` | 用例文件基名变了，golden 子目录名取自它 |
| `参数数量不匹配` | 用例包的输入列表与真实签名不一致，回生成侧修 |
| `error code:507015` | aicore 异常，算子实现缺陷，收集日志报给作者，不要改用例绕开 |
