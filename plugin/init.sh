#!/usr/bin/env bash
# ==============================================================================
# OpRunway init.sh — 安装期跨 CLI 扇出（T3-P2）
#
# 把中立单一源 `AGENTS.md`（编排 + 硬门）+ `skills/` + `agents/` 扇出到各 CLI 的
# 约定落点：Claude → CLAUDE.md + .claude/{skills,agents}/；其余（opencode/trae/
# cursor/copilot）→ AGENTS.md + 各自目录。skills/agents 走 **symlink 发现**（保相对
# 拓扑），symlink 不可用时 materialize-with-provenance 兜底。
#
# 设计红线（对齐 canon cross-cli-unified-form-agents-md·proposed / CLAUDE.md 工程约定）：
#   - 主变量 OPRUNWAY_PLUGIN_ROOT（默认=脚本自身目录），Claude 分支兼容别名 ${CLAUDE_PLUGIN_ROOT}
#   - 不 sed 私有/绝对路径进任何制品；不照搬 cannbot 的 external-directory 白名单反模式
#   - 不写 ~/.config、不改 shell rc；产物只落目标 project/global 的 CLI 约定目录
#   - --dry-run 零文件系统写；真实写入前显式二次确认（--yes 或交互 y）
#   - 冲突默认不覆盖、报错退出；--force 才覆盖并先备份 <file>.bak.<ts>；幂等；--uninstall 逆操作
#   - day-1 只对 Claude 分支实跑；opencode/trae/cursor/copilot 干跑/静态审（真机多 CLI 验证挂起）
#
# 安全红线（对抗门修复，勿削弱）：
#   - 写入前对落点祖先逐级 lstat，拒绝 symlink 祖先 + 断言物理路径落在 BASE 内（防越出重定向）
#   - 注册文件本身若为 symlink 拒绝写入（防 >> / awk 写穿受害文件）
#   - 托管块要求 BEGIN..END 唯一成对非嵌套，异常一律 abort 不改文件
#   - 卸载只删「可证明本脚本所装」的 symlink/materialize（symlink 校验 realpath；拷贝校验 provenance）
#   - 备份/临时文件用 mktemp 唯一名 + O_EXCL（拒 symlink 预植、防同秒覆盖）
#   - 非 claude（含卸载）一律强制干跑
# ==============================================================================
set -euo pipefail

# ── 常量 ──────────────────────────────────────────────────────────────────────
PROG="$(basename "$0")"
MANAGED_BEGIN="# >>> OpRunway plugin (managed by init.sh — 勿手改块内) >>>"
MANAGED_END="# <<< OpRunway plugin (managed by init.sh) <<<"
PROVENANCE_FILE=".oprunway-provenance"   # materialize 兜底时写在目标目录，记来源

# ── 默认参数 ──────────────────────────────────────────────────────────────────
TOOL=""
LEVEL="project"
DRY_RUN=0
FORCE=0
UNINSTALL=0
ASSUME_YES=0
MATERIALIZE=0

