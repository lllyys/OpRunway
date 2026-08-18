# ATK 接口事实：调用、输入、输出与 profiler

**本文件只记录「ATK 客观如此」的事实**，每条附可复核的 `文件:行号`，字面量逐字照抄（全角半角、空格数、
大小写均按源码原样）。不含任何处置规则、取舍或建议。

| | |
|---|---|
| ATK 仓 commit | `7220f27e83740cd43b300ed2d4362e12d6cc0d42` |
| 对应 `atk --version` | `26.5.14`（`atk/__init__.py:21`：`PACKAGE_VERSION = "26.5.14"`） |
| 行号基准 | 上述 commit 下的 `atk/` 子树；文档类引用另注明路径 |

**版本一旦不同，本文件整体失效**，须回源码逐条重核。行号对得上不等于语义没变。

本文件不重复以下已有材料：五份上游逐字副本、其 README 的勘误与索引、以及同批《ATK 源码事实（十条）》。

---

## 一、调用与参数

### 1.1 命令入口与版本探测

- `atk` 是 `console_scripts` 入口 `atk = atk.__main__:main`（`setup.py:41-43`）；`main()` 体为
  `sys.exit(cli())`（`atk/__main__.py:25-31`），故 `python -m atk` 与 `atk` 等价。
- 主命令组声明为 `@click.group(cls=DynamicCLI, invoke_without_command=True, chain=True)`
  （`atk/bin/atk.py:29`）。`--version` 是组级 `is_flag` 选项（`:30`），不是子命令。
- 命中 `--version` 时执行 `click.echo(VERSION_BANNER)`（`atk/bin/atk.py:48-49`）；
  `VERSION_BANNER = f"{PACKAGE_VERSION}"`（`atk/__init__.py:24`）。stdout 内容是不带前缀或横幅的裸版本串。
- 同函数下一行 `logging.info(f'ATK Version: {VERSION_BANNER}')`（`atk/bin/atk.py:50`）走 stdlib root
  logger。`log_init` 只在 `Task.init()` 内调用（`atk/bin/base.py:208-211`），`--version` 路径不经过它；
  root logger 保持默认 WARNING 级别，该 INFO 记录不产生输出（本机以最小 Python 复现验证）。
- ATK 实际只有 `case`、`aclnn`、`pytorch`、`task`、`node` 等子命令与一个组级 `--version` flag；
  版本探测不是子命令。

### 1.2 `atk case` 的全部开关（`atk/bin/case.py:31-101`）

| 开关 | 类型/默认 | 行号 |
|---|---|---|
| `-f` / `--case_file` | `click.Path(exists=True)` | `:31-38` |
| `-df` / `--dtype_filter` | str，默认 None | `:39-46` |
| `-ps` / `--perf_standard` | str，默认 None，取两个逗号分隔浮点 | `:47-56` |
| `-s` / `--seed` | int，**默认 1234** | `:57-65` |
| `-l` / `--log` | `Choice(["debug","info","warning","error","fatal"])`，默认 `info` | `:66-74` |
| `-p` / `--plugin_path` | `click.Path(exists=True, file_okay=True, dir_okay=True)`，默认 None | `:75-83` |
| `-dt` / `--dtype_numbers` | str，默认 None | `:84-92` |
| `-en` / `--extra_numbers` | str，默认 None，取 `'all'` 或非负整数 | `:93-101` |

- `-s/--seed` 的实现是 `set_seed(args.seed)`（`atk/bin/case.py:109`）→ `random.seed(seed)`
  （`atk/common/utils.py:43-44`），不 seed `numpy` 或 `torch`。
- `atk case` 没有插件自动发现，插件只能由 `-p` 指定（`atk/bin/case.py:75-83`）。

### 1.3 `atk aclnn` / `atk pytorch` 别名（`atk/bin/op_alias.py`）

- `DEFAULT_ALIAS_CONFIG`（`:31-34`）：`"pytorch": {"backend": "npu", "compare_backend": "cpu"}`、
  `"aclnn": {"backend": "pyaclnn", "compare_backend": "cpu"}`。待测后端字面量是 `pyaclnn`，不是 `aclnn`。
