#!/bin/bash
# M1 回归门一键脚本：三道检查全绿才算「无行为变化」。
#   1) 10 项派生物 SHA-256 与 sparse-r1-baseline-digests.txt 一致（gen_csv.py 两行除外）
#   2) cherk/sasum 示例 package.py check 退出码 0
#   3) 重跑 record_fixture.py 后 fixture.json 的 results 子树与重跑前逐字节一致
# 从仓根（worktree 根）执行：bash dev-doc/sparse-r1-regression-gate.sh
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
CG=$ROOT/plugin/skill/repo-task-blas-case-gen
EX=$CG/assets/example
FIX=$ROOT/dev-doc/sparse-r1-projection-fixture
DIG=$ROOT/dev-doc/sparse-r1-baseline-digests.txt
fail=0

echo "=== 1) baseline digests (10 derived artifacts) ==="
while read -r sum path; do
  case "$sum" in ''|'#'*) continue;; esac
  case "$path" in */gen_csv.py) continue;; esac
  actual=$(shasum -a 256 "$EX/$path" | cut -d' ' -f1)
  if [ "$actual" = "$sum" ]; then
    echo "  OK  $path"
  else
    echo "  DIFF $path"; fail=1
  fi
done < "$DIG"

echo "=== 2) example checks ==="
for op in cherk sasum; do
  ( cd "$EX/$op" && python3 "$CG/scripts/package.py" check --facts gen_csv.py >/dev/null 2>&1 )
  code=$?
  if [ "$code" -eq 0 ]; then echo "  OK  $op check"; else echo "  FAIL $op check exit=$code"; fail=1; fi
done

echo "=== 3) projection fixture gate (record_fixture.py --check) ==="
( cd "$FIX" && python3 record_fixture.py --check )
code=$?
if [ "$code" -eq 0 ]; then echo "  OK  fixture --check"; else echo "  FAIL fixture --check exit=$code"; fail=1; fi

if [ "$fail" -eq 0 ]; then echo "GATE: GREEN"; else echo "GATE: RED"; fi
exit $fail
