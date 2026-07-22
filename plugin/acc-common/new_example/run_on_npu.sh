#!/bin/bash
# OpRunway new_example 真机编排（在 a3 上跑）：建双 exe → 正确性(custom) → 性能(msprof custom + 内置 TBE 基线)。
# ⚠ 共享机：只写用户目录；op 走用户态 ASCEND_CUSTOM_OPP_PATH，不碰共享 opp/vendors。
# 入参经环境变量：OPRUNWAY_OPS_REPO/OPP/RUN_DIR/SOC/VENDOR/RUNNER(runner cpp 名)/OPNAME(CamelCase, msprof 行名)
#   /OP_SRC(被测 op 源码子路径·相对 OPS 仓·绑 provenance 用·必填) [/SETENV] [/OPP_REBUILD(=1 授权从源重建 opp)]
set -e   # 不用 -u：vendor set_env.bash 引用未绑定变量会在 set -u 下直接退出（|| true 拦不住）
: "${OPRUNWAY_OPS_REPO:?}"; : "${OPRUNWAY_OPP:?}"; : "${OPRUNWAY_RUN_DIR:?}"
: "${OPRUNWAY_SOC:?}"; : "${OPRUNWAY_VENDOR:?}"
: "${OPRUNWAY_RUNNER:?}"; : "${OPRUNWAY_OPNAME:?}"
: "${OPRUNWAY_OP_SRC:?被测 op 源码子路径(相对 OPS 仓，如 experimental/math/is_close)——绑 opp provenance 用，必填}"
source "${OPRUNWAY_SETENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}" 2>/dev/null || true
SYS_LD="${LD_LIBRARY_PATH:-}"   # 系统基线 LD（内置 TBE 测试用，避免被 custom 用户态库污染）

OPS="$OPRUNWAY_OPS_REPO"; OPP="$OPRUNWAY_OPP"; RUN="$OPRUNWAY_RUN_DIR"
SOC="$OPRUNWAY_SOC"; VEN="$OPRUNWAY_VENDOR"
OP_SRC="$OPRUNWAY_OP_SRC"   # 短名桥接：下文 OPHASH/EXP/OP_BUILD/WANT_PROV 全用 $OP_SRC；缺此赋值 → $OP_SRC 恒空
                            # → SRC=$OPS/（整仓）、EXP 空、--ops 空、stamp op_src 空 = provenance 绑到垃圾、且没走 --experimental
# 纵深防御（不只靠 repo_adapter._ne_cfg）：脚本侧自校 OP_SRC——拒前导 /、`..`/`.` 段、非嵌套裸值/仓根，
# 防直接调用脚本时路径逃逸、或 `.`/裸子树使 OPHASH 绑整仓 + provenance 非算子专属（跨算子复用异源 opp 假通过）。
case "/$OP_SRC/" in //*|*/../*|*/./*)
  echo "[run_on_npu] fail-closed：OPRUNWAY_OP_SRC=$OP_SRC 非法（前导 / 或含 ./.. 段）" >&2; exit 3;; esac
case "$OP_SRC" in */?*) : ;; *)
  echo "[run_on_npu] fail-closed：OPRUNWAY_OP_SRC=$OP_SRC 须为 ≥2 段嵌套路径(如 experimental/math/is_close)、非仓根/裸子树" >&2; exit 3;; esac
# vendor 目录后缀：算子仓的 build.sh 产出的是 `${VENDOR}_<仓族>`（ops-math→`_math`、ops-cv→`_cv`…）。
# ⚠ 原来这里写死 `_math`——与本仓「零硬编码」约定直接冲突，且 **ops-cv 的算子（如 Upsample 系）真机跑必撞**。
# 现按序解析：① 显式 OPRUNWAY_VENDOR_SUFFIX（用户/编排层给）；② 从 OPS 仓目录名推（ops-math→math、
# ops-cv→cv、cann-ops-xxx→xxx）；③ 推不出来 → **fail-closed**，不猜。
if [ -n "${OPRUNWAY_VENDOR_SUFFIX:-}" ]; then
  VSUF="$OPRUNWAY_VENDOR_SUFFIX"
else
  VSUF="$(basename "$OPS" | sed -n 's/^\(cann-\)\{0,1\}ops-\([A-Za-z0-9_]\{1,\}\)$/\2/p')"
fi
case "$VSUF" in
  "" ) echo "[run_on_npu] fail-closed：推不出 vendor 目录后缀。OPS 仓目录名=$(basename "$OPS")" \
         "不匹配 ops-<族> 形态，请显式设 OPRUNWAY_VENDOR_SUFFIX（如 math / cv / blas）。" >&2; exit 3;;
  *[!A-Za-z0-9_]* ) echo "[run_on_npu] fail-closed：vendor 后缀 $VSUF 含非法字符" >&2; exit 3;;
