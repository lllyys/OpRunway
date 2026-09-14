"""按待处理文件所属的 skill 侧，隔离各侧同名模块。

各 skill 各自自足，`tests/` 与 `scripts/` 下会出现同名模块——现在是
`probe_env.py`，生成侧与 ATK 跑测侧各一份。测试用 `sys.path.insert` 加裸 import
定位它们，于是谁先被收集谁就把名字占进 `sys.modules`，后收集的那一侧再
import 拿到的是别人的模块，`SKILL_ROOT` 指向别人的目录。

症状是单独跑每一侧全绿，`pytest skill/` 一聚合就是成片的
ModuleNotFoundError，且报错落在哪一侧取决于命令行里谁写在前面。

`pytest.ini` 的 `--import-mode=importlib` 解决不了这件事：它按路径决定
pytest 自己给模块起的名字，管不到测试代码手写的 `sys.path` 定位。所以隔离
放在仓根这唯一一份 conftest 里。

按侧放 `tests/conftest.py` 是允许的（`cann-issue-report` 与 `cann-issue-track`
各有一份），**但那个 `tests/` 不能是包**：只要目录下有 `__init__.py`，
importlib 模式给两侧算出的插件名同为 `tests.conftest`，pytest 以
`ValueError: Plugin already registered under a different name` 中断整场收集，
而分侧单跑全绿。本仓三份空 `__init__.py` 已删，无人 `from tests import`。

**换手而不是逐出。** 离开一侧时把它的模块从 `sys.modules` 摘下来存好，
回到这一侧时原样挂回去，每个模块对象全程只创建一次。逐出的做法会让
下一次 import 造出同一模块的第二份，类对象身份随之改变，已经
`from verdict import GateFailure` 的测试再 `assertRaises` 就认不出新抛的
那个——表现为"明明抛了 GateFailure 却判失败"。

换手挂两处，因为 pytest 先收集完全部模块再开跑：`makemodule` 管导入期，
`runtest_setup` 管运行期。只挂导入期的话，跑到第一侧时现场已经被最后
导入的那一侧占了。
"""

import sys
from pathlib import Path

SKILL_HOME = Path(__file__).resolve().parent / "skill"

# 已摘下的模块，按侧存放：{侧目录: {模块名: 模块对象}}。
_parked = {}
_current = None


def _side_of(path):
    """返回该路径所属的 skill 侧目录，不在任何一侧则返回 None。"""
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(SKILL_HOME):
        return None
    return SKILL_HOME / resolved.relative_to(SKILL_HOME).parts[0]


def _owner_of(module):
    """返回该模块所属的 skill 侧目录；不属于任何一侧返回 None。"""
    origin = getattr(module, "__file__", None)
    if not origin:
        return None
    try:
        return _side_of(origin)
    except OSError:
        return None


def _park_others(side):
    """把不属于 side 的侧内模块摘下来存好。"""
    for name, module in list(sys.modules.items()):
        owner = _owner_of(module)
        if owner is None or owner == side:
            continue
        _parked.setdefault(owner, {})[name] = module
        del sys.modules[name]


def _restore(side):
    """把先前存下的 side 模块原样挂回去。"""
    for name, module in _parked.pop(side, {}).items():
        sys.modules.setdefault(name, module)


def _prefer(side):
    # 三个入口都要：`import verdict`（裸模块）走 scripts/，`from scripts import dedup`
    # （包）走 side 本身。两种写法在本仓并存，缺哪个都是成片的 ModuleNotFoundError。
    for entry in (side, side / "tests", side / "scripts"):
        value = str(entry)
        while value in sys.path:
            sys.path.remove(value)
        sys.path.insert(0, value)


def _switch_to(path):
    global _current
    side = _side_of(path)
    if side is None or side == _current:
        return
    _park_others(side)
    _restore(side)
    _prefer(side)
    _current = side


def pytest_pycollect_makemodule(module_path, parent):
    """导入本侧测试模块前换手。返回 None 走 pytest 默认收集。"""
    _switch_to(module_path)
    return None


def pytest_runtest_setup(item):
    """跑本侧每条用例前换手，覆盖用例体内的延迟 import。"""
    _switch_to(item.path)