- `NodeType` 只有三个成员：`npu`、`cpu`、`pyaclnn`（`atk/configs/nodetype_config.py:20-23`）。
- 位置参数 `case_file` 为 `click.Path(exists=True)`（`:217`）。别名自身声明的开关：
  - 设备组：`--devices`、`--compare-backend`（默认 `"cpu"`）、`--compare-devices`、`--compare-host`、
    `--compare-port`（`:44-57`）；
  - 任务组：`--task`（`:60-64`）；
  - 筛选组：`-s`/`--start`、`-e`/`--end`、`--white_list`、`--black_list`、`--random`（`:67-78`）；
  - 执行组：`--timeout`、`--concurrency`、`-l`/`--log`、`-p`/`--plugin`（`:81-92`）；
  - 输出组：`-o`/`--output`、`--input-data`（`:95-101`）。
  除 `--compare-backend` 默认 `"cpu"` 外，上列所有开关默认值均为 `None`。
- `ALIAS_CONTEXT_SETTINGS = {"ignore_unknown_options": True, "allow_extra_args": True}`（`:35`，用于
  `:216`）。别名未声明的开关经 `ctx.args`（`:225`）原样追加到 task 参数列表末尾（`:207`），即透传。
- 别名开关到 task 开关的名字映射（`:104-122`）：`task`→`-tk`、`start`→`-s`、`end`→`-e`、
  `white_list`→`-wl`、`black_list`→`-bl`、`random`→`-rn`、`timeout`→`-to`、`concurrency`→`-mt`、
  `log`→`-l`、`input_data`→`--input_data`。值为 `None` 的项不传（`:118-120`）。
- 既没有 `--task` 也没有 `-tk`/`--task` 出现在透传参数里时，追加 `--task accuracy`（`:200-201`）。
- `-p/--plugin` 未给时，取 case 文件同目录 `sorted(case_dir.glob("function_*.py"))`，且仅当结果恰好
  一个才采用（`:158-163`、`:177-179`）。
- `--white_list` / `--black_list` 在别名层归一化（`:125-141`、`:170-171`）：值以 `[` 开头则原样透传；
  否则按逗号拆成 `int` 再 `json.dumps(items, separators=(",", ":"))`。
- task 层 `-wl` / `-bl` 的值经 `json.loads` 解析（`atk/tasks/main.py:397`、`:405`）；元素可为 int 或
  二元 list，二元 list 的语义是**左闭右开**——`[1,10]` 表示 1..9（`atk/tasks/main.py:76-87`）。

### 1.4 task 层取值

- `TaskType` 恰好六个成员（`atk/configs/base_config.py:50-56`）：`accuracy`、`performance_e2e`、
  `performance_device`、`accuracy_load`、`accuracy_dc`、`run`。
- `-mt` / `--max_task` 默认 `100`（`atk/bin/task.py:212-220`）；取值小于 1 时记一条 warning 并回落 100
  （`atk/tasks/main.py:390-393`）。
- `-to` / `--timeout` 默认 `None`；未给时取 `TASK_TIME_OUT = 650`（`atk/configs/base_config.py:23`、
  `atk/tasks/main.py:240`）。
- `--save_data` 是 `multiple=True` 选项，格式 `<item>[:<format>]`（`atk/bin/task.py:138-148`）。
  item ∈ `SAVE_DATA_LIST = ["profile", "input", "input_final", "output", "db"]`
  （`atk/configs/base_config.py:27`）；format ∈ `VALID_EXPORT_FORMATS = ['bin', 'txt']`
  （`atk/bin/task.py:33`），冒号缺省时为 `'bin'`（`:85`）。item 或 format 非法抛
  `click.BadParameter`（`:87-90`）。同一 item 多次传入会累加 format 列表（`:93-98`）。
- `-cp` / `--cpp_func_signature_type_path` 是 task 层 `is_flag` 选项，默认 `False`
  （`atk/bin/task.py:301-309`）。

### 1.5 路径检查门（`atk/common/file_check.py`）

- **受检对象**：`atk/bin/base.py:216-223` 共六处——`case`、`nodes`、`plugin_path`、`input_data`，以及
  每个 node 的 `bm_file` 与 `output_path`；用例生成侧另有 `atk/case_generator/utils/reports.py:359-361`
  的 `case_file` 与 `plugin_path` 两处。别名 `-o/--output` 落到 node 的 `output_path`
  （`atk/bin/op_alias.py:181-188` 的 `:187`）。
- **执行顺序**：`self.file_check()` 在 `self.init()` 之前（`atk/bin/base.py:183-185`），而 `-o` 目录由
  `init()` 里的 `OutputManager` 创建（`atk/common/output_manager.py:39-40`）。
