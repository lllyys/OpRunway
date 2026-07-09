#!/bin/bash
# OpRunway · 把 catlass generated_harness 幂等注入 catlass 工作副本（补 codex #2）。
#
# build.sh 只构建 examples 树内 add_subdirectory 注册的 target（已实地核验 examples/CMakeLists.txt 的
# foreach(EXAMPLE ${EXAMPLE_LIST}) add_subdirectory(${EXAMPLE}) 机制）。故 deploy 期须：
#   1) 把 <harness>/{runner.cpp, CMakeLists.txt(实名化)} 拷进 repos/catlass/examples/<harness>/；
#   2) 幂等注入 add_subdirectory(<harness>)（带 sentinel、可检测/可回退）到 examples/CMakeLists.txt。
# 之后 `build.sh <harness> -DCATLASS_ARCH=<arch>` 即可纳入构建。
#
# ⚠ 副作用：改 catlass **工作副本**（非提交 clone）。首跑须人工确认（CLAUDE.md #1/#3）。支持 --revert 回退注入块。
# ⚠ 待真机验证：staging 与 build.sh 的交互须在 ascend-a5/a3 实测。
# 入参经环境变量（零硬编码）：
#   OPRUNWAY_CATLASS_DIR   catlass 工作副本根（如 /home/lys/catlass）
#   OPRUNWAY_HARNESS       harness 名（= build target，如 oprunway_catlass_basic_matmul_950）
#   OPRUNWAY_RUNNER        runner cpp 名
#   OPRUNWAY_TEMPLATE_DIR  本目录（含 runner + CMakeLists.txt 模板）
set -e
: "${OPRUNWAY_CATLASS_DIR:?需 OPRUNWAY_CATLASS_DIR}"
: "${OPRUNWAY_HARNESS:?需 OPRUNWAY_HARNESS}"
: "${OPRUNWAY_RUNNER:?需 OPRUNWAY_RUNNER}"
TPL="${OPRUNWAY_TEMPLATE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

# 白名单校验（防 shell 注入 / 路径穿越）
case "$OPRUNWAY_HARNESS" in *[!A-Za-z0-9_]*) echo "非法 harness 名" >&2; exit 3;; esac
case "$OPRUNWAY_RUNNER"  in *[!A-Za-z0-9_.]*) echo "非法 runner 名" >&2; exit 3;; esac

EX="$OPRUNWAY_CATLASS_DIR/examples"
ROOT_CMAKE="$EX/CMakeLists.txt"
[ -f "$ROOT_CMAKE" ] || { echo "找不到 $ROOT_CMAKE（OPRUNWAY_CATLASS_DIR 是否为 catlass 根？）" >&2; exit 4; }
SENTINEL="# >>> OPRUNWAY_STAGE ${OPRUNWAY_HARNESS} >>>"
SENTINEL_END="# <<< OPRUNWAY_STAGE ${OPRUNWAY_HARNESS} <<<"

revert() {
  # 删除注入块（幂等回退）
  if grep -qF "$SENTINEL" "$ROOT_CMAKE"; then
    sed -i.bak "/$(printf '%s' "$SENTINEL" | sed 's/[][\.*^$/]/\\&/g')/,/$(printf '%s' "$SENTINEL_END" | sed 's/[][\.*^$/]/\\&/g')/d" "$ROOT_CMAKE"
    rm -f "$ROOT_CMAKE.bak"
    echo "[stage] 已回退注入块 $OPRUNWAY_HARNESS"
  fi
  rm -rf "$EX/$OPRUNWAY_HARNESS"
}

if [ "${1:-}" = "--revert" ]; then revert; exit 0; fi

# 1) 拷 harness 目录（runner + 实名化 CMakeLists）
mkdir -p "$EX/$OPRUNWAY_HARNESS"
cp "$TPL/$OPRUNWAY_RUNNER" "$EX/$OPRUNWAY_HARNESS/$OPRUNWAY_RUNNER"
sed -e "s/@HARNESS@/$OPRUNWAY_HARNESS/g" -e "s/@RUNNER@/$OPRUNWAY_RUNNER/g" \
    "$TPL/CMakeLists.txt" > "$EX/$OPRUNWAY_HARNESS/CMakeLists.txt"

# 2) 幂等注入 add_subdirectory（已注入则跳过）
if grep -qF "$SENTINEL" "$ROOT_CMAKE"; then
  echo "[stage] add_subdirectory($OPRUNWAY_HARNESS) 已注入，跳过（幂等）"
else
  {
    echo ""
    echo "$SENTINEL"
    echo "add_subdirectory($OPRUNWAY_HARNESS)"
    echo "$SENTINEL_END"
  } >> "$ROOT_CMAKE"
  echo "[stage] 已注入 add_subdirectory($OPRUNWAY_HARNESS) 到 $ROOT_CMAKE"
fi
