"""c_api 双节点执行器样例。

本样例按真机验证代码整理，证据报告是 aclblas-spike-report.md 和
aclblas-formal-chain-report.md。它只读取调用序列表，不导入生成侧脚本。

运行时加载日志必须保持这一行合同：
[c_api_executor] loaded_library=<absolute realpath> exported_name=<resolved name>

注册名不要含 ``tensor`` 或 ``method``。ATK 会把这两个子串当成额外输入组开关，
没有对应输入文件时会在装载阶段失败。
"""

import ast
import ctypes
import json
import os
from pathlib import Path

import torch

from atk.configs.dataset_config import InputDataset
from atk.tasks.api_execute import register
from atk.tasks.api_execute.base_api import BaseApi


_ARG_CLASSES = frozenset({
    "context", "enum", "dim", "layout_param", "host_scalar",
    "device_ptr", "device_ptr_array",
})
_STRUCT_CTYPES = {
    "c_void_p": ctypes.c_void_p,
    "c_size_t": ctypes.c_size_t,
    "c_bool": ctypes.c_bool,
    "c_int32": ctypes.c_int32,
    "c_int64": ctypes.c_int64,
    "c_float": ctypes.c_float,
    "c_double": ctypes.c_double,
}
_VALUE_CTYPES = {
    "int8": ctypes.c_int8,
    "uint8": ctypes.c_uint8,
    "int16": ctypes.c_int16,
    "uint16": ctypes.c_uint16,
    "int32": ctypes.c_int32,
    "uint32": ctypes.c_uint32,
    "int64": ctypes.c_int64,
    "uint64": ctypes.c_uint64,
    "long": ctypes.c_long,
    "ulong": ctypes.c_ulong,
    "size_t": ctypes.c_size_t,
    "ssize_t": ctypes.c_ssize_t,
    "ptrdiff_t": ctypes.c_ssize_t,
}
_POINTEE_CTYPES = {
    "bool": ctypes.c_bool,
    "int8": ctypes.c_int8,
    "uint8": ctypes.c_uint8,
    "int16": ctypes.c_int16,
    "uint16": ctypes.c_uint16,
    "int32": ctypes.c_int32,
    "uint32": ctypes.c_uint32,
    "int64": ctypes.c_int64,
    "uint64": ctypes.c_uint64,
    "size_t": ctypes.c_size_t,
    "float32": ctypes.c_float,
    "float64": ctypes.c_double,
}


def _require(condition, message):
    if not condition:
        raise RuntimeError(message)


def _load_table(path):
    table = json.loads(path.read_text(encoding="utf-8"))
    _require(table.get("schema_version") == 1,
             "调用序列表 schema_version 必须等于 1")
    for key in ("symbol", "sequence", "output", "layout"):
        _require(key in table, f"调用序列表缺少 {key}")
    _require(isinstance(table["symbol"], str) and table["symbol"],
             "调用序列表 symbol 必须是非空字符串")
    exported = table.get("exported_name")
    _require(exported is None or isinstance(exported, str) and exported,
             "调用序列表 exported_name 必须是 null 或非空字符串")
    _require(isinstance(table["sequence"], list),
             "调用序列表 sequence 必须是列表")
    _require([item.get("step") for item in table["sequence"]
              if isinstance(item, dict)] == ["context", "execute"],
             "sequence 必须按 context、execute 排列")

    steps = {}
    for item in table["sequence"]:
        _require(isinstance(item, dict), "sequence 的每一步必须是对象")
        step = item.get("step")
        _require(step in {"context", "execute"},
                 f"sequence 含未开放步骤 {step!r}")
        _require(step not in steps, f"sequence 重复声明 {step}")
        steps[step] = item
    _require(set(steps) == {"context", "execute"},
             "sequence 必须各含一个 context 和 execute 步骤")

    context = steps["context"]
    _require(isinstance(context.get("type"), str) and context["type"],
             "context.type 必须是非空字符串")
    _require(context.get("shape") in {"struct_handle", "opaque_functions"},
             "context.shape 只接受 struct_handle 或 opaque_functions")
    if context["shape"] == "struct_handle":
        struct = context.get("struct")
        _require(isinstance(struct, dict) and struct.get("name"),
                 "struct_handle 缺少 struct.name")
        fields = struct.get("fields")
        _require(isinstance(fields, list) and fields,
                 "struct_handle 缺少 struct.fields")
        for field in fields:
            _require(isinstance(field, dict) and field.get("name"),
                     "struct 字段缺少 name")
            _require(field.get("ctype") in _STRUCT_CTYPES,
                     f"struct 字段使用未开放 ctype：{field.get('ctype')!r}")
    else:
        _require(context.get("create") and context.get("destroy"),
                 "opaque_functions 缺少 create 或 destroy")
        _require(context.get("set_stream") is None
                 or isinstance(context.get("set_stream"), str),
                 "opaque_functions.set_stream 必须是 null 或函数名")

    execute = steps["execute"]
    _require(isinstance(execute.get("call"), str) and execute["call"],
             "execute.call 必须是非空字符串")
    _require(execute.get("timed") is True, "execute.timed 必须是 true")
    _require(isinstance(execute.get("args"), list),
             "execute.args 必须是列表")
    for arg in execute["args"]:
        _require(isinstance(arg, dict), "execute.args 的每项必须是对象")
        _require(set(("position", "name", "c_type", "class", "ctype", "rule"))
                 <= set(arg), f"参数缺少必填键：{arg!r}")
        _require(arg.get("name") and arg.get("class") in _ARG_CLASSES,
                 f"参数缺少 name 或使用未开放 class：{arg!r}")
    _require(isinstance(execute.get("status_ok"), int),
             "execute.status_ok 必须是整数")

    layout = table["layout"]
    _require(isinstance(layout, dict), "layout 必须是对象")
    _require(isinstance(layout.get("order"), str) and layout["order"],
             "layout.order 仍为 null；layout 是 S1 constraint-table item，先确认内存布局")
    output = table["output"]
    _require(isinstance(output, dict) and output.get("in_place"),
             "output.in_place 必须是非空参数名")
    return table, steps