- `FileCheck.check`（`:54-69`）：入参为 None 或空串直接返回；是目录则 `os.walk` 递归对每个文件调
  `_check_file`；否则直接 `_check_file`。空目录不产生任何逐文件检查。
- `_check_file`（`:166-172`）= `check_path_is_exist_and_valid` + `os.path.isfile` 断言 + `check_file_owner`。
- `check_path_is_exist_and_valid`（`:98-109`）：非 `str` 报错；`len(path) > MAX_PATH_LENGTH` 报错
  （`MAX_PATH_LENGTH = 1024`，`:42`）；`not os.path.exists(path)` 抛
  `FileCheckError(f"path '{path}' is not exists")`（`:106-107`）。
- `check_input_path_valid`（`:79-96`）：`if ".." in path` 是**整串子串判断**，不是路径分量判断
  （`:87-88`）；`Path(path).resolve() != Path(path).absolute()` 即拒（`:90-91`）。
- `check_file_owner`（`:132-156`）：**文件本身与其 `os.path.dirname(os.path.abspath(file_path))`
  的 `st_uid` 都必须等于 `os.getuid()`**，否则 `FileCheckError`。
- `check_blacklist` 形参默认 `False`（`:80`），仓内无任何调用方传 `True`，故 `BLACKLIST_PATH`
  （`:44-52`）实际不生效。
- 「必须绝对路径」只出现在 `dir_check` 的 `file_path.startswith("/")`（`:112-122`），该方法在全仓
  `*.py` 中无调用方。
- ATK 源码没有任何 ASCII 或字符集限制：全仓 `*.py` 中 `ascii` 只命中两处无关内容
  （`atk/tasks/backends/pyaclnn_backend.py:295` 的注释、`atk/tasks/report/csv_report.py:108` 的
  `ensure_ascii=False`）。
- `FileCheckError` 从 `Task.__init__` 抛出（`atk/bin/base.py:183`），不在 `run_task` 的
  `except BaseException` 覆盖范围内（`:236-253`）；`atk/bin/task.py` 末尾的 `cli` 体是
  `Task(ctx, **kwargs).run()`，未捕获；`atk/bin/op_alias.py:209-212` 也未捕获。

---

## 二、输入侧

### 2.1 设计文件中副本未覆盖的字段（`atk/configs/design_config.py`）

- `dtypes`、`ranges.valid`、`ranges.invalid`、`shapes.dim_numbers`、`shapes.dim_values`、
  `tuple_numbers` 都是同一个 `RandomConfig`。`RandomConfig` 有且只有六个键（`:141-148`）：
  `type`（`Literal["choices"]`，默认 `"choices"`）、`values`、`weights`、`invalid_values`、
  `random_types`、`custom_dist_ratio`；模型为 `extra='forbid'`（`:142`）。
  `weights` 非空时长度必须等于 `values`，否则 `ValueError`（`:150-153`）。
- `random_types` 的元素是 `RandomTypesConfig`（`:119-123`）：`name` ∈ `{"default", "nd"}`
  （`RandomTypes`，`:114-116`），`mean` 默认 `[-100, 100]`，`std` 默认 `[1, 25]`。
- `RangeConfig`（`:288-292`）除 `valid`/`invalid` 外还有 `valid_weights`，默认
  `VALID_WEIGHTS = 0.95`（`:109`）。
- `InputDesignConfig` 三个副本未出现的字段：`aclnn_name`（`:322`）、`outlier_values`（`:327`，非空时
  必须恰好两个值，否则 `ValueError`，`:370-375`）、`expected_error_msg`（`:333`）。
- **per-input 的 `expected_error_msg` 不进入生成的用例**：`_create_case_config` 只从顶层
  `DesignConfig.expected_error_msg` 取值（`atk/case_generator/generator/base_generator.py:206-218`
  的 `:213`），且 `InputCaseConfig` 没有该字段（`atk/configs/case_config.py:42-51`）。全部读取方读的
  都是 case 级字段（`atk/tasks/opp_tasks.py:106`、`:204`；`atk/tasks/executors/compare_excutor.py:52`）。
- `CaseGenerator` 有两个可覆写钩子，副本只提到后者：`after_input_config(index, input_case)`
  （`atk/case_generator/generator/base_generator.py:60-66`）与 `after_case_config(case_config)`
  （`:68-70`）。

### 2.2 精度比较器（`atk/tasks/post_process/`）

