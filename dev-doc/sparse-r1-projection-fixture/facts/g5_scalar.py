#!/usr/bin/env python3
"""按事实表 FACTS 生成 CSV 驱动 GTest 用例。只编辑 FACTS 区；通用代码区禁止修改。"""

# ===== FACTS 区开始 =====
FACTS = {'schema_version': 1,
 'generator_version': 1,
 'op': 'g5_scalar',
 'family': 'synth5',
 'symbol': 'aclblasG5Scalar',
 'returns': 'aclblasStatus_t',
 'params': [{'name': 'handle', 'ctype': 'aclblasHandle_t', 'role': 'handle'},
            {'name': 'trans', 'ctype': 'aclblasOperation_t', 'role': 'enum', 'values': ['N', 'T']},
            {'name': 'dt',
             'ctype': 'aclDataType',
             'role': 'enum',
             'enum_kind': 'dtype',
             'values': ['FP16', 'FP32']},
            {'name': 'n', 'ctype': 'int', 'role': 'dim'},
            {'name': 'alpha',
             'ctype': 'const float*',
             'role': 'scalar',
             'dtype': 'float32',
             'values': [1.0, 0.5, -1.5],
             'mem': 'host'},
            {'name': 'beta',
             'ctype': 'const aclblasComplex*',
             'role': 'scalar',
             'dtype': 'complex64',
             'mem': 'device'},
            {'name': 'c',
             'ctype': 'float*',
             'role': 'inout_scalar',
             'dtype': 'float32',
             'mem': 'device'},
            {'name': 's',
             'ctype': 'const void*',
             'role': 'scalar',
             'dtype_from': 'dt',
             'values': [[0.5, 0.0], [2.0, 1.0]],
             'mem': 'device'},
            {'name': 'x',
             'ctype': 'const float*',
             'role': 'vector',
             'dtype': 'float32',
             'dir': 'in',
             'len': 'n',
             'inc': 1},
            {'name': 'y',
             'ctype': 'const float*',
             'role': 'vector',
             'dtype': 'float32',
             'dir': 'in',
             'len': 'n',
             'inc': 'incy'},
            {'name': 'incy', 'ctype': 'int', 'role': 'layout', 'kind': 'inc', 'of': 'y'}],
 'constraints': ['n >= 1'],
 'golden': {'kind': 'loop', 'symbol': 'ref_g5_scalar'},
 'verify': ['inout_scalars', 'vector_inc'],
 'dtype_profiles': [{'name': 'pc16',
                     'assign': {'dt': 'FP16'},
                     'scalar_dtype': 'complex64',
                     'golden_dtype': 'complex64',
                     'precision_row': 'FLOAT16'},
                    {'name': 'pc32',
                     'assign': {'dt': 'FP32'},
                     'scalar_dtype': 'complex64',
                     'golden_dtype': 'complex64',
                     'precision_row': 'FLOAT32'}],
 'edge_cases': [{'name': 'enum_key',
                 'set': {'trans': 'T'},
                 'expect': 'ACLBLAS_STATUS_SUCCESS',
                 'source': '合成 fixture：ED 的 enum 直接键'},
                {'name': 'layout_key',
                 'set': {'incy': 5},
                 'expect': 'ACLBLAS_STATUS_SUCCESS',
                 'source': '合成 fixture：ED 的 layout 直接键（写最终整数）'},
                {'name': 'scalar_key',
                 'set': {'alpha': 2.5},
                 'expect': 'ACLBLAS_STATUS_SUCCESS',
                 'source': '合成 fixture：ED 的实标量直接键'},
                {'name': 'scalar_complex_key',
                 'set': {'beta': [2.0, 1.0]},
                 'expect': 'ACLBLAS_STATUS_SUCCESS',
                 'source': '合成 fixture：ED 的复标量直接键（[re, im]）'}],
 'perf': {'key': ['n', 'incy'],
          'sweep': False,
          'rows': [{'n': 1, 'incy': 1, 'gpu_ms': 0.05},
                   {'n': 1, 'incy': 3, 'gpu_ms': 0.06},
                   {'n': 2, 'incy': 1},
                   {'n': 2, 'incy': 3},
                   {'n': 3, 'incy': 1},
                   {'n': 3, 'incy': 3},
                   {'n': 4, 'incy': 1},
                   {'n': 4, 'incy': 3},
                   {'n': 5, 'incy': 1},
                   {'n': 5, 'incy': 3},
                   {'n': 6, 'incy': 1},
                   {'n': 6, 'incy': 3},
                   {'n': 7, 'incy': 1},
                   {'n': 7, 'incy': 3},
                   {'n': 8, 'incy': 1},
                   {'n': 8, 'incy': 3},
                   {'n': 9, 'incy': 1},
                   {'n': 9, 'incy': 3},
                   {'n': 10, 'incy': 1},
                   {'n': 10, 'incy': 3},
                   {'n': 11, 'incy': 1},
                   {'n': 11, 'incy': 3},
                   {'n': 12, 'incy': 1},
                   {'n': 12, 'incy': 3},
                   {'n': 13, 'incy': 1},
                   {'n': 13, 'incy': 3},
                   {'n': 14, 'incy': 1},
                   {'n': 14, 'incy': 3},
                   {'n': 15, 'incy': 1},
                   {'n': 15, 'incy': 3},
                   {'n': 16, 'incy': 1},
                   {'n': 16, 'incy': 3},
                   {'n': 17, 'incy': 1},
                   {'n': 17, 'incy': 3},
                   {'n': 18, 'incy': 1},
                   {'n': 18, 'incy': 3},
                   {'n': 19, 'incy': 1},
                   {'n': 19, 'incy': 3},
                   {'n': 20, 'incy': 1},
                   {'n': 20, 'incy': 3},
                   {'n': 21, 'incy': 1},
                   {'n': 21, 'incy': 3},
                   {'n': 22, 'incy': 1},
                   {'n': 22, 'incy': 3},
                   {'n': 23, 'incy': 1},
                   {'n': 23, 'incy': 3},
                   {'n': 24, 'incy': 1},
                   {'n': 24, 'incy': 3},
                   {'n': 25, 'incy': 1},
                   {'n': 25, 'incy': 3},
                   {'n': 26, 'incy': 1},
                   {'n': 26, 'incy': 3},
                   {'n': 27, 'incy': 1},
                   {'n': 27, 'incy': 3},
                   {'n': 28, 'incy': 1},
                   {'n': 28, 'incy': 3},
                   {'n': 29, 'incy': 1},
                   {'n': 29, 'incy': 3},
                   {'n': 30, 'incy': 1},
                   {'n': 30, 'incy': 3},
                   {'n': 31, 'incy': 1},
                   {'n': 31, 'incy': 3},
                   {'n': 32, 'incy': 1},
                   {'n': 32, 'incy': 3},
                   {'n': 33, 'incy': 1},
                   {'n': 33, 'incy': 3},
                   {'n': 34, 'incy': 1},
                   {'n': 34, 'incy': 3},
                   {'n': 35, 'incy': 1},
                   {'n': 35, 'incy': 3},
                   {'n': 36, 'incy': 1},
                   {'n': 36, 'incy': 3},
                   {'n': 37, 'incy': 1},
                   {'n': 37, 'incy': 3},
                   {'n': 38, 'incy': 1},
                   {'n': 38, 'incy': 3},
                   {'n': 39, 'incy': 1},
                   {'n': 39, 'incy': 3},
                   {'n': 40, 'incy': 1},
                   {'n': 40, 'incy': 3},
                   {'n': 41, 'incy': 1},
                   {'n': 41, 'incy': 3},
                   {'n': 42, 'incy': 1},
                   {'n': 42, 'incy': 3},
                   {'n': 43, 'incy': 1},
                   {'n': 43, 'incy': 3},
                   {'n': 44, 'incy': 1},
                   {'n': 44, 'incy': 3},
                   {'n': 45, 'incy': 1},
                   {'n': 45, 'incy': 3},
                   {'n': 46, 'incy': 1},
                   {'n': 46, 'incy': 3},
                   {'n': 47, 'incy': 1},
                   {'n': 47, 'incy': 3},
                   {'n': 48, 'incy': 1},
                   {'n': 48, 'incy': 3},
                   {'n': 49, 'incy': 1},
                   {'n': 49, 'incy': 3},
                   {'n': 50, 'incy': 1},
                   {'n': 50, 'incy': 3},
                   {'n': 51, 'incy': 1},
                   {'n': 51, 'incy': 3},
                   {'n': 52, 'incy': 1},
                   {'n': 52, 'incy': 3},
                   {'n': 53, 'incy': 1},
                   {'n': 53, 'incy': 3},
                   {'n': 54, 'incy': 1},
                   {'n': 54, 'incy': 3},
                   {'n': 55, 'incy': 1},
                   {'n': 55, 'incy': 3},
                   {'n': 56, 'incy': 1},
                   {'n': 56, 'incy': 3},
                   {'n': 57, 'incy': 1},
                   {'n': 57, 'incy': 3},
                   {'n': 58, 'incy': 1},
                   {'n': 58, 'incy': 3},
                   {'n': 59, 'incy': 1},
                   {'n': 59, 'incy': 3},
                   {'n': 60, 'incy': 1},
                   {'n': 60, 'incy': 3},
                   {'n': 61, 'incy': 1},
                   {'n': 61, 'incy': 3},
                   {'n': 62, 'incy': 1},
                   {'n': 62, 'incy': 3},
                   {'n': 63, 'incy': 1},
                   {'n': 63, 'incy': 3},
                   {'n': 64, 'incy': 1},
                   {'n': 64, 'incy': 3},
                   {'n': 65, 'incy': 1},
                   {'n': 65, 'incy': 3},
                   {'n': 66, 'incy': 1},
                   {'n': 66, 'incy': 3},
                   {'n': 67, 'incy': 1},
                   {'n': 67, 'incy': 3},
                   {'n': 68, 'incy': 1},
                   {'n': 68, 'incy': 3},
                   {'n': 69, 'incy': 1},
                   {'n': 69, 'incy': 3},
                   {'n': 70, 'incy': 1},
                   {'n': 70, 'incy': 3},
                   {'n': 71, 'incy': 1},
                   {'n': 71, 'incy': 3},
                   {'n': 72, 'incy': 1},
                   {'n': 72, 'incy': 3},
                   {'n': 73, 'incy': 1},
                   {'n': 73, 'incy': 3},
                   {'n': 74, 'incy': 1},
                   {'n': 74, 'incy': 3},
                   {'n': 75, 'incy': 1},
                   {'n': 75, 'incy': 3},
                   {'n': 76, 'incy': 1},
                   {'n': 76, 'incy': 3},
                   {'n': 77, 'incy': 1},
                   {'n': 77, 'incy': 3},
                   {'n': 78, 'incy': 1},
                   {'n': 78, 'incy': 3},
                   {'n': 79, 'incy': 1},
                   {'n': 79, 'incy': 3},
                   {'n': 80, 'incy': 1},
                   {'n': 80, 'incy': 3},
                   {'n': 81, 'incy': 1},
                   {'n': 81, 'incy': 3},
                   {'n': 82, 'incy': 1},
                   {'n': 82, 'incy': 3},
                   {'n': 83, 'incy': 1},
                   {'n': 83, 'incy': 3},
                   {'n': 84, 'incy': 1},
                   {'n': 84, 'incy': 3},
                   {'n': 85, 'incy': 1},
                   {'n': 85, 'incy': 3},
                   {'n': 86, 'incy': 1},
                   {'n': 86, 'incy': 3},
                   {'n': 87, 'incy': 1},
                   {'n': 87, 'incy': 3},
                   {'n': 88, 'incy': 1},
                   {'n': 88, 'incy': 3},
                   {'n': 89, 'incy': 1},
                   {'n': 89, 'incy': 3},
                   {'n': 90, 'incy': 1},
                   {'n': 90, 'incy': 3},
                   {'n': 91, 'incy': 1},
                   {'n': 91, 'incy': 3},
                   {'n': 92, 'incy': 1},
                   {'n': 92, 'incy': 3},
                   {'n': 93, 'incy': 1},
                   {'n': 93, 'incy': 3},
                   {'n': 94, 'incy': 1},
                   {'n': 94, 'incy': 3},
                   {'n': 95, 'incy': 1},
                   {'n': 95, 'incy': 3},
                   {'n': 96, 'incy': 1},
                   {'n': 96, 'incy': 3},
                   {'n': 97, 'incy': 1},
                   {'n': 97, 'incy': 3},
                   {'n': 98, 'incy': 1},
                   {'n': 98, 'incy': 3},
                   {'n': 99, 'incy': 1},
                   {'n': 99, 'incy': 3},
                   {'n': 100, 'incy': 1},
                   {'n': 100, 'incy': 3}],
          'meta': {'device': 'synthetic', 'statistic': 'median'}},
 'sources': {'params': '合成 fixture：sparse-r1-projection-matrix.md §D 组5（标量取值组）',
             'perf': '合成 fixture：dim+inc layout 性能键，200 点位'}}
