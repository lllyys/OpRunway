# msprof 性能通路问题汇总（供逐条评审）

整理人：_robin（2026-09-11 离线日志排查）。数据来源：cgerc（0910 文件夹）、cgeru（同批
远端 session 存档）、Ctpmv（0911 文件夹 + prof 原文 + performance JSON）、a3 真机受控实验。
本页只列问题、证据、状态与归属，供 Mr.0 逐条裁定；修法细节见各自专档。

状态口径：**确认**=有直接证据；**推断**=证据链闭合但有一环未直接见到；**待验**=需再取证。

## A. accept msprof 通路缺陷（我方 skill）

- **A1. op_summary 双份计数。** `--application` 自动导出 + 显式 `--export=on` 导两遍，
  `parse_op_summary` glob 后不去重全累加 → launches/kernel_us 翻倍 → 好算子假 FAIL。
  证据：Ctpmv/910B3 launches 全 200 例=[2,2,2,2,2]、日志两次导出、kernel÷2 逐条吻合
  a3/910_93、去重后 61→192 PASS；cgeru/950 同型（[2,2,2,2,2]→[1,1,1,1,1]）。
  **a3 穿刺逐字节直证**：collect+export 产出**两份 op_summary，md5 完全相同**（`d700c354…`），
  批量/单发都如此——即"两遍导出→两份相同文件"这环已坐实（机制层），来自 `--application`
  自动导 + `--export=on` 再导。**状态：确认（机制逐字节坐实；Ctpmv/910B3 具体那次是同工具同流水线，
  强推断）。** 详见 [msprof-op-summary-double-count.md](msprof-op-summary-double-count.md)。
  **修法（Mr.0 定案）：不执行显式 `--export`，只靠 `--application` 自动导出 → 单份 op_summary →
  双份计数从源头消除**（第二份根本不产生，不留 md5 去重等兜底）。auto-export 会触发这点由
  a3/910_93、910B3、950 三平台的"两份现象"反证（两份=自动导+显式导）。前提：保留采集开关
  `--ai-core=on --task-time=on`（A2）确保有数可导；且"是否自动导"平台相关（A6），换新平台/CANN
  需真机确认它确实自动产 op_summary，否则解析取不到数、自然裁 NO_KERNEL。

- **A2. 默认采集参数不足 → 0 op_summary → NO_KERNEL。** `msprof --application` + `--export=on`
  在 a3/CANN 9.0.1 报 "no summary data to export"，产不出 op_summary；需加
  `--ai-core=on --task-time=on` 才出数。即默认口径在某些机器上直接得 NO_KERNEL。
  **状态：确认（a3 实测）。**

- **A3. 基线重复键 → 采集前崩（BASELINE_INVALID 退 3）。** `_load_gpu_baseline` 撞重复
  `(m,n,...)` 键即 raise → 退 3 → msprof 一次不跑 → 证据不足。
  证据：cgerc、cgeam 均栽此。**状态：确认。**

- **A4. 解析无去重守卫，launches 只记不校验。** `parse_op_summary` glob 后逐文件累加、
  不按内容去重；`launches` 记了保留行数却不参与任何机械校验（协议明写"不得再除以 launches"）。
  这是 A1 的直接使能，但独立成一条"缺机械门"。**状态：确认（代码）。**

- **A5. 多 launch 与双份计数数值上分不清。** 协议自述"一次调用可产 2–4 个真 launch
  （coo2csr 实测）"，又把"MIX task 是否产生需去重的明细行"标为待实测。真多 kernel 与
  重复计数在数值上无法机械区分——Ctpmv 的 launches=2 要靠"÷2 对齐 910_93"人工判出是双份。
  这点本身仍立，影响 A1 去重的判定。**原先它还是 A7 批量 profiling 的 demux 前提**（op_summary
  无 case 标识：`Op Name`=kernel 名、`Input Shapes`=N/A，按 case 拆只能靠"顺序 + 每 case 恰好
  1 kernel"）；**但 Mr.0 定案改走「每 case 单次」后此前提作废**——每次 msprof 只跑一个 case，输出
  天然归属，无需按 case 拆分（见 A7/A8）。**状态：确认（协议 + 现象 + a3 穿刺）。**

- **A6. 平台假设过时。** 协议对"`--application` 是否产 op_summary"的判断标注"实测
  A3/910_93"，换到 910B3 不成立（它自动导出了），正是 A1 的触发根；"ascend950 文件名与列"
  也标待实测。**状态：确认。**