# ── 根解析：OPRUNWAY_PLUGIN_ROOT 优先，否则脚本自身目录 ─────────────────────────
_script_dir() {
  # 解析脚本真实目录（跟随 symlink），不依赖 GNU realpath
  local src="${BASH_SOURCE[0]}" dir
  while [ -h "$src" ]; do
    dir="$(cd -P "$(dirname "$src")" && pwd)"
    src="$(readlink "$src")"
    case "$src" in /*) ;; *) src="$dir/$src" ;; esac
  done
  cd -P "$(dirname "$src")" && pwd
}
# Claude 别名兼容：若调用方已导出 CLAUDE_PLUGIN_ROOT 而未给 OPRUNWAY_PLUGIN_ROOT，用它
PLUGIN_ROOT="${OPRUNWAY_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$(_script_dir)}}"

# ── 日志 ──────────────────────────────────────────────────────────────────────
log()  { printf '  %s\n' "$*"; }
plan() { printf '  [计划] %s\n' "$*"; }        # 干跑/预演打印
warn() { printf '  ⚠ %s\n' "$*" >&2; }
die()  { printf '✗ %s\n' "$*" >&2; exit 1; }

# 便携取文件权限（八进制）：mac 用 stat -f，Linux 用 stat -c；取不到回空串
_stat_mode() { stat -f '%Lp' "$1" 2>/dev/null || stat -c '%a' "$1" 2>/dev/null || true; }

usage() {
  cat <<EOF
用法: $PROG --tool <claude|opencode|trae|cursor|copilot> [选项]

选项:
  --tool <name>     目标 CLI（必给）。day-1 只有 claude 实跑；其余为静态审/干跑。
  --level <lvl>     project（默认，落当前目录）| global（落 ~/.claude 等用户级）
  --dry-run         只打印计划，零文件系统写
  --force           冲突时覆盖，先备份 <file>.bak.<时间戳>.<唯一后缀>
  --uninstall       逆操作：删本脚本装的 symlink/materialize 拷贝、移除 CLAUDE.md/AGENTS.md 托管块。
                    不自动还原 .bak.* 备份——它们保留原处待你手工取用（移除托管块本身已抵消安装的改动）。
  --materialize     不用 symlink，改拷贝真文件 + 写 provenance（symlink 不可用时兜底）
  --yes             真实写入的显式二次确认（非交互环境必给；交互环境可回车 y）
  -h, --help        本帮助

变量: OPRUNWAY_PLUGIN_ROOT（默认=脚本目录）；Claude 分支兼容 \${CLAUDE_PLUGIN_ROOT}。
EOF
}

# ── 参数解析 ──────────────────────────────────────────────────────────────────
# 末位 --tool/--level 时先断言还有第 2 个参数，避免 shift 2 在 set -e 下非零退出。
while [ $# -gt 0 ]; do
  case "$1" in
    --tool)        [ $# -ge 2 ] || { usage; die "--tool 需要一个参数"; }; TOOL="$2"; shift 2 ;;
    --tool=*)      TOOL="${1#*=}"; shift ;;
    --level)       [ $# -ge 2 ] || { usage; die "--level 需要一个参数"; }; LEVEL="$2"; shift 2 ;;
    --level=*)     LEVEL="${1#*=}"; shift ;;
    --dry-run)     DRY_RUN=1; shift ;;
    --force)       FORCE=1; shift ;;
    --uninstall)   UNINSTALL=1; shift ;;
    --materialize) MATERIALIZE=1; shift ;;
    --yes)         ASSUME_YES=1; shift ;;
    -h|--help)     usage; exit 0 ;;
    *)             die "未知参数: $1（--help 看用法）" ;;
  esac
done

[ -n "$TOOL" ] || { usage; die "缺 --tool"; }
case "$TOOL" in claude|opencode|trae|cursor|copilot) ;; *) die "不支持的 --tool: $TOOL" ;; esac
case "$LEVEL" in project|global) ;; *) die "不支持的 --level: ${LEVEL}（project|global）" ;; esac

# 校验插件根结构（AGENTS.md + skills/ + agents/ 必在）
[ -f "$PLUGIN_ROOT/AGENTS.md" ] || die "PLUGIN_ROOT 无 AGENTS.md: $PLUGIN_ROOT"
[ -d "$PLUGIN_ROOT/skills" ]    || die "PLUGIN_ROOT 无 skills/: $PLUGIN_ROOT"
[ -d "$PLUGIN_ROOT/agents" ]    || die "PLUGIN_ROOT 无 agents/: $PLUGIN_ROOT"
PLUGIN_ROOT_PHYS="$(cd "$PLUGIN_ROOT" 2>/dev/null && pwd -P)" || die "无法解析 PLUGIN_ROOT 物理路径: $PLUGIN_ROOT"

# ── 目标落点（安装矩阵）───────────────────────────────────────────────────────
# base_dir: project=当前目录（可 OPRUNWAY_TARGET_DIR 覆盖用于测试）；global=$HOME
_base_dir() {
  if [ "$LEVEL" = "global" ]; then printf '%s' "${OPRUNWAY_TARGET_DIR:-$HOME}"
  else printf '%s' "${OPRUNWAY_TARGET_DIR:-$PWD}"; fi
}
BASE="$(_base_dir)"
# 归一化：去掉尾部斜杠（根 "/" 除外），保证下游前缀匹配与拼接一致
case "$BASE" in
  /) ;;
  */) BASE="${BASE%/}" ;;
esac
# BASE 必须已存在（安装到既有 project/global；不隐式创建用户指定的根，避免误落点）
[ -d "$BASE" ] || die "目标 BASE 不存在（请先创建再安装）: $BASE"
BASE_PHYS="$(cd "$BASE" 2>/dev/null && pwd -P)" || die "无法解析 BASE 物理路径: $BASE"