# ===== FACTS 区结束 =====
# ===== 通用代码区（由 repo-task-blas-case-gen 渲染，禁止修改）=====

import ast
import csv
import itertools
from pathlib import Path
import sys


GENERATOR_VERSION = 1

# 每个 2^n 处给出 (2^n-1, 2^n, 2^n+1) 三元组，夹住 tiling 的「差一个/刚好一块/多一个」。
# 加退化 1、2、3 与一个大尺寸。文档写了维度上限就在 cases.dim_tiers 里裁剪。
MAT_DIM_TIERS = [1, 2, 3, 15, 16, 17, 63, 64, 65, 255, 256, 257, 1024]
# 纯向量长度再加中、大两个大值：100003 约 fp32 400 KB（medium），1050001 约 fp32 4 MB、
# fp16 2 MB（large），让归约类算子的规模覆盖真正落进 medium 与 large 两档。
VEC_DIM_TIERS = [1, 2, 3, 15, 16, 17, 63, 64, 65, 255, 256, 257, 1024, 100003, 1050001]
# 覆盖单批、双批和小奇数批量。
BATCH_TIERS = [1, 2, 5]
# min 使用最小合法值，pad 制造非对齐的额外间隔。
LD_TIERS = ["min", "pad"]
STRIDE_TIERS = ["min", "pad"]
# 0 和负步长只由 edge_cases 显式给出。
INC_TIERS = [1, 3]
# 覆盖单位元、零、负数和分数。
REAL_SCALAR_TIERS = [1.0, 0.0, -1.5, 0.5]
COMPLEX_SCALAR_TIERS = [
    (1.0, 0.0),
    (0.0, 0.0),
    (0.5, -1.5),
    (-2.0, 1.0),
]
# 与 CSV 框架的数据填充词表保持一致。
FILL_TIERS = [
    "RANDOM_NORM_1",
    "VALUE_NORM_0",
    "RANDOM_ALTER",
    "RANDOM_EXTREME",
]
# L0 使用两个小尺寸，ED 使用中等对齐尺寸。
L0_SIZES = [4, 8]
ED_SIZE = 16
SEED_BASE = 20260000
# 单用例 host 侧缓冲的设计上限。
DEFAULT_MAX_FOOTPRINT_BYTES = 4 * 1024 ** 3
DTYPE_BYTES = {
    "float16": 2,
    "bfloat16": 2,
    "float32": 4,
    "float64": 8,
    "complex64": 8,
    "complex128": 16,
    "int8": 1,
    "uint8": 1,
    "int16": 2,
    "uint16": 2,
    "int32": 4,
    "int64": 8,
}
COMPLEX_DTYPES = {"complex64", "complex128"}
SCALAR_ROLES = {"scalar", "inout_scalar", "out_scalar"}
BUFFER_ROLES = {"vector", "fixed_vector", "matrix", "int_array"}
PF_SWEEP_SIZES = [64, 128, 256, 512, 1024, 2048, 4096]

