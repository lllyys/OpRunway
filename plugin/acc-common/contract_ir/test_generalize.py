"""泛化冒烟测试：同一份 prober 零改跑一大批域内算子，证 #0（无 op 名分支、换算子零改）。
⚠ 需 clone 仓在场（gitignore、本机有）；缺则 skip。"""
import glob
import os
import unittest

import prober

HERE = os.path.dirname(os.path.abspath(__file__))
REPOS = os.path.abspath(os.path.join(HERE, "..", "..", "..", "repos"))
ROOTS = ["ops-nn/foreach", "ops-math/math", "ops-math/experimental/math",
         "ops-cv/image", "ops-nn/activation", "ops-nn/index", "ops-math/conversion"]


def _op_dirs(limit=40):
    dirs = set()
    for r in ROOTS:
        for h in glob.glob(os.path.join(REPOS, r, "**", "op_api", "aclnn_*.h"), recursive=True):
            if "_v2.h" in h or "build" in h:
                continue
            dirs.add(os.path.dirname(os.path.dirname(os.path.dirname(h))))
    return sorted(dirs)[:limit]


@unittest.skipUnless(os.path.isdir(REPOS), "repos/ 未在场")
class TestGeneralize(unittest.TestCase):
    def test_one_prober_extracts_many_ops(self):
        dirs = _op_dirs()
        if len(dirs) < 10:
            self.skipTest(f"可扫算子 {len(dirs)} 个 < 10")
        ok, failclosed, crashed = 0, 0, []
        for d in dirs:
            try:
                ir = prober.probe(d)
                self.assertIn("exec_symbol", ir["aclnn_entry"])
                ok += 1
            except SystemExit:
                failclosed += 1  # 域外/非标准，诚实拒绝——合法
            except Exception as e:  # noqa: BLE001
                crashed.append((os.path.basename(d), f"{type(e).__name__}: {e}"))
        # 不许崩（fail-closed 允许，未捕获异常不允许）
        self.assertEqual(crashed, [], f"prober 崩溃（非 fail-closed）：{crashed}")
        # 绝大多数应零改抠出签名
        self.assertGreaterEqual(ok / len(dirs), 0.9, f"泛化率 {ok}/{len(dirs)} < 90%")


if __name__ == "__main__":
    unittest.main()