- `ACCURACY_REGISTRY` 中被注册的名字只有四个：
  - `single_bm` 与 `default`——**同一个类挂两个装饰器**（`single_benchmark_compare.py:36-37`）；
  - `mixed_tolerance_bm`（`mixed_tolerance_benchmark_compare.py:34`）；
  - `equal`（`equal_compare.py:24`）。
- `equal` 的判定顺序（`equal_compare.py:26-34`）：两侧张量**全为 nan** 时直接
  `AccuracyConfig(result=True, error_info="torch.equal pass")` 返回；否则用 `torch.equal` 逐位比较。
- `StandardConfig.acc` 默认值是字符串 `"default"`，类型 `Optional[Union[str, dict]]`
  （`atk/configs/design_config.py:380`）。
- dict 形态时，比较器名取 `list(acc.keys())[0]`，该键的 value 原样作 kwargs 展开进比较器构造
  （`atk/tasks/executors/compare_excutor.py:173-177`），因此 dict 顶层只能有一个键。
  str 形态走另一分支并额外传 `need_md5`（`:166-172`）。
- **`random_bm` 在两处被特判但从未注册**：`compare_excutor.py:180`（`acc != "random_bm"` 决定
  `need_md5`）与 `atk/configs/results_config.py:313-319`（按 KS 检验参数 `0.01` / `-3.0902` 计算
  `acc_pass_ratio`）；全仓无 `register("random_bm")`。`Registry.__getitem__` 是裸 dict 取值
  （`atk/common/registry.py:54-55`），取不到即 `KeyError`。
- 每条 case 的输入数据以 `case_config.id` 为随机种子：`OpsDataset.__init__(params, seed)` 调
  `seed_everything(seed)`（`atk/tasks/dataset/base_dataset.py:30-34`），后者同时 seed `random`、
  `os.environ["PYTHONHASHSEED"]`、`np.random`、`torch`（`:75-82`）；调用点传入的正是
  `case_config.id`（`atk/tasks/executors/dataset_executor.py:127-130`）。

### 2.3 插件装载器（`atk/common/registry.py:111-224`）

- `register_from_path`（`:111-124`）：目录走 `register_all_from_dir`，文件必须以 `.py` 结尾，
  两者都不满足抛 `ValueError`。目录模式 `os.walk` 递归加载子目录下**所有** `.py`（`:211-224`）。
- 模块 import 失败或语法错误只记一条 `Failed to load module from {file_path}: {e}` 然后 `return`
  （`:147-151`），ATK 继续执行。
- 三种 mode 的基类路径与装饰器正则（`:154-170`）：
  - `"Generate"`：`['module.CaseGenerator']`，正则 `@GENERATOR_REGISTRY\.register\(["\'](.+?)["\']\)`；
  - `"Api"`：`['module.BaseApi', 'module.AclnnBaseApi']`，正则 `@register\(["\'](.+?)["\']\)`；
  - `"Data"`：`['module.BaseDataType']`。
- 基类以 `eval(path, {"__builtins__": None, "module": module})` 在**插件模块自身命名空间**求值
  （`:177-180`）。任一名字在该命名空间缺失即抛 `AttributeError`，被 `except (AttributeError, NameError)`
  捕获后只记一条 warning 并 `return`（`:181-183`）——该文件中一个类都不会注册。因此 `"Api"` 模式要求
  插件模块命名空间里 `BaseApi` 与 `AclnnBaseApi` **两个名字同时存在**。已在本机以等价最小脚本复现该
  `tuple(eval(...))` 语义。
- 注册名不是装饰器运行时产物：先 `inspect.getsource(obj)` 取类源码，再用上述正则从**文本**里抠出
  （`:193-197`）；匹配不到则退回类名（`:198-200`）。
- 调用入口：task 侧 `-p` 同时触发 `mode="Api"`（`atk/tasks/api_execute/__init__.py:39-41`）与
  `mode="Data"`（`atk/case_generator/generator/data_types/__init__.py:49-50`），两者由
  `atk/tasks/main.py:262-265` 依次调用；`atk case` 的 `-p` 触发 `mode="Generate"`
  （`atk/case_generator/generator/generate_types/__init__.py:40-42`）。
- 取不到执行器时 `ApiExecuteFactory.get_executor` 抛 `KeyError`，消息为
  「自定义API{e}找不到或者import失败…」（`atk/tasks/api_execute/__init__.py:29-37`）；生成器侧对应
  `GeneratorFactory.create_customer_generate` 的英文消息（`generate_types/__init__.py:30-38`）。
