import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

ANCHOR = re.compile(
    r'<a href="\.\./\.\./([^/]+)/([^/]+)/README\.md">([^<]+)</a>'
)

@dataclass
class Operator:
    category: str
    op_dir: str
    name: str

    @property
    def relpath(self) -> str:
        return f"{self.category}/{self.op_dir}"

def parse_op_list(path: Path) -> List[Operator]:
    text = Path(path).read_text(encoding="utf-8")
    ops = []
    seen = set()
    for m in ANCHOR.finditer(text):
        category, op_dir, name = m.groups()
        key = (category, op_dir)
        if key in seen:
            continue
        seen.add(key)
        ops.append(Operator(category=category, op_dir=op_dir, name=name))
    return ops