# harness_profile registry：算子域之间的惯例差异只进这张数据表，引擎代码不按域开分枝。
# 字段面已冻结，加字段要过评审；本期只实例化 blas，稀疏域的值随 FACTS schema v2 落地。
HARNESS_REGISTRY = {
    "blas": {
        # 以下取值与参数化之前的硬编码逐字节一致，改成查表不改变任何产物。
        "seed_columns": ["random_seed"],
        "description_column": "description",
        "expect_column": "expect_result",
        # 生成侧 expect 列的默认写值；没有 expect 列的域这里必须是 None。
        "expect_default_token": "ACLBLAS_STATUS_SUCCESS",
        # 域级闭合上界（parseStatus 全名）。单算子只能声明它的子集。
        # 顺序即 README 状态词表小节的渲染顺序，重排会改产物。
        "status_vocab_bound": [
            "ACLBLAS_STATUS_SUCCESS", "ACLBLAS_STATUS_NOT_INITIALIZED",
            "ACLBLAS_STATUS_ALLOC_FAILED", "ACLBLAS_STATUS_INVALID_VALUE",
            "ACLBLAS_STATUS_MAPPING_ERROR", "ACLBLAS_STATUS_EXECUTION_FAILED",
            "ACLBLAS_STATUS_INTERNAL_ERROR", "ACLBLAS_STATUS_NOT_SUPPORTED",
            "ACLBLAS_STATUS_ARCH_MISMATCH", "ACLBLAS_STATUS_HANDLE_IS_NULLPTR",
            "ACLBLAS_STATUS_INVALID_ENUM", "ACLBLAS_STATUS_UNKNOWN",
        ],
        # blas 的精度阈值写在 FACTS 里，主 CSV 不带阈值列。
        "threshold_columns": [],
        # aclblas 全部接口首参是 handle；None 表示该域不做这项断言。
        "first_param_ctype": "aclblasHandle_t",
        # 验收侧靠这些入口头文件认出算子仓属于哪个域。
        "entry_headers": ["cann_ops_blas.h"],
        # dense_formula 走静态显存估算；no_static_check 只跳过静态判定。
        "footprint_policy": "dense_formula",
        # 构建/绑卡惯例（M7 真机实测入面，checkpoint thread 01a060de）：
        # blas 的 build.sh 用 --device 编译期定卡，无运行时绑卡变量与额外库路径。
        "build_device_flag": True,
        "visible_devices_env": None,
        "runtime_library_dirs": [],
    },
    "sparse_frame": {
        # 值按 ops-sparse@5b2a5ba 普查实例化（仓级默认；逐算子偏差走 FACTS 覆盖）。
        "seed_columns": ["seed"],
        "description_column": None,
        "expect_column": "expect_result",
        "expect_default_token": "SUCCESS",
        # 26 个 frame 算子 expect 词表的并集（Lt 家族除外），普查即出处；
        # 含小写变体与算子私有 token，单算子在 FACTS 里声明精确子集。
        "status_vocab_bound": [
            "SUCCESS", "ACL_SPARSE_STATUS_SUCCESS", "SUCCESS_NO_OUTPUT",
            "INVALID_VALUE", "NOT_SUPPORTED", "SINGULAR",
            "success", "singular",
        ],
        # 仓级默认三件套；无阈值列的算子覆盖为空表。
        "threshold_columns": [
            "mere_threshold", "mare_multiplier", "abs_threshold",
        ],
        # 稀疏仓 handle 形态不一（含无 handle 的 accessor），不做首参断言。
        "first_param_ctype": None,
        "entry_headers": ["cann_ops_sparse.h"],
        "footprint_policy": "no_static_check",
        # ops-sparse 的 build.sh 无 --device（TEST_DEVICE_ID 恒 0）：跑测用
        # ASCEND_RT_VISIBLE_DEVICES 把目标物理卡映射为逻辑 0，且测试二进制
        # 运行时需要 build_out/lib64。
        "build_device_flag": False,
        "visible_devices_env": "ASCEND_RT_VISIBLE_DEVICES",
        "runtime_library_dirs": ["build_out/lib64"],
    },
}


