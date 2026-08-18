# CP-C 前置失败 attempt 契约

本页只描述验收 workflow 的工件边界；机器连接、容器、传输与代理不属于本契约。

## 两级事实

`vendor_build_receipt.py emit --failure-out` 证明 producer 在一次已完成 snapshot-digest 与 build 前树对账的
受控构建中，观测到 build、ELF、package resolution、target closure 或 build 后源码子树失败。它只持久化
typed stage/code、脱敏后的稳定错误文本、source snapshot、target request、producer hash，以及能取得的实测
safe facts；完整 argv/env 与原始异常文本不落盘，只保留在执行控制台。safe facts 在 producer 计算摘要前按
exact-key、RFC3339、有限非负时长、非 bool 整数与 ELF 前后状态严格校验。该工件的
`formal_eligible=false`、`acceptance_verdict=null`，不能单独作为 workflow
attempt，更不能代替 `VERIFIED` receipt。

`pre_execution_failure.py` 是升级为标准 workflow attempt 的唯一入口。它重新校验 vendor attempt 自身摘要，
并与原始 CP-A `source_facts.json`、显式 spec 的公开/内部身份、content anchor、源码 scope/摘要、provenance 与
target request 严格对账。live receipt preflight 失败也使用该入口；被检查失败的 receipt 只记录为
`trusted=false` 的字节 claim，不能反过来为当前输入背书。

## 终态事务

finalizer 与正式/attempt publisher 共用每个报告根的排他锁。失败事务先清理可能复活旧裁决的 downstream，
再原子写 `vendor_build_attempt.json`、`前置执行失败明细.md` 和 schema-v2 `attempt_record.json`，最后以
`pre_execution_terminal.json` 作为 commit manifest；marker 逐一绑定前三个 payload 与原始 vendor attempt
的 SHA-256，consumer 必须重算。原子替换后同步父目录。正式 receipt 与 failure attempt 互斥；成功清 stale
failure，失败清 stale receipt，vendor 侧锁覆盖 cleanup → build/package/closure → publish 整代，避免旧代晚到
覆盖新代。

durable marker 是 fail-closed 终态：`finalize_clean_acceptance`、正式 publisher 与 renderer 都必须验证其
manifest 后拒绝它。marker 提交前崩溃留下的任一 pre-execution 保留 payload 也视为 incomplete terminal，
不能因 marker 缺席而回落正式路径。renderer 的 terminal 检查、JSON 读取与 Markdown 原子写全程持有同一
报告根锁。只有一次新的完整 workflow 可以在开跑时于同一工件锁内显式失效 marker/orphan。任何中途崩溃
至多留下不可正式消费的非正式 payload，
不能把旧 evidence/verdict/perf 重新发布成 acceptance。

所有输出路径从调用方已建立信任的稳定 base 开始逐段 `lstat`，因此允许 base 之上的系统级路径 alias
（如 macOS `/var -> /private/var`），仍拒绝 base 之下任一父级软链、非目录和 leaf 软链。报告根不得等于
可信输入或落在可信输入之下；标准布局允许可信 `spec/source_facts/vendor attempt` 保存在
`<out>/work/`，但 finalizer 必须显式枚举本事务实际会写或清理的全部根级路径，并拒绝任一受控路径与可信
输入相等或互为祖先。它不递归清理报告根，`work/` 及其可信输入必须原样保留。保留输入从报告根稳定 base
向下逐段 `openat(O_NOFOLLOW)` 一次读成 payload + SHA，并记录 device/inode；marker 提交前复核 inode/SHA，
marker 记录相对路径与 SHA，消费方再次 no-follow 打开复核。报告根事务从加锁到 marker/正式总结 commit 全程
持有 `O_DIRECTORY|O_NOFOLLOW` dirfd，lock、临时文件、删除、replace 和 fsync 均走相对 fd；根路径换绑只会
fail-closed，不会把工件写进替换目录。所有原子 writer 共用 `.oprunway-artifact-tmp-` 前缀：进事务时发现
旧轮 orphan 即拒绝，异常清理只删除本轮自己创建且 inode 未换绑的临时文件。fresh workflow 创建、正式
publisher、clean-finalize 与 renderer 继续共用父链/inode 守卫。消费 marker 时还须把 retained spec raw SHA、
source_facts envelope digest、spec+facts 重建的 op/execution identity、retained vendor payload 分别与 binding、
marker 和根级 terminal payload 交叉，不能只相信 marker 自报 path/hash。renderer 的正式 JSON、自动发现的
根级或 `work/source_facts.json`、Markdown 写入与明细删除也都走同一 transaction；仅报告根外显式普通
source_facts 保持兼容，根内父链或 leaf symlink 一律拒绝。
报告根外显式 source_facts 也不是先 `islink/isfile` 再按路径重开：它须以
`O_NOFOLLOW` 单次打开、`fstat` 证明 regular，并只从该 fd/inode 读取和校验，避免检查与打开之间被原子换成
symlink。

## 执行边界

前置失败收口不需要 golden/caseset，也不得调用 Task1、外部 driver、DUT 或 profiler；不产生
`acceptance.json`、`verdict.json`、`perf_report.json` 或正式中文验收报告。参数、落点、snapshot 本身不可信
等发生在 build 前树对账之前的错误可以由 producer 报 rc=2，但不得升级为标准 workflow attempt。
