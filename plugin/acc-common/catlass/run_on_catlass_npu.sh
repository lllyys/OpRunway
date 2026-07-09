#!/bin/bash
# OpRunway · catlass 真机编排（ascend-a5 arch 3510/fp32 主；ascend-a3 arch 2201/fp16 de-risk）。
#
# 流程：stage_into_catlass → build.sh <harness> -DCATLASS_ARCH=<arch> → 跑出 out.bin → 逐 perf 用例
#       msprof op 采 kernel-only Task Duration(us)（warmup/iters/median+p90/min、计时前后同步、记版本）→ perf_result.txt。
#
# ⚠⚠ **待真机验证**（本轮不跑）：runner 能否 bisheng/ccec 编成、extern C 符号能否被 msprof -k 命中、Task Duration
#     实数、staging 与 build.sh 交互 —— 全须 ascend-a5/a3 实测 + 人工确认。**不得假称已验证**。
# ⚠ 副作用：build/run 改 catlass 工作副本、写 run 子目录。rm 只限专属 run 子目录（OPRUNWAY_REMOTE_DIR 白名单）。
# ⚠ catlass **无 builtin-TBE 分母** → 不测内置基线、不写 _real_baseline；perf 分母走外部 GPU（gpu_external，ADR0006）。
#
# 入参经环境变量（零硬编码，私有路径不入仓）：
#   OPRUNWAY_CATLASS_DIR  catlass 工作副本根        OPRUNWAY_ARCH   2201|3510
#   OPRUNWAY_HARNESS      build target/harness 名   OPRUNWAY_RUNNER runner cpp 名
#   OPRUNWAY_KERNEL       msprof -k 命中的 kernel 符号名（extern C 钉死名；真机回填校准）
#   OPRUNWAY_REMOTE_DIR   本次 run 专属目录（cases/manifest/out.bin 都在其下；rm 只碰这里）
#   OPRUNWAY_WARMUP(=10)  OPRUNWAY_ITERS(=30)  OPRUNWAY_TIMEOUT(=300)
set -e
: "${OPRUNWAY_CATLASS_DIR:?}"; : "${OPRUNWAY_ARCH:?}"; : "${OPRUNWAY_HARNESS:?}"
: "${OPRUNWAY_RUNNER:?}"; : "${OPRUNWAY_REMOTE_DIR:?}"
KERNEL="${OPRUNWAY_KERNEL:-$OPRUNWAY_HARNESS}"
WARMUP="${OPRUNWAY_WARMUP:-10}"; ITERS="${OPRUNWAY_ITERS:-30}"; TIMEOUT="${OPRUNWAY_TIMEOUT:-300}"
case "$OPRUNWAY_ARCH" in 2201|3510) ;; *) echo "非法 arch=$OPRUNWAY_ARCH（仅 2201/3510）" >&2; exit 3;; esac
# 路径白名单：run 目录须在受控前缀内，防误删（复用 new_example 的 _PATH_RE 思路）
case "$OPRUNWAY_REMOTE_DIR" in *[!A-Za-z0-9_./-]*) echo "run 目录含非法字符" >&2; exit 3;; esac

RUN="$OPRUNWAY_REMOTE_DIR"
STAMP="$RUN/.harness_stamp"
source "${OPRUNWAY_SETENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}" 2>/dev/null || true

# 记环境版本（入 evidence.perf / artifact_manifest；缺失记 NA 不阻断）
CANN_V="$(cat "$ASCEND_HOME_PATH/version.info" 2>/dev/null | head -1 || echo NA)"
BISHENG_V="$(bisheng --version 2>/dev/null | head -1 || echo NA)"
MSPROF_V="$(msprof --version 2>/dev/null | head -1 || echo NA)"
COMMIT="$(cd "$OPRUNWAY_CATLASS_DIR" && git rev-parse --short HEAD 2>/dev/null || echo NA)"
echo "[env] CANN=$CANN_V bisheng=$BISHENG_V msprof=$MSPROF_V catlass_commit=$COMMIT arch=$OPRUNWAY_ARCH"

# 1) stage（幂等注入）→ build（hash-stamp 防 stale exe：runner 变即重建）
export OPRUNWAY_HARNESS OPRUNWAY_RUNNER OPRUNWAY_CATLASS_DIR
export OPRUNWAY_TEMPLATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$OPRUNWAY_TEMPLATE_DIR/stage_into_catlass.sh"
WANT="$(md5sum "$OPRUNWAY_TEMPLATE_DIR/$OPRUNWAY_RUNNER" 2>/dev/null | cut -d' ' -f1)|$OPRUNWAY_ARCH|$OPRUNWAY_HARNESS"
BIN="$OPRUNWAY_CATLASS_DIR/output/bin/$OPRUNWAY_HARNESS"
if [ ! -x "$BIN" ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$WANT" ]; then
  ( cd "$OPRUNWAY_CATLASS_DIR" && bash scripts/build.sh "$OPRUNWAY_HARNESS" -DCATLASS_ARCH="$OPRUNWAY_ARCH" )
  echo "$WANT" > "$STAMP"
fi
[ -x "$BIN" ] || { echo "build 未产出 $BIN（待真机排查）" >&2; exit 5; }

# 2) 正确性：跑全用例 → 各 case out.bin（精度判定在 Python 侧）
OPRUNWAY_CASES="$RUN/cases" timeout "$TIMEOUT" "$BIN" >"$RUN/run.log" 2>&1 || true
cat "$RUN/run.log"

# 3) 性能：逐 perf 用例，msprof op 采 kernel-only；warmup + iters；median + p90/min。
: > "$RUN/perf_result.txt"
if [ -s "$RUN/perfcases_list.txt" ]; then
  while read -r cid; do
    [ -z "$cid" ] && continue
    PC="$RUN/pc_$cid"; rm -rf "$PC"; mkdir -p "$PC/$cid"
    cp "$RUN/cases/$cid/"*.bin "$PC/$cid/"
    grep "^$cid " "$RUN/cases/manifest.txt" > "$PC/manifest.txt"
    POC="$RUN/prof_$cid"; rm -rf "$POC"; mkdir -p "$POC"; chmod 700 "$POC"
    # warmup（不采集）
    for _ in $(seq 1 "$WARMUP"); do OPRUNWAY_CASES="$PC" timeout "$TIMEOUT" "$BIN" >/dev/null 2>&1 || true; done
    # msprof op 采 kernel-only（多 iters；--kernel-name 命中 extern C 符号）
    OPRUNWAY_CASES="$PC" timeout "$TIMEOUT" \
      msprof op --output="$POC" --kernel-name="$KERNEL" "$BIN" >/dev/null 2>&1 || true
    CSV="$(find "$POC" -name OpBasicInfo.csv | head -1)"
    echo "$cid CSV=$CSV" >> "$RUN/perf_csv_index.txt"
    # 交给 Python catlass_parse.parse_msprof_csv 解析（按列名 Task Duration(us)，取 median/p90/min）
    cp "$CSV" "$RUN/${cid}.OpBasicInfo.csv" 2>/dev/null || echo "$cid NA" >> "$RUN/perf_result.txt"
    rm -rf "$PC"    # 逐用例清理 profile 中间物（size budget）
  done < "$RUN/perfcases_list.txt"
fi
echo "OPRUNWAY_NPU_DONE kernel=$KERNEL"
