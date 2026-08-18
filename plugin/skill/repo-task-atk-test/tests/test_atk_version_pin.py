"""子模块锁定的 ATK 版本，必须是能力矩阵实测过的那一版。

能力矩阵是从某一版 ATK 反射与实测得来的，换一版 ATK 它未必还成立。

在这组用例之前，两个版本号分别躺在两处，中间没有任何东西核对：
一处是子模块里 ATK 自报的 `PACKAGE_VERSION`，
一处是 `references/atk-parameter-capabilities.json` 里人手写的 `atk_versions`。

后果是升级子模块不会有任何红灯，矩阵继续声称自己按旧版实测，
而实际装上的已经不是那一版了。「版本不一致时重探」当时只是一句写给人看的提示。

按红线 4，判据要从数据推导，不能靠人自觉，所以把这次核对做成机械判据。

skill 部署到跑测机后是一个普通目录，那里没有 `third_party/`，
核对无从谈起，跳过而不是失败——否则 agent 在跑测机上自查量具时会看到一条假红。
"""

import json
import re
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_ROOT.parents[1]
THIRD_PARTY = REPO_ROOT / "third_party"
ATK_INIT = THIRD_PARTY / "ATK" / "atk" / "__init__.py"
MATRIX = SKILL_ROOT / "references" / "atk-parameter-capabilities.json"

REPROBE = ("对不上时跑 scripts/probe_atk_capabilities.py 重探，"
           "拿实测结果替换矩阵，并把新版本号写进 atk_versions。")


def matrix():
    return json.loads(MATRIX.read_text(encoding="utf-8"))


def submodule_version():
    """从源码文本里取版本号，不 import atk。

    这条核对要在没装 ATK 的机器上也能跑，import 会把它绑死在装机状态上。
    """
    found = re.search(r'^PACKAGE_VERSION\s*=\s*["\']([^"\']+)["\']',
                      ATK_INIT.read_text(encoding="utf-8"), re.M)
    return found.group(1) if found else None


class SubmoduleVersionTest(unittest.TestCase):
    def setUp(self):
        if not THIRD_PARTY.is_dir():
            self.skipTest("不在本仓工作区里，没有子模块可核对")

    def test_submodule_is_checked_out(self):
        self.assertTrue(
            ATK_INIT.is_file(),
            f"{ATK_INIT} 不存在，ATK 子模块没拉下来。\n"
            "  → 跑 git submodule update --init")

    def test_the_version_is_readable_from_source(self):
        if not ATK_INIT.is_file():
            self.skipTest("子模块未拉取，另一条用例已经红了")
        self.assertIsNotNone(
            submodule_version(),
            f"{ATK_INIT} 里读不到 PACKAGE_VERSION，ATK 改了版本号的写法。\n"
            "  → 改本文件的正则去匹配新写法，不要绕过这次核对")

    def test_submodule_version_is_one_the_matrix_covers(self):
        if not ATK_INIT.is_file():
            self.skipTest("子模块未拉取，另一条用例已经红了")
        pinned = submodule_version()
        covered = matrix()["atk_versions"]
        self.assertIn(
            pinned, covered,
            f"子模块锁的是 ATK {pinned}，但能力矩阵只覆盖 {covered}。\n"
            f"  → {REPROBE}")


class MatrixSelfConsistencyTest(unittest.TestCase):
    """矩阵内部两个版本号也要对得上，这条不需要子模块。"""

    def test_probed_version_is_declared_as_covered(self):
        data = matrix()
        probed = data["probe"]["probed_atk_version"]
        covered = data["atk_versions"]
        self.assertIn(
            probed, covered,
            f"矩阵自称从 ATK {probed} 探出，却没把它列进 atk_versions {covered}。\n"
            "  → 两处写的是同一件事，改一处就要改另一处")


if __name__ == "__main__":
    unittest.main()