# 各 CLI 的 CLI 根目录（skills/agents symlink 的父）+ 注册文件
_cli_home() {   # 打印该 CLI 在 BASE 下的配置根
  case "$TOOL" in
    claude)   printf '%s/.claude' "$BASE" ;;
    opencode) printf '%s/.opencode' "$BASE" ;;
    trae)     printf '%s/.trae' "$BASE" ;;
    cursor)   printf '%s/.cursor' "$BASE" ;;
    copilot)  printf '%s/.github' "$BASE" ;;   # copilot 约定 .github/
  esac
}
_reg_file() {   # 注册文件落点
  if [ "$TOOL" = "claude" ]; then
    if [ "$LEVEL" = "global" ]; then printf '%s/.claude/CLAUDE.md' "$BASE"
    else printf '%s/CLAUDE.md' "$BASE"; fi
  else
    printf '%s/AGENTS.md' "$BASE"   # 其余 CLI 读中立 AGENTS.md
  fi
}
CLI_HOME="$(_cli_home)"
SKILLS_DST="$CLI_HOME/skills"
AGENTS_DST="$CLI_HOME/agents"
REG_FILE="$(_reg_file)"

# day-1 实跑面：只有 claude 真写；其余（含卸载）一律强制干跑（静态审）。
# 关键修复：不再带 UNINSTALL 例外——否则非 claude 的卸载会真实写盘。
if [ "$TOOL" != "claude" ]; then
  if [ "$DRY_RUN" -eq 0 ]; then
    warn "day-1 仅 Claude 分支实跑；'$TOOL'（含卸载）为静态审/干跑（真机多 CLI 验证挂起，见 open_decision④）→ 自动转 --dry-run"
    DRY_RUN=1
  fi
fi