- 基类签名：`BaseApi.__init__(self, task_result)`（`atk/tasks/api_execute/base_api.py:24-25`）；
  `class AclnnBaseApi(BaseApi)` 的 `__init__(self, task_result, backend)`
  （`atk/tasks/api_execute/aclnn_base_api.py:26-29`）。pyaclnn 后端以两个位置参数实例化执行器
  （`atk/tasks/backends/pyaclnn_backend.py:154`）。

---

## 三、输出侧与报告

### 3.1 执行侧目录布局

- 任务目录名 = `<case 文件名去后缀>_<时间戳>`：head_name 取自 `-c/--case`（或 `-fc/--fast_case`）的
  basename 去后缀（`atk/bin/base.py:188-194`、`:203-204`）；时间戳为 UTC+8、格式
  `%Y-%m-%d-%H-%M-%S-%f`（`atk/common/output_manager.py:88-97`）。
- 任务目录 = `<-o 取值，缺省进程 cwd>/atk_output/<任务目录名>`
  （`ATK_OUTPUT = "atk_output"`，`atk/common/output_manager.py:26`；`:39`；`:161-163`）。
- 其下子目录：`log`（`atk/common/output_manager.py:43`）、`report`（`atk/tasks/report/csv_report.py:298`）、
  `input`/`input_final`/`output`/`profile`（`atk/configs/nodes_config.py:43-60`）、
  `db`（`atk/tasks/celery_config.py:26`）。
- ATK 自身日志固定为 `<任务目录>/log/atk.log`（`DEFAULT_LOG_FILE = "atk.log"`，
  `atk/common/log.py:25`；`atk/common/output_manager.py:43-44`；`atk/bin/base.py:208-212`）。
  `log_init` 先 `logger.handlers.clear()`，再挂 `RotatingFileHandler`
  （`MAX_LOG_BYTES = 100 * 1024 * 1024`、`DEFAULT_BACKUP_COUNT = 30`），并 `coloredlogs.install`
  到控制台（`atk/common/log.py:121-140`、`:32-33`）。日志格式为
  `'[%(asctime)s] [%(levelname)s] [%(processName)s] [%(process)d] [%(filename)s:%(lineno)d]  %(message)s'`
  （`:29-31`，`lineno]` 与 `%(message)s` 之间是两个空格）。worker 是 `multiprocessing.Process` 子类
  且其 `run()` 不另行调用 `log_init`（`atk/tasks/task_creator/worker_manager.py:47`、`:61-74`）。
- per-case 数据目录统一由 `get_output_path_by_case` 拼出：
  `os.path.join(base_dir, backend, config.save_name, str(config.id))`（`atk/common/utils.py:105-111`）。
  其中 `backend` 是 `Node.get_backend_name()` = `f"{self.backend}_{self.name}"`
  （`atk/configs/nodes_config.py:62-63`），`atk aclnn` 下两个节点即 `pyaclnn_0` 与 `cpu_0`
  （`atk/bin/op_alias.py:33` + `atk/configs/nodes_config.py:100-117` 的自动命名）；
  `save_name` = case 文件名去后缀（`atk/tasks/main.py:476`、`atk/tasks/result_process.py:56-57`）。
- 因此输出根为 `<任务目录>/output/<backend>_<name>/<save_name>/<case id>/`，
  profile 根为 `<任务目录>/profile/<backend>_<name>/<save_name>/<case id>/`
  （`atk/tasks/backends/backend.py:238-239` → `atk/configs/results_config.py:476-480` →
  `atk/common/utils.py:105-111`）。
- 另有一种 `"{}_benchmark"` 目录名模式（`BENCHMARK_PATH_PATTERN`，`atk/configs/nodes_config.py:25`），
  只在 bm_file 比对路径出现（`atk/tasks/executors/compare_excutor.py:149-161`）。
- 用例生成侧的产物路径相对**进程 cwd**：`result/<yaml 文件名去后缀>/{json,csv,excel}`
  （`atk/case_generator/utils/reports.py:364`、`:381`、`:411`）；csv 以 `encoding="utf-8-sig"` 写出
  （`:418`），excel 文件名附 `time.strftime("%Y%m%d%H%M%S")`（`:311`、`:384`），json 汇总文件名
  `all_<yaml 文件名去后缀>.json`（`:371`），逐 case json 仅在 `every_case=True` 时生成（`:365-370`），
  而 `atk/bin/case.py:114` 调用 `report.save_json()` 不传该参数。

### 3.2 输出张量文件

