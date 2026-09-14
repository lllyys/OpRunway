"""
render_report.py - Stage 2 Renderer

Renders intermediate JSON to markdown reports using Jinja2 templates.
"""

import json
from pathlib import Path
from typing import Tuple
from jinja2 import Environment, FileSystemLoader, select_autoescape


def _make_env(templates_dir: Path) -> Environment:
    """Create a Jinja2 environment configured for markdown rendering."""
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(disabled_extensions=("md", "j2")),
        trim_blocks=False,
        lstrip_blocks=False,
    )


def _transform_intermediate(intermediate: dict) -> dict:
    """
    Transform intermediate JSON to template format.

    The templates expect 'op.readme.support_950' but the intermediate
    has 'op.readme_status.support_950'. Also normalize other fields.
    """
    import copy
    from datetime import datetime

    # Deep-copy so adding template-only fields (total_ops/by_rule/scanned_at …)
    # to result["stats"] doesn't mutate the caller's dict — write_reports writes
    # the *original* intermediate to _intermediate.json, which must stay clean.
    result = copy.deepcopy(intermediate)

    # Transform operators
    operators = []
    for op in result.get("operators", []):
        op_copy = dict(op)

        # Rename readme_status to readme for template compatibility
        if "readme_status" in op_copy:
            op_copy["readme"] = op_copy.pop("readme_status")
        else:
            op_copy["readme"] = {"exists": False, "support_950": "absent"}

        operators.append(op_copy)

    result["operators"] = operators

    # Normalize stats field names and compute by_rule counts
    if "stats" in result:
        stats = result["stats"]
        stats["total_ops"] = stats.get("total_operators", stats.get("total_ops", 0))
        stats["target_count"] = stats.get("targets", stats.get("target_count", 0))

        # Count operators by rule
        by_rule = {
            "simt": 0,
            "hif8": 0,
            "regbase": 0,
        }
        for op in result["operators"]:
            if op["rules_hit"].get("simt"):
                by_rule["simt"] += 1
            if op["rules_hit"].get("hif8"):
                by_rule["hif8"] += 1
            if op["rules_hit"].get("regbase"):
                by_rule["regbase"] += 1
        stats["by_rule"] = by_rule

    # Add scanned_at timestamp if missing
    if "scanned_at" not in result:
        result["scanned_at"] = datetime.now().isoformat()

    return result


def render_reports(intermediate: dict, templates_dir: Path) -> Tuple[str, str]:
    """
    Render summary and detail reports from intermediate JSON.

    Args:
        intermediate: Intermediate JSON dict from scan_repo
        templates_dir: Path to templates directory

    Returns:
        Tuple of (summary_md, detail_md) strings
    """
    env = _make_env(templates_dir)
    data = _transform_intermediate(intermediate)

    summary = env.get_template("summary.md.j2").render(**data)
    detail = env.get_template("detail.md.j2").render(**data)

    return summary, detail


def write_reports(
    intermediate: dict,
    out_dir: Path,
    templates_dir: Path,
) -> None:
    """
    Render and write reports to output directory.

    Creates three files:
    - summary.md: High-level operator table and inconsistency warnings
    - detail.md: Evidence details for each target operator
    - _intermediate.json: Copy of intermediate JSON for reference

    Args:
        intermediate: Intermediate JSON dict from scan_repo
        out_dir: Output directory (created if missing)
        templates_dir: Path to templates directory
    """
    summary, detail = render_reports(intermediate, templates_dir)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "summary.md").write_text(summary, encoding="utf-8")
    (out_dir / "detail.md").write_text(detail, encoding="utf-8")
    (out_dir / "_intermediate.json").write_text(
        json.dumps(intermediate, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Render summary and detail reports from intermediate JSON"
    )
    parser.add_argument("intermediate", help="Path to intermediate.json")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--templates",
                        default=str(Path(__file__).resolve().parent.parent / "templates"),
                        help="模板目录。默认取本 skill 自带的 templates/；命令在项目根跑，不要传相对 CWD 的路径")

    args = parser.parse_args()

    data = json.loads(Path(args.intermediate).read_text(encoding="utf-8"))
    write_reports(data, Path(args.out), Path(args.templates))
    print(f"wrote {args.out}/summary.md and detail.md")
