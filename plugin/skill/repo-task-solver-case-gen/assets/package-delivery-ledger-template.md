# {delivery_group} 统一交付对账表

（交付组装包步骤消费本模板：替换全部 {placeholder} 后随交付树落盘为
DELIVERY_LEDGER.md。对账表是「交付了什么、怎么验过」的唯一清单；指纹复核
由验收侧执行。）

交付形态：{delivery_shape}。
包体均为纯脚本（零数据数组），交付树 `{delivery_tree}` + 本说明两件 +
性能采集说明。

## 包指纹（manifest sha256 前 16 / 文件数）

| 包 | 指纹 | 文件数 | 体量 |
| --- | --- | --- | --- |
{fingerprint_size_table}

## 固化字段指纹（v3，防换基/换 mean 的逐包核对）

| 包 | index 条目数 | info 用例数 | mean 固化（index 顶层） | sample_map 条数（批量包） |
| --- | --- | --- | --- | --- |
{frozen_fields_table}

## 构建与验证证据

{build_evidence}

## 已知边界（交付即声明）

- 数值通过 ≠ 正式验收通过（`formal` 恒 `PENDING_RULING`）；复数残差按复模一体判定
  （不拆实虚）。
- {known_boundaries}
