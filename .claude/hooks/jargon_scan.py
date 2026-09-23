#!/usr/bin/env python3
"""自造词候选扫描：本仓文档高频、参考语料里罕见的中文词。

写作者看不出自己的用词习惯，黑名单不能凭语感列。本脚本按汉字 n-gram
对照两份语料，列出候选，由人逐个定替代写法后写进 doc_style_lint.py 的词表。

用法：

    python3 .claude/hooks/jargon_scan.py --ref <参考语料目录>...
    python3 .claude/hooks/jargon_scan.py --ref third_party/ATK ../ops-nn --top 200

参考语料取人写的领域文档，例如 ATK 文档与 CANN 算子仓文档。目录由调用方
传入，不写死路径。输出是候选，不是结论：常用词在小语料里也可能罕见。

退出码：0 正常；2 参考语料为空。
"""

import argparse
import collections
import math
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import doc_style_lint as lint              # noqa: E402

CJK_RUN = re.compile(r"[一-鿿]+")


def repo_files(root):
    """扫描对象：lint 覆盖的文档，加各 skill 的开发文档与仓根 CLAUDE.md。"""
    extra = (sorted(root.glob("skill/*/CLAUDE.md"))
             + sorted(root.glob("plugin/skill/*/CLAUDE.md"))
             + [f for f in [root / "CLAUDE.md", root / "plugin" / "CLAUDE.md"]
                if f.is_file()])
    return [f for f in lint.collect(root) + extra if f.is_file()]


def ref_files(dirs):
    out = []
    for d in dirs:
        out += [f for f in pathlib.Path(d).rglob("*.md") if ".git" not in f.parts]
    return out


def ngrams(files, sizes):
    counts = collections.Counter()
    chars = 0
    for f in files:
        try:
            text = lint.blank_code(f.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        for run in CJK_RUN.findall(text):
            chars += len(run)
            for n in sizes:
                for i in range(len(run) - n + 1):
                    counts[run[i:i + n]] += 1
    return counts, chars


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref", nargs="+", required=True, help="参考语料目录")
    parser.add_argument("--root", default=".", help="仓根")
    parser.add_argument("--min-count", type=int, default=8, help="本仓出现次数下限")
    parser.add_argument("--top", type=int, default=150)
    args = parser.parse_args()

    root = pathlib.Path(args.root).resolve()
    sizes = (2, 3, 4)
    mine, mine_chars = ngrams(repo_files(root), sizes)
    theirs, ref_chars = ngrams(ref_files(args.ref), sizes)
    if not ref_chars:
        print("参考语料里没有中文 markdown，检查 --ref 目录")
        return 2

    rows = []
    for gram, k in mine.items():
        if k < args.min_count:
            continue
        rate_mine = k / mine_chars * 1e6
        rate_ref = (theirs.get(gram, 0) + 0.5) / ref_chars * 1e6
        score = math.log(rate_mine / rate_ref) * math.log(k)
        rows.append((score, gram, k, theirs.get(gram, 0)))
    rows.sort(reverse=True)

    # 长串的片段不重复列：「判据表」已列时不再列「据表」
    shown = []
    for row in rows:
        gram = row[1]
        if any(gram in s[1] and s[2] <= row[2] * 1.2 for s in shown):
            continue
        shown.append(row)
        if len(shown) >= args.top:
            break

    print(f"本仓 {mine_chars} 字，参考语料 {ref_chars} 字")
    print(f"{'候选':<8}{'本仓次数':>8}{'参考次数':>8}")
    for _, gram, k, r in shown:
        print(f"{gram:<8}{k:>8}{r:>8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
