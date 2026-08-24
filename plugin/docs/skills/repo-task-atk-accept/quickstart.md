# repo-task-atk-accept 上手

已有封印交接包、工程与 NPU 时，用这个 skill 完成验收。

## 前提

- 交接包由 `repo-task-case-gen` 成功封印，原件保持只读
- 验收机已安装交接包记录的 ATK 版本
- NPU 可用，CANN `set_env.sh` 可以加载
- 待验收工程已在本地，任务书与生成侧使用的是同一份

只有任务书时先看 [生成侧上手](../repo-task-case-gen/quickstart.md)。

## 第一条消息

```text
用 repo-task-atk-accept 验收 aclnnMedian。
交接包：/home/user/cases/atk-case-median
算子工程：/home/user/ops-nn
任务书：/home/user/task/median.md
device 0；CANN：/usr/local/Ascend/ascend-toolkit/latest
```

助手会复制交接包，在副本内依次完成 S0、S3、S4、S5。两类情况会先停下来：

- S0 发现摘要、任务书、ATK 版本或工程接口不一致
- 环境、构建、绑定或冒烟门禁不通过

## 你会收到什么

成功时交付精度与性能结论、环境和加载路径证据，以及可复现包。失败或环境阻塞时，
报告会写明阶段号、失败门名与解除条件，不会强跑后续阶段。

## 常见卡点

- ATK 版本不同：安装交接包记录的版本，决定升级则回生成侧产出新包
- 助手找不到签名：只读待验收工程头文件，不去 CANN 装机目录找同名接口
- `check_bundle.py` 报任务书不同：改用封印时的原任务书
- S3 或 S4 失败：不得回 S2 重新生成
- 精度未通过：性能按规则不执行，不能写成人工通过