def _input_values(input_data, arg_specs):
    values = dict(input_data.kwargs or {})
    positional = iter(input_data.args or ())
    for spec in arg_specs:
        if spec["class"] == "context" or spec["name"] in values:
            continue
        try:
            values[spec["name"]] = next(positional)
        except StopIteration as exc:
            raise RuntimeError(f"用例缺少参数 {spec['name']}") from exc
    return values


def _shape_expression(node, values):
    if isinstance(node, ast.Name):
        _require(node.id in values, f"derive 使用未知输入 {node.id}")
        return values[node.id]
    if isinstance(node, ast.Attribute) and node.attr == "shape":
        value = _shape_expression(node.value, values)
        _require(hasattr(value, "shape"), "derive 的 .shape 对象不是张量")
        return tuple(value.shape)
    if isinstance(node, ast.Subscript):
        value = _shape_expression(node.value, values)
        index = node.slice
        _require(isinstance(index, ast.Constant)
                 and isinstance(index.value, int),
                 "derive 的 shape 下标必须是整数字面值")
        return value[index.value]
    raise RuntimeError("derive 只接受 <输入>.shape[<整数>] 形式")


def _derive_dimension(expression, values):
    try:
        parsed = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise RuntimeError(f"derive 不是合法表达式：{expression!r}") from exc
    result = _shape_expression(parsed.body, values)
    _require(isinstance(result, int), "derive 结果必须是整数 shape 维度")
    return result


def _plain_number(value):
    if isinstance(value, torch.Tensor):
        return value.item()
    return value


def _call_number(value, name):
    _require(not isinstance(value, torch.Tensor),
             f"NPU 直调参数 {name} 必须是 Python 标量，不能在计时路径做张量取值")
    return value


def _enum_value(table, argument, fragment, fallback):
    execute = next(item for item in table["sequence"] if item["step"] == "execute")
    spec = next(item for item in execute["args"] if item["name"] == argument)
    for name, value in spec.get("enum_values", {}).items():
        if fragment in name.upper():
            return value
    return fallback


def _trsm_baseline(table, values):
    """把 trsm 的布局、枚举和原地语义翻译到声明的 torch 基线。"""
    side = int(_plain_number(values["side"]))
    uplo = int(_plain_number(values["uplo"]))
    trans = int(_plain_number(values["transa"]))
    diag = int(_plain_number(values["diag"]))
    m = int(_plain_number(values["m"]))
    n = int(_plain_number(values["n"]))
    alpha = float(_plain_number(values["alpha"]))
    left_value = _enum_value(table, "side", "LEFT", 141)
    upper_value = _enum_value(table, "uplo", "UPPER", 121)
    no_trans_value = _enum_value(table, "transa", "_N", 111)
    unit_value = _enum_value(table, "diag", "UNIT", 132)
    left = side == left_value
    k = m if left else n

    # 两层基线政策：数值计算只调用声明的 torch 基线；本层只翻译布局、枚举、
    # 标量与原地写回。存储是 (batch, rows, ld) 行主序，padding 列原样保留。
    a_math = values["aArray"][:, :k, :k]
    b_math = values["bArray"][:, :m, :n]
    upper = uplo == upper_value
    if trans != no_trans_value:
        a_math = a_math.mT
        upper = not upper
    result = torch.linalg.solve_triangular(
        a_math, alpha * b_math, upper=upper, left=left,
        unitriangular=diag == unit_value)
    output = values["bArray"].detach().clone()
    output[:, :m, :n] = result
    return output


