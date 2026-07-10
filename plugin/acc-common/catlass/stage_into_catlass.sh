#!/bin/bash
# OpRunway · 把 catlass generated_harness 幂等注入 catlass 工作副本（补 codex #2）。
#
# ⚠ 就地运行于 **NPU host**（非从 Mac ssh/scp 远端编排）；改的是 catlass **工作副本**（非提交 clone）。
#
# build.sh 只构建 examples 树内 add_subdirectory 注册的 target（已实地核验 examples/CMakeLists.txt 的
# foreach(EXAMPLE ${EXAMPLE_LIST}) add_subdirectory(${EXAMPLE}) 机制）。故 deploy 期须：
#   1) 把 <harness>/{runner.cpp, CMakeLists.txt(实名化)} 拷进 repos/catlass/examples/<harness>/；
#   2) 幂等注入 add_subdirectory(<harness>)（带 sentinel、可检测/可回退）到 examples/CMakeLists.txt。
# 之后 `build.sh <harness> -DCATLASS_ARCH=<arch>` 即可纳入构建。
#
# ⚠ 副作用先确认（CLAUDE.md #1/#3）：本脚本改外部 catlass 工作副本 → **须显式 OPRUNWAY_CATLASS_REAL=1 opt-in**，
#    否则 fail-fast、零副作用（防被直接调用绕过 catlass_adapter.run_catlass 的门）。
# 入参经环境变量（零硬编码）：
#   OPRUNWAY_CATLASS_REAL 必须 =1（副作用 opt-in 门）
#   OPRUNWAY_CATLASS_DIR   catlass 工作副本根（**须绝对路径、非 symlink、含 scripts/build.sh + examples/**）
#   OPRUNWAY_HARNESS       harness 名（= build target，如 oprunway_catlass_basic_matmul_950；白名单 [A-Za-z0-9_]）
#   OPRUNWAY_RUNNER        runner cpp 名（白名单 [A-Za-z0-9_.]，拒 . / .. / 隐藏名）
#   OPRUNWAY_TEMPLATE_DIR  本目录（含 runner + CMakeLists.txt 模板）
set -euo pipefail

# ── 副作用 opt-in 门（codex #1）：最前面 fail-fast，未显式 opt-in 则零副作用退出 ──────────────
if [ "${OPRUNWAY_CATLASS_REAL:-}" != "1" ]; then
  echo "[stage] 拒绝：本脚本会改外部 catlass 工作副本（cp/注入/回退），须显式 OPRUNWAY_CATLASS_REAL=1 opt-in 并人工确认副作用。" >&2
  exit 2
fi

: "${OPRUNWAY_CATLASS_DIR:?需 OPRUNWAY_CATLASS_DIR}"
: "${OPRUNWAY_HARNESS:?需 OPRUNWAY_HARNESS}"
: "${OPRUNWAY_RUNNER:?需 OPRUNWAY_RUNNER}"
TPL="${OPRUNWAY_TEMPLATE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

# ── 白名单校验（防 shell 注入 / 路径穿越）────────────────────────────────────────────────
case "$OPRUNWAY_HARNESS" in *[!A-Za-z0-9_]*) echo "非法 harness 名（仅 A-Za-z0-9_）" >&2; exit 3;; esac
case "$OPRUNWAY_RUNNER" in
  .|..) echo "非法 runner 名（. / ..）" >&2; exit 3;;
  .*)   echo "非法 runner 名（隐藏名，禁以 . 开头）" >&2; exit 3;;
  *[!A-Za-z0-9_.]*) echo "非法 runner 名（仅 A-Za-z0-9_.）" >&2; exit 3;;
esac

