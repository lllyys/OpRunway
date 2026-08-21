# repo-task-atk-test 上手

让 AI 助手用这套流程验收一个算子。流程本体由助手执行，你的工作是把它缺的信息一次给全。

## 前提

- 两侧都已按 [README 的安装](../../../README.md#安装) 装好 skill 与 ATK
- 生成侧有 Python 与 torch（CPU），不需要 NPU 或 CANN
- 验收侧有 NPU，CANN 已装好且 `set_env.sh` 可以 source
- 验收侧的算子工程已下载到本地，收到的是 PR 时先归约成本地工程

开始之前亲自跑一次 `atk case --help`，确认它正常返回。

## 第一条消息怎么写

### 只有任务书时
```text
用 repo-task-case-gen 为 aclnnMedian 生成用例交接包。
任务书：/home/user/task/median.md
提交形态：aclnn；对标基线：torch.median
```

### 已有交接包时
```text
用 repo-task-atk-accept 验收 aclnnMedian。
交接包：/home/user/cases/atk-case-median
算子工程：/home/user/ops-nn
任务书：/home/user/task/median.md
device 0；CANN：/usr/local/Ascend/ascend-toolkit/latest
```

## 中途会问你什么

有两类问题助手必须问，答复会被记进证据里：

- **任务书没写清的地方** —— 标成待确认项，确认之前不进入下一阶段
- **要不要测另一套语义** —— 比如同一接口在某个出参传空时换算法，默认不测，你明确要求才测

## 常见卡点

安装类卡点如下：
- **clone 时不用 `--recursive`** —— 子模块里是本 skill 专用的 ATK 源码
- **不要 `pip install atk`** —— 索引上是同名无关包，只从 `third_party/ATK/` 构建锁定版本
- **`pip show atk` 通过仍不能运行** —— 安装后还要运行 `atk case --help`

运行类卡点如下：
- **助手找不到签名** —— 只读算子工程的头文件，不去 CANN 装机目录找同名接口
- **`check_bundle.py` 报不一致** —— 任务书不一致就用封印原件，更新过则重新生成并封印。
  ATK 版本不一致就安装交接包记录的版本，决定升级则用新版本重新生成并封印。
- **助手想重新生成用例** —— S2 后输入已冻结，S3 或 S4 失败都不能回到 S2
