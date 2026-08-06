"""OpRunway 精度 golden · GaussianBlur（ops-cv 仓根一级目录 `gaussian_blur`）——**同形输出 + OpenCV CPU 对标**形态。

引擎（`gen_cases.load_golden`）按算子从用户侧 `<ops_root>/<op>/golden.py` 加载。句式与诚实边界写法照
`plugin/samples/golden/IsClose/golden.py` / `UpsampleNearest3d/golden.py` 抄。

—— 为什么**不**导出 `out_shape` ——
`dst` 与 `src` 同 shape（`README.md:60`「输出图像，shape、数据类型和格式与 `src` 相同」；
`op_host/gaussian_blur_infershape.cpp` 逐维照抄输入），落契约 C1 的**缺省语义「输出同输入形状」**，
按 `load_golden` 的规定不必也不应导出 `out_shape`（导出反而多一处会漂的声明）。

—— 算子真实语义（**实读 PR 源码**，2026-08-03）——
· `op_host/gaussian_blur_def.cpp:30-33`：attr 名为 `ksize`(ListInt) / `sigma_x`(Float) / `sigma_y`(Float) /
  `border_type`(Int)；`src`/`dst` 均 `DT_FLOAT` + `FORMAT_ND`。本文件**按 op_def 名**取 attr。
· `README.md:31-33`：输入布局只有 `[H, W]`（单通道）与 `[H, W, C]`（通道在最后一维）两种，rank 必须 2 或 3。
· `README.md:57`：`ksize` 是 **`[kernelWidth, kernelHeight]`**——与 `op_api/aclnn_gaussian_blur.cpp:182`
  `CanonicalizeParams((*ksize)[0], (*ksize)[1], ...)`（形参序 `kernelW, kernelH`）一致。
  OpenCV 的 `ksize` 同样是 `(width, height)`，故 `(ksize[0], ksize[1])` **逐位直传、不换序**。
· `op_host/gaussian_blur_utils.h:79`：`sigmaY <= 0.0 ? sigmaX : sigmaY`；
  `:44-53`：`ksize` 某一维为 0 时按 `llround(sigma * 4.0 * 2.0 + 1.0) | 1` 反推核尺寸
  （`SIGMA_INFER_SCALE_FLOAT = 4.0`，见 `:27`）。
  这两条与 OpenCV `createGaussianKernels` 的 `if (sigma2 <= 0) sigma2 = sigma1;` /
  `cvRound(sigma*4*2 + 1) | 1`（非 CV_8U 深度）**语义同款** —— ⚠ 这一句是**据 OpenCV 已知实现的推断**，
  本机无 cv2、未复核源码；`cvRound`（半值取偶）与 `llround`（半值远离零）在 `sigma*8+1` 恰为 .5 时可分歧，
  属**已知残余差异**，不声称逐位一致。
· `op_host/gaussian_blur_utils.h:21-26`：`BORDER_CONSTANT=0 / REPLICATE=1 / REFLECT=2 / REFLECT_101=4`，
  `BORDER_ISOLATED=16`；`README.md:78`：不支持 `BORDER_WRAP`(3)、`BORDER_ISOLATED` 及其它未列出模式。
  这几个数值与 OpenCV 的 `cv2.BORDER_*` **完全同值**，故 `border_type` **直传 cv2、不建映射表**
  （建表反而要维护一份可能与 DUT 漂移的第二真源）。

后端（ADR 0011 决策 4 · R6「生成期选型并写死」）：生成期选定 **OpenCV CPU**，这不是可换的偏好——
任务书把功能比对标杆写成 OpenCV（用户口径 P2：**只对 OpenCV CPU**）。运行时不兜底：cv2 缺失即
fail-closed（确定性红线），**不静默换 torch/numpy 自拼一个高斯核**（那是无授权多步自拼，按
`derive_golden_tier` 规则 ⑧ 直接 tier 4 blocked）。

⚠ **本文件措辞会被后续 agent 照抄** —— `GOLDEN_PROVENANCE` 必须逐字属实。
"""
# ⚠ 顶层只 import stdlib（本文件顶层其实一个 import 都不需要）：`check_golden.py` 不带 `--load` 时
#    会**执行整个本文件**只为取 `GOLDEN_CONTRACT`，此时不该被 cv2/numpy 的安装状态卡住。
#    cv2 延迟到 `_require_cv2()`、numpy 延迟到 `golden_fn()` 内，同手册 §3 骨架。