- 命名规则（`atk/tasks/executors/opp_executor.py:154-190`）：`torch.Tensor` 走 `torch.save`，文件名
  `output_<序号>.pt`；`np.ndarray` 走 `tofile`，文件名 `output_<序号>.bin`；反向时前缀整体变为
  `output_grad_<序号>`（`:162-166`、`:178-182`）。序号 `output_index` 从 0 起逐个自增
  （`:174`、`:189`）。同目录另写 `output_info.json`（`:228-234`）。
- 比对侧 `compare_for_data` 只处理后缀 ∈ `{'.pt', '.bin'}` 的文件（
  `atk/tasks/post_process/base_compare.py:103-105`）；文件个数核对只 glob `*.pt`（`:136-145`）。
- `--save_data` 的 format 段不影响落盘后缀：`TaskResult.save_data` 只保留 item 键
  （`atk/tasks/task_creator/base_task.py:109-117` 的 `:116` `list(export_cfg.keys())`），
  各消费方一律做成员判断（`atk/tasks/executors/compare_excutor.py:201`、`:228`；
  `atk/tasks/executors/dataset_executor.py:190`；`atk/tasks/backends/npu_backend.py:204-205`）。
  完整 format 字典仅由 `export_config` 另行携带（`base_task.py:117`），读取方是
  `atk/configs/dataset_config.py:192-193` 与 `atk/tasks/executors/opp_executor.py:70`。

### 3.3 工作簿与列名

- 报告目录 `<任务目录>/report`（`atk/tasks/report/csv_report.py:298`）。中间文件
  `<case 名>_statistic_<时间戳>.csv`（`:375-377`）、最终 `<case 名>_reports_<时间戳>.xlsx`
  （`:378-380`），时间戳格式 `%Y-%m-%d-%H-%M-%S`（`:297`）。保存后记
  `save result excel file: <path>`（`:517`、`:520`）。
- 工作簿共四个 sheet，标题字面量：`statistic`（`:474`）、`summary`（`:481`）、
  `failed cases`（`:486`）、`accuracy false cases`（`:492`）。`statistic` 只在中间 csv 存在时创建
  （`:471-477`），创建后 csv 被 `os.remove`（`:477`）。
- `statistic` 每行一条 case，行数据来自 `self.all_cases[csv_report.id]`
  （`:388-391`）。`add_and_write_case` 与 `add_report` 外层都是 `except Exception`，出错时只记
  `add task report failed` 并继续（`:382-394`、`:396-407`），该 case 不进入 `all_cases`。
  `all_cases` 为空时 `save_csv` 记 `All cases is running failed.` 并早退（`:409-412`）。
- 列名构成（`atk/tasks/report/report_title/base_report_title.py:172-189`）：`INSTANCE = True` 的列
  直接用其 `ZH`；其余列为 `f"{node.get_backend_name()}_{ZH}"`，且对每个 `compare_nodes` 各出一列。
  `compare_nodes` = 所有 `is_compare` 为真的节点（`atk/configs/nodes_config.py:257-261`，`Node.is_compare`
  默认 `True`，`:38`）。
- `ZH` 字面量（`atk/tasks/report/report_title/result_report_title.py`）：
  - `编号`（`:150`，INSTANCE）、`运行结果`（`:419`，INSTANCE）、`失败原因`（`:430`，INSTANCE）、
    `用例json信息`（`:441`，INSTANCE）、`精度详情`（`:393`，INSTANCE）；
  - `精度通过`（`:373`，按节点）、`Device性能（us）`（`:235`，按节点，**全角括号**）、
    `端到端性能(us)`（`:205`，按节点，**半角括号**）、`同步端到端99th（us）`（`:225`，全角括号）、
    `Device性能比`（`:270`）、`端到端性能比`（`:245`）、`同步端到端标准差`（`:215`）。
- `运行结果` 取 `RunStatus` 枚举的字面值（`atk/configs/results_config.py:243-252`），九个成员：
  `SUCCESS`、`FAILED`、`TIMEOUT`、`PROCESSING`、`CREATE_DATASET`、`SAVE_CSV`、`POST_PROCESS`、
  `SAVE_OUPUT_DATASET`（上游拼写如此，不是 `OUTPUT`）、`UNDO`；取值处为
  `self.report.task_result.run_status.value`（`result_report_title.py:422-423`）。
