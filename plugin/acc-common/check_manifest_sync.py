"""清单同步校验（P0）——`AGENTS.md` 是跨 CLI 单一事实源；本脚本验它与 `plugin.json`
+ 实际 agent/skill 文件一致，**杜绝双写漂移**（cannbot 那种手抄两份清单的坑）。

用法: python3 check_manifest_sync.py [--plugin-root <plugin 根>]
默认 plugin 根 = 本脚本(acc-common)的上一级。只读、stdlib、抗坏输入。打印 `STATUS: SYNCED|DRIFT`，exit 0/1。
"""
import argparse, json, os, sys


def _parse_frontmatter(path):
    """极简 YAML frontmatter（首尾 `---`）解析：标量→str，block/flow 列表→list。不引 yaml 依赖。
    跳过空行与 `#` 注释；`key: [a, b]` 识别为列表；不认识的缩进行退出列表上下文。
    文件不存在→None；无 frontmatter→{}。"""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out, cur = {}, None
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if not ln[:1].isspace() and ":" in s:  # 顶层 key
            key, _, rest = s.partition(":")
            key, rest = key.strip(), rest.strip()
            if rest.startswith("[") and rest.endswith("]"):  # flow list
                out[key] = [x.strip().strip("'\"") for x in rest[1:-1].split(",") if x.strip()]
                cur = None
            elif rest:  # 标量
                out[key] = rest.strip("'\"")
                cur = None
            else:  # 期待后续 block list
                out[key] = []
                cur = key
        elif cur is not None and s.startswith("- "):  # block list 项
            out[cur].append(s[2:].strip().strip("'\""))
        else:
            cur = None  # 不认识的缩进行 → 退出列表上下文
    return out


def _stem(p):
    b = os.path.basename(p)
    return b[:-3] if b.endswith(".md") else b


def main(argv):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="AGENTS.md ↔ plugin.json ↔ 文件 同步校验")
    ap.add_argument("--plugin-root", default=os.path.dirname(here))
    a = ap.parse_args(argv)
    root = a.plugin_root
    errs = []

    fm = _parse_frontmatter(os.path.join(root, "AGENTS.md"))
    if fm is None:
        print("  ✗ 缺 AGENTS.md（跨 CLI 单一事实源）")
        print("STATUS: DRIFT")
        return 1
    for req in ("name", "description"):
        if not fm.get(req):
            errs.append(f"AGENTS.md frontmatter 缺 {req}")
    agents = fm.get("agents") if isinstance(fm.get("agents"), list) else []
    skills = fm.get("skills") if isinstance(fm.get("skills"), list) else []
    if not agents:
        errs.append("AGENTS.md 未声明 agents（或写成了非列表/flow 未识别）")

    for ag in agents:
        if not os.path.exists(os.path.join(root, "agents", ag + ".md")):
            errs.append(f"AGENTS.md 声明 agent {ag!r} 但缺 agents/{ag}.md")
    for sk in skills:
        if not os.path.exists(os.path.join(root, "skills", sk, "SKILL.md")):
            errs.append(f"AGENTS.md 声明 skill {sk!r} 但缺 skills/{sk}/SKILL.md")

    pj_path = os.path.join(root, ".claude-plugin", "plugin.json")
    if not os.path.exists(pj_path):
        errs.append("缺 .claude-plugin/plugin.json")
    else:
        try:
            with open(pj_path, encoding="utf-8") as f:
                pj = json.load(f)
            pj_agents = {_stem(p) for p in pj.get("agents", [])}
            if pj_agents != set(agents):
                errs.append(f"plugin.json agents {sorted(pj_agents)} ≠ AGENTS.md agents {sorted(agents)}（双写漂移）")
        except (json.JSONDecodeError, OSError) as ex:
            errs.append(f"plugin.json 解析失败：{ex}")

    for e in errs:
        print(f"  ✗ {e}")
    ok = not errs
    if ok:
        print(f"  AGENTS.md(name={fm.get('name')}): agents={agents} skills={skills} ↔ plugin.json 一致")
    print(f"STATUS: {'SYNCED' if ok else 'DRIFT'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
