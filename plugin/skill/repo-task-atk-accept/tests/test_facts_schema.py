"""`facts_schema.py` 与脚本里的读取点必须一一对上。

**这是防漂移的那道闸**：字段表一旦落后于脚本，下一个人还是只能去 grep 源码，
而那正是这份表要消除的动作。两个方向都查——漏登记会让人以为不用填，
留着已经没人读的字段会让人白填。
"""

import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import facts_schema  # noqa: E402

READ = re.compile(r'facts(?:_data)?(?:\.get\("([a-z_]+)"|\["([a-z_]+)"\])')


def _fields_read():
    found = {}
    for script in sorted(SCRIPTS.glob("*.py")):
        if script.name == "facts_schema.py":
            continue
        for match in READ.finditer(script.read_text(encoding="utf-8")):
            found.setdefault(match.group(1) or match.group(2), set()).add(script.name)
    return found


def test_every_field_the_scripts_read_is_registered():
    missing = sorted(set(_fields_read()) - set(facts_schema.FIELDS))
    assert not missing, f"这些字段脚本在读，facts_schema.py 里没有：{missing}"


def test_no_stale_field_is_left_in_the_table():
    stale = sorted(set(facts_schema.FIELDS) - set(_fields_read()))
    assert not stale, f"这些字段表里有，已经没有脚本读：{stale}"


def test_readers_column_names_the_real_readers():
    for field, readers in _fields_read().items():
        listed = facts_schema.FIELDS[field][0]
        for script in readers:
            assert script in listed, f"{field} 由 {script} 读，「谁读」那列没写它"


def test_table_renders_one_row_per_field():
    rows = facts_schema.as_table().splitlines()
    assert len(rows) == len(facts_schema.FIELDS) + 2
