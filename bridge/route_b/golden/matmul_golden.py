"""OpRunway · AscendOpTest 桥 · 路线 B 的 expect_func（basic matmul golden）。

AscendOpTest 的 golden/golden_gen/compute.py 会：
  - 按 case 的 input_desc 逐个 np.fromfile(dtype).reshape(shape) 加载输入（与假 exe 读的是同一份 bin）；
  - 调 custom_func(*input_list)，即 matmul_golden(x1, x2)；
  - 把返回的每个 numpy 数组 data.tofile 到 golden_<name>.bin。
返回值必须是「numpy 数组的 list」，顺序/dtype 与 output_desc 一致（此处 y: float32, (M,N)）。

口径：float32 累加，对齐 catlass CPU golden（examples/common/golden）。全 RowMajor，无转置。
"""

import numpy as np


def matmul_golden(x1, x2):
    # x1: (M, K) float32 RowMajor; x2: (K, N) float32 RowMajor
    y = (x1.astype(np.float32) @ x2.astype(np.float32)).astype(np.float32)
    return [y]
