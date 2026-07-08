#!/bin/bash
# =============================================================================
# OpRunway · AscendOpTest 桥 · 路线 B 真机去风险 orchestration
#
# 命题：证明 AscendOpTest 能驱动一个 catlass kernel 走完 data_gen→假exe→compare 精度闭环，
#       并能用 msprof op 采到 kernel-only 性能。载体 = catlass basic matmul（默认 43，arch 3510）。
#
# 在真机（如 ascend-a5）上、把本目录（route_b/）整体 rsync 过去后，从本目录运行。
# 全程只在用户目录内活动、绝不写共享 CANN 的 opp/vendors；不带 --build（避免 aclnngen 覆盖）。
#
# 用法：  bash run_derisk.sh <stage>
#   stage: env | build | stage | precision | perf | perf_e2e | all
#     perf     = optest kernel-only 采集（msprof op）
#     perf_e2e = optest 端到端采集（msprof --application，含 H2D/D2H；解析/裁窗归我们）
#   DRY_RUN=1 只打印命令不执行。
#
# 配置（env 覆盖，默认仅为常见值）：
#   OPRUNWAY_CATLASS   catlass 仓          默认 /home/lys/catlass
#   OPRUNWAY_AOT       AscendOpTest 仓     默认 /home/lys/AscendOpTest
#   OPRUNWAY_ARCH      catlass arch        默认 3510（Ascend950；A2/A3 用 2201）
#   OPRUNWAY_EXAMPLE   catlass 载体 example 默认 43_ascend950_basic_matmul
#   OPRUNWAY_EXAMPLE_SRC 载体源文件名       默认 basic_matmul_tla.cpp
#   OPRUNWAY_CONDA_SH  conda profile.d 路径 默认 /home/lys/miniconda3/etc/profile.d/conda.sh
#   OPRUNWAY_CONDA_ENV conda 环境名        默认 oprunway
#   OPRUNWAY_SETENV    CANN set_env.sh     默认 /usr/local/Ascend/ascend-toolkit/set_env.sh
#   OPRUNWAY_KERNEL    perf 的 msprof -k 符号（缺省不传，先跑一次读 OpBasicInfo.csv 拿真符号）
#   OPRUNWAY_CASE      case 名             默认 Test_001
# =============================================================================
set -o errexit
set -o nounset
set -o pipefail

# BRIDGE = 本目录（rsync 到真机后的部署位置）。可用 OPRUNWAY_BRIDGE 显式覆盖（便于 DRY_RUN 预览真机路径）。
BRIDGE="${OPRUNWAY_BRIDGE:-$(cd "$(dirname "$(realpath "$0")")" && pwd)}"

CATLASS_DIR="${OPRUNWAY_CATLASS:-/home/lys/catlass}"
AOT_DIR="${OPRUNWAY_AOT:-/home/lys/AscendOpTest}"
ARCH="${OPRUNWAY_ARCH:-3510}"
EXAMPLE="${OPRUNWAY_EXAMPLE:-43_ascend950_basic_matmul}"
EXAMPLE_SRC="${OPRUNWAY_EXAMPLE_SRC:-basic_matmul_tla.cpp}"
CONDA_SH="${OPRUNWAY_CONDA_SH:-/home/lys/miniconda3/etc/profile.d/conda.sh}"
CONDA_ENV="${OPRUNWAY_CONDA_ENV:-oprunway}"
SETENV="${OPRUNWAY_SETENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}"
KERNEL="${OPRUNWAY_KERNEL:-}"
CASE="${OPRUNWAY_CASE:-Test_001}"

EXE_NAME="execute_matmul_op"                 # 必须与 aclnn_op/CMakeLists.txt 里的 add_executable 名一致
ACLNN_DIR="aclnn_op"                         # run_test.py -a
CASES_RUN="matmul_cases.run.json"            # 注入 golden 绝对路径后的工作副本

run() { echo "+ $*"; if [ "${DRY_RUN:-0}" != "1" ]; then eval "$@"; fi; }

require_file() {
    local path="$1"
    if [ ! -f "$path" ]; then
        if [ "${DRY_RUN:-0}" = "1" ]; then
            echo "[dry-run warn] required file not found locally: $path" >&2
            return 0
        fi
        echo "[err] required file not found: $path" >&2
        exit 1
    fi
}

require_dir() {
    local path="$1"
    if [ ! -d "$path" ]; then
        if [ "${DRY_RUN:-0}" = "1" ]; then
            echo "[dry-run warn] required dir not found locally: $path" >&2
            return 0
        fi
        echo "[err] required dir not found: $path" >&2
        exit 1
    fi
}

write_cases_run() {
    local src="$BRIDGE/optest_cases/matmul_cases.json"
    local dst="$BRIDGE/$CASES_RUN"
    local golden="$BRIDGE/golden/matmul_golden.py"
    if [ "${DRY_RUN:-0}" = "1" ]; then
        echo "+ python - <golden-path-replacer> '$golden' '$src' '$dst'"
        return 0
    fi
    python - "$golden" "$src" "$dst" <<'PY'
from pathlib import Path
import sys

golden, src, dst = sys.argv[1:4]
text = Path(src).read_text(encoding="utf-8")
Path(dst).write_text(text.replace("__GOLDEN_PY__", golden), encoding="utf-8")
PY
}

stage_env() {
    echo "== env: 激活 CANN + conda =="
    run "source '$SETENV'"
    run "source '$CONDA_SH' && conda activate '$CONDA_ENV'"
    run "python -c 'import numpy,ml_dtypes;print(\"deps ok\",numpy.__version__)'"
    run "which aclnngen || echo '[warn] aclnngen 不在 PATH——首次跑 run_test.py 会尝试自装（用户 env 内，可接受）'"
}