def _harness_profile(facts):
    """本任务包适用的 profile。schema v1 隐式 blas；v2 由 FACTS 的 harness_profile 选。"""
    if facts.get("schema_version") == 2:
        return HARNESS_REGISTRY[facts["harness_profile"]]
    return HARNESS_REGISTRY["blas"]


def _resolved_profile(facts):
    """profile 叠加 FACTS 的 harness_overrides（整键替换，恰四个可覆盖键）。
    覆盖键合法性由 S1 校验把关，这里只做机械合并。"""
    profile = dict(_harness_profile(facts))
    if facts.get("schema_version") == 2:
        profile.update(facts.get("harness_overrides", {}))
    return profile


class GeneratorError(Exception):
    """表示带生成阶段上下文的确定性错误。"""


def _is_projected(facts, param):
    """「不投影」原语的唯一判定：v2 且 projection=="none" 的参数在引擎侧
    列/轴/state/控制列（null/batch）与维度种类推导全部静默。所有消费者都从
    这里取答案，不得各自再看 projection 键。"""
    return not (
        facts.get("schema_version") == 2 and param.get("projection") == "none"
    )


def _param_map(facts):
    return {param["name"]: param for param in facts["params"]}


def _profile_map(facts):
    return {profile["name"]: profile for profile in facts.get("dtype_profiles", [])}


def _case_options(facts):
    cases = facts.get("cases", {})
    return {
        "dim_tiers": cases.get("dim_tiers", MAT_DIM_TIERS),
        "vec_dim_tiers": cases.get("vec_dim_tiers", VEC_DIM_TIERS),
        "batch_tiers": cases.get("batch_tiers", BATCH_TIERS),
        "inc_tiers": cases.get("inc_tiers", INC_TIERS),
        "fill_tiers": cases.get("fill_tiers", FILL_TIERS),
        "max_footprint_bytes": cases.get(
            "max_footprint_bytes", DEFAULT_MAX_FOOTPRINT_BYTES
        ),
    }


def _expression_names(expression):
    tree = ast.parse(expression, mode="eval")
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id not in {"max", "min", "rows", "cols", "len"}
    }


def _dimension_kinds(facts):
    matrix_names = set()
    vector_names = set()
    batch_names = set()
    for param in facts["params"]:
        if not _is_projected(facts, param):
            continue
        role = param["role"]
        if role == "matrix":
            for field in ("rows", "cols"):
                matrix_names.update(_expression_names(param[field]))
        elif role in {"vector", "int_array"}:
            vector_names.update(_expression_names(param["len"]))
        batch = param.get("batch")
        if isinstance(batch, dict):
            batch_names.add(batch["count"])
    return matrix_names, vector_names, batch_names


def _scalar_axis_values(param, facts):
    if "values" in param:
        return list(param["values"]), "scalar_value"
    if "dtype" in param:
        tiers = COMPLEX_SCALAR_TIERS if param["dtype"] in COMPLEX_DTYPES else REAL_SCALAR_TIERS
        return list(tiers), "scalar_value"
    return list(range(len(REAL_SCALAR_TIERS))), "scalar_tier"


def build_axes(facts):
    """按 params 顺序派生轴；单值轴仍保留在返回值中。"""
    options = _case_options(facts)
    matrix_names, vector_names, batch_names = _dimension_kinds(facts)
    profiles = facts.get("dtype_profiles", [])
    profile_inserted = False
    axes = []
    version = facts.get("schema_version")
    for param in facts["params"]:
        if not _is_projected(facts, param):
            continue
        name = param["name"]
        role = param["role"]
        enum_kind = param.get("enum_kind", "op")
        if role == "enum":
            if enum_kind in {"dtype", "compute"}:
                if profiles and not profile_inserted:
                    axes.append(
                        {
                            "name": "profile",
                            "values": [profile["name"] for profile in profiles],
                            "kind": "profile",
                        }
                    )
                    profile_inserted = True
                continue
            axes.append({"name": name, "values": list(param["values"]), "kind": "op_enum"})
        elif role == "dim":
            if name in batch_names:
                values = options["batch_tiers"]
                kind = "batch_dim"
            elif name in matrix_names:
                values = options["dim_tiers"]
                kind = "mat_dim"
            elif name in vector_names:
                values = options["vec_dim_tiers"]
                kind = "vec_dim"
            else:
                values = options["dim_tiers"]
                kind = "mat_dim"
            axes.append({"name": name, "values": list(values), "kind": kind})
        elif role == "layout":
            kind = param["kind"]
            values = {
                "ld": LD_TIERS,
                "stride": STRIDE_TIERS,
                "inc": options["inc_tiers"],
                "batch": options["batch_tiers"],
            }[kind]
            axes.append({"name": name, "values": list(values), "kind": kind})
        elif role in {"scalar", "inout_scalar"}:
            values, kind = _scalar_axis_values(param, facts)
            axes.append({"name": name, "values": values, "kind": kind})
        elif role in {"vector", "matrix"}:
            direction = param.get("dir", "in")
            if direction not in {"in", "inout"} or "producer" in param:
                continue
            if role == "matrix" and param.get("conditioning"):
                axes.append(
                    {
                        "name": f"{name}_matrix_type",
                        "values": list(param["conditioning"]),
                        "kind": "matrix_type",
                    }
                )
            else:
                axes.append(
                    {
                        "name": f"{name.lower()}_fill",
                        "values": list(options["fill_tiers"]),
                        "kind": "fill",
                    }
                )
        elif role == "fixed_vector" and param.get("dir") in {"in", "inout"}:
            axes.append(
                {
                    "name": name,
                    "values": list(range(len(param["samples"]))),
                    "kind": "fixed_vector",
                }
            )
    if version == 2:
        for control in facts.get("case_controls", []):
            axes.append(
                {
                    "name": control["name"],
                    "values": list(control["values"]),
                    "kind": f"control_{control['kind']}",
                }
            )
        # 轴名命名空间冲突机械拒绝（覆盖 trap_6 的 sparse 面；v1 现状不动）。
        names = [axis["name"] for axis in axes]
        duplicated = sorted({name for name in names if names.count(name) > 1})
        if duplicated:
            raise GeneratorError(f"v2 轴名命名空间冲突：{duplicated}")
    # 一条不变量覆盖所有轴来源：轴取值必须唯一，否则 pairwise 用索引配对会不收敛。
    for axis in axes:
        hashable = [tuple(v) if isinstance(v, list) else v for v in axis["values"]]
        if len(hashable) != len(set(hashable)):
            raise GeneratorError(f"轴 {axis['name']!r} 含重复取值：{axis['values']}")
    return axes