# ── OPRUNWAY_CATLASS_DIR 强校验（codex #2）：绝对路径 + 拒 symlink + realpath + catlass 特征 ──
case "$OPRUNWAY_CATLASS_DIR" in
  /*) ;;
  *) echo "OPRUNWAY_CATLASS_DIR 须绝对路径（拒相对路径，防写错目录）：$OPRUNWAY_CATLASS_DIR" >&2; exit 4;;
esac
if [ -L "$OPRUNWAY_CATLASS_DIR" ]; then
  echo "OPRUNWAY_CATLASS_DIR 是 symlink，拒绝（防指向仓外）：$OPRUNWAY_CATLASS_DIR" >&2; exit 4
fi
[ -d "$OPRUNWAY_CATLASS_DIR" ] || { echo "OPRUNWAY_CATLASS_DIR 不存在/非目录：$OPRUNWAY_CATLASS_DIR" >&2; exit 4; }
CATLASS_DIR="$(realpath -- "$OPRUNWAY_CATLASS_DIR" 2>/dev/null)" || {
  echo "OPRUNWAY_CATLASS_DIR realpath 失败：$OPRUNWAY_CATLASS_DIR" >&2; exit 4; }
[ -f "$CATLASS_DIR/scripts/build.sh" ] || {
  echo "非 catlass 根：缺 scripts/build.sh（OPRUNWAY_CATLASS_DIR=${CATLASS_DIR}）" >&2; exit 4; }
[ -d "$CATLASS_DIR/examples" ] || {
  echo "非 catlass 根：缺 examples/（OPRUNWAY_CATLASS_DIR=${CATLASS_DIR}）" >&2; exit 4; }

EX="$CATLASS_DIR/examples"
ROOT_CMAKE="$EX/CMakeLists.txt"
[ -f "$ROOT_CMAKE" ] || { echo "找不到 ${ROOT_CMAKE}（OPRUNWAY_CATLASS_DIR 是否为 catlass 根？）" >&2; exit 4; }
[ -L "$ROOT_CMAKE" ] && { echo "$ROOT_CMAKE 是 symlink，拒绝写入" >&2; exit 4; }

HDIR="$EX/$OPRUNWAY_HARNESS"
SENTINEL="# >>> OPRUNWAY_STAGE ${OPRUNWAY_HARNESS} >>>"
MLINE="add_subdirectory(${OPRUNWAY_HARNESS})"
SENTINEL_END="# <<< OPRUNWAY_STAGE ${OPRUNWAY_HARNESS} <<<"
STAMP_FILE="$HDIR/.oprunway_stamp"
STAMP_VAL="oprunway_stage_v1 ${OPRUNWAY_HARNESS} runner=${OPRUNWAY_RUNNER}"

# ── 注入块状态分类（codex #4/#5）：只认「严格相邻、内容精确匹配」的完整三行块 ──────────────
#   $1==S 用**字面串精确比对**（awk 非正则），彻底避开 sed 正则区间删的 orphan/歧义吞内容。
#   退出码：0=恰一个干净三行块（且无游离 sentinel）；10=完全无注入痕迹；7=残留/伪造/多份/歧义（拒绝改动）。
_classify_block() {
  awk -v S="$SENTINEL" -v M="$MLINE" -v E="$SENTINEL_END" '
    { lines[NR]=$0 }
    END {
      n=NR; s=0; e=0; b=0
      for (i=1;i<=n;i++){ if(lines[i]==S) s++; if(lines[i]==E) e++ }
      for (i=1;i+2<=n;i++){ if(lines[i]==S && lines[i+1]==M && lines[i+2]==E) b++ }
      if (b==1 && s==1 && e==1) exit 0
      if (b==0 && s==0 && e==0) exit 10
      exit 7
    }' "$ROOT_CMAKE"
}

# ── 精确删除唯一干净三行块（仅在 _classify_block==0 时调用）──────────────────────────────
_remove_block() {
  awk -v S="$SENTINEL" -v M="$MLINE" -v E="$SENTINEL_END" '
    { lines[NR]=$0 }
    END {
      n=NR; bs=0
      for (i=1;i+2<=n;i++){ if(lines[i]==S && lines[i+1]==M && lines[i+2]==E){ bs=i; break } }
      for (i=1;i<=n;i++){ if (bs && i>=bs && i<=bs+2) continue; print lines[i] }
    }' "$ROOT_CMAKE"
}

revert() {
  # 先全部校验，再动手（validate-all-then-mutate，防半途破坏）。
  # a) harness 目录 ownership stamp（codex #3）：无匹配 stamp 一律拒删（可能是用户目录）。
  if [ -e "$HDIR" ]; then
    if [ -L "$HDIR" ]; then
      echo "[stage] 拒绝回退：$HDIR 是 symlink" >&2; exit 6
    fi
    if [ ! -f "$STAMP_FILE" ] || [ "$(cat -- "$STAMP_FILE" 2>/dev/null)" != "$STAMP_VAL" ]; then
      echo "[stage] 拒绝删除 ${HDIR}：无匹配 .oprunway_stamp（非本脚本 staging，疑似用户目录），不 rm。" >&2
      exit 6
    fi
  fi
  # b) 注入块须为干净三行块或完全不存在，否则拒绝（codex #4）。
  local rc=0
  _classify_block || rc=$?
  case "$rc" in
    0)
      local tmp="$ROOT_CMAKE.oprunway.tmp"
      _remove_block > "$tmp"
      mv -- "$tmp" "$ROOT_CMAKE"
      echo "[stage] 已回退注入块 ${OPRUNWAY_HARNESS}（精确三行块）"
      ;;
    10) : ;;  # 无注入痕迹，跳过（幂等）
    *)
      echo "[stage] 拒绝回退：examples/CMakeLists.txt 的 sentinel 非干净三行块（orphan/多份/伪造/歧义），不自动删改，请人工核对。" >&2
      exit 7
      ;;
  esac
  # c) 删 harness 目录（stamp 已在 a) 校验匹配）。
  if [ -e "$HDIR" ]; then
    rm -rf -- "$HDIR"
    echo "[stage] 已删除 staged harness 目录 $HDIR"
  fi
  echo "[stage] 回退完成 $OPRUNWAY_HARNESS"
}

if [ "${1:-}" = "--revert" ]; then revert; exit 0; fi

# ── 注入前先分类（codex #5：伪造/残留 sentinel → 拒绝，绝不误报幂等而漏注册）───────────────
rc=0
_classify_block || rc=$?
case "$rc" in
  0)  INJECT=0; echo "[stage] add_subdirectory($OPRUNWAY_HARNESS) 干净三行块已在，跳过注入（幂等）" ;;
  10) INJECT=1 ;;
  *)  echo "[stage] 拒绝注入：examples/CMakeLists.txt 已含残留/伪造 sentinel（非干净三行块），请先人工清理再重跑。" >&2
      exit 7 ;;
esac

# 1) 拷 harness 目录（runner + 实名化 CMakeLists + ownership stamp）
[ -L "$HDIR" ] && { echo "$HDIR 是 symlink，拒绝写入" >&2; exit 4; }
[ -f "$TPL/$OPRUNWAY_RUNNER" ] || { echo "缺 runner 模板：$TPL/$OPRUNWAY_RUNNER" >&2; exit 4; }
[ -f "$TPL/CMakeLists.txt" ] || { echo "缺 CMakeLists.txt 模板：$TPL/CMakeLists.txt" >&2; exit 4; }
mkdir -p -- "$HDIR"
cp -- "$TPL/$OPRUNWAY_RUNNER" "$HDIR/$OPRUNWAY_RUNNER"
sed -e "s/@HARNESS@/$OPRUNWAY_HARNESS/g" -e "s/@RUNNER@/$OPRUNWAY_RUNNER/g" \
    "$TPL/CMakeLists.txt" > "$HDIR/CMakeLists.txt"
printf '%s\n' "$STAMP_VAL" > "$STAMP_FILE"

# 2) 幂等注入 add_subdirectory（仅当完全无注入痕迹时）
if [ "$INJECT" = 1 ]; then
  {
    echo ""
    echo "$SENTINEL"
    echo "$MLINE"
    echo "$SENTINEL_END"
  } >> "$ROOT_CMAKE"
  echo "[stage] 已注入 add_subdirectory($OPRUNWAY_HARNESS) 到 $ROOT_CMAKE"
fi
