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

生成侧不需要 NPU。给出任务书路径、提交形态和对标基线，任务书没写清的地方由助手继续问。

```text
用 repo-task-case-gen 为 aclnnMedian 生成用例交接包。

任务书：/home/user/task/median.md     # 精度与性能判据的唯一来源
提交形态是 aclnn                      # pytorch / aclnn / kernel / triton / atb 之一
对标基线是 torch.median
```

助手建立 `atk-case-<op>` 工作目录，跑完 S1–S2，并以 `seal_bundle.py` 退出码 0 为出口。

### 已有交接包时

验收侧需要交接包、工程目录、同一份任务书、device 和 CANN 路径。

```text
用 repo-task-atk-accept 验收 aclnnMedian。

交接包：/home/user/cases/atk-case-median
算子工程：/home/user/ops-nn
任务书：/home/user/task/median.md
用 device 0
CANN 在 /usr/local/Ascend/ascend-toolkit/latest
```

助手复制交接包建立 `atk-verify-<op>` 工作目录，从 S0 接收门开始，随后推进 S3–S5。

## 中途会问你什么

有两类问题助手必须问，答复会被记进证据里：

- **任务书没写清的地方** —— 标成待确认项，确认之前不进入下一阶段
- **要不要测另一套语义** —— 比如同一接口在某个出参传空时换算法，默认不测，你明确要求才测

## 常见卡点

- **clone 时不用 `--recursive`** —— 子模块里是 ATK 源码，只有本 skill 用得上
- **不要 `pip install atk`** —— 索引上有一个同名的无关软件包。ATK 只能从
  `third_party/ATK/` 源码构建，子模块锁定的是本流程验证过的那版，换版本能力矩阵会失效
- **`pip show atk` 过了不代表能跑** —— 真机上出现过缺 pytz：包导得进来，`atk case`
  一调就报错，任务在 0 秒内「正常结束」，产物是空的。装完必须再跑一次 `atk case --help`
- **助手说找不到签名** —— 签名只从算子工程目录里的头文件读，不去 CANN 装机目录找同名
  接口；这条只适用于验收侧核对 PR。社区算子与官方接口重名很常见，装机目录里那份是
  另一份代码。工程里确实没有时请补齐工程，不要让助手换个目录继续找
- **`check_bundle.py` 报任务书不一致** —— 改用封印时的原始任务书，不要改清单或摘要绕过。
  如果任务书确实已更新，用新任务书重新运行生成侧并封印一份新交接包
- **`check_bundle.py` 报 ATK 版本不一致** —— 在验收机安装交接包记录的 ATK 版本。若决定升级
  ATK，用升级后的版本重新生成并封印用例；不要手改 `env.json` 或 `bundle.json`
- **助手想回头重新生成用例** —— S2 结束后输入已冻结，S3 或 S4 失败都不允许回到 S2。
  这是有意为之，防止用测试配置去迁就算子的实际表现
