import re
from dataclasses import dataclass
from pathlib import Path

ROW_950 = re.compile(
    r'\|\s*(?:<term>)?\s*Ascend\s*950(?:[A-Z]+)?(?:/Ascend\s*950[A-Z]+)?\s*(?:</term>)?\s*\|\s*(√|×)\s*\|'
)
HAS_MATRIX = re.compile(r'是否支持')

@dataclass
class ReadmeStatus:
    exists: bool
    support_950: str   # "yes" | "no" | "unknown" | "absent"
    raw_match: str = ""

def parse_readme_support_950(path: Path) -> ReadmeStatus:
    p = Path(path)
    if not p.exists():
        return ReadmeStatus(exists=False, support_950="absent")
    text = p.read_text(encoding="utf-8", errors="replace")
    m = ROW_950.search(text)
    if m:
        return ReadmeStatus(
            exists=True,
            support_950="yes" if m.group(1) == "√" else "no",
            raw_match=m.group(0),
        )
    if HAS_MATRIX.search(text):
        return ReadmeStatus(exists=True, support_950="unknown")
    return ReadmeStatus(exists=True, support_950="absent")
