#!/bin/bash
# OpRunway · catlass 真机编排（ascend-a5 arch 3510/fp32 主；ascend-a3 arch 2201/fp16 de-risk）。
#
# ⚠ 就地运行于 **NPU host**（非从 Mac ssh/scp 远端编排；文件名的 "remote" 指本机是那台远端 NPU）。
#
# 流程：stage_into_catlass → build.sh <harness> -DCATLASS_ARCH=<arch> → 跑出 out.bin → 逐 perf 用例
#       msprof op 采 kernel-only Task Duration(us) → 逐 case OpBasicInfo.csv → 交 Python catlass_parse 统计。
#
# ⚠⚠ **待真机验证**（本轮不跑）：runner 能否 bisheng/ccec 编成、extern C 符号能否被 msprof -k 命中、Task Duration
#     实数、staging 与 build.sh 交互 —— 全须 ascend-a5/a3 实测 + 人工确认。**不得假称已验证**。
# ⚠ 副作用先确认（CLAUDE.md #1/#3）：build/run/msprof 改 catlass 工作副本、写 run 子目录 → **须显式
#    OPRUNWAY_CATLASS_REAL=1 opt-in**，否则 fail-fast、零副作用。rm 只限受控前缀内的专属 run 子目录。
# ⚠ 假通过零容忍：BIN/msprof 关键步失败一律非零退出并打印 OPRUNWAY_NPU_FAILED，**绝不 || true 吞失败后照报 DONE**。
# ⚠ catlass **无 builtin-TBE 分母** → 不测内置基线、不写 _real_baseline；perf 分母走外部 GPU（gpu_external，ADR0006）。
#
# 入参经环境变量（零硬编码，私有路径不入仓）：
#   OPRUNWAY_CATLASS_REAL 必须 =1（副作用 opt-in 门）
#   OPRUNWAY_CATLASS_DIR  catlass 工作副本根（绝对路径、非 symlink、含 scripts/build.sh + examples/）
#   OPRUNWAY_ARCH         2201|3510                OPRUNWAY_HARNESS build target/harness 名（[A-Za-z0-9_]）
#   OPRUNWAY_RUNNER       runner cpp 名            OPRUNWAY_KERNEL  msprof -k 命中的 kernel 符号（[A-Za-z0-9_]）
#   OPRUNWAY_REMOTE_DIR   本次 run 专属目录（绝对、非 symlink、须落在 OPRUNWAY_REMOTE_ROOT|$HOME 前缀内；rm 只碰这里）
#   OPRUNWAY_REMOTE_ROOT  受控前缀根（可选；缺省 $HOME）
#   OPRUNWAY_SETENV       set_env.sh（可选；须绝对路径、普通文件、非 symlink）
#   OPRUNWAY_WARMUP(=10)  OPRUNWAY_ITERS(=30)  OPRUNWAY_TIMEOUT(=300)（均须非负整数）
set -euo pipefail

fail() { echo "OPRUNWAY_NPU_FAILED: $1" >&2; exit "${2:-6}"; }

# ── 副作用 opt-in 门（codex #7）─────────────────────────────────────────────────────────
if [ "${OPRUNWAY_CATLASS_REAL:-}" != "1" ]; then
  echo "[run_on] 拒绝：本脚本在 NPU host 就地跑 build/BIN/msprof（真机副作用），须显式 OPRUNWAY_CATLASS_REAL=1 opt-in 并人工确认。" >&2
  exit 2
fi

: "${OPRUNWAY_CATLASS_DIR:?}"; : "${OPRUNWAY_ARCH:?}"; : "${OPRUNWAY_HARNESS:?}"
: "${OPRUNWAY_RUNNER:?}"; : "${OPRUNWAY_REMOTE_DIR:?}"
KERNEL="${OPRUNWAY_KERNEL:-$OPRUNWAY_HARNESS}"
WARMUP="${OPRUNWAY_WARMUP:-10}"; ITERS="${OPRUNWAY_ITERS:-30}"; TIMEOUT="${OPRUNWAY_TIMEOUT:-300}"

