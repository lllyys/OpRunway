# ATK 验收 Skill 开发原则详解

本文档是 CLAUDE.md 核心理念的实施细节，包含代码示例、反例、决策树。

**职责：** 指导修改 skill 的架构决策（开发态文档，不进入 skill 包）

**使用：** 按需引用，不自动加载

---

## §1 知识层前置构建详解

### 1.1 五大知识域覆盖清单

#### 1.1.1 目标产物规范

**必备文档：** `artifact-contracts.json`

**Agent 需要知道：**
- S2 必须产出什么（`<op>.yaml`, `constraint.py`, `evidence/adapter_binding.json`）
- 每个产物的字段 schema（如 `<op>.yaml` 的 `inputs` 是 `list[dict]`）
- 字段约束（如 `inputs[].name` 必须匹配基线参数名）

**检查方法：**
```bash
# 骨架是否登记了新产物？
jq '.artifacts | keys' skill/repo-task-atk-test/references/artifact-contracts.json

# 产物字段是否有 spec？
jq '.artifacts["<op>.yaml"].fields' skill/repo-task-atk-test/references/artifact-contracts.json
```

**不完备的后果：**
```
Agent 生成 constraint.py
  → S3 跑测失败（ATK 报 schema 错误）
  → Agent 读 ATK 源码理解 schema
  → 重新生成 constraint.py
  → S3 再次跑测（浪费 1 个完整跑测周期）
```

#### 1.1.2 ATK 框架知识

**必备文档：** `atk-cli.md`, `case-design.md`

**Agent 需要知道：**
- `atk case` 输出在 `output/<case_name>/`
- `atk node ... task` 输出在 `output/tasks/<task_id>/`
- `range_values: "null"` 表示空张量（需适配器处理）
- `group_config.tuple_numbers` 控制可变长复合组的长度

**检查方法：**
```bash
# ATK 命令参数是否都文档化？
grep -E "^(atk case|atk node)" skill/repo-task-atk-test/references/atk-cli.md
```

**不完备的后果：**
- Agent 找不到 ATK 报告路径 → 解析失败 → 返工

#### 1.1.3 约束器生成规则

**必备文档：** `atk-parameter-capabilities.json`

**Agent 需要知道：**
- `tuple[int]` 类型 → 必须声明 `group_types` → 用 `case.inputs[index][0].shape`
- `group_length` 参数 → runtime type 是 `list[InputCaseConfig]` → 用 `rebuild_group()`

**检查方法：**
```python
# 新参数类型是否在白皮书里？
import json
caps = json.load(open("skill/repo-task-atk-test/references/atk-parameter-capabilities.json"))
assert "new_param_type" in caps
```

**不完备的后果：**
- Agent 生成错误的约束器逻辑 → S3 跑测 TypeError → 返工

#### 1.1.4 跑测适配规则

**必备文档：** `plugin-authoring.md`

**Agent 需要知道：**
- 用例集中出现 `"null"` / `["null"]` / `"default"` → 需要适配器
- 适配器四类预警的触发条件（typed-null, varargs, overload, unsupported-feature）

**检查方法：**
```python
# 条件性预警是否都有文档？
doc = open("skill/repo-task-atk-test/references/plugin-authoring.md").read()
assert "typed-null" in doc
assert "varargs" in doc
```

**不完备的后果：**
- Agent 不知道何时需要适配器 → 该写没写 → S3 跑测类型错误 → 返工

#### 1.1.5 环境依赖链

**必备文档：** `probe_env.py` 的 docstring

**Agent 需要知道：**
- `source evidence/env.sh` 后 `$SKILL_ROOT` 可用
- `$DEVICE_LIST` 是可用设备的逗号分隔列表
- CANN 环境变量的设置顺序

**检查方法：**
```bash
# 环境变量是否都在 env.sh 里？
source evidence/env.sh
echo $SKILL_ROOT $DEVICE_LIST
```

**不完备的后果：**
- Agent 不知道环境变量如何设置 → S3 跑测环境错误 → 返工

### 1.2 量具可靠性保证机制

#### 1.2.1 输出确定性

**原则：** 相同输入 → 相同输出（幂等性）

**例外：** `group_length` 的 `random_choice_then_shuffled` 机制（已在 schema 标注）