GOLDEN_SOURCE = "external_ref cv2.GaussianBlur (OpenCV CPU 4.11.0)"
# 首 token `external_ref` → `oracle_source_from_golden` 直接命中六枚举，且在 `PRODUCIBLE_ORACLE_SOURCES` 内。
# ⚠ 借 `external_ref` 是**有意的折中**（计划 §2.2 D7）：六枚举里没有「第三方 CPU 库现算」这一格，
#    `cpu_ref` 的语义是「仓/PR 的 CPU 参考」，按 R2 禁产。账本上读起来像「外部给了一份数据」，
#    **实际是本机 cv2 现算**，真实出处只落在本串的自由文本里 —— 如实记在这，不新增第七个枚举
#    （那是 canonical 契约，牵动 gate 与多处对账）。
# ⚠ `4.11.0` 是**验收容器 2026-08-03 实测的 cv2.__version__**，不是机校收据（计划 §2.2 D9）：
#    `GOLDEN_CONTRACT` 没有第三方库版本槽位，本文件只做 `_MIN_CV2_VERSION` 最低版本断言。
#    任务书要求的「同版本 OpenCV」**未被机器核实**，报告不得声称已核。

GOLDEN_CONTRACT = {
    "source": "single_api",              # 一个现成 API（cv2.GaussianBlur）直出，非多步自拼
    "method_kind": "opencv_cpu",         # R3 第二档的可跑方法族之一（CPU 上的 OpenCV）
    "method": "cv2.GaussianBlur",        # 人读：到底调的哪个 API
    "authorization": {
        # ⚠ 这里**故意填 none 而不是 oracle_method**，理由必须看清楚：
        #   任务书确实就「拿谁当标杆」作了指定（§4「功能比对以 OpenCV CPU（同版本）为标杆」、
        #   §6 表格「CV_32F（L1）→ 对标 OpenCV CPU」），字面上够得着 oracle_method；
        #   但 R12 要求授权锚可机核 —— `verify_authorization` 要读同目录 `task_doc.snapshot.md`
        #   并逐字比对 cite 行区间。**本轮没有把任务书全文快照入库**，锚无从核实。
        #   声称 oracle_method 就必须填 `taskdoc_snapshot.sha256`，而填一个未经核实的哈希是捏造（5.8）；
        #   `derive_golden_tier` 规则 ② 也会把「声称有授权却核不实」直接判 tier 4 blocked。
        #   故按 fail-safe **下调**为无授权 → 规则 ⑦（none + single_api）→ tier 2、不需人核、可往下跑。
        #   快照入库后应由**人**改成 oracle_method + cite/quote/sha256 并重跑 `check_golden.py GaussianBlur`。
        "kind": "none",
    },
}

GOLDEN_PROVENANCE = (
    "第二档（tier 2）·本轮按**无授权**处理（authorization.kind=none）"
    "——GaussianBlur 任务书确有「功能比对以 OpenCV CPU（同版本）为标杆」与「CV_32F（L1）→ 对标 OpenCV CPU」，"
    "字面上够得着 oracle_method；但本轮**未把任务书全文快照入库**（同目录无 task_doc.snapshot.md），"
    "引文锚无从机核，宁可下调为无授权，也不填未经核实的 taskdoc_snapshot.sha256（R12 / 5.8）"
    "→ CPU 现成 API cv2.GaussianBlur；单 API 单调、按 R5 不需人核。"
    "边界一：**只算 OpenCV CPU 真值**。任务书 §6 另有 OpenCV GPU 口径，本轮按用户口径 P2 未实现、"
    "**未验证**——cv::GaussianBlur 的 CPU 与 CUDA 实现并非逐位一致，不得据本文件声称已对标 GPU。"
    "边界二：本文件**不声称与 DUT 逐位一致**。已知残余差异至少两处："
    "① 核尺寸反推 DUT 用 llround（半值远离零）、OpenCV 用 cvRound（半值取偶）；"
    "② DUT 是两趟 separable 卷积（README「先水平后垂直」），OpenCV sepFilter2D 的累加顺序与中间精度未逐一核对。"
    "边界三：`[H, W, 1]` 输入 cv2 会把末轴挤掉（返回 `[H, W]`），而 NPU 输出恒与输入同形，"
    "本文件按 `src.shape` 显式补回该轴；这条只在 C==1 时触发。"
    "边界四：inf/-inf/nan 用例由 `gen_cases._special_entries` 无条件注入（operator_class=floating_compute），"
    "任务书 CV_32F 口径并未要求这些语义；5×5 核会把整行污染，OpenCV 与 NPU 两趟 separable 的 inf−inf 顺序差异"
    "足以产生不可比结果 —— 本文件照直算、不特判，**报告须单列这几条**，不得据其归因 DUT。"
    "边界五：空 Tensor fail-closed（README「所有维度必须大于 0」），spec 应设 allow_empty_tensor:false。"
    "边界六：OpenCV 版本未机校（GOLDEN_CONTRACT 无版本槽位），只做最低版本断言，"
    "任务书要求的「同版本 OpenCV」**未被核实**。"
)