class _Evaluator:
    def __init__(self, facts, state):
        self.params = _param_map(facts)
        self.state = state

    def evaluate(self, expression):
        return self._visit(ast.parse(expression, mode="eval").body)

    def _visit(self, node):
        if isinstance(node, ast.Name):
            return self.state[node.id]
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BinOp):
            left = self._visit(node.left)
            right = self._visit(node.right)
            operations = {
                ast.Add: lambda: left + right,
                ast.Sub: lambda: left - right,
                ast.Mult: lambda: left * right,
                ast.FloorDiv: lambda: left // right,
                ast.Mod: lambda: left % right,
            }
            return operations[type(node.op)]()
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.USub):
                return -self._visit(node.operand)
            if isinstance(node.op, ast.Not):
                return not self._visit(node.operand)
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                for value in node.values:
                    if not self._visit(value):
                        return False
                return True
            for value in node.values:
                if self._visit(value):
                    return True
            return False
        if isinstance(node, ast.Compare):
            values = [self._visit(node.left)] + [self._visit(item) for item in node.comparators]
            for left, operation, right in zip(values, node.ops, values[1:]):
                comparisons = {
                    ast.Eq: left == right,
                    ast.NotEq: left != right,
                    ast.Lt: left < right,
                    ast.LtE: left <= right,
                    ast.Gt: left > right,
                    ast.GtE: left >= right,
                }
                if not comparisons[type(operation)]:
                    return False
            return True
        if isinstance(node, ast.IfExp):
            branch = node.body if self._visit(node.test) else node.orelse
            return self._visit(branch)
        if isinstance(node, ast.Call):
            name = node.func.id
            if name in {"rows", "cols", "len"}:
                param_name = node.args[0].id
                return self._buffer_size(name, self.params[param_name])
            values = [self._visit(argument) for argument in node.args]
            return max(values) if name == "max" else min(values)
        raise ValueError(f"不支持的表达式节点 {type(node).__name__}")

    def _buffer_size(self, function, param):
        if function in {"rows", "cols"}:
            return self.evaluate(param[function])
        length = param.get("len")
        return length if isinstance(length, int) else self.evaluate(length)


def _profile_for_selection(facts, selection):
    profiles = facts.get("dtype_profiles", [])
    if not profiles:
        return None
    name = selection.get("profile", profiles[0]["name"])
    return _profile_map(facts)[name]


def _dtype_token(token):
    mapping = {
        "FP16": "float16",
        "FP32": "float32",
        "BF16": "bfloat16",
        "INT8": "int8",
    }
    try:
        return mapping[token]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"未知 aclDataType 记号 {token!r}") from exc


def _param_dtype(param, profile):
    if "dtype" in param:
        return param["dtype"]
    if param["role"] in SCALAR_ROLES:
        return profile["scalar_dtype"]
    return _dtype_token(profile["assign"][param["dtype_from"]])


def _first_target(param):
    target = param.get("of")
    return target[0] if isinstance(target, list) else target


def _complete_selection(axes, partial):
    selection = dict(partial)
    for axis in axes:
        selection.setdefault(axis["name"], axis["values"][0])
    return selection


def _materialize(facts, axes, partial, overrides=None):
    selection = _complete_selection(axes, partial)
    overrides = dict(overrides or {})
    params = _param_map(facts)
    profile = _profile_for_selection(facts, selection)
    state = {}
    if profile:
        state.update(profile["assign"])
        state["profile"] = profile["name"]
    version = facts.get("schema_version")
    for param in facts["params"]:
        if not _is_projected(facts, param):
            continue
        name = param["name"]
        role = param["role"]
        if role == "enum" and param.get("enum_kind", "op") in {"op", "algo"}:
            state[name] = selection[name]
        elif role == "dim":
            state[name] = selection[name]
    for param in facts["params"]:
        name = param["name"]
        role = param["role"]
        if role in {"scalar", "inout_scalar"}:
            value = selection[name]
            if "dtype_from" in param and "values" not in param:
                tiers = (
                    COMPLEX_SCALAR_TIERS
                    if profile["scalar_dtype"] in COMPLEX_DTYPES
                    else REAL_SCALAR_TIERS
                )
                value = tiers[value]
            state[name] = value
        elif role in {"vector", "matrix"}:
            direction = param.get("dir", "in")
            if direction in {"in", "inout"} and "producer" not in param:
                if role == "matrix" and param.get("conditioning"):
                    state[f"{name.lower()}_fill"] = _case_options(facts)["fill_tiers"][0]
                    state[f"{name}_matrix_type"] = selection[f"{name}_matrix_type"]
                else:
                    state[f"{name.lower()}_fill"] = selection[f"{name.lower()}_fill"]
        elif role == "fixed_vector" and param.get("dir") in {"in", "inout"}:
            state[name] = list(param["samples"][selection[name]])
    if facts.get("schema_version") == 2:
        for control in facts.get("case_controls", []):
            # 控制值是原始字符串，从轴选值原样进 state，端到端不转型。
            state[control["name"]] = selection[control["name"]]
    for key, value in overrides.items():
        if key in params and params[key]["role"] != "layout":
            state[key] = value
    evaluator = _Evaluator(facts, state)
    for param in facts["params"]:
        if param["role"] != "layout" or param["kind"] == "stride":
            continue
        name = param["name"]
        if name in overrides:
            state[name] = overrides[name]
            continue
        value = selection[name]
        if param["kind"] == "ld":
            rows_value = evaluator.evaluate(params[_first_target(param)]["rows"])
            value = max(1, rows_value) if value == "min" else rows_value + 5
        state[name] = value
    evaluator = _Evaluator(facts, state)
    for param in facts["params"]:
        if param["role"] != "layout" or param["kind"] != "stride":
            continue
        name = param["name"]
        if name in overrides:
            state[name] = overrides[name]
            continue
        target = params[_first_target(param)]
        minimum = _buffer_base_elements(target, state, evaluator)
        state[name] = minimum if selection[name] == "min" else minimum + 7
    for key, value in overrides.items():
        if key in state or key in params:
            state[key] = value
            continue
        for param in facts["params"]:
            if param["role"] != "fixed_vector" or not key.startswith(param["name"]):
                continue
            suffix = key[len(param["name"]):]
            if suffix.isdigit() and param["name"] in state:
                state[param["name"]][int(suffix)] = value
                break
    return selection, state, profile