# ── 白名单 / 数值校验（codex #10/#12）──────────────────────────────────────────────────
case "$OPRUNWAY_ARCH" in 2201|3510) ;; *) fail "非法 arch=${OPRUNWAY_ARCH}（仅 2201/3510）" 3;; esac
case "$OPRUNWAY_HARNESS" in *[!A-Za-z0-9_]*) fail "非法 harness 名（仅 A-Za-z0-9_）" 3;; esac
case "$KERNEL" in ""|*[!A-Za-z0-9_]*) fail "非法 kernel 符号名（仅 A-Za-z0-9_）：$KERNEL" 3;; esac
case "$OPRUNWAY_RUNNER" in
  .|..) fail "非法 runner 名（. / ..）" 3;;
  .*)   fail "非法 runner 名（隐藏名）" 3;;
  *[!A-Za-z0-9_.]*) fail "非法 runner 名（仅 A-Za-z0-9_.）" 3;;
esac
case "$WARMUP"  in ''|*[!0-9]*) fail "OPRUNWAY_WARMUP 非非负整数：$WARMUP" 3;; esac
case "$ITERS"   in ''|*[!0-9]*) fail "OPRUNWAY_ITERS 非非负整数：$ITERS" 3;; esac
case "$TIMEOUT" in ''|*[!0-9]*) fail "OPRUNWAY_TIMEOUT 非非负整数：$TIMEOUT" 3;; esac

