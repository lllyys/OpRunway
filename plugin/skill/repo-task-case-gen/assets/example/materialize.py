"""物化脚本模板：把 must_cover 的语义组合填成具体 shape 与 attr 取值。

`make_must_cover.py` 只产出语义轴的取值（dtype / rank / size_class / ...），
具体 shape 和 attr 取什么，需要按算子契约逐条填写——这一步就是本脚本。

规模档摊到各轴那段推导与算子无关，由 `scripts/_shapes.py` 提供，不要重写。

复制这份文件，改两处即可：
1. `fill()`      每条 combo 要补哪些键（本例是 shape 和 dim）
2. `TARGETED`    按 coverage_policy.targeted 补的边界用例

本机 golden 预算更紧时，给 `shape_for` 传 `numel=` 覆盖默认档位。

写完运行：
    source evidence/env.sh && <python> materialize.py <must_cover.json> <materialized.json>
再用 materialized.json 跑 make_yaml.py / check_coverage.py / validate_cases.py。
"""

import json
import os
import sys

# 量具目录由 evidence/env.sh 固化成 ATK_SKILL_DIR。压缩上下文后靠 find
# 现找 skill 目录，真机上跑到过前一天的陈旧副本。
SKILL_DIR = os.environ.get("ATK_SKILL_DIR")
if not SKILL_DIR:
    raise SystemExit(
        "没有 ATK_SKILL_DIR：先 source evidence/env.sh"
        "（由 probe_env.py --env-sh 产出）")
sys.path.insert(0, os.path.join(SKILL_DIR, "scripts"))

from _shapes import axis_for, shape_for, shape_for_form  # noqa: E402


def fill(combo, index):
    """给一条 combo 补上 YAML 和覆盖核对需要的具体取值。

    补的键要和 decl.json 的 `extract` 与 `parameters.*.combo_key` 对得上，
    否则 make_yaml 会报「combos 里没有 <key>」，check_coverage 会命中 0 组。

    有 `shape_form` 轴时**一律走 `shape_for_form`**，不要自己分派：
    `shape_for` 默认 `ragged=True`，会让某一根轴取 `2^n-1`，那正是
    `unaligned_tail` 的定义——normal 于是和它撞成同一个形状，重复 combo
    门禁退回，物化要重跑一遍。
    """
    rank = combo["rank"]
    if "shape_form" in combo:
        combo["shape"] = shape_for_form(rank, combo["size_class"],
                                        combo["shape_form"], index)
    else:
        combo["shape"] = shape_for(rank, combo["size_class"], index)
    combo["dim"] = axis_for(rank, combo["reduce_axis_pos"])
    return combo


# coverage_policy.targeted 里声明的标签，每个都要有 combo 带上，
# 否则 audit_coverage 报「combos 缺少定向覆盖标签」。
TARGETED = [
    {"tag": "empty", "rank": 2, "size_class": "small",
     "reduce_axis_pos": "last", "keepdim": False, "shape": [0, 4], "dim": 1},
    {"tag": "single_element", "rank": 1, "size_class": "small",
     "reduce_axis_pos": "first", "keepdim": False, "shape": [1], "dim": 0},
]


def main(src, dst):
    spec = json.load(open(src, encoding="utf-8"))
    combos = [fill(dict(combo), index)
              for index, combo in enumerate(spec["combos"])]

    # 定向用例沿用主用例集的 dtype 轴取值，避免多引入一个未声明的取值。
    dtype = spec["coverage_policy"]["baseline"]["dtype"]
    for extra in TARGETED:
        combo = {key: value for key, value in extra.items() if key != "tag"}
        combo["dtype"] = dtype
        combo["coverage_tags"] = [extra["tag"]]
        combos.append(combo)

    spec["combos"] = combos
    json.dump(spec, open(dst, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"物化 {len(combos)} 条（含定向 {len(TARGETED)} 条）→ {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