def _condition_trsm_inputs(table, values):
    required = {"side", "uplo", "m", "n", "aArray"}
    if not required <= values.keys():
        return
    side = int(_plain_number(values["side"]))
    uplo = int(_plain_number(values["uplo"]))
    m = int(_plain_number(values["m"]))
    n = int(_plain_number(values["n"]))
    left = side == _enum_value(table, "side", "LEFT", 141)
    upper = uplo == _enum_value(table, "uplo", "UPPER", 121)
    k = m if left else n
    matrix = values["aArray"][:, :k, :k]
    matrix.clamp_(-0.3, 0.3)
    matrix.copy_(torch.triu(matrix) if upper else torch.tril(matrix))
    diagonal = matrix.diagonal(dim1=-2, dim2=-1)
    pushed = torch.where(
        diagonal.abs() < 1.5,
        torch.where(diagonal < 0, -1.5, 1.5),
        diagonal,
    )
    diagonal.copy_(pushed)


@register("example_c_api")
class ExampleCApi(BaseApi):
    """同一注册类服务 NPU 待测节点与 CPU 基线节点。"""

    def __init__(self, task_result):
        super().__init__(task_result)
        library_text = os.environ.get("ATK_C_API_LIBRARY")
        sequence_text = os.environ.get("ATK_C_API_CALL_SEQUENCE")
        if not library_text or not sequence_text:
            raise RuntimeError(
                "ATK_C_API_LIBRARY 与 ATK_C_API_CALL_SEQUENCE 都必须设置；"
                "配置方法见 references/build-deploy.md")
        _require(os.path.isabs(library_text),
                 "ATK_C_API_LIBRARY 必须是绝对 .so 路径；见 references/build-deploy.md")
        _require(os.path.isabs(sequence_text),
                 "ATK_C_API_CALL_SEQUENCE 必须是绝对 JSON 路径；"
                 "见 references/build-deploy.md")
        _require(library_text.endswith(".so"),
                 "ATK_C_API_LIBRARY 必须指向 .so 文件")
        self._library_path = os.path.realpath(library_text)
        self._sequence_path = Path(sequence_text).resolve(strict=True)
        _require(os.path.isfile(self._library_path),
                 f"ATK_C_API_LIBRARY 不是普通文件：{self._library_path}")
        self._table, self._steps = _load_table(self._sequence_path)
        self._library = None
        self._function = None

    def init_by_input_data(self, input_data: InputDataset):
        values = _input_values(input_data, self._steps["execute"]["args"])
        # 两个节点都会调用调理钩子；操作必须幂等，才能让两侧落到完全相同的输入。
        _condition_trsm_inputs(self._table, values)

    def _load_function(self):
        if self._function is not None:
            return self._function
        self._library = ctypes.CDLL(self._library_path, mode=ctypes.RTLD_GLOBAL)
        resolved_name = self._table.get("exported_name")
        if resolved_name is None and self._table.get("mangled") is True:
            resolved_name = os.environ.get("ATK_C_API_EXPORTED_NAME")
            _require(
                resolved_name,
                "mangled 调用序列表必须设置 ATK_C_API_EXPORTED_NAME；"
                "先运行 check_c_api_binding.py 取得 resolved_exported_name")
        resolved_name = resolved_name or self._table["symbol"]
        try:
            self._function = getattr(self._library, resolved_name)
        except AttributeError as exc:
            raise RuntimeError(f"动态库没有导出函数 {resolved_name}") from exc
        self._function.restype = ctypes.c_int
        print(
            f"[c_api_executor] loaded_library={self._library_path} "
            f"exported_name={resolved_name}",
            flush=True,
        )
        return self._function

    def _context(self):
        spec = self._steps["context"]
        stream = int(torch.npu.current_stream().npu_stream)
        if spec["shape"] == "struct_handle":
            fields = [(item["name"], _STRUCT_CTYPES[item["ctype"]])
                      for item in spec["struct"]["fields"]]
            context_type = type(spec["struct"]["name"], (ctypes.Structure,), {
                "_fields_": fields,
            })
            context = context_type()
            _require(any(name == "stream" for name, _ in fields),
                     "struct_handle 没有可填写的 stream 字段")
            context.stream = stream
            pointer = ctypes.cast(ctypes.pointer(context), ctypes.c_void_p)
            return pointer, context, None

        handle = ctypes.c_void_p()
        # 生成器只记录 create(handle*) / set_stream(handle, stream) /
        # destroy(handle) 的精确形态，因此这里的固定 ctypes 参数个数有前置保证。
        create = getattr(self._library, spec["create"])
        create.restype = ctypes.c_int
        create.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        code = int(create(ctypes.byref(handle)))
        if code != self._steps["execute"]["status_ok"]:
            raise RuntimeError(f"c_api status {code}")
        return handle, handle, spec["destroy"]

    def _set_opaque_stream(self, context_pointer):
        spec = self._steps["context"]
        if not spec.get("set_stream"):
            return
        set_stream = getattr(self._library, spec["set_stream"])
        set_stream.restype = ctypes.c_int
        set_stream.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        stream = ctypes.c_void_p(int(torch.npu.current_stream().npu_stream))
        code = int(set_stream(context_pointer, stream))
        if code != self._steps["execute"]["status_ok"]:
            raise RuntimeError(f"c_api status {code}")

    def _call_args(self, values, context_pointer):
        argtypes = []
        arguments = []
        keepalive = []
        for spec in self._steps["execute"]["args"]:
            arg_class = spec["class"]
            name = spec["name"]
            if arg_class == "context":
                argtypes.append(ctypes.c_void_p)
                arguments.append(context_pointer)
            elif arg_class == "enum":
                argtypes.append(ctypes.c_int32)
                arguments.append(int(_call_number(values[name], name)))
            elif arg_class in {"dim", "layout_param"}:
                ctype = _VALUE_CTYPES.get(spec.get("ctype"))
                _require(ctype is not None,
                         f"参数 {name} 使用未开放整数 ctype {spec.get('ctype')!r}")
                value = (_derive_dimension(spec["derive"], values)
                         if spec.get("derive") else _call_number(values[name], name))
                argtypes.append(ctype)
                arguments.append(int(value))
            elif arg_class == "host_scalar":
                ctype = _POINTEE_CTYPES.get(spec.get("pointee_ctype"))
                _require(ctype is not None,
                         f"参数 {name} 使用未开放标量 ctype")
                scalar = ctype(_call_number(values[name], name))
                keepalive.append(scalar)
                argtypes.append(ctypes.POINTER(ctype))
                arguments.append(ctypes.byref(scalar))
            elif arg_class == "device_ptr":
                argtypes.append(ctypes.c_void_p)
                arguments.append(ctypes.c_void_p(values[name].data_ptr()))
            elif arg_class == "device_ptr_array":
                tensor = values[name]
                batch = int(tensor.shape[0])
                pointers = (ctypes.c_void_p * batch)(
                    *[tensor[index].data_ptr() for index in range(batch)])
                keepalive.append(pointers)
                argtypes.append(ctypes.POINTER(ctypes.c_void_p))
                arguments.append(pointers)
        return argtypes, arguments, keepalive

    def _npu_call(self, values):
        # NPU 分支禁止 torch 计算算子
        import torch_npu  # noqa: F401

        torch.npu.set_device(self.device_id)
        function = self._load_function()
        context_pointer = context_keepalive = destroy_name = None
        try:
            context_pointer, context_keepalive, destroy_name = self._context()
            if self._steps["context"]["shape"] == "opaque_functions":
                self._set_opaque_stream(context_pointer)
            argtypes, arguments, argument_keepalive = self._call_args(
                values, context_pointer)
            function.argtypes = argtypes
            code = int(function(*arguments))
            if code != self._steps["execute"]["status_ok"]:
                raise RuntimeError(f"c_api status {code}")
            torch.npu.synchronize()
            # 局部变量保持 ctypes 标量、指针数组和上下文存活到同步完成。
            _ = context_keepalive, argument_keepalive
            return values[self._table["output"]["in_place"]]
        finally:
            if destroy_name and context_pointer:
                destroy = getattr(self._library, destroy_name)
                destroy.restype = ctypes.c_int
                destroy.argtypes = [ctypes.c_void_p]
                destroy(context_pointer)

    def __call__(self, input_data: InputDataset, with_output: bool = False):
        values = _input_values(input_data, self._steps["execute"]["args"])
        if self.device == "npu":
            return self._npu_call(values)
        return _trsm_baseline(self._table, values)
