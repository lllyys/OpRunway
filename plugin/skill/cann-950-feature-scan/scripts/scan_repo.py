"""
scan_repo.py - Stage 1 Orchestrator

Coordinates all scanning modules to produce the intermediate JSON with:
- Loaded operator list from op_list.md
- For each operator: rules scan, README parse, inconsistency detection
- Unlisted operators detection
- Statistics and warnings
"""

import sys
from pathlib import Path
from typing import Dict, Any
import json

# 从项目根按路径直跑（`python3 <skill>/scripts/scan_repo.py`）时，`scripts` 不在
# sys.path 上。把 skill 根塞进去，让同包模块可导入；CWD 保持在项目根，产物才落对地方。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.op_list_parser import parse_op_list  # noqa: E402
from scripts.readme_parser import parse_readme_support_950  # noqa: E402
from scripts.rules import scan_simple_rules  # noqa: E402


def has_delegation_indicators(op_dir: Path) -> bool:
    """
    Check if operator delegates to another operator's kernel.

    Returns True if op_api or op_graph exist (indicating delegation pattern).
    Returns False if both are missing (indicating incomplete implementation).
    """
    return (op_dir / "op_api").exists() or (op_dir / "op_graph").exists()


def scan_repo(
    repo_root: Path,
    op_list_path: Path,
) -> Dict[str, Any]:
    """
    Orchestrate the complete scan of a repo.

    Args:
        repo_root: Root directory of the repo (where attention/ lives)
        op_list_path: Path to docs/zh/op_list.md

    Returns:
        Intermediate JSON dict with structure:
        {
            "repo": str (repo_root path),
            "operators": [
                {
                    "name": str,
                    "category": str,
                    "is_target": bool,
                    "readme_status": {
                        "exists": bool,
                        "support_950": str,
                    },
                    "rules_hit": {
                        "simt": [Hit, ...],
                        "hif8": [Hit, ...],
                        "regbase": [Hit, ...],
                    },
                    "inconsistency": str or None,
                    "missing_dir": bool (optional - True if operator directory doesn't exist),
                    "is_delegated": bool (optional - True if op_kernel missing but op_api/op_graph exist),
                },
                ...
            ],
            "unique_targets": [str, ...],   # sorted hit op names; cann-ops-run 仅在用户选 A5/950 专项时读取（resolve_ops fallback）
            "unlisted_ops": [str, ...],
            "stats": {
                "total_operators": int,
                "targets": int,
                "with_inconsistency": int,
            },
            "warnings": [str, ...],
        }
    """
    repo_root = Path(repo_root)
    op_list_path = Path(op_list_path)

    # Parse op_list.md
    ops_listed = parse_op_list(op_list_path)
    listed_names = {op.name for op in ops_listed}

    # Scan for each operator in the list
    operators = []
    scanned_names = set()
    warnings = []

    for op in ops_listed:
        scanned_names.add(op.name)
        op_dir = repo_root / op.relpath

        op_result = {
            "name": op.name,
            "category": op.category,
            "is_target": False,
            "readme_status": {},
            "rules_hit": {
                "simt": [],
                "hif8": [],
                "regbase": [],
            },
            "inconsistency": None,
        }

        # Check if directory exists
        if not op_dir.exists():
            op_result["missing_dir"] = True
            warnings.append(f"Directory not found: {op.relpath}")
            operators.append(op_result)
            continue

        # Parse README
        readme_path = op_dir / "README.md"
        readme_status = parse_readme_support_950(readme_path)
        op_result["readme_status"] = {
            "exists": readme_status.exists,
            "support_950": readme_status.support_950,
        }

        # Track if this is a delegated operator (has op_api/op_graph but no op_kernel)
        is_delegated = (
            not (op_dir / "op_kernel").exists()
            and has_delegation_indicators(op_dir)
        )
        if is_delegated:
            op_result["is_delegated"] = True

        # Scan code rules
        simple_hits = scan_simple_rules(op_dir)
        op_result["rules_hit"]["simt"] = [
            {"file": h.file, "line": h.line, "match": h.match} for h in simple_hits["simt"]
        ]
        op_result["rules_hit"]["hif8"] = [
            {"file": h.file, "line": h.line, "match": h.match} for h in simple_hits["hif8"]
        ]
        op_result["rules_hit"]["regbase"] = [
            {"file": h.file, "line": h.line, "match": h.match} for h in simple_hits["regbase"]
        ]

        # Determine if it's a target (any rule hit)
        has_any_hit = (
            bool(simple_hits["simt"])
            or bool(simple_hits["hif8"])
            or bool(simple_hits["regbase"])
        )
        op_result["is_target"] = has_any_hit

        # Detect inconsistencies
        if has_any_hit and readme_status.support_950 == "no":
            op_result["inconsistency"] = "code_hit_but_readme_no"
        elif readme_status.support_950 == "yes" and not has_any_hit:
            op_result["inconsistency"] = "readme_says_yes_no_code_evidence"

        operators.append(op_result)

    # Detect unlisted operators (scan attention/ directory)
    attention_dir = repo_root / "attention"
    if attention_dir.exists():
        for item in attention_dir.iterdir():
            if item.is_dir() and item.name not in listed_names:
                scanned_names.add(item.name)
                # Add to unlisted list (we'll report them later)

    # Build result
    result = {
        "repo": str(repo_root),
        "operators": operators,
        # cann-ops-run 的 resolve_ops() 以 unique_targets 为契约字段（命中算子名，去重排序）
        "unique_targets": sorted(op["name"] for op in operators if op["is_target"]),
        "unlisted_ops": sorted(scanned_names - listed_names),
        "stats": {
            "total_operators": len(operators),
            "targets": sum(1 for op in operators if op["is_target"]),
            "with_inconsistency": sum(1 for op in operators if op["inconsistency"]),
        },
        "warnings": warnings,
    }

    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Scan a CANN ops repository for 950-specific operators"
    )
    parser.add_argument("repo_root", help="Root directory of the repository")
    parser.add_argument("--op-list", required=True, help="Path to op_list.md")
    parser.add_argument("--output", required=True, help="Output path for intermediate.json")

    args = parser.parse_args()

    result = scan_repo(
        repo_root=Path(args.repo_root),
        op_list_path=Path(args.op_list),
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {args.output}")