# 最低版本：本文件依赖的 `cv2.GaussianBlur` 语义（sigma<=0 取另一维、ksize=0 按 sigma 反推、
# BORDER_* 数值编码）在 OpenCV 4.x 内稳定。4.x 以下未核，直接拒。
_MIN_CV2_VERSION = (4, 0, 0)

_RANK_MIN, _RANK_MAX = 2, 3          # README:31-33「rank 必须为 2 或 3」（[H,W] / [H,W,C]）
_KSIZE_LEN = 2                       # README:78「`ksize` 必须包含两个元素」


def _parse_version(text):
    """把 `cv2.__version__`（如 "4.11.0" / "4.11.0-dev"）解析成 (major, minor, patch)。

    只用 stdlib、只取前导数字段；解析不出的段按 0 记（不猜、不抬高）。"""
    parts = []
    for tok in str(text or "").split(".")[:3]:
        digits = ""
        for ch in tok:
            if not ch.isdigit():
                break
            digits += ch
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _require_cv2():
    """延迟 import cv2 + 最低版本断言。缺失/损坏/版本过低一律要求安装，**不静默兜底**。"""
    try:
        import cv2
    except Exception as e:                 # noqa: BLE001 —— 缺失/损坏一律要求安装、不静默兜底
        raise RuntimeError(
            "golden 需 OpenCV(CPU) 作真值标杆、但未安装/不可用。请装 CPU 版："
            "pip install opencv-python-headless。"
            "不静默回退——确定性红线（ADR 0011 决策 4）；本算子尤其不可换 torch/numpy 自拼高斯核"
            "（无授权 + 多步自拼 = derive_golden_tier 规则 ⑧ 判 tier 4 blocked）。") from e
    ver = getattr(cv2, "__version__", "")
    if _parse_version(ver) < _MIN_CV2_VERSION:
        raise RuntimeError(
            f"GaussianBlur golden: cv2.__version__={ver!r} 低于最低要求 "
            f"{'.'.join(str(v) for v in _MIN_CV2_VERSION)}——本文件依赖的 GaussianBlur 语义"
            f"（sigma<=0 取另一维 / ksize=0 按 sigma 反推 / BORDER_* 编码）只在 OpenCV 4.x 上核过，"
            f"更早版本未核，fail-closed。")
    return cv2


def _attr(attrs, *names):
    """按**op_def 名优先**取一个必填 attr；四个 attr 在 op_def 里都是 REQUIRED，缺了就 fail-closed。

    收多个别名只是为了容忍 spec 写 aclnn 侧的驼峰名（`sigmaX`）而非 op_def 的蛇形名（`sigma_x`），
    **不提供任何默认值**——golden 不替 spec 猜属性（同 Im2col/UpsampleNearest3d 的规矩）。"""
    for n in names:
        if n in attrs:
            return attrs[n]
    raise ValueError(
        f"GaussianBlur golden: 缺属性 {names[0]!r}（可接受的别名 {list(names)}）——"
        f"须由 spec 的 params[io=attr].default / attr_matrix 给出；op_def 里四个 attr 全是 REQUIRED，"
        f"golden 不擅自补默认值")


def _as_int(v, where):
    """整数取值。**不 import numpy** 也要能收 numpy 标量，故用「转得成 int 且不丢值」鸭子判定，
    而不是 `isinstance(v, int)`（那会把 np.int64 判非法，表现为整轮一条 case 都不出）。
    bool 单独拒（Python 里 True 是 int，但 attr 位置上它一定是写错了）。"""
    if isinstance(v, bool):
        raise ValueError(f"GaussianBlur golden: {where}={v!r} 是 bool，须为整数")
    try:
        i = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"GaussianBlur golden: {where}={v!r} 不是整数")
    if i != v:
        raise ValueError(f"GaussianBlur golden: {where}={v!r} 不是整数（转 int 会丢值）")
    return i


def _ksize(attrs):
    """取并校 `ksize`，返回 `(width, height)` —— 与 cv2 的 `ksize` 同序，直传。"""
    v = _attr(attrs, "ksize")
    if not isinstance(v, (list, tuple)) or len(v) != _KSIZE_LEN:
        raise ValueError(
            f"GaussianBlur golden: attr ksize={v!r} 非法——须为长度 {_KSIZE_LEN} 的 int 列表 "
            f"[kernelWidth, kernelHeight]（README「`ksize` 必须包含两个元素」）")
    out = []
    for e in v:
        x = _as_int(e, f"attr ksize={v!r} 的元素")
        if x < 0 or (x != 0 and x % 2 == 0):
            raise ValueError(
                f"GaussianBlur golden: attr ksize={v!r} 含非法值 {x}——"
                f"显式核尺寸必须为正奇数，0 表示按对应 sigma 反推"
                f"（`op_host/gaussian_blur_utils.h` IsExplicitKernelSizeValid 同款；OpenCV 亦断言奇数）")
        out.append(x)
    return (out[0], out[1])