**测试模板：**
```python
def test_make_yaml_is_idempotent(self):
    """make_yaml.py 相同输入必须产生相同输出。"""
    result1 = run_make_yaml("decl.json")
    result2 = run_make_yaml("decl.json")
    self.assertEqual(result1, result2)
```

#### 1.2.2 防漂移测试

**原则：** 模板不保留局部副本，必须导入共享模块

**测试模板：**
```python
def test_template_does_not_keep_its_own_copy_of_the_ladder(self):
    """模板不能保留 SIZE_NUMEL 的局部副本，必须导入 _shapes。"""
    template = (SKILL_ROOT / "assets/example/materialize.py").read_text()
    self.assertNotIn("SIZE_NUMEL", template)
    self.assertIn("from _shapes import", template)
```

#### 1.2.3 Fail-fast 实施

**原则：** 错误前置暴露

**实施检查清单：**
- [ ] 可表达性检查：S1 `make_must_cover.py` 调用 `_expressibility.check_contracts()`
- [ ] 基线绑定检查：S2 `make_yaml.py` 入口调用 `check_baseline_binding()`
- [ ] 环境检查：S0 `probe_env.py` 退出码非 0 即阻断

#### 1.2.4 故障隔离

**原则：** 脚本间通过产物 JSON 通信，不通过全局状态

**禁止：**
- ❌ 脚本间通过全局变量传递状态
- ❌ 脚本间通过文件锁协调执行顺序
- ❌ 脚本间通过环境变量传递业务数据

**允许：**
- ✅ 通过产物 JSON 传递数据（如 `decl.json` → `make_yaml.py`）
- ✅ 通过环境变量传递配置（如 `$SKILL_ROOT`）
- ✅ 通过退出码传递执行结果（0/2/3）

### 1.3 成本对比数据

| 场景 | 工具调用次数 | Token 消耗 | 说明 |
|---|---|---|---|
| 知识缺失（roll 验收） | 398 次 | 未统计 | Agent 反复读 ATK 源码、探索式返工 |
| 知识完备（Plan C） | ~68 次 / task | ~680k tokens / 9 tasks | 平均 <100k/task |

**倍数差异：** 5-10x

**结论：** 前置知识投入 1 小时 → 节省执行期 5-10 小时

---

## §2 产物契约骨架详解

### 2.1 骨架四问

`artifact-contracts.json` 每个产物必须回答：

#### 2.1.1 谁产出（owner）

```json
{
  "owner": "agent",  // Agent 自己判断的决策
  "owner": "script", // 量具脚本确定性生成
  "owner": "atk"     // ATK 框架产出
}
```

**决策树：**
- 需要读任务书才能确定 → `agent`
- 需要算法才能确定（如覆盖计算） → `script`
- ATK 跑测产出 → `atk`

#### 2.1.2 依据什么（spec）

```json
{
  "spec": "references/case-design.md#输入规范"
}
```

**检查：**
- [ ] spec 指向的文件存在
- [ ] spec 指向的章节存在
- [ ] spec 内容足够 agent 生成该产物

#### 2.1.3 谁消费（consumed_by）

```json
{
  "consumed_by": ["make_yaml.py", "S2终审"]
}
```

**用途：**
- 追踪产物依赖链
- 删除产物时检查是否有消费者
- 修改产物 schema 时通知所有消费者

#### 2.1.4 写错了怎么炸（failure）

```json
{
  "failure": "ATK 报 schema 错误，S3 跑测中断"
}
```

**用途：**
- 帮助 agent 理解为什么这条规范重要
- 设计门禁时知道在哪个阶段校验

### 2.2 派生视图机制

#### 2.2.1 作战卡渲染

**命令：**
```bash
python3 scripts/mark_step.py S2
```

**原理：**
- 从骨架提取 `owner: "agent"` 的产物
- 过滤出当前阶段的产物
- 渲染成决策清单卡片
- 插入 SKILL.md 的对应位置

**禁止手工编辑作战卡：** 它是派生视图，编辑会被覆盖

#### 2.2.2 决策点清单生成

**命令：**
```bash
python3 scripts/render_views.py --write
```

**原理：**
- 从骨架提取全局 `owner: "agent"` 的产物
- 生成决策点全集表格
- 写入 `decision-points.md`

**用途：** 完备性检查——agent 必须掌握的知识点是否都有文档

### 2.3 四条结构不变量