- **A7. 极慢。** 200 例 ×（warmup + 5 次 msprof 采样，每次独立进程 + profiling + export）
  ≈ 1200 次带 profiling 的进程启动。证据：Ctpmv 那次跑 ~3.5 小时。**状态：确认。**
  **正解（Mr.0 定案）= 每 case 单次采样 + 免 warmup（见 A8），全 200 例就跑 200 次 msprof。**
  1200 次（200×(1+5)）→ 200 次，~3.5h → ~30–35min（**~6×**，a3 单次 msprof ~10s/case 外推）。
  **曾考虑「方案一」批量**（一次 msprof 打多 case、按 case 拆，a3 实测 12 条批量 ~10s vs 单发
  ~120s、~12× 更快），因 demux 无可靠锚点（见 A5）且照样双份计数（见 A1），风险高于那多出来的
  速度，**Mr.0 选更简单的 200 次单发路径**——每次 msprof 只跑一个 case，输出天然归属该 case，
  不需要 demux。**A1 也一并从源头消除**：定案不跑显式 `--export`，只留 `--application` 自动导出 →
  单份 op_summary → 不再双份计数（见 A1）。未验：msprof 单次内部预热行为（见 A8）、
  自动导出在各目标平台确实产 op_summary（见 A1/A6）、仅 910_93 未测 910B3。

- **A8. 采样定为「每 case 单次、免 warmup、不批量」（Mr.0 定案，即 A7 正解）。** 不做 5 次取平均、
  不挂我方 warmup、也不批量——**全 200 例跑 200 次 msprof，每 case 一次**；单次即可，msprof 内部
  自行预热。这条直接落定 A7：1200 次 → 200 次（~6×，~3.5h → ~30–35min）。**比批量更优的点**：每次
  msprof 只跑一个 case，输出天然归属，**不需要 demux**——A5 那个「无 case 锚点、靠顺序+每 case
  1 kernel 拆分」的前提随之作废（见 A5）。**A1 同样从源头消除**：定案还不跑显式 `--export`，只留
  `--application` 自动导出 → 单份 op_summary → 无双份计数（见 A1），不必再做 md5 去重。佐证多采样
  收益有限：a3 穿刺样本 spread ~5%、批量比单发仅低 2–10%。**落地前确认两点**：msprof 单次内部预热
  行为（丢弃/摊掉冷启动，单次数不偏冷）、自动导出在各目标平台确实产 op_summary（A1/A6）。
  **状态：Mr.0 定案；两个前提待真机确认。**

## C. 每算子性能结论（现状）

- **cgerc / 950**：msprof 崩（A3 重复键退 3），真实性能 **200/200 PASS**（aclrtEvent，任务书口径）。
- **cgeru / 950**：msprof 假 FAIL（A1 双份计数），真实 **200/200 PASS**（同卡 aclrtEvent，与历史吻合）。
- **Ctpmv / 910B3**：msprof 假 FAIL 139/200（A1 双份计数），去重后 **192/200 PASS**；
  剩 8 例最小尺寸（n≤~32）去重后 ratio 0.45–0.80，边界待 aclrtEvent 复核。**状态：推断**
  （去重结论基于 ÷2 对齐 910_93，op_summary 原文未回传逐字节验）。

## D. 流程 / 执行问题（会话现场）

- **D1. Ctpmv 只用 msprof 判"不通过"，未做交叉验证。** 二进制自带的是 host 墙钟诊断
  （chrono，量纲不对、只诊断不判），没有 kernel 级 aclrtEvent 自判可对照；会话让 msprof
  跑完 3.5h 直接判，没像 cgeru 那样做同卡对照。**状态：确认。**

- **D2. cgerc 会话手动跑任务包脚本漏带基线 → 全 NO_REF。** 把脚本单独拷到工作目录、
  没把 `gpu_baseline.csv` 一起拷，脚本按同目录找不到基线 → 报 "已回填 0 条" → 全 NO_REF。
  taskpkg_perf.log 即此。**状态：确认。**

## 待你逐条裁定的点

1. A 组各条哪些进本轮修、哪些遗留（A1/A2/A4 是同通路可一并修）。
2. Ctpmv 那 8 条小尺寸边界是否要 aclrtEvent 复核后再给最终结论。
3. 是否需要把本页 + 双份计数专档整理成对外可发的报告。
