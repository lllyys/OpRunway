"""两侧共用的 dtype 名称映射词表。"""


# ATK 的 dtype 名与工程声明里的 C 侧名字对不上，逐个映射。
# 表里没有的名字按字面去找，既不放行也不误拦。
DTYPE_SOURCE_ALIASES = {
    "fp16": ("FLOAT16", "HALF", "FP16"),
    "bf16": ("BFLOAT16", "BF16"),
    "fp32": ("FLOAT", "FLOAT32", "FP32"),
    "fp64": ("DOUBLE", "FLOAT64", "FP64"),
    "hf32": ("HIFLOAT32", "HF32"),
    "fp8e4m3": ("FLOAT8_E4M3", "FP8E4M3"),
    "fp8e5m2": ("FLOAT8_E5M2", "FP8E5M2"),
    "int8": ("INT8",),
    "uint8": ("UINT8",),
    "int16": ("INT16",),
    "uint16": ("UINT16",),
    "int32": ("INT32",),
    "uint32": ("UINT32",),
    "int64": ("INT64",),
    "uint64": ("UINT64",),
    "bool": ("BOOL",),
    "complex64": ("COMPLEX64",),
    "complex128": ("COMPLEX128",),
}