```python
# test_contracts.py

def test_skeleton_is_well_formed(self):
    """骨架自身合法（字段完整、枚举值合法）。"""
    data = load_contracts()
    for name, spec in data["artifacts"].items():
        self.assertIn("owner", spec)
        self.assertIn(spec["owner"], {"agent", "script", "atk"})

def test_references_are_complete(self):
    """引用完整（consumed_by 链不断）。"""
    data = load_contracts()
    for name, spec in data["artifacts"].items():
        for consumer in spec.get("consumed_by", []):
            if consumer.endswith(".py"):
                self.assertTrue((SCRIPTS / consumer).exists())

def test_decision_points_are_closed(self):
    """决策点封闭（owner: "agent" 的产物都有 spec）。"""
    data = load_contracts()
    for name, spec in data["artifacts"].items():
        if spec["owner"] == "agent":
            self.assertIsNotNone(spec.get("spec"))

def test_gauge_references_exist(self):
    """量具引用存在（骨架里的脚本名真实存在）。"""
    data = load_contracts()
    refs = script_refs(data)
    for artifact, field, script in refs:
        self.assertTrue((SCRIPTS / script).exists(),
                        f"{artifact}.{field} 引用的 {script} 不存在")
```

---

## §3 判据机械化详解

### 3.1 适配器判定

**从数据推导：**
```python
# check_adapter_binding.py
NULL_TOKENS = {"null", "default"}

def nulled_parameters(cases):
    """扫描用例集，返回包含 null 的参数名集合。"""
    nulled = set()
    for case in cases:
        for inp in case.get("inputs", []):
            value = inp.get("range_values")
            # 三种 null 形式
            if value in NULL_TOKENS:
                nulled.add(inp["name"])
            elif isinstance(value, list) and len(value) == 1 and value[0] in NULL_TOKENS:
                nulled.add(inp["name"])
    return nulled
```

**禁止 agent 声明：**
```json
// ❌ 不可信
{
  "alignment": {
    "requires_adapter": true,
    "reason": "我检查过了，input 参数会传 None"
  }
}
```

**正确做法：**
```python
# check_adapter_binding.py 从 case.json 扫描 range_values
nulled = nulled_parameters(cases)
if "input" in nulled:
    requires_adapter = True  # ✅ 从数据推导
```

### 3.2 基线绑定

**从函数签名反射：**
```python
# make_yaml.py
import inspect

def baseline_parameter_names(baseline_symbol):
    """反射基线函数的形参名列表。"""
    sig = inspect.signature(baseline_symbol)
    return list(sig.parameters.keys())

def check_baseline_binding(baseline_symbol, yaml_input_names):
    """检查 YAML 输入名是否匹配基线形参名。"""
    expected = set(baseline_parameter_names(baseline_symbol))
    actual = set(yaml_input_names)
    
    if actual != expected:
        missing = expected - actual
        extra = actual - expected
        return [
            (f"YAML 缺少输入: {missing}" if missing else None),
            (f"YAML 多余输入: {extra}" if extra else None),
            "运行期 **kwargs 绑定会 TypeError"
        ]
    return []
```

**为什么必须匹配：**
- PyTorch baseline 用 `**kwargs` 接收参数
- YAML 的 `inputs[].name` 变成 kwargs 的 key
- 不匹配 → `TypeError: unexpected keyword argument`

### 3.3 可表达性

**从 semantic 推导合法组合：**
```python
# _expressibility.py
TYPE_BY_SEMANTICS = {
    ("shape", "shape"): "int",
    ("dtype", "dtype"): "torch.dtype",
    ("other", "other"): None,
    # ("shape", "dtype") 不在白名单 → 不可表达
}

def check_contracts(dims):
    """检查轴组合是否可表达。"""
    problems = []
    for combo, axes in dims.items():
        semantics = tuple(axis_metadata[ax]["semantic"] for ax in axes)
        if semantics not in TYPE_BY_SEMANTICS:
            problems.append((
                combo,
                f"混合 semantic {semantics} ATK 无法表达"
            ))
    return problems
```

**为什么要检查：**
- ATK 的 combo 生成依赖 semantic 一致性
- 混合 semantic（如 shape + dtype）无法生成笛卡尔积
- 不检查 → S4 物化失败 → 浪费 S1→S2→S3

### 3.4 环境可用性