# ── OPRUNWAY_CATLASS_DIR 强校验（codex #11/#2）：绝对 + 拒 symlink + realpath + catlass 特征 ──
case "$OPRUNWAY_CATLASS_DIR" in /*) ;; *) fail "OPRUNWAY_CATLASS_DIR 须绝对路径：$OPRUNWAY_CATLASS_DIR" 3;; esac
[ -L "$OPRUNWAY_CATLASS_DIR" ] && fail "OPRUNWAY_CATLASS_DIR 是 symlink，拒绝：$OPRUNWAY_CATLASS_DIR" 3
[ -d "$OPRUNWAY_CATLASS_DIR" ] || fail "OPRUNWAY_CATLASS_DIR 不存在/非目录：$OPRUNWAY_CATLASS_DIR" 3
CATLASS_DIR="$(realpath -- "$OPRUNWAY_CATLASS_DIR" 2>/dev/null)" || fail "OPRUNWAY_CATLASS_DIR realpath 失败" 3
[ -f "$CATLASS_DIR/scripts/build.sh" ] || fail "非 catlass 根：缺 scripts/build.sh（${CATLASS_DIR}）" 3
[ -d "$CATLASS_DIR/examples" ] || fail "非 catlass 根：缺 examples/（${CATLASS_DIR}）" 3

# ── OPRUNWAY_REMOTE_DIR 强校验（codex #9）：绝对 + 拒 .. + 拒 symlink + 落在受控前缀内 + 非前缀根本身 ──
case "$OPRUNWAY_REMOTE_DIR" in *[!A-Za-z0-9_./-]*) fail "OPRUNWAY_REMOTE_DIR 含非法字符" 3;; esac
case "$OPRUNWAY_REMOTE_DIR" in /*) ;; *) fail "OPRUNWAY_REMOTE_DIR 须绝对路径：$OPRUNWAY_REMOTE_DIR" 3;; esac
case "$OPRUNWAY_REMOTE_DIR" in *..*) fail "OPRUNWAY_REMOTE_DIR 含 .. 穿越：$OPRUNWAY_REMOTE_DIR" 3;; esac
[ -L "$OPRUNWAY_REMOTE_DIR" ] && fail "OPRUNWAY_REMOTE_DIR 是 symlink，拒绝：$OPRUNWAY_REMOTE_DIR" 3
[ -d "$OPRUNWAY_REMOTE_DIR" ] || fail "OPRUNWAY_REMOTE_DIR 不存在/非目录（须先部署 run 目录）：$OPRUNWAY_REMOTE_DIR" 3
RUN="$(realpath -- "$OPRUNWAY_REMOTE_DIR" 2>/dev/null)" || fail "OPRUNWAY_REMOTE_DIR realpath 失败" 3
ROOT_RAW="${OPRUNWAY_REMOTE_ROOT:-${HOME:-}}"
[ -n "$ROOT_RAW" ] || fail "无受控前缀根：请设 OPRUNWAY_REMOTE_ROOT 或 HOME" 3
ROOT="$(realpath -- "$ROOT_RAW" 2>/dev/null)" || fail "受控前缀根 realpath 失败：$ROOT_RAW" 3
case "$RUN/" in
  "$ROOT"/*) ;;
  *) fail "OPRUNWAY_REMOTE_DIR=$RUN 不在受控前缀 $ROOT 内（设 OPRUNWAY_REMOTE_ROOT 放宽）" 3;;
esac
[ "$RUN" = "$ROOT" ] && fail "OPRUNWAY_REMOTE_DIR 不得等于受控前缀根本身（须为其子目录）" 3
STAMP="$RUN/.harness_stamp"

# ── OPRUNWAY_SETENV 校验后再 source（codex #11）：绝对路径 + 普通文件 + 非 symlink ──────────
SETENV="${OPRUNWAY_SETENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}"
case "$SETENV" in /*) ;; *) fail "OPRUNWAY_SETENV 须绝对路径：$SETENV" 3;; esac
[ -L "$SETENV" ] && fail "OPRUNWAY_SETENV 是 symlink，拒绝 source：$SETENV" 3
if [ -f "$SETENV" ]; then
  set +u +e; source "$SETENV" 2>/dev/null || true; set -u -e
else
  echo "[env] setenv 缺失或非普通文件（${SETENV}），跳过 source" >&2
fi

# 记环境版本（入 evidence.perf / artifact_manifest；缺失记 NA 不阻断）
CANN_V="$(cat "${ASCEND_HOME_PATH:-}/version.info" 2>/dev/null | head -1 || echo NA)"
BISHENG_V="$(bisheng --version 2>/dev/null | head -1 || echo NA)"
MSPROF_V="$(msprof --version 2>/dev/null | head -1 || echo NA)"
COMMIT="$(cd "$CATLASS_DIR" && git rev-parse --short HEAD 2>/dev/null || echo NA)"
echo "[env] CANN=$CANN_V bisheng=$BISHENG_V msprof=$MSPROF_V catlass_commit=$COMMIT arch=$OPRUNWAY_ARCH"

# 1) stage（幂等注入）→ build（hash-stamp 防 stale exe：runner 变即重建）
export OPRUNWAY_CATLASS_REAL          # 把 opt-in 传递给 stage 子脚本（其入口同样 fail-fast）
export OPRUNWAY_HARNESS OPRUNWAY_RUNNER
export OPRUNWAY_CATLASS_DIR="$CATLASS_DIR"    # 传校验后的 realpath
export OPRUNWAY_TEMPLATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$OPRUNWAY_TEMPLATE_DIR/stage_into_catlass.sh"
RUNNER_MD5="$(md5sum "$OPRUNWAY_TEMPLATE_DIR/$OPRUNWAY_RUNNER" 2>/dev/null | cut -d' ' -f1 || true)"
WANT="${RUNNER_MD5}|$OPRUNWAY_ARCH|$OPRUNWAY_HARNESS"
BIN="$CATLASS_DIR/output/bin/$OPRUNWAY_HARNESS"
if [ ! -x "$BIN" ] || [ "$(cat "$STAMP" 2>/dev/null || true)" != "$WANT" ]; then
  ( cd "$CATLASS_DIR" && bash scripts/build.sh "$OPRUNWAY_HARNESS" -DCATLASS_ARCH="$OPRUNWAY_ARCH" )
  printf '%s\n' "$WANT" > "$STAMP"
fi
[ -x "$BIN" ] || fail "build 未产出可执行 ${BIN}（待真机排查）" 5

# 2) 正确性：跑全用例 → 各 case out.bin（精度判定在 Python 侧）。
#    **关键步**：BIN 非零退出或缺完成信号 = 失败，立即 FAILED，绝不 || true 吞后照报 DONE（codex #8）。
rc=0
OPRUNWAY_CASES="$RUN/cases" timeout -- "$TIMEOUT" "$BIN" >"$RUN/run.log" 2>&1 || rc=$?
cat "$RUN/run.log" || true
[ "$rc" -eq 0 ] || fail "被测 BIN 退出码 ${rc}（run 失败，不宣布完成；见 $RUN/run.log）" 6
grep -q "OPRUNWAY_CATLASS_DONE" "$RUN/run.log" || fail "run.log 缺完成信号 OPRUNWAY_CATLASS_DONE（BIN 未正常跑完）" 6

# 3) 性能：逐 perf 用例，msprof op 采 kernel-only。
#    warmup=WARMUP 次（不采集，尽力而为）；msprof op **单次采集**，median/p90/min 由下游 catlass_parse 从 CSV 行统计
#    （本脚本不在 shell 层按 iters 循环采样——不声称没做的事，codex #13）。OPRUNWAY_ITERS 透传给 runner（若其内部
#    按 iters 重复 launch 则生效）。CSV 缺失/未命中 kernel = 采集失败 → PERF_FAIL，末尾 FAILED 不报 DONE（codex #8）。
: > "$RUN/perf_result.txt"
: > "$RUN/perf_csv_index.txt"
PERF_FAIL=0
if [ -s "$RUN/perfcases_list.txt" ]; then
  while IFS= read -r cid; do
    [ -z "$cid" ] && continue
    case "$cid" in *[!A-Za-z0-9_]*) fail "perfcases_list 含非法 cid=${cid}（仅 A-Za-z0-9_）" 3;; esac
    PC="$RUN/pc_$cid"; POC="$RUN/prof_$cid"
    rm -rf -- "$PC" "$POC"
    mkdir -p -- "$PC/$cid" "$POC"; chmod 700 "$POC"   # POC 恒绝对路径（$RUN/prof_$cid），无需 --（且 BSD chmod 不认 --）
    if ! cp -- "$RUN/cases/$cid/"*.bin "$PC/$cid/" 2>/dev/null; then
      echo "$cid NA(缺 .bin)" >> "$RUN/perf_result.txt"; PERF_FAIL=1; rm -rf -- "$PC" "$POC"; continue
    fi
    awk -v c="$cid" '$1==c' "$RUN/cases/manifest.txt" > "$PC/manifest.txt" || true
    # warmup（不采集，尽力而为）
    i=0
    while [ "$i" -lt "$WARMUP" ]; do
      OPRUNWAY_CASES="$PC" OPRUNWAY_ITERS="$ITERS" timeout -- "$TIMEOUT" "$BIN" >/dev/null 2>&1 || true
      i=$((i + 1))
    done
    # msprof op 采 kernel-only（--kernel-name 命中 extern C 符号）
    mrc=0
    OPRUNWAY_CASES="$PC" OPRUNWAY_ITERS="$ITERS" timeout -- "$TIMEOUT" \
      msprof op --output="$POC" --kernel-name="$KERNEL" "$BIN" >/dev/null 2>&1 || mrc=$?
    CSV="$(find "$POC" -name OpBasicInfo.csv -type f 2>/dev/null | head -1 || true)"
    echo "$cid CSV=${CSV:-NA}" >> "$RUN/perf_csv_index.txt"
    if [ "$mrc" -ne 0 ] || [ -z "$CSV" ] || [ ! -f "$CSV" ]; then
      echo "$cid NA(msprof rc=$mrc csv=${CSV:-none})" >> "$RUN/perf_result.txt"; PERF_FAIL=1
    elif ! grep -Fq -- "$KERNEL" "$CSV"; then
      echo "$cid NA(CSV 未命中 kernel $KERNEL)" >> "$RUN/perf_result.txt"; PERF_FAIL=1
    else
      cp -- "$CSV" "$RUN/${cid}.OpBasicInfo.csv"
      echo "$cid OK CSV=$RUN/${cid}.OpBasicInfo.csv" >> "$RUN/perf_result.txt"
    fi
    rm -rf -- "$PC" "$POC"   # 逐用例清理 profile 中间物（size budget）
  done < "$RUN/perfcases_list.txt"
fi
[ "$PERF_FAIL" -eq 0 ] || fail "perf 采集失败：部分/全部 perf 用例 msprof 未产 CSV 或未命中 kernel ${KERNEL}（见 $RUN/perf_result.txt）" 7

echo "OPRUNWAY_NPU_DONE kernel=$KERNEL"
