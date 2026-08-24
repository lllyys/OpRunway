# repo-task-case-gen 上手

只有任务书时，用这个 skill 生成可交给 NPU 验收侧的封印用例包。

## 前提

- 已安装本仓锁定的 ATK 与 CPU 版 torch
- `atk case --help` 正常返回
- 任务书至少写清接口签名、dtype、精度要求与性能口径
- 不需要 NPU、CANN 或待验收工程

## 第一条消息

```text
用 repo-task-case-gen 为 aclnnMedian 生成用例交接包。
任务书：/home/user/task/median.md
提交形态：aclnn；对标基线：torch.median
```

助手会完成 S1、S2 与封印，并把产物放在独立工作目录。任务书有歧义时，它会列出
待确认项；确认前不会读取工程补答案，也不会进入 S2。

## 交给验收侧

成功出口是 `evidence/bundle.json`。保留原目录只读，为每个待验收 PR 复制一份，
再按 [repo-task-atk-accept 上手](../repo-task-atk-accept/quickstart.md) 提供：

- 封印交接包
- 待验收工程目录
- 同一份任务书
- device 与 CANN 路径

## 常见卡点

- 子模块为空：运行 `git submodule update --init`
- `pip install atk` 装到同名无关包：只从 `third_party/ATK/` 构建
- 任务书缺签名或 dtype：先用 `repo-task-doc-write` 补齐
- 封印门报错：按失败门名补齐产物，不绕过门禁
- 封印后想改用例：回到未封印的生成流程重新产出一份新交接包