**从 npu-smi 输出解析：**
```python
# probe_env.py
def device_list():
    """解析 npu-smi 输出，返回健康设备列表。"""
    result = subprocess.run(
        ["npu-smi", "info"],
        capture_output=True,
        text=True,
        timeout=10
    )
    
    devices = []
    for line in result.stdout.splitlines():
        if "NPU" in line and "OK" in line:
            device_id = extract_device_id(line)
            devices.append(device_id)
    
    return devices
```

**为什么不能信任 agent 声明：**
- 设备状态动态变化（被其他任务占用、驱动异常）
- Agent 无法直接观测硬件状态
- 必须通过系统工具实时探测

---

## §4 Fail-fast 策略详解

### 4.1 可表达性检查前移

**原来（S4）：**
```
S1 decl.json (声明 shape+dtype 混合轴)
  ↓
S2 make_yaml.py (生成 YAML)
  ↓
S3 冻结
  ↓
S4 make_yaml.py 物化时报"ATK 无法表达 shape+dtype 混合"
  ↓ 浪费 S1→S2→S3 三阶段
回 S1 修正
```

**现在（S1）：**
```
S1 make_must_cover.py 调用 _expressibility.check_contracts()
  ↓ 立即报"shape+dtype 混合 ATK 无法表达"
修正 decl.json
  ↓ 0 浪费
继续 S2
```

**实施要点：**
- `make_must_cover.py` 必须导入 `_expressibility`
- 检查逻辑必须与 `make_yaml.py` 一致（共享同一模块）
- 退出码 2 表示可表达性错误

### 4.2 基线绑定检查前移

**原来（S3）：**
```
S2 YAML 输入名写成 x（应该是 input）
  ↓
S3 冻结
  ↓
S3 跑测 baseline 节点 TypeError: roll() got unexpected keyword 'x'
  ↓ 回 S2 重做
重新生成 YAML + 冻结
```

**现在（S2）：**
```
S2 make_yaml.py 入口调用 check_baseline_binding()
  ↓ 立即报"YAML 输入名 x 不匹配基线形参名 input"
修正 YAML
  ↓ S2 内修正，无需回退
继续 S3
```

**实施要点：**
- `make_yaml.py` 入口处检查，生成 YAML 之前
- 错误信息必须包含 expected vs actual
- 退出码 2 表示绑定错误

### 4.3 环境检查前移

**原来（S3）：**
```
S1 开始生成 decl.json
  ↓
S3 跑测时发现 CANN 环境变量未设置
  ↓ 回 S0 补环境
重新走 S1→S2→S3
```

**现在（S0）：**
```
S0 probe_env.py 检查 CANN 环境、设备健康
  ↓ 不满足即退出码非 0，阻断后续阶段
修正环境
  ↓ 重新 S0，通过后继续
S1 开始
```

**实施要点：**
- `probe_env.py` 必须是阶段流程的第一步
- 检查 CANN 路径、设备可用性、Python 解释器
- 退出码 3 表示环境问题

### 4.4 成本收益

| 检查 | 原位置 | 现位置 | 提前阶段数 | 单次返工成本 |
|---|---|---|---|---|
| 可表达性 | S4 | S1 | 3 | ~30 分钟（S1+S2+S3） |
| 基线绑定 | S3 | S2 | 1 | ~10 分钟（S2+S3） |
| 环境问题 | S3 | S0 | 3 | ~30 分钟（S1+S2+S3） |

**累计收益：** 平均节省 2 个阶段的返工成本

---

## §5 受控修改通道详解

### 5.1 唯一出口机制

`rewire_adapter.py` 是接线字段改写的**唯一出口**。

**白名单：**
```python
WIRING_KEYS = frozenset({"api_type", "aclnn_api_type"})
```

**为什么只有这两个字段：**
- `api_type`：CPU baseline 执行器选择
- `aclnn_api_type`：aclnn 执行器选择
- 其他字段（如 `inputs`）改了就是语义变化，必须回 S2

### 5.2 语义不变性校验

```python
def strip_wiring(case):
    """去除接线字段，返回语义部分。"""
    semantic = case.copy()
    for key in WIRING_KEYS:
        semantic.pop(key, None)
    return semantic

def diff_cases(old, new):
    """比对用例集，返回差异诊断。"""
    old_stripped = [strip_wiring(c) for c in old]
    new_stripped = [strip_wiring(c) for c in new]
    
    if old_stripped != new_stripped:
        return {
            "semantic_changed": True,
            "details": "除接线字段外还有其他变化"
        }
    
    return {"semantic_changed": False}
```