esac
V="$OPP/vendors/${VEN}_${VSUF}"
EXE="$RUN/runner_exe"; BEXE="$RUN/runner_builtin"; RUNNER="$RUN/$OPRUNWAY_RUNNER"
STAMP="$RUN/.exe_stamp"
# ---- OPHASH：绑**真实 op 源** $OPS/$OP_SRC 的内容(sha256)。**fail-closed**：源目录不存在/无文件→非零退出，
#      不吞 find 错误产恒定空 hash（旧洞：experimental/math/$OP 对多数 op 路径不存在→恒定 hash→未绑源→stale opp 假通过）。
SRC="$OPS/$OP_SRC"
if [ ! -d "$SRC" ] || [ -z "$(find "$SRC" -type f | head -1)" ]; then
  echo "[run_on_npu] fail-closed：op 源目录不存在或无文件：$SRC（OPRUNWAY_OP_SRC=$OP_SRC 指错？）——拒在未绑源下建/复用 opp" >&2
  exit 3
fi
OPHASH="$(find "$SRC" -type f | sort | xargs -r sha256sum | sha256sum | cut -d' ' -f1)"   # -r：空输入不跑 sha256sum（配合上文非空守卫，堵 TOCTOU 常量空 hash）
case "$OP_SRC" in experimental/*) EXP="--experimental";; *) EXP="";; esac   # experimental 源 → build 带 --experimental
OP_BUILD="$(basename "$OP_SRC")"                                            # --ops 用源目录名(如 is_close)、非 OPRUNWAY_OP
PROV="$V/.oprunway_opp_provenance"
WANT_PROV="op_src=$OP_SRC|ophash=$OPHASH|soc=$SOC|vendor=$VEN|build=${EXP:-none} --ops=$OP_BUILD"

# ---- opp provenance 门（顶层·每次跑）：缺 opp→建；provenance 全字段符→复用；不符/缺失→**fail-closed**（拒复用
#      来路不明/stale opp、防假通过），除非 OPRUNWAY_OPP_REBUILD=1 显式授权从当前源重建(含 rm -rf $V 删除副作用)。
need_build=0
if [ ! -d "$V/op_api/include" ]; then
  need_build=1
elif [ "$(cat "$PROV" 2>/dev/null)" != "$WANT_PROV" ]; then
  if [ "${OPRUNWAY_OPP_REBUILD:-0}" = "1" ]; then
    need_build=1
  else
    echo "[run_on_npu] fail-closed：现存 opp($V) provenance 与当前 op 源不符/缺失，拒复用(防 stale/异源假通过)：" >&2
    echo "  期望: $WANT_PROV" >&2
    echo "  实得: $(cat "$PROV" 2>/dev/null || echo '(无 .oprunway_opp_provenance)')" >&2
    echo "  → 确要从当前源 provenance-clean 重建 opp，请设 OPRUNWAY_OPP_REBUILD=1(会 rm -rf $V 重装)。" >&2
    exit 4
  fi
fi

if [ "$need_build" = "1" ]; then
  cd "$OPS"
  # 清 stale build 缓存：build/ 里的 CMakeCache 记录**建它时的绝对路径**，换机器/换挂载路径(如 host 建的
  # /home/x/ops-math/build 到容器里成 /work/ops-math/build)会致 CMake「directory is different」configure 失败。
  # provenance-clean 重建本就该新鲜 build dir，故 build 前一律清（build/ 与 build_out/ 是产物、非源码，可再生）。
  rm -rf "$OPS/build" "$OPS/build_out" 2>/dev/null || true
  if ! bash build.sh --pkg $EXP --ops="$OP_BUILD" --soc="$SOC" --vendor_name="$VEN" -j8 > "$RUN/build.log" 2>&1; then
    echo "[run_on_npu] fail-closed：build.sh 失败(op 源 $OP_SRC 可能不支持 SOC=$SOC；如 A3 上跑 950-only 源)——build.log 尾：" >&2
    tail -25 "$RUN/build.log" >&2; exit 5
  fi
  PKG="$(find build_out -maxdepth 1 -name "*${VEN}*.run" | head -1)"
  if [ -z "$PKG" ]; then
    echo "[run_on_npu] fail-closed：build 无 .run 产出(op 源未产 custom kernel)——build.log 尾：" >&2
    tail -25 "$RUN/build.log" >&2; exit 5
  fi
  chmod -R u+w "$V" 2>/dev/null || true   # 只放权/删**本 vendor**的 opp($V)，不碰 $OPP 下别的 vendor
  rm -rf "$V"; mkdir -p "$OPP"
  "$PKG" --install-path="$OPP" --quiet > /dev/null 2>&1
  echo "$WANT_PROV" > "$PROV"             # 落 provenance stamp(绑当前源) → 后续复用逐字段校
fi
OPP_ID="$(printf '%s' "$WANT_PROV" | sha256sum | cut -d' ' -f1)"   # opp 身份摘要，级联 runner stamp

# 1) 建双 exe。stamp = runner_sha | OPP_ID：opp 重建→OPP_ID 变→runner 必级联重链；runner cpp 单改→只重链 runner。
WANT="$(sha256sum "$RUNNER" | cut -d' ' -f1)|$OPP_ID"
if [ ! -x "$EXE" ] || [ ! -x "$BEXE" ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$WANT" ]; then
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
    cp "$RUN/cases/$cid/"x*.bin "$PC/$cid/" 2>/dev/null || true   # perf 尽力而为（同下 msprof||true）：缺输入→该 perf 用例记 NA、不 set -e 崩整跑
    grep "^$cid " "$RUN/cases/manifest.txt" > "$PC/manifest.txt" || true   # grep 无匹配退 1，set -e 下会在 NPU_DONE 哨兵前崩掉已过的正确性跑
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