# ── 安全护栏：祖先 symlink 拒绝 + 物理路径落在 BASE 内 ──────────────────────────
# target 的**祖先目录**（BASE 之下、leaf 之上）任一为 symlink → die（防 .claude→外部 之类重定向）；
# 再断言 target 最深已存在目录祖先的物理路径仍在 BASE_PHYS 下。**不**校验 leaf 自身
# （skills/agents 的 leaf 正是我们要建/替换的 symlink；REG_FILE 的 leaf 由 _guard_regfile 另判）。
_guard_target() {
  local target="$1"
  case "$target" in "$BASE"|"$BASE"/*) ;; *) die "内部错误：写入目标越出 BASE：${target}（BASE=${BASE}）" ;; esac
  [ "$target" = "$BASE" ] && return 0   # BASE 自身即可信根，无 leaf 之上分量可查
  local rel="${target#"$BASE"/}"
  local parent_rel
  case "$rel" in
    */*) parent_rel="${rel%/*}" ;;
    *)   parent_rel="" ;;
  esac
  # 逐级 lstat 祖先（用参数展开切分，兼容含换行/glob 元字符的分量）
  local cur="$BASE" rest="$parent_rel" comp
  while [ -n "$rest" ]; do
    comp="${rest%%/*}"
    if [ "$comp" = "$rest" ]; then rest=""; else rest="${rest#*/}"; fi
    [ -n "$comp" ] || continue
    cur="$cur/$comp"
    if [ -L "$cur" ]; then
      die "安全阻断：祖先分量为 symlink，拒绝写入（防越出 BASE 重定向）：$cur -> $(readlink "$cur")"
    fi
  done
  # 物理复核：取最深已存在目录祖先，物理路径必须仍在 BASE_PHYS 下。
  # 关键：若 leaf 自身是 symlink（skills/agents 的 leaf 正是指向插件源的 symlink），**不**解析它，
  # 退到父目录起算——否则会把插件源路径误判成「越出 BASE」。祖先已逐级 lstat 保证无 symlink。
  local d="$target"
  [ -L "$d" ] && d="$(dirname "$d")"
  while [ ! -d "$d" ]; do d="$(dirname "$d")"; done
  local phys; phys="$(cd "$d" 2>/dev/null && pwd -P || true)"
  [ -n "$phys" ] || die "无法解析物理路径：$target"
  case "$phys/" in
    "$BASE_PHYS"/*|"$BASE_PHYS/") ;;
    *) die "安全阻断：解析后物理路径越出 BASE（$phys 不在 $BASE_PHYS 下）：$target" ;;
  esac
}

# 注册文件自身若为 symlink 拒绝（防 >> 追加 / awk 读回写穿受害文件）
_guard_regfile() {
  _guard_target "$REG_FILE"
  [ -L "$REG_FILE" ] && die "安全阻断：注册文件本身是 symlink，拒绝写入（防写穿）: $REG_FILE"
  return 0
}

# 写前一次性预检所有落点，越界即刻 die（避免「先写了注册块又在 skills 处 die」的半装）
_preflight_guard() {
  _guard_target "$SKILLS_DST"
  _guard_target "$AGENTS_DST"
  _guard_regfile
}

# ── 二次确认（真实写入前）─────────────────────────────────────────────────────
confirm() {
  [ "$DRY_RUN" -eq 1 ] && return 0
  [ "$ASSUME_YES" -eq 1 ] && return 0
  if [ -t 0 ]; then
    printf '  将对 %s（%s/%s）执行真实写入，确认? [y/N] ' "$BASE" "$TOOL" "$LEVEL"
    local ans; read -r ans
    case "$ans" in y|Y|yes) return 0 ;; *) die "用户未确认，已中止" ;; esac
  else
    die "非交互环境拒绝真实写入：请加 --yes 显式确认，或用 --dry-run 预演"
  fi
}

# ── 写工具（尊重 DRY_RUN）─────────────────────────────────────────────────────
ensure_dir() {
  local d="$1"
  _guard_target "$d"
  if [ "$DRY_RUN" -eq 1 ]; then plan "mkdir -p $d"; else mkdir -p "$d"; fi
}

# 备份：mktemp 唯一名（O_EXCL，拒 symlink 预植 + 防同秒覆盖）；目录用 -d + cp -pR
backup_file() {
  local f="$1"
  [ -e "$f" ] || return 0
  local ts; ts="$(date +%Y%m%d%H%M%S)"
  if [ "$DRY_RUN" -eq 1 ]; then plan "备份 $f -> ${f}.bak.${ts}.<唯一后缀>"; return 0; fi
  local bak
  if [ -d "$f" ] && [ ! -L "$f" ]; then
    bak="$(mktemp -d "${f}.bak.${ts}.XXXXXX")" || die "备份目录 mktemp 失败: $f"
    cp -pR "$f/." "$bak/" || die "备份目录失败: $f -> $bak"
  else
    bak="$(mktemp "${f}.bak.${ts}.XXXXXX")" || die "备份 mktemp 失败: $f"
    cp -p "$f" "$bak" || die "备份失败: $f -> $bak"
  fi
  log "已备份 $f -> $bak"
}

# 相对路径计算：从 $1(目录) 到 $2(目标)，保相对拓扑（不烤绝对路径）。
# 用索引数组按 "/" 分量算，正确处理「仅共享根 /」的跨树情形（不依赖 GNU realpath）。
_relpath() {
  local from="$1" to="$2"
  # 尽量解析物理路径（消解祖先 symlink，如 macOS /var→/private/var），使相对 symlink 在物理位置正确解析；
  # 目录不存在（如 dry-run 未真建）时退回字面路径（仅用于打印计划，不真建链接）。
  [ -d "$from" ] && from="$(cd "$from" 2>/dev/null && pwd -P)"
  [ -e "$to" ]   && to="$(cd "$(dirname "$to")" 2>/dev/null && pwd -P)/$(basename "$to")"
  local -a fa ta
  # read -r -a 按 IFS=/ 切分，天然不做 glob（避免含 * ? [ 的分量被通配污染）
  IFS=/ read -r -a fa <<<"$from"
  IFS=/ read -r -a ta <<<"$to"
  local i=0
  while [ $i -lt ${#fa[@]} ] && [ $i -lt ${#ta[@]} ] && [ "${fa[$i]}" = "${ta[$i]}" ]; do
    i=$((i+1))
  done
  local rel="" j="$i"
  while [ $j -lt ${#fa[@]} ]; do
    [ -n "${fa[$j]}" ] && rel="${rel}../"
    j=$((j+1))
  done
  j="$i"
  while [ $j -lt ${#ta[@]} ]; do
    [ -n "${ta[$j]}" ] && rel="${rel}${ta[$j]}/"
    j=$((j+1))
  done
  rel="${rel%/}"
  [ -n "$rel" ] || rel="."
  printf '%s' "$rel"
}

# 链接一个源(绝对)到 dst(绝对)：symlink 保相对拓扑；--materialize 或 symlink 失败 → 拷贝 + provenance
link_or_materialize() {
  local src="$1" dst="$2" dstdir; dstdir="$(dirname "$dst")"
  _guard_target "$dst"
  ensure_dir "$dstdir"                         # 先建父目录（real 模式），使 _relpath 能解析物理路径
  local rel; rel="$(_relpath "$dstdir" "$src")"
  # 幂等：dst 已是指向同一相对目标的 symlink → 跳过
  if [ -L "$dst" ]; then
    local cur; cur="$(readlink "$dst")"
    if [ "$cur" = "$rel" ]; then log "幂等跳过（已链接）: $dst -> $rel"; return 0; fi
    [ "$FORCE" -eq 1 ] || die "冲突: $dst 已是指向别处的 symlink（${cur}；--force 覆盖）"
    if [ "$DRY_RUN" -eq 1 ]; then plan "rm ${dst}（覆盖旧链接）"; else rm -f "$dst"; fi
  elif [ -e "$dst" ]; then
    [ "$FORCE" -eq 1 ] || die "冲突: $dst 已存在真实文件（默认不覆盖；--force 覆盖并备份）"
    backup_file "$dst"
    if [ "$DRY_RUN" -eq 1 ]; then plan "rm -rf ${dst}（--force 覆盖）"; else rm -rf "$dst"; fi
  fi

  if [ "$MATERIALIZE" -eq 1 ]; then
    _materialize "$src" "$dst"; return 0
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    plan "ln -s $rel $dst   （相对拓扑）"
    return 0
  fi
  if ln -s "$rel" "$dst" 2>/dev/null; then
    # symlink 存活 + 断链检测（-e 解析目标；readlink 复核相对目标一致）
    if [ -e "$dst" ] && [ "$(readlink "$dst")" = "$rel" ]; then
      log "symlink: $dst -> $rel"
    else
      warn "symlink 断链，改 materialize: $dst"; rm -f "$dst"; _materialize "$src" "$dst"
    fi
  else
    warn "symlink 创建失败（文件系统不支持?），改 materialize: $dst"
    _materialize "$src" "$dst"
  fi
}

_materialize() {   # 拷贝真文件 + 写 provenance（不 sed 私有路径；只记来源相对名）
  local src="$1" dst="$2" dstdir; dstdir="$(dirname "$dst")"
  if [ "$DRY_RUN" -eq 1 ]; then plan "cp -R $src $dst  + 写 $dstdir/${PROVENANCE_FILE}（materialize 兜底）"; return 0; fi
  cp -R "$src" "$dst"
  {
    printf 'OpRunway materialized copy (symlink 不可用时兜底)\n'
    printf 'source_basename: %s\n' "$(basename "$src")"
    printf 'materialized_at: %s\n' "$(date +%Y-%m-%dT%H:%M:%S)"
    printf 'note: 由 init.sh --materialize 或 symlink 兜底生成；升级插件后需重跑 init.sh\n'
  } >> "$dstdir/$PROVENANCE_FILE"
  log "materialized: ${dst}（+ provenance）"
}

# ── 注册文件托管块（CLAUDE.md / AGENTS.md）────────────────────────────────────
# 块内不写任何绝对/私有路径：只声明「已装 + skills/agents 在 CLI 目录、靠 symlink 发现」
_reg_block() {
  cat <<EOF
$MANAGED_BEGIN
# OpRunway 算子验收插件已由 init.sh 安装（tool=$TOOL, level=${LEVEL}）。
# 编排/依赖/硬门单一事实源见本插件 AGENTS.md；能力 skill 与 agent 已链接到
# 本 CLI 的 skills/ 与 agents/ 目录（靠 symlink 发现，勿在此写死路径）。
$MANAGED_END
EOF
}

# 扫描托管块完整性：把 BEGIN/END 当配对定界符（$0== 整行相等，与移除 awk 同语义）。
# 打印 absent | present | anomaly：
#   absent  = 0 BEGIN & 0 END（干净未装）
#   present = 恰 1 对、成对、非嵌套、BEGIN 在 END 前
#   anomaly = 其余（缺 END / 缺 BEGIN / 多组 / 嵌套 / 顺序错）→ 调用方一律 abort 不改文件
_block_scan() {
  local f="$1"
  [ -f "$f" ] || { printf 'absent'; return 0; }
  awk -v b="$MANAGED_BEGIN" -v e="$MANAGED_END" '
    $0==b { nb++; depth++; if (depth>1) bad=1 }
    $0==e { ne++; if (depth==0) bad=1; else depth-- }
    END {
      if (nb==0 && ne==0) { print "absent"; exit }
      if (bad || depth!=0 || nb!=1 || ne!=1) { print "anomaly"; exit }
      print "present"
    }
  ' "$f"
}

# 原子移除唯一成对托管块：同目录 mktemp（O_EXCL 唯一名，拒 .tmp symlink 预植）+ 原子 mv。
# 前置条件：调用方已确认 _block_scan == present 且 REG_FILE 非 symlink。
_remove_block() {
  local f="$1" dir tmp mode
  dir="$(dirname "$f")"
  mode="$(_stat_mode "$f")"
  tmp="$(mktemp "$dir/.oprunway-reg.XXXXXX")" || die "mktemp 失败（同目录）: $dir"
  # shellcheck disable=SC2064
  trap 'rm -f "$tmp"' EXIT
  awk -v b="$MANAGED_BEGIN" -v e="$MANAGED_END" '
    $0==b {skip=1; next} $0==e {skip=0; next} skip!=1 {print}
  ' "$f" > "$tmp"
  mv -f "$tmp" "$f"
  trap - EXIT
  [ -n "$mode" ] && chmod "$mode" "$f" 2>/dev/null || true
  log "已移除托管块: $f"
}

install_reg() {
  _guard_regfile
  local st; st="$(_block_scan "$REG_FILE")"
  case "$st" in
    present) log "幂等跳过（托管块已在，成对唯一）: $REG_FILE"; return 0 ;;
    anomaly) die "标记异常（BEGIN/END 缺失/多组/嵌套/顺序错），拒绝改动文件: $REG_FILE" ;;
    absent)  ;;
    *)       die "内部错误：未知块状态 '$st'（${REG_FILE}）" ;;
  esac
  if [ "$DRY_RUN" -eq 1 ]; then
    plan "向 $REG_FILE 追加 OpRunway 托管块（若文件存在先备份）"
    return 0
  fi
  [ -e "$REG_FILE" ] && backup_file "$REG_FILE"
  ensure_dir "$(dirname "$REG_FILE")"
  printf '\n%s\n' "$(_reg_block)" >> "$REG_FILE"
  log "已写托管块 -> $REG_FILE"
}

# ── 安装 / 卸载主流程 ─────────────────────────────────────────────────────────
# -print0：目录/文件名含空格、glob 元字符、甚至换行都作为单条 NUL 定界记录传出，
# 消费端 `read -r -d ''` 按 NUL 读，杜绝换行拆分成伪路径。
_iter_skills() { find "$PLUGIN_ROOT/skills" -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null; }
_iter_agents() { find "$PLUGIN_ROOT/agents" -mindepth 1 -maxdepth 1 -type f -name '*.md' -print0 2>/dev/null; }

do_install() {
  log "PLUGIN_ROOT = $PLUGIN_ROOT"
  log "目标 BASE   = ${BASE}（tool=$TOOL, level=${LEVEL}）"
  log "注册文件    = $REG_FILE"
  log "skills 落点  = $SKILLS_DST"
  log "agents 落点  = $AGENTS_DST"
  [ "$DRY_RUN" -eq 1 ] && log "模式: DRY-RUN（零文件系统写）" || log "模式: 真实写入"
  _preflight_guard
  confirm

  install_reg

  local s a name
  while IFS= read -r -d '' s; do
    [ -n "$s" ] || continue
    name="$(basename "$s")"
    link_or_materialize "$s" "$SKILLS_DST/$name"
  done < <(_iter_skills)

  while IFS= read -r -d '' a; do
    [ -n "$a" ] || continue
    name="$(basename "$a")"
    link_or_materialize "$a" "$AGENTS_DST/$name"
  done < <(_iter_agents)

  log "完成（$([ "$DRY_RUN" -eq 1 ] && echo 干跑 || echo 已写)）。"
}

# 删一个「可证明本脚本所装」的落点：
#   symlink → readlink 解析物理目标，仅当指向 PLUGIN_ROOT 内才删，否则跳过并 warn；
#   真实文件/目录 → 仅当同目录 provenance 记录了它的 basename 才删（materialize 拷贝），否则保守跳过。
_rm_path() {
  local p="$1"
  _guard_target "$p"
  if [ -L "$p" ]; then
    local tgt abs phys
    tgt="$(readlink "$p")"
    case "$tgt" in /*) abs="$tgt" ;; *) abs="$(dirname "$p")/$tgt" ;; esac
    phys="$(cd "$(dirname "$abs")" 2>/dev/null && pwd -P || true)/$(basename "$abs")"
    case "$phys/" in
      "$PLUGIN_ROOT_PHYS"/*)
        if [ "$DRY_RUN" -eq 1 ]; then plan "rm symlink ${p}（指向本插件源，确认）"; else rm -f "$p"; log "删 symlink $p"; fi ;;
      *)
        warn "跳过（symlink 不指向本插件源 ${PLUGIN_ROOT}，疑非本脚本所装，保守不删）: $p -> $tgt" ;;
    esac
  elif [ -e "$p" ]; then
    _rm_materialized "$p"
  fi
}

# 删 materialize 拷贝：只删同目录 provenance 佐证 basename 的那个；无佐证保守跳过。
_rm_materialized() {
  local p="$1" dir base prov
  dir="$(dirname "$p")"; base="$(basename "$p")"; prov="$dir/$PROVENANCE_FILE"
  if [ -f "$prov" ] && grep -qxF "source_basename: $base" "$prov"; then
    if [ "$DRY_RUN" -eq 1 ]; then plan "rm -rf materialized ${p}（provenance 佐证本脚本所装）"
    else rm -rf "$p"; log "删 materialized ${p}（provenance 确认）"; fi
  else
    warn "跳过（非 symlink 且无 provenance 佐证本脚本所装，保守不删）: $p"
  fi
}

# 卸载收尾：若某 CLI 目录的 provenance 记录的 basename 已全部不在，清掉这份 provenance（best-effort）
_cleanup_provenance() {
  local dir prov
  dir="$1"; prov="$dir/$PROVENANCE_FILE"
  [ -f "$prov" ] || return 0
  _guard_target "$prov"
  local remain=0 line base
  while IFS= read -r line; do
    case "$line" in
      "source_basename: "*) base="${line#source_basename: }"; [ -e "$dir/$base" ] && remain=1 ;;
    esac
  done < "$prov"
  if [ "$remain" -eq 0 ]; then
    if [ "$DRY_RUN" -eq 1 ]; then plan "rm ${prov}（materialize 记录已全部卸载）"
    else rm -f "$prov"; log "清理 provenance: $prov"; fi
  fi
}

do_uninstall() {
  log "卸载: BASE=$BASE tool=$TOOL level=$LEVEL"
  [ "$DRY_RUN" -eq 1 ] && log "模式: DRY-RUN" || log "模式: 真实卸载"
  _preflight_guard
  confirm
  local s a name
  while IFS= read -r -d '' s; do
    [ -n "$s" ] || continue; name="$(basename "$s")"; _rm_path "$SKILLS_DST/$name"
  done < <(_iter_skills)
  while IFS= read -r -d '' a; do
    [ -n "$a" ] || continue; name="$(basename "$a")"; _rm_path "$AGENTS_DST/$name"
  done < <(_iter_agents)
  _cleanup_provenance "$SKILLS_DST"
  _cleanup_provenance "$AGENTS_DST"
  # 移除 CLAUDE.md/AGENTS.md 托管块（标记异常一律 abort 不动文件）
  _guard_regfile
  local st; st="$(_block_scan "$REG_FILE")"
  case "$st" in
    anomaly) die "标记异常（BEGIN/END 缺失/多组/嵌套/顺序错），拒绝改动文件: $REG_FILE" ;;
    present)
      if [ "$DRY_RUN" -eq 1 ]; then plan "从 $REG_FILE 移除 OpRunway 托管块（先备份）"
      else backup_file "$REG_FILE"; _remove_block "$REG_FILE"; fi ;;
    absent)  log "无托管块可移除: $REG_FILE" ;;
    *)       die "内部错误：未知块状态 '$st'（${REG_FILE}）" ;;
  esac
  log "卸载完成（备份 .bak.* 保留原处，可手工取用；本脚本不自动还原）。"
}

# ── 入口 ──────────────────────────────────────────────────────────────────────
printf '== OpRunway init.sh ==\n'
if [ "$UNINSTALL" -eq 1 ]; then do_uninstall; else do_install; fi