**为什么要 strip + deep-equal：**
- 不能简单比 hash（字段顺序可能变）
- 不能只比接线字段（可能同时改了其他字段）
- 必须剥离接线字段后逐字段 deep-equal

### 5.3 退出码语义

```python
# rewire_adapter.py
if semantic_changed:
    print("语义变化，必须回 S2 重做", file=sys.stderr)
    sys.exit(2)  # 语义错误

if input_error:
    print("输入错误：YAML 文件不存在", file=sys.stderr)
    sys.exit(3)  # 输入错误

# 成功
print("改写成功，语义不变")
sys.exit(0)
```

### 5.4 冻结纪律保留

改写后记录留痕：
```json
{
  "case_json_sha256": "abc123...",           // 新 SHA256
  "previous_case_json_sha256": "def456...",  // 改写前 SHA256
  "rewired_at": "2026-08-16T10:30:00Z",
  "rewired_fields": ["aclnn_api_type"]
}
```

**实质：** 冻结的是「输入没重算」，不是「一个字节都不能变」。

### 5.5 禁止事项

❌ Agent 自己发明改写逻辑
❌ 直接 patch YAML（绕过 `rewire_adapter.py`）
❌ 修改 SHA256 校验逻辑（绕过冻结门禁）
❌ 在 `WIRING_KEYS` 外增加可改字段（必须通过修改白名单）

---

## §6 知识分层详解

### 6.1 四层定义

**L0（事实层）：** 直接观测的事实
- ATK 源码：`atk/configs/case_config.py:90-91` 的 `DEFAULT_WIRING`
- 任务书：§2.3 的精度阈值 `loss_thr: 0.001`
- 真机现实：npu-smi 输出的设备状态

**L1（推导层）：** 从 L0 推导的事实
- `baseline_signature(torch.roll)` → `(input, shifts, dims=None)`
- npu-smi 输出 → 健康设备列表 `[0, 1, 2]`

**L2（策略层）：** 跨算子通用规则
- `SIZE_NUMEL = {"small": 2**10, "medium": 2**16, "large": 2**20}`
- `TYPE_BY_SEMANTICS = {("shape", "shape"): "int", ...}`

**L3（模板层）：** 引用 L0–L2 的实现
- `constraint.py` 模板引用 `atk-parameter-capabilities.json:group_length.rebuild_idiom`
- `materialize.py` 模板导入 `_shapes.shape_for()`

### 6.2 锁定机制

**L3 显式引用 L0–L2：**
```python
# constraint.py 模板（L3）
def rebuild_group(case, index, values, dtype):
    """重建可变长复合组。
    
    L0 引用：
    - atk-parameter-capabilities.json:group_length.runtime_type
    - atk-parameter-capabilities.json:group_length.rebuild_idiom
    
    这些引用锁定事实层，防止模板与事实漂移。
    """
    template = case.inputs[index][0]  # L0: case JSON schema
    return [template.model_copy(update={...}) for _ in values]
```

**防漂移测试：**
```python
# test_shapes.py
def test_template_does_not_keep_its_own_copy_of_the_ladder(self):
    """模板不能保留 SIZE_NUMEL 的局部副本。"""
    template = (SKILL_ROOT / "assets/example/materialize.py").read_text()
    self.assertNotIn("SIZE_NUMEL", template)
    self.assertIn("from _shapes import", template)
```

**收益：**
- L0 事实变更时，L3 引用失效 → 测试红灯
- 强制同步更新模板与事实
- 防止「事实已变但模板还用旧规则」的静默失败

### 6.3 通用化提取（L2 策略层）

**原则：** 算子无关逻辑提取到共享模块

**示例：`_shapes.py`**
```python
# L2 策略层：跨算子通用规则
SIZE_NUMEL = {"small": 2**10, "medium": 2**16, "large": 2**20}
MAX_DIM = 2**20

def shape_for(rank, size_class, index=0, numel=None, ragged=True):
    """size_class × rank → shape 的生成逻辑（所有算子通用）。"""
    target_numel = numel if numel else SIZE_NUMEL[size_class]
    
    # 分配到各轴
    per_axis = int(target_numel ** (1 / rank))
    shape = [per_axis] * rank
    
    # ragged: 让各轴长度不同（避免正方形张量）
    if ragged:
        shape[index % rank] = target_numel // (per_axis ** (rank - 1))
    
    # 单轴上限
    shape = [min(dim, MAX_DIM) for dim in shape]
    
    # 避免零轴
    shape = [max(dim, 1) for dim in shape]
    
    return shape
```

