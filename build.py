#!/usr/bin/env python3
"""Merge menus.json (appendix table) and entries.json (reference entries) into commands.json."""
import json, re, sys, pathlib

SCRATCH = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(".")
OUT = pathlib.Path(__file__).parent / "commands.json"

menus = json.loads((SCRATCH / "menus.json").read_text())
entries_path = SCRATCH / "entries.json"
entries = json.loads(entries_path.read_text()) if entries_path.exists() else {}


RENAME = {"RS&NUM.SLV": "NUM.SLV", "RS&TIME": "TIME", "RS&CHARS": "CHARS", "LS&MODE": "MODE",
          "MTH/CONVERT": "MTH", "S.SLV": "SYMB SOLVER"}


def category(loc):
    """Classify one printed location into [top] or [top, sub]. Catch-alls are named explicitly."""
    loc = loc.replace("\u00a0", " ").strip()
    low = loc.lower()
    if "menuxy" in low or re.search(r"\bmenu\b", low):
        return ["MENU only"]            # reached with '<n> MENU' / '<n> DUP MENUXY'
    if low.startswith("cat"):
        return ["CAT only"]
    if low.startswith("in "):
        return ["Editors (EQW/FILER)"]
    if "typed in" in low or low.startswith("must"):
        return ["Typed only"]
    if low.startswith("alpha") or low.startswith("key") or loc in ("J",):
        return ["Keyboard"]
    if loc.startswith("\u00ab"):        # \u00abDIFF\u00bb style library menus
        return ["Library menus", loc.split()[0].strip("\u00ab\u00bb,")]
    toks = [t.strip("[]") for t in loc.split() if t != "NXT"]
    toks = ["PRG" if t == "PRG/TOOL" else t for t in toks]
    # drop page numbers / stray table junk after the first token
    toks = toks[:1] + [t for t in toks[1:] if not re.fullmatch(r"[\d./+\u2013-]+", t)]
    if not toks or toks[0].startswith("LS]"):
        return ["Other"]
    if toks[0] == "TOOL":
        return ["TOOL"]
    if toks[0].startswith("NUM.SLV") or toks[0] == "S.SLV":
        return [RENAME.get(toks[0], toks[0].split()[0])]
    toks = [RENAME.get(t, t) for t in toks]
    return toks[:2] if len(toks) > 1 else toks


topics = json.loads((SCRATCH / "topics.json").read_text()) if (SCRATCH / "topics.json").exists() else {}
topic_names = {t: [x["name"] for x in subs] for t, subs in topics.items()}
cmds = {}
for name, m in menus["commands"].items():
    e = entries.get(name) or entries.get(name.replace("-", "–"), {})
    cats, pages = [], []
    for loc in m.get("locations", []):
        c = category(loc)
        if c not in cats:
            cats.append(c)
            pages.append(loc.split().count("NXT") + 1)
    if not cats:
        cats, pages = [["Keyboard"]], [1]
    for t, subs in topics.items():
        for x in subs:
            if name in x["commands"]:
                cats.append([t, x["name"]])
                pages.append(1)
    cmds[name] = {
        "type": e.get("type") or {"C": "Command", "F": "Function"}.get(m.get("kind"), m.get("kind")),
        "summary": e.get("summary", ""),
        "stack": e.get("stack", []),
        "flags": e.get("flags", []),
        "see_also": e.get("see_also", []),
        "locations": m.get("locations", []),
        "menu_numbers": m.get("menus", []),
        "notes": m.get("notes", []),
        "categories": cats,
        "pages": pages,
        "origin": m.get("origin"),
    }

known = set(cmds)
for t, subs in topics.items():
    for x in subs:
        bad = [c for c in x["commands"] if c not in known]
        if bad:
            print(f"warning: topic {t} / {x['name']}: unknown commands {bad}")

OUT.write_text(json.dumps({"commands": cmds, "menu_legend": menus.get("menu_legend", {}),
                           "topics": topic_names,
                           "topic_blurbs": {t: {x["name"]: x["blurb"] for x in subs} for t, subs in topics.items()}},
                          ensure_ascii=False, indent=1))
print(f"{len(cmds)} commands, {sum(1 for c in cmds.values() if c['summary'])} with summaries -> {OUT}")