def _buffer_base_elements(param, state, evaluator):
    role = param["role"]
    if role == "matrix":
        if param.get("storage", "full") == "packed":
            rows = evaluator.evaluate(param["rows"])
            return max(0, rows * (rows + 1) // 2)
        return max(0, state[param["ld"]] * evaluator.evaluate(param["cols"]))
    if role == "vector":
        length = evaluator.evaluate(param["len"])
        if length <= 0:
            return 0
        increment = param["inc"]
        inc = increment if isinstance(increment, int) else state[increment]
        return 1 + (length - 1) * abs(inc)
    length = param["len"]
    return length if isinstance(length, int) else evaluator.evaluate(length)


def _footprint(facts, state, profile):
    evaluator = _Evaluator(facts, state)
    total = 0
    for param in facts["params"]:
        role = param["role"]
        if role not in BUFFER_ROLES:
            continue
        dtype = _param_dtype(param, profile) if role != "int_array" else param.get("dtype", "int32")
        elements = _buffer_base_elements(param, state, evaluator)
        batch = param.get("batch")
        if batch:
            count = state[batch["count"]]
            if batch["model"] == "strided" and count > 0:
                elements += (count - 1) * state[batch["stride"]]
            else:
                elements *= count
        total += DTYPE_BYTES[dtype] * max(0, elements)
    return total


def _row_is_valid(facts, state, profile):
    evaluator = _Evaluator(facts, state)
    if any(not evaluator.evaluate(item) for item in facts.get("constraints", [])):
        return False
    if _resolved_profile(facts)["footprint_policy"] == "no_static_check":
        # 最窄分支：只跳过静态显存判定，不承诺任何运行时护栏（冻结字段 9）。
        return True
    limit = _case_options(facts)["max_footprint_bytes"]
    return _footprint(facts, state, profile) <= limit


def _param_column_specs(facts, version):
    """params 走出来的语义列（两版共用；v2 跳过不投影参数）。"""
    profiles = facts.get("dtype_profiles", [])
    profile_has_complex = any(
        profile["scalar_dtype"] in COMPLEX_DTYPES for profile in profiles
    )
    specs = []
    for param in facts["params"]:
        if not _is_projected(facts, param):
            continue
        name = param["name"]
        role = param["role"]
        direction = param.get("dir", "in")
        if role in {"handle", "out_scalar", "int_array"}:
            continue
        if role in {"enum", "dim", "layout"}:
            specs.append({"name": name, "kind": role, "source": name})
        elif role in {"scalar", "inout_scalar"}:
            is_complex = param.get("dtype") in COMPLEX_DTYPES or (
                "dtype_from" in param and profile_has_complex
            )
            if is_complex:
                specs.append(
                    {"name": f"{name}_re", "kind": "scalar_re", "source": name}
                )
                specs.append(
                    {"name": f"{name}_im", "kind": "scalar_im", "source": name}
                )
            else:
                specs.append({"name": name, "kind": "scalar", "source": name})
        elif role in {"vector", "matrix"}:
            if direction in {"in", "inout"} and "producer" not in param:
                # fill 列用小写参数名（a_fill），与社区旧任务包和 param.h 的读法一致。
                specs.append(
                    {"name": f"{name.lower()}_fill", "kind": "fill", "source": name}
                )
                if role == "matrix" and param.get("conditioning"):
                    specs.append(
                        {
                            "name": f"{name}_matrix_type",
                            "kind": "matrix_type",
                            "source": name,
                        }
                    )
        elif role == "fixed_vector" and direction in {"in", "inout"}:
            specs.extend(
                {
                    "name": f"{name}{index}",
                    "kind": "fixed_vector_elem",
                    "source": name,
                    "index": index,
                }
                for index in range(param["len"])
            )
    return specs


def _flag_column_specs(facts):
    """nullable 与 batch 的控制列（两版共用，排在 expect 之后）。"""
    specs = []
    for param in facts["params"]:
        if not _is_projected(facts, param):
            continue
        name = param["name"]
        if param.get("nullable", False):
            specs.append(
                {
                    "name": f"null{name[:1].upper()}{name[1:]}",
                    "kind": "null_flag",
                    "source": name,
                }
            )
        if "batch" in param:
            specs.append(
                {"name": f"{name}_batch_pattern", "kind": "batch_pattern", "source": name}
            )
    return specs


def _column_specs(facts):
    """主 CSV 的统一列描述，一处定列序，多处消费（表头、行写入、README 契约表）。

    每列一个普通 dict：name 列名；kind 列类别（框架列 id/description/expect/seed，
    参数列 enum/dim/layout/scalar/scalar_re/scalar_im/fill/matrix_type/
    fixed_vector_elem/null_flag/batch_pattern，v2 另有 control_enum/control_tier）；
    source 派生自哪个参数，框架列与控制列为 None；fixed_vector 元素列另带 index。
    v1 基座列名硬编码保持现状；v2 基座列名来自 resolved profile。
    """
    if facts.get("schema_version") == 2:
        resolved = _resolved_profile(facts)
        if resolved["threshold_columns"]:
            # 阈值列值源契约未建：先 fail-closed，禁止静默漏列。
            raise GeneratorError(
                "阈值列值源未建：本版要求 harness_overrides.threshold_columns "
                "覆盖为空表"
            )
        specs = [{"name": "case_name", "kind": "id", "source": None}]
        if resolved["description_column"] is not None:
            specs.append(
                {
                    "name": resolved["description_column"],
                    "kind": "description",
                    "source": None,
                }
            )
        specs += _param_column_specs(facts, 2)
        for control in facts.get("case_controls", []):
            specs.append(
                {
                    "name": control["name"],
                    "kind": f"control_{control['kind']}",
                    "source": None,
                }
            )
        if resolved["expect_column"] is not None:
            specs.append(
                {"name": resolved["expect_column"], "kind": "expect", "source": None}
            )
        specs += _flag_column_specs(facts)
        for seed_name in resolved["seed_columns"]:
            specs.append({"name": seed_name, "kind": "seed", "source": None})
        # 命名空间冲突检查（冻结 §2.5）：控制列、参数投影列、覆盖出的基座列两两不重。
        names = [spec["name"] for spec in specs]
        duplicated = sorted({name for name in names if names.count(name) > 1})
        if duplicated:
            raise GeneratorError(f"v2 列名命名空间冲突：{duplicated}")
        return specs
    specs = [
        {"name": "case_name", "kind": "id", "source": None},
        {"name": "description", "kind": "description", "source": None},
    ]
    specs += _param_column_specs(facts, 1)
    specs.append({"name": "expect_result", "kind": "expect", "source": None})
    specs += _flag_column_specs(facts)
    specs.append({"name": "random_seed", "kind": "seed", "source": None})
    return specs


def _header_columns(facts):
    return [spec["name"] for spec in _column_specs(facts)]


def _body_mapping(facts, state, profile, expect, control_overrides=None):
    result = {}
    version = facts.get("schema_version")
    profile_has_complex = any(
        item["scalar_dtype"] in COMPLEX_DTYPES
        for item in facts.get("dtype_profiles", [])
    )
    for param in facts["params"]:
        if not _is_projected(facts, param):
            continue
        name = param["name"]
        role = param["role"]
        direction = param.get("dir", "in")
        if role in {"enum", "dim", "layout"}:
            result[name] = state[name]
        elif role in {"scalar", "inout_scalar"}:
            value = state[name]
            is_complex = param.get("dtype") in COMPLEX_DTYPES
            is_complex = is_complex or ("dtype_from" in param and profile_has_complex)
            if is_complex:
                if profile and profile["scalar_dtype"] not in COMPLEX_DTYPES:
                    value = (value, 0.0)
                result[f"{name}_re"], result[f"{name}_im"] = value
            else:
                result[name] = value
        elif role in {"vector", "matrix"}:
            if direction in {"in", "inout"} and "producer" not in param:
                result[f"{name.lower()}_fill"] = state[f"{name.lower()}_fill"]
                if role == "matrix" and param.get("conditioning"):
                    result[f"{name}_matrix_type"] = state[f"{name}_matrix_type"]
        elif role == "fixed_vector" and direction in {"in", "inout"}:
            for index, value in enumerate(state[name]):
                result[f"{name}{index}"] = value
    if version == 2:
        resolved = _resolved_profile(facts)
        for control in facts.get("case_controls", []):
            result[control["name"]] = state[control["name"]]
        if resolved["expect_column"] is not None:
            result[resolved["expect_column"]] = expect
    else:
        result["expect_result"] = expect
    for param in facts["params"]:
        if not _is_projected(facts, param):
            continue
        name = param["name"]
        if param.get("nullable", False):
            result[f"null{name[:1].upper()}{name[1:]}"] = 0
        if "batch" in param:
            result[f"{name}_batch_pattern"] = "UNIFORM"
    result.update(control_overrides or {})
    return result


def _description(axes, selection):
    parts = []
    for axis in axes:
        value = selection[axis["name"]]
        if axis["kind"] in {"scalar_tier", "fixed_vector"}:
            value = f"tier{value}"
        parts.append(f"{axis['name']}={value}")
    return " ".join(parts)


def _make_body(facts, axes, partial, expect=None, overrides=None):
    if expect is None:
        expect = _harness_profile(facts)["expect_default_token"]
    selection, state, profile = _materialize(facts, axes, partial, overrides)
    valid = _row_is_valid(facts, state, profile)
    controls = {
        key: int(value) if key.startswith("null") and isinstance(value, bool) else value
        for key, value in (overrides or {}).items()
        if key.startswith("null") or key.endswith("_batch_pattern")
    }
    body = _body_mapping(facts, state, profile, expect, controls)
    return selection, state, profile, body, valid


def _l0_rows(facts, axes, report):
    op_axes = [axis for axis in axes if axis["kind"] == "op_enum"]
    profile_axes = [axis for axis in axes if axis["kind"] == "profile"]
    op_values = [axis["values"] for axis in op_axes]
    profile_values = profile_axes[0]["values"] if profile_axes else [None]
    combinations = itertools.product(*op_values) if op_values else [()]
    rows = []
    for op_values_row in combinations:
        op_partial = {
            axis["name"]: value for axis, value in zip(op_axes, op_values_row)
        }
        for profile_name in profile_values:
            for size in L0_SIZES:
                partial = dict(op_partial)
                if profile_name is not None:
                    partial["profile"] = profile_name
                for axis in axes:
                    if axis["kind"] in {"mat_dim", "vec_dim"}:
                        partial[axis["name"]] = size
                    elif axis["kind"] in {"batch_dim", "batch"}:
                        partial[axis["name"]] = 2
                selection, _, _, body, valid = _make_body(facts, axes, partial)
                if not valid:
                    report["rows_dropped"] += 1
                    continue
                description_axes = op_axes + profile_axes
                description = _description(description_axes, selection)
                suffix = f" size={size}" if description else f"size={size}"
                rows.append((description + suffix, body))
    return rows


def _pair_key(left_axis, left_value, right_axis, right_value):
    return left_axis, left_value, right_axis, right_value


def _selection_pairs(selection, variable_axes):
    pairs = set()
    for left in range(len(variable_axes)):
        for right in range(left + 1, len(variable_axes)):
            left_value = variable_axes[left]["values"].index(
                selection[variable_axes[left]["name"]]
            )
            right_value = variable_axes[right]["values"].index(
                selection[variable_axes[right]["name"]]
            )
            pairs.add(_pair_key(left, left_value, right, right_value))
    return pairs


def _candidate_for_seed(facts, axes, variable_axes, uncovered, seed, report):
    assigned = {seed[0]: seed[1], seed[2]: seed[3]}
    remaining = [index for index in range(len(variable_axes)) if index not in assigned]
    attempts = [0]

    def ordered_values(axis_index, current):
        scores = []
        for value_index in range(len(variable_axes[axis_index]["values"])):
            score = 0
            for other_axis, other_value in current.items():
                left, right = sorted((axis_index, other_axis))
                pair = (
                    left,
                    value_index if left == axis_index else other_value,
                    right,
                    other_value if right == other_axis else value_index,
                )
                score += pair in uncovered
            scores.append((-score, value_index))
        return [value for _, value in sorted(scores)]

    exhausted = [False]

    def search(position, current):
        if attempts[0] >= 2000:
            exhausted[0] = True
            return None
        if position == len(remaining):
            attempts[0] += 1
            partial = {
                axis["name"]: axis["values"][current[index]]
                for index, axis in enumerate(variable_axes)
            }
            selection, _, _, body, valid = _make_body(facts, axes, partial)
            if valid:
                return selection, body
            report["rows_dropped"] += 1
            return None
        axis_index = remaining[position]
        for value_index in ordered_values(axis_index, current):
            current[axis_index] = value_index
            result = search(position + 1, current)
            if result is not None:
                return result
        current.pop(axis_index, None)
        return None

    candidate = search(0, dict(assigned))
    if candidate is not None:
        return candidate, "found"
    return None, "search_exhausted" if exhausted[0] else "infeasible"


def _pairwise_rows(facts, axes, report):
    variable_axes = [axis for axis in axes if len(axis["values"]) > 1]
    if len(variable_axes) < 2:
        selection, _, _, body, valid = _make_body(facts, axes, {})
        report["pairs_total"] = 0
        report["pairs_covered"] = 0
        if not valid:
            report["rows_dropped"] += 1
            return []
        return [(_description(variable_axes, selection) or "baseline", body)]
    uncovered = set()
    for left in range(len(variable_axes)):
        for right in range(left + 1, len(variable_axes)):
            for left_value in range(len(variable_axes[left]["values"])):
                for right_value in range(len(variable_axes[right]["values"])):
                    uncovered.add(_pair_key(left, left_value, right, right_value))
    report["pairs_total"] = len(uncovered)
    rows = []
    while uncovered:
        seed = min(uncovered)
        candidate, outcome = _candidate_for_seed(
            facts, axes, variable_axes, uncovered, seed, report
        )
        if candidate is None:
            uncovered.remove(seed)
            left, left_value, right, right_value = seed
            pair = {
                "left": variable_axes[left]["name"],
                "left_value": variable_axes[left]["values"][left_value],
                "right": variable_axes[right]["name"],
                "right_value": variable_axes[right]["values"][right_value],
            }
            if outcome == "search_exhausted":
                # 搜索预算耗尽 ≠ 已证明不可行；不静默降级，直接失败。
                raise GeneratorError(f"pairwise 搜索预算耗尽，未能判定值对：{pair}")
            report["pairs_infeasible"].append(pair)
            continue
        selection, body = candidate
        covered = _selection_pairs(selection, variable_axes) & uncovered
        uncovered.difference_update(covered)
        rows.append((_description(variable_axes, selection), body))
    unresolved = len(report["pairs_infeasible"])
    report["pairs_covered"] = report["pairs_total"] - len(uncovered) - unresolved
    return rows


def _edge_rows(facts, axes):
    partial = {}
    for axis in axes:
        if axis["kind"] in {"mat_dim", "vec_dim"}:
            partial[axis["name"]] = ED_SIZE
        elif axis["kind"] in {"batch_dim", "batch"}:
            partial[axis["name"]] = 2
        elif axis["kind"] in {"ld", "stride"}:
            partial[axis["name"]] = "min"
    rows = []
    for edge in facts.get("edge_cases", []):
        _, _, _, body, _ = _make_body(
            facts,
            axes,
            partial,
            expect=edge["expect"],
            overrides=edge["set"],
        )
        rows.append((edge["name"], body))
    return rows


def _perf_rows(facts, axes, report):
    perf = facts.get("perf")
    if not perf:
        return []
    base = {}
    for axis in axes:
        if axis["kind"] in {"mat_dim", "vec_dim"}:
            base[axis["name"]] = ED_SIZE
        elif axis["kind"] in {"batch_dim", "batch"}:
            base[axis["name"]] = 2
        elif axis["kind"] in {"ld", "stride"}:
            base[axis["name"]] = "min"
    rows = []
    for perf_row in perf["rows"]:
        partial = dict(base)
        partial.update({key: perf_row[key] for key in perf["key"]})
        _, _, _, body, valid = _make_body(facts, axes, partial)
        if not valid:
            # 显式声明的 perf 行不能被静默丢；它是声明，不是"尽量生成"。
            raise GeneratorError(
                f"perf.rows 显式行不满足 constraints 或 footprint：{perf_row}"
            )
        description = "pf " + " ".join(
            f"{key}={perf_row[key]}" for key in perf["key"]
        )
        rows.append((description, body))
    if perf.get("sweep", False):
        matrix_axes = [axis for axis in axes if axis["kind"] == "mat_dim"]
        for size in PF_SWEEP_SIZES:
            partial = dict(base)
            for axis in matrix_axes:
                partial[axis["name"]] = size
            _, _, _, body, valid = _make_body(facts, axes, partial)
            if not valid:
                report["rows_dropped"] += 1
                break
            rows.append((f"pf sweep={size}", body))
    return rows


def _stage(name, function, *args):
    try:
        return function(*args)
    except GeneratorError:
        raise
    except Exception as exc:
        raise GeneratorError(f"{name}: {exc}") from exc


def _assemble_rows(facts, header, blocks, report):
    version = facts.get("schema_version")
    if version == 2:
        resolved = _resolved_profile(facts)
        seed_columns = resolved["seed_columns"]
        if len(seed_columns) > 1:
            # 多种子列（csrgeam2 形态）的写值策略未定，进场算子时裁定；fail-closed。
            raise GeneratorError(f"多种子列写值策略未定：{seed_columns}")
        description_column = resolved["description_column"]
    rows = []
    global_index = 0
    for block_name, block_rows in blocks:
        report["blocks"][block_name] = len(block_rows)
        for block_index, (description, body) in enumerate(block_rows, 1):
            global_index += 1
            body["case_name"] = f"TC_{block_name}_{block_index:03d}"
            if version == 2:
                if description_column is not None:
                    body[description_column] = description
                for seed_name in seed_columns:
                    body[seed_name] = SEED_BASE + global_index
            else:
                body["description"] = description
                body["random_seed"] = SEED_BASE + global_index
            rows.append([body[column] for column in header])
    return rows


def generate(facts):
    """生成确定性 CSV 表头、行和覆盖报告。"""
    axes = _stage("轴派生", build_axes, facts)
    report = {
        "axes": [{"name": axis["name"], "values": len(axis["values"])} for axis in axes],
        "pairs_total": 0,
        "pairs_covered": 0,
        "pairs_infeasible": [],
        "rows_dropped": 0,
        "blocks": {},
    }
    blocks = []
    for name, function, arguments in (
        ("L0", _l0_rows, (facts, axes, report)),
        ("PW", _pairwise_rows, (facts, axes, report)),
        ("ED", _edge_rows, (facts, axes)),
        ("PF", _perf_rows, (facts, axes, report)),
    ):
        blocks.append((name, _stage(name, function, *arguments)))
    header = _stage("表头投影", _header_columns, facts)
    rows = _stage("行合并", _assemble_rows, facts, header, blocks, report)
    return {"header": header, "rows": rows, "report": report}


def write_csv(path, header, rows):
    """使用 UTF-8、LF 和 QUOTE_MINIMAL 写出 CSV。"""
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def main():
    try:
        result = generate(FACTS)
        output = Path(__file__).resolve().parent / f"{FACTS['op']}_test.csv"
        _stage("写 CSV", write_csv, output, result["header"], result["rows"])
    except GeneratorError as exc:
        print(f"生成失败：{exc}", file=sys.stderr)
        return 2
    print(f"{output.name}: {len(result['rows'])} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