stage_build() {
    echo "== build: 用 catlass build.sh 把假 exe 编成，放进 $ACLNN_DIR/build/$EXE_NAME =="
    local tgt_src="$CATLASS_DIR/examples/$EXAMPLE/$EXAMPLE_SRC"
    require_dir "$CATLASS_DIR"
    require_file "$CATLASS_DIR/scripts/build.sh"
    require_file "$tgt_src"
    require_file "$BRIDGE/fake_exe/oprunway_bridge_matmul.cpp"
    restore_example_src() {
        trap - RETURN
        if [ -f "$tgt_src.oprunway-orig" ]; then
            run "cp '$tgt_src.oprunway-orig' '$tgt_src'"
        fi
    }
    trap restore_example_src RETURN
    run "cp -n '$tgt_src' '$tgt_src.oprunway-orig' || true"          # 备份原 example 源（幂等）
    run "cp '$BRIDGE/fake_exe/oprunway_bridge_matmul.cpp' '$tgt_src'" # 换成假 exe（保持文件名/靶名，复用 43 CMake 注册）
    run "cd '$CATLASS_DIR' && bash scripts/build.sh '$EXAMPLE' -DCATLASS_ARCH=$ARCH"
    run "mkdir -p '$BRIDGE/$ACLNN_DIR/build'"
    run "cp '$CATLASS_DIR/output/bin/$EXAMPLE' '$BRIDGE/$ACLNN_DIR/build/$EXE_NAME'"
    run "chmod +x '$BRIDGE/$ACLNN_DIR/build/$EXE_NAME'"
    echo "   假 exe 就位：$BRIDGE/$ACLNN_DIR/build/$EXE_NAME"
}

stage_stage() {
    echo "== stage: 校对 exe 名、注入 golden 绝对路径 =="
    require_file "$BRIDGE/$ACLNN_DIR/CMakeLists.txt"
    require_file "$BRIDGE/optest_cases/matmul_cases.json"
    require_file "$BRIDGE/golden/matmul_golden.py"
    # 验证 get_exe_name() 会抠到正确的名字
    run "grep -m1 add_executable '$BRIDGE/$ACLNN_DIR/CMakeLists.txt'"
    # 把 case json 里的 __GOLDEN_PY__ 换成 golden 绝对路径，写工作副本
    write_cases_run
    run "test -x '$BRIDGE/$ACLNN_DIR/build/$EXE_NAME' && echo 'exe 可执行 ok' || echo '[err] 假 exe 未就位，先跑 build'"
}

# run_test.py 三选一触发 aclnngen（重生成覆盖工程）的条件必须全为假：
#   aclnn_dir 存在 ✓ + 不带 --build ✓ + aclnn_dir/build/<exe> 存在 ✓
_run_test() {
    local extra="$1"
    run "cd '$BRIDGE' && export OPRUNWAY_CASE_PATH='$BRIDGE' && \
        python -B '$AOT_DIR/run_test.py' -i '$BRIDGE/optest_cases/matmul_ir.json' \
        -c '$BRIDGE/$CASES_RUN' -n '$CASE' -a '$ACLNN_DIR' $extra"
}

stage_precision() {
    echo "== precision: data_gen→假exe→compare（不带 --build）=="
    _run_test ""
    echo "---- result.csv ----"
    run "cat '$BRIDGE/result.csv'"
    echo "Go 判据：该 case 的 compare_result 列 == pass"
}

stage_perf() {
    echo "== perf: msprof op（kernel-only）—— optest 的 kernel-only 采集通路 =="
    if [ -z "$KERNEL" ]; then
        echo "[提示] 未指定 OPRUNWAY_KERNEL：先跑一次 msprof op（-k 默认用 op_name，多半不精确命中），"
        echo "       跑完到 *_msprof_op/ 目录读 OpBasicInfo.csv 找 catlass 实际 demangled 符号，"
        echo "       再 OPRUNWAY_KERNEL=<符号substring> 重跑本 stage 精确命中。"
        _run_test "--msprof --op"
    else
        _run_test "--msprof --op -k '$KERNEL'"
    fi
    echo "产物：$BRIDGE 下 ${CASE}_*_msprof_op/ 里的 OpBasicInfo.csv（Task Duration us = kernel-only）"
}

stage_perf_e2e() {
    echo "== perf_e2e: msprof --application（端到端，含 H2D/D2H）—— optest 的 e2e 采集通路（不带 --op）=="
    # run_test.py:341 —— --msprof 不带 --op 即 application 模式，采整程 timeline。
    # optest 自带 get_prof.py 只解析 kernel-only OpBasicInfo.csv、不解析 application 产物 → 解析/裁窗归我们。
    _run_test "--msprof"
    echo "产物：$BRIDGE 下 ${CASE}_*_msprof_application/ 里的 msprof timeline（op_summary.csv 等，含 H2D/D2H）"
    echo "  ⚠ 假 exe 整程含 aclInit/读bin/WriteFile/aclFinalize —— 原始 e2e 被 init+文件IO 污染；"
    echo "     device-e2e 需自解析 op_summary.csv 并裁到「H2D→kernel→D2H」窗口（验收层做，本轮先证能采到 timeline）。"
    echo "  本轮 Go：application 目录生成、op_summary.csv 里能看到本算子的 H2D/D2H/kernel 三段。"
}

case "${1:-all}" in
    env)       stage_env ;;
    build)     stage_build ;;
    stage)     stage_stage ;;
    precision) stage_precision ;;
    perf)      stage_perf ;;
    perf_e2e)  stage_perf_e2e ;;
    all)       stage_build; stage_stage; stage_precision; stage_perf; stage_perf_e2e ;;
    *) echo "usage: bash run_derisk.sh <env|build|stage|precision|perf|perf_e2e|all>"; exit 1 ;;
esac