- `失败原因` 即 `task_result.failed_message`（`result_report_title.py:426-434`）。
- `<节点>_精度通过` 取 `True` / `False` / `None`，`None` 落成空单元格：值来自
  `accuracy_configs.get(node.get_backend_name())`，取不到即返回 `NONE_RET`
  （`atk/configs/report_config.py:143-154`）。而 `accuracy_configs` 只以**对比节点**的
  `get_backend_name()` 为键写入（`atk/tasks/executors/compare_excutor.py:131-142` 的 `:132`、`:142`），
  故主节点那一列始终取不到值。
- `summary` sheet 每行一个节点、第一列列名为 `名称`
  （`atk/tasks/report/report_title/summary_report_title.py:61-85` 的 `:66`、`:72`）。
  `总用例数` 是该表的**列名**（`:96-106` 的 `:100`），不是固定单元格；其
  `is_in_not_backends` 返回 `self.is_main_node(node)`（`:102-103`），即主节点不出现在 summary 行中。
  同表其它列名：`执行成功用例个数`（`:113`）、`确定性计算执行成功用例个数`（`:126`）、
  `执行失败用例个数`（`:139`）、`精度比对通过用例个数`（`:152`）、`错误信息匹配用例个数`（`:178`）、
  `精度比对通过率`（`:191`）、`e2e性能通过率`（`:217`）、`device性能通过率`（`:230`）、
  `平均e2e性能比`（`:243`）、`平均device性能比`（`:256`）。取不到值的单元格写成 `"-"`（`:77`）。

### 3.4 日志行字面量

- pyaclnn 后端在解析出 so 后打印的结论行（`atk/tasks/backends/pyaclnn_backend.py:233`）：

  ```python
  logging.info(f"import {self.op_name}GetWorkspaceSize  from {aclnn_func_path} success!")
  ```

  `GetWorkspaceSize` 与 `from` 之间是**两个空格**（已用 `od -c` 逐字节确认）。该行的路径即
  `get_opp_lib_path(f"{self.op_name}GetWorkspaceSize")` 的返回值（`:229`）。INFO 级，落在 `atk.log`。
- 容易与之混淆的另一对，出自 `check_interface_exists`，在逐个试探候选库时打印，同一轮可能出现多条：
  - `atk/tasks/backends/lib_interface/acl_wrapper.py:451`：`在 {so_path} 中找到接口：{func_name}`
    （全角冒号，INFO）；
  - 同文件 `:454`：`在 {so_path} 中未找到接口：{func_name}`（WARNING）；
  - 文件不存在时 `:438-439`：`{so_path}文件不存在`（INFO）。
- so 选择顺序（`acl_wrapper.py:461-513`），命中即返回：
  1. `ATK_CUSTOM_OPP_PATH`：取值直接当作 so 路径，只要 `os.path.exists` 成立就原样返回，**不校验里面
     有没有目标符号**（`:472-475`）；
  2. `ASCEND_CUSTOM_OPP_PATH`：拼 2×5=10 条
     `<值>/vendors/{customize|custom}{_math|_nn|_cv|_transformer|""}/op_api/lib/libcust_opapi.so`，
     逐条要求存在**且** `check_interface_exists` 命中（`:477-490`）；
  3. `ASCEND_OPP_PATH`：先 `<值>/vendors/customize/op_api/lib/libcust_opapi.so`（`:495-498`），
     再 3×5=15 条 `<值>/{"../aarch64-linux/"|"../x86_64-linux/"|""}lib64/{libopapi_math|libopapi_nn|
     libopapi_cv|libopapi_transformer|libopapi}.so`（`:499-510`），同样要求存在且符号命中；
  4. 全落空抛 `FileNotFoundError("Can't find aclnn function library path")`（`:512-513`）。
- ATK 用 `ctypes.CDLL(<绝对路径>)` 加载库（`acl_wrapper.py:443`、
  `atk/tasks/backends/lib_interface/lib_manager.py:114-121`）。`LD_LIBRARY_PATH` 在 ATK 全部 `*.py`
  中零命中，只出现在上游文档 `docs/ATK使用指南/04 相关说明/安全声明.md:71`。

---

## 四、CANN profiler 相关

### 4.1 两条不同的采集路径

- `NPUBackend.run_device_perf`（`atk/tasks/backends/npu_backend.py:184-195`）用
  `torch_npu.profiler` + `profiler.tensorboard_trace_handler`（`:131-151`）采集；解析前先
  `os.path.join(profiler_result_path, os.listdir(profiler_result_path)[0])` **多下一层目录**
  （`:192-194`），反向路径同（`:179-181`）。