**L3 模板使用：**
```python
# materialize.py 模板（L3）
import sys
from pathlib import Path

ATK_SKILL_DIR = os.getenv("ATK_SKILL_DIR")
if not ATK_SKILL_DIR:
    raise RuntimeError("环境变量 ATK_SKILL_DIR 未设置")

sys.path.insert(0, str(Path(ATK_SKILL_DIR) / "scripts"))
from _shapes import shape_for, axis_for  # 导入 L2 策略
```

---

## §7 门禁职责边界详解

### 7.1 校验范围

**校验：**
1. **Agent 写的东西**
   - `decl.json`（轴与取值声明）
   - `constraint.py`（约束器逻辑）
   - `<op>.yaml`（ATK 输入格式）
   - `plugin.py`（适配器）

2. **真机现实**
   - ATK 报告（精度 loss、性能数据）
   - 基线失败数（冻结门禁核验）
   - 设备健康状态（npu-smi 输出）

**不校验：**
- 脚本确定性生成的产物（如从骨架派生的 `decision-points.md`）
- 脚本自己刚写的 JSON（如 `make_yaml.py` 生成 YAML 后不校验 YAML 语法）

### 7.2 删除决策树

```
门禁校验的是什么？
├─ 脚本刚生成的产物
│  └─ 删除门禁
│     理由：脚本输出确定性，无需校验
│
├─ Agent 写的 + 有价值检查
│  └─ 并入消费者脚本
│     理由：不新开门禁，就近合并
│
└─ Agent 写的 + 独立判据
   └─ 保留独立门禁
      理由：职责清晰，便于维护
```

### 7.3 实际案例（Plan A）

**删除的门禁：**
- `check_function_signature.py`：校验脚本刚生成的 `function_<op>.py` → 删掉
- `check_yaml_schema.py`：校验 `make_yaml.py` 刚生成的 YAML 语法 → 删掉

**保留的门禁：**
- `check_adapter_binding.py`：校验 agent 写的适配器是否满足用例集需求 → 保留
- `check_baseline_binding.py`：校验 agent 写的 YAML 输入名是否匹配基线参数名 → 保留

---

## §8 开发流程检查清单

### 8.1 新增产物

- [ ] 在 `artifact-contracts.json` 登记产物
- [ ] 填写 owner/spec/consumed_by/failure 四个字段
- [ ] 在 `references/` 补充规范文档（spec 指向的文件）
- [ ] 运行 `render_views.py --write --cards` 更新派生视图
- [ ] 运行 `test_contracts.py` 确认四条不变量通过

### 8.2 新增判据

- [ ] 判据从数据推导，不依赖 agent 声明
- [ ] 写代码示例到 `references/<spec>.md`
- [ ] 实现量具（门禁脚本或内部模块）
- [ ] 写 TDD 测试（先写失败测试）
- [ ] 确认退出码语义明确（0/2/3）

### 8.3 新增知识域

- [ ] 在 `atk-parameter-capabilities.json` 登记（如 Task 4 的 `group_length`）
- [ ] 补充 L0 事实（ATK 源码引用 + 行号）
- [ ] 补充 L2 策略（如共享模块 `_shapes.py`）
- [ ] 更新 L3 模板（显式引用 L0-L2）
- [ ] 写防漂移测试

### 8.4 修改量具

- [ ] 更新量具的 docstring
- [ ] 更新 `references/<spec>.md`
- [ ] 运行回归测试
- [ ] 检查 `artifact-contracts.json` 的 consumed_by 链

### 8.5 架构决策

**遇到问题时：**
1. 先读本文档（`docs/development/skill-development-principles.md`）
2. 再读 `skill/repo-task-atk-test/references/`（运行时知识）
3. 最后读源码（量具实现）

**不确定时：**
- 判据能从数据推导吗？ → 能 → 机械化；不能 → 补充前置知识
- 这是开发态还是运行态知识？ → 开发态 → 放 `docs/`；运行态 → 放 `skill/references/`
- 门禁该删还是留？ → 校验脚本生成的 → 删；校验 agent 写的 → 留

---

**最后更新：** 2026-08-16（Plan C 完成后梳理）
