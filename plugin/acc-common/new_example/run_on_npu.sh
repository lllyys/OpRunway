#!/bin/bash
# OpRunway new_example 真机编排（在 a3 上跑）：建双 exe → 正确性(custom) → 性能(msprof custom + 内置 TBE 基线)。
# ⚠ 共享机：只写用户目录；op 走用户态 ASCEND_CUSTOM_OPP_PATH，不碰共享 opp/vendors。
# 入参经环境变量：OPRUNWAY_OPS_REPO/OPP/RUN_DIR/SOC/OP/VENDOR/RUNNER(runner cpp 名)/OPNAME(CamelCase, msprof 行名) [/SETENV]
set -e   # 不用 -u：vendor set_env.bash 引用未绑定变量会在 set -u 下直接退出（|| true 拦不住）
: "${OPRUNWAY_OPS_REPO:?}"; : "${OPRUNWAY_OPP:?}"; : "${OPRUNWAY_RUN_DIR:?}"
: "${OPRUNWAY_SOC:?}"; : "${OPRUNWAY_OP:?}"; : "${OPRUNWAY_VENDOR:?}"
: "${OPRUNWAY_RUNNER:?}"; : "${OPRUNWAY_OPNAME:?}"
source "${OPRUNWAY_SETENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}" 2>/dev/null || true
SYS_LD="${LD_LIBRARY_PATH:-}"   # 系统基线 LD（内置 TBE 测试用，避免被 custom 用户态库污染）

OPS="$OPRUNWAY_OPS_REPO"; OPP="$OPRUNWAY_OPP"; RUN="$OPRUNWAY_RUN_DIR"
SOC="$OPRUNWAY_SOC"; OP="$OPRUNWAY_OP"; VEN="$OPRUNWAY_VENDOR"
V="$OPP/vendors/${VEN}_math"
EXE="$RUN/runner_exe"; BEXE="$RUN/runner_builtin"; RUNNER="$RUN/$OPRUNWAY_RUNNER"
STAMP="$RUN/.exe_stamp"
# stamp 绑：runner cpp + 被测 op 源码 + SOC + vendor → op 源码变(PR 改了)也重建，防 stale exe/OPP 假通过
OPHASH="$(find "$OPS/experimental/math/$OP" -type f 2>/dev/null | sort | xargs md5sum 2>/dev/null | md5sum | cut -d' ' -f1)"
WANT="$(md5sum "$RUNNER" 2>/dev/null | cut -d' ' -f1)|$SOC|$VEN|$OPHASH"

# 1) 建双 exe（缺 or runner/SOC/vendor 变才重建；hash-stamp 防 stale exe 假通过）
if [ ! -x "$EXE" ] || [ ! -x "$BEXE" ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$WANT" ]; then
  cd "$OPS"
  bash build.sh --pkg --experimental --ops="$OP" --soc="$SOC" --vendor_name="$VEN" -j8 >/dev/null 2>&1
  PKG="$(find build_out -maxdepth 1 -name "*${VEN}*.run" | head -1)"
  chmod -R u+w "$OPP" 2>/dev/null || true   # .run 装的目录可能只读，先放权再删
  rm -rf "$OPP"; mkdir -p "$OPP"
  "$PKG" --install-path="$OPP" --quiet >/dev/null 2>&1
  source "$V/bin/set_env.bash" 2>/dev/null || true
  g++ -std=c++17 "$RUNNER" -o "$EXE" -I"$ASCEND_HOME_PATH/include" -I"$V/op_api/include" \
    -L"$ASCEND_HOME_PATH/lib64" -L"$V/op_api/lib" -lascendcl -lnnopbase -lcust_opapi
  g++ -std=c++17 "$RUNNER" -o "$BEXE" -I"$ASCEND_HOME_PATH/include" \
    -L"$ASCEND_HOME_PATH/lib64" -lascendcl -lnnopbase -lopapi
  echo "$WANT" > "$STAMP"
fi
chmod 755 "$RUN" "$EXE" "$BEXE"

source "$V/bin/set_env.bash" 2>/dev/null || true
export LD_LIBRARY_PATH="$V/op_api/lib:${LD_LIBRARY_PATH:-}"

# 2) 正确性（custom Ascend C op）→ out.bin
ASCEND_CUSTOM_OPP_PATH="$V" OPRUNWAY_CASES="$RUN/cases" "$EXE"

# 3) 性能：逐 perf 用例，msprof custom + 内置 TBE → 真 kernel-only Task Duration(us) 中位
med() { grep -i "^${OPRUNWAY_OPNAME}" "$1" 2>/dev/null | cut -d, -f3 | sort -n \
        | awk '{a[NR]=$1} END{if(NR)printf "%s", a[int(NR/2)+1]}'; }
: > "$RUN/perf_result.txt"
if [ -s "$RUN/perfcases_list.txt" ]; then
  while read -r cid; do
    [ -z "$cid" ] && continue
    PC="$RUN/pc_$cid"; rm -rf "$PC"; mkdir -p "$PC/$cid"
    cp "$RUN/cases/$cid/"x*.bin "$PC/$cid/"        # 通用：一元只 x1.bin、二元 x1+x2
    grep "^$cid " "$RUN/cases/manifest.txt" > "$PC/manifest.txt"
    POC="$RUN/prof_${cid}_c"; rm -rf "$POC"; mkdir -p "$POC"; chmod 700 "$POC"
    ASCEND_CUSTOM_OPP_PATH="$V" OPRUNWAY_CASES="$PC" msprof op --output="$POC" "$EXE" >/dev/null 2>&1 || true
    CUS="$(med "$(find "$POC" -name OpBasicInfo.csv | head -1)")"
    POT="$RUN/prof_${cid}_t"; rm -rf "$POT"; mkdir -p "$POT"; chmod 700 "$POT"
    env -u ASCEND_CUSTOM_OPP_PATH LD_LIBRARY_PATH="$SYS_LD" OPRUNWAY_CASES="$PC" \
        msprof op --output="$POT" "$BEXE" >/dev/null 2>&1 || true
    TBE="$(med "$(find "$POT" -name OpBasicInfo.csv | head -1)")"
    echo "$cid ${CUS:-NA} ${TBE:-NA}" >> "$RUN/perf_result.txt"
    rm -rf "$PC" "$POC" "$POT"
  done < "$RUN/perfcases_list.txt"
fi
echo "OPRUNWAY_NPU_DONE"