- `PyAclnnBackend` 继承 `NPUBackend`（`atk/tasks/backends/pyaclnn_backend.py:125`）但**覆盖了**
  `run_device_perf`（`:452-486`）：
  - `acl.prof.init(profiler_result_path)`（`:462`）；
  - `acl.prof.create_config([self.device_id], 0, 0, ACL_PROF_ACL_API | ACL_PROF_TASK_TIME |
    ACL_PROF_AICORE_METRICS | ACL_PROF_TASK_MEMORY)`（`:463-466`；四个常量值 `0x0001`、`0x0002`、
    `0x0004`、`0x1000`，见 `:43-46`）；
  - `mem_freq = "15"` 后 `acl.prof.set_config(ACL_PROF_SYS_HARDWARE_MEM_FREQ, mem_freq)`
    （`:467-468`；常量值 `3`，见 `:47`）；
  - 跑 `get_per_times()` 次后 `stop` / `destroy_config` / `finalize`（`:471-480`）；
  - 再调 `export_profile_result`（`:482`），并把 `profiler_result_path` **原样**交给
    `parser_profile_result`（`:486`），不像基类那样多下一层。
- `export_profile_result`（`:159-175`）：命令字符串为
  `f"msprof --export=on --output={profiler_result_path}"`（`:161`），先 `logging.info(f"Profiling CMD:
  {cmd}")`（`:162`），再 `cmd.split()` 后 `subprocess.run(cmd_list, shell=False, stdout=PIPE,
  stderr=PIPE, timeout=600)`（`:165-171`）；返回码非 0 时记
  `export summary failed, return code is {rc}, please check` 并返回 `False`（`:172-174`）。

### 4.2 CSV 的发现方式与列名

- 两个类别名与文件名前缀写死在 `NPUBackend.__init__`（`atk/tasks/backends/npu_backend.py:44-45`）：
  `self.profiling_prefix = "op_statistic"`、`self.profiling_summary = "op_summary"`。
- 发现方式是两条 `glob.glob`（模式 `:231-238`，调用 `:239-240`）：
  `f"{profiler_result_path}/PROF*/mindstudio_profiler_output/{前缀}_*.csv"`，随后各取 `[0]`
  （`:244`、`:255`）。任一 glob 为空时记 `The op_summary is null` 并返回（`:241-243`）。
- 同一 profiler 目录下另有 `ASCEND_PROFILER_OUTPUT/memory_record.csv`；缺失时只记
  `The profiling memory_record is null` 的 warning（`:53-61`）。
- ATK 读取的列名共四个，且**强制程度不同**：
  - `op_statistic` 的 `Total Time(us)`（`:46` 赋给 `total_time_title`，`:253` 使用）走
    `float(line_dic.get(self.total_time_title))`——缺列时 `float(None)` 抛 `TypeError`；
  - `op_statistic` 的 `OP Type`（`:254`）走 `line_dic.get('OP Type')`——**缺列只得到 `None`，
    ATK 不报错**；
  - `op_summary` 的 `OP Type` 与 `Task Duration(us)` 走 `header.index(...)`
    （`:90-91`）——缺列直接 `ValueError`。
- 除这四个列名外，ATK 不读也不校验其它列：表头只由 `next(readers)` 取一次，随后按位置或按名取值
  （`:244-254`、`:87-96`）。完整表头由 CANN 侧 `msprof` 决定，ATK 源码中查不到。
- 各列的**语义**在 ATK 源码中没有任何定义或注释，ATK 只按列名取值。
- `parser_performance` 逐行构造 `per_list` 并作为第三个返回值（`:226`、`:252`、`:264`），但
  `parser_profile_result` 接收后未再使用（`:197-198` 的 `time_all_info`），逐行原始数据只存在于 CSV 中。

---

## 附：本文件核实不到的相邻内容

以下条目在 ATK 26.5.14 源码中查不到依据，未写入正文，列在此处以免被误当作已核实：

- 两类 CSV 的完整表头，以及各列的语义解释——属 CANN `msprof` 侧，ATK 只按列名取值。
- profiler 原始采集目录的体量——ATK 源码无任何体量事实。
- `ASCEND_RT_VISIBLE_DEVICES` 的取值语义——该变量名在 ATK 全仓零命中，属 CANN runtime 侧。
- `LD_LIBRARY_PATH` 对 ATK 加载行为的实际影响——ATK 源码从不读取该变量，其作用属动态加载器行为。
