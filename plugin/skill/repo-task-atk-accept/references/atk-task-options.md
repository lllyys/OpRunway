# ATK 执行参数

**真源是 `atk/bin/task.py` 的 click 定义，不是 ATK 的《任务执行参数说明》。**
那份文档少了 30 多个参数——`--slice_input` 就不在里面，非连续场景因此漏了一轮。

版本 26.8.8，`task` 子命令有 55 个参数（长短选项合计 93 个名字）。
下面只列与社区算子验收相关的，**完整清单重查**（真机验证过这条能跑）：

```bash
python -c "
import re,inspect,atk.bin.task as T
print(re.findall(r'\"(-{1,2}[a-zA-Z0-9_-]+)\"', inspect.getsource(T)))"
```

## `run_atk.py` 已经在传的

不用自己加，改这些要改脚本：

| 参数 | 值 | 为什么 |
| --- | --- | --- |
| `--backend aclnn` `--devices` | 用户给的卡号 | 待验收侧实算 |
| `--task accuracy_load` + `--output_path` | golden 目录 | 读冻好的标杆离线比对，不重算 |
| `--task accuracy` / `performance_device` | 按 `--mode` | 精度轮 / 性能轮 |
| `-p` | `function_<op>.py` | 自动 glob 到的执行器 |
| `--slice_input non_contiguous --slice_input_ratio <ratio>` | `facts.json` 的 `non_contiguous.required` 为真时，ratio 取 `non_contiguous.ratio` | 切中哪几条由 `random.Random(用例 id)` 定，可复算，见 run-accuracy.md「精度的连续与非连续在同一轮里混跑」 |

## 三个会毁掉 golden 比对的参数

**这三个碰了 `accuracy_load` 就全错，而且不报错。**

| 参数 | 碰了会怎样 |
| --- | --- |
| `--disable_id_seed` | 输入数据的随机种子默认是**用例 id**（`atk/tasks/backends/backend.py:148`）。关掉它，跑出来的输入与冻 golden 时不是同一批，比对结果全部无意义 |
| `--input_data` | 直接换掉输入来源，同上 |
| `-s/-e/--white_list/--black_list` 改了范围又复用同一份报告 | golden 按用例 id 对号入座，筛过的报告与全量报告不能混着判 |

`run_atk.py` 的隔离复验用 `--white_list` 单条跑，那是**每条各出一份报告**，不是筛完当全量用。

## 验收相关但现在没用上的

按价值排。要用先在真机上单跑一次确认参数被接受，再改脚本。

| 参数 | 默认 | 干什么 | 为什么值得考虑 |
| --- | --- | --- | --- |
| `-ms/--mssanitizer` | `False` | 开内存检测工具 | **社区算子最常见的缺陷是越界写**。跑通不等于没越界，这个能抓 |
| `-cp/--cpp_func_signature_type_path` | `False` | 按头文件做 aclnn 参数校验 | 抓签名与实现不一致 |
| `--clean_l2_cache` | `False` | 性能任务前清 L2 | 不清的话前一条用例的缓存会让后一条偏快 |
| `--fluctuation_check` | `False` | 性能波动校验 | 任务书要「不劣化于 X」，波动没界定时结论不稳 |
| `--performance_data` | — | 按顺序给 warmup 次数、采集次数等 | 性能轮的预热次数直接影响首条用例的读数 |
| `-bf/--boundary_filter` | `default` | `boundary` 只跑边界用例 / `no_boundary` 只跑非边界 | 生成侧 `extra_numbers: 0` 时全量里没有边界用例，这个参数用不上；开了边界才有意义 |
| `-rn_32b/--random_align_32b_nums` | `None` | 每个 dtype 下 32B 对齐与非对齐各跑几条 | 与 `align_32B` 配套，**跟非连续是两回事** |
| `--dc_loop_nums` | `50` | 确定性计算的重复次数 | 只在判定确定性时用 |

## 超时与并发

| 参数 | 默认 | 备注 |
| --- | --- | --- |
| `-to/--timeout` | 650 秒 | 单个任务。**不是整轮** |
| `-dt/--db_timeout` | 11 分钟 | 数据表查询 |
| `-mt/--max_task` | 100 | 最大并发任务数 |
| `-sp/--single_process` | `False` | 单进程模式，**定位段错误时开它**，多进程下 worker 崩成 zombie 只表现为永久等待 |

`-sp` 那条对应 troubleshooting.md 里「跑测长时间停在 `0/N`」那个坑：
ATK 没有超时兜底，worker 崩了就是永久等。单进程能把段错误暴露成栈。

## 落盘与调试

| 参数 | 备注 |
| --- | --- |
| `--save_data <item>[:<format>]` | 生成侧冻 golden 用的就是 `--save_data output`，可多次传 |
| `--print_data input/output/all` | 打印数据，只在单条复现时用 |
| `-o/--output` | 输出目录 |
| `--need_time` | 默认 `True`，输出目录带时间戳。**`run_atk.py` 的 `_newest_report` 靠它区分轮次**，不要关 |
| `--trace` / `-ecp` | profiling，性能定位时才用 |

## 清单出处

`atk/bin/task.py`，ATK 26.8.8，2026-08-27 从源码抽取。
表里没有的参数按上面的命令重查一次再判定「不支持」。
