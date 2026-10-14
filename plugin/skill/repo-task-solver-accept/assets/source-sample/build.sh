#!/usr/bin/env bash
set -euo pipefail
soc=ascend910_93
for arg in "$@"; do
  case "$arg" in
    --soc=*) soc=${arg#*=} ;;
    --ops=sgetrf) ;;
    *) echo "Usage: bash build.sh [--ops=sgetrf] [--soc=ascend950|ascend910_93]" >&2; exit 2 ;;
  esac
done
root=$(cd -- "$(dirname -- "$0")" && pwd)
cmake -S "$root" -B "$root/build" -DSOC_VERSION="$soc" -DCMAKE_BUILD_TYPE=Release
cmake --build "$root/build" --parallel 2
cmake --install "$root/build" --prefix "$root/build_out"