def _sigma(attrs, *names):
    """取一个 sigma 并转 float（op_def 声明为 Float，aclnn 签名是 double；cv2 收 double）。"""
    v = _attr(attrs, *names)
    if isinstance(v, bool):
        raise ValueError(f"GaussianBlur golden: attr {names[0]}={v!r} 是 bool，须为数值")
    try:
        return float(v)                    # 鸭子判定，同 `_as_int`：不 import numpy 也收 numpy 标量
    except (TypeError, ValueError):
        raise ValueError(f"GaussianBlur golden: attr {names[0]}={v!r} 须为数值")


def _border_type(attrs):
    """取 `border_type` 并**原值直传** cv2。

    PR 的编码（0=CONSTANT / 1=REPLICATE / 2=REFLECT / 4=REFLECT_101 / 16=ISOLATED，
    见 `op_host/gaussian_blur_utils.h:21-26`）与 OpenCV 的 `cv2.BORDER_*` 完全同值，**不做映射**。
    合法性由两侧各自把关：DUT 侧 `CanonicalizeBorderType` 会拒 WRAP(3) 与带 ISOLATED 位的值，
    cv2 侧自会对不认识的值报错——本文件不复述那张表（复述 = 第二真源，会漂）。"""
    v = _attr(attrs, "border_type", "borderType")
    return _as_int(v, "attr border_type")


def _check_in_shape(shape):
    shape = tuple(int(d) for d in shape)
    if not (_RANK_MIN <= len(shape) <= _RANK_MAX):
        raise ValueError(
            f"GaussianBlur golden: 输入 rank={len(shape)}（shape={shape}），"
            f"但该算子只支持 rank {_RANK_MIN}（[H,W]）或 {_RANK_MAX}（[H,W,C]）"
            f"（README「`src` rank 必须为 2 或 3」）")
    if any(d == 0 for d in shape):
        raise ValueError(
            f"GaussianBlur golden: 输入含 0 维（shape={shape}）——README「所有维度必须大于 0」，"
            f"高斯卷积在空轴上无源像素可取、**无真值可言**，fail-closed。"
            f"spec 应设 allow_empty_tensor:false 把这条用例关掉，"
            f"而不是靠 golden 编一个假输出绕过去")
    return shape


def golden_fn(inputs, attrs):
    """`cv2.GaussianBlur(src, (ksize[0], ksize[1]), sigmaX, sigmaY, borderType)` 单调直出。

    输出恒与 `src` **同 shape 同 dtype**（README「输出图像，shape、数据类型和格式与 `src` 相同」）。
    """
    import numpy as np                     # 延迟 import，理由见文件头注释
    cv2 = _require_cv2()

    x = np.ascontiguousarray(inputs[0])
    src_shape = _check_in_shape(x.shape)
    kw, kh = _ksize(attrs)
    sigma_x = _sigma(attrs, "sigma_x", "sigmaX")
    sigma_y = _sigma(attrs, "sigma_y", "sigmaY")
    border = _border_type(attrs)

    y = cv2.GaussianBlur(x, (kw, kh), sigmaX=sigma_x, sigmaY=sigma_y, borderType=border)

    # ⚠ **整轮最容易全灭的一处**：cv2 把 `[H, W, 1]` 当单通道 Mat，返回时末轴被挤掉（(256,128,1) → (256,128)）。
    #    NPU 的 dst 恒与 src 同 shape，形状对不上 → gen_cases 当场 fail-closed，**整轮一条 case 都不出**。
    #    只在 C==1 时触发。这里按 src.shape 显式补回；元素数不等才是真出了事，那种情况必须炸。
    if tuple(y.shape) != src_shape:
        if int(y.size) != int(x.size):
            raise ValueError(
                f"GaussianBlur golden: cv2 返回 shape={tuple(y.shape)}（{y.size} 元素）与输入 "
                f"shape={src_shape}（{x.size} 元素）元素数不等——不是已知的「C==1 末轴被挤掉」那种情况，"
                f"不猜、不 reshape，fail-closed")
        y = y.reshape(src_shape)
    # dtype 同样按 src 对齐：cv2 对 fp32 输入返回 fp32，这里只是把「同 dtype」这条契约钉死，不做隐式提升。
    return np.ascontiguousarray(y.astype(x.dtype, copy=False))
