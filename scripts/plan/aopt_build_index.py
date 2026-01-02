
# (content same as prepared above; re-injected)
from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import re
import yaml

def parse_iso_datetime(s: Optional[str]) -> Optional[dt.datetime]:
    if not s:
        return None
    try:
        s2 = s.replace("Z", "")
        return dt.datetime.fromisoformat(s2)
    except Exception:
        try:
            return dt.datetime.combine(dt.date.fromisoformat(str(s)[:10]), dt.time(0,0))
        except Exception:
            return None

def parse_iso_date(s: Optional[str]) -> Optional[dt.date]:
    if not s:
        return None
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except Exception:
        return None

def parse_duration_minutes(x: Any) -> Optional[float]:
    if x is None: return None
    if isinstance(x, (int, float)): return float(x)
    if isinstance(x, str):
        s = x.strip().lower()
        m = re.match(r"^(\d{1,2}):(\d{2})$", s)
        if m: return int(m.group(1))*60 + int(m.group(2))
        m = re.match(r"^(?:(\d+(?:\.\d+)?)h)?(?:(\d+)m)?$", s)
        if m and (m.group(1) or m.group(2)):
            h = float(m.group(1)) if m.group(1) else 0.0
            mm = float(m.group(2)) if m.group(2) else 0.0
            return h*60 + mm
        m = re.match(r"^(\d+(?:\.\d+)?)\s*h$", s)
        if m: return float(m.group(1))*60
        m = re.match(r"^(\d+)\s*m$", s)
        if m: return float(m.group(1))
        try: return float(s)
        except Exception: return None
    return None

def read_yaml(p: Path) -> Dict[str, Any]:
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}

@dataclass
class Node:
    uid: str
    kind: str
    title: str
    status: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    due: Optional[str] = None
    duration_minutes: Optional[float] = None
    area_uid: Optional[str] = None
    objective_uid: Optional[str] = None
    project_uid: Optional[str] = None
    file_path: Optional[str] = None

def best_title(d: Dict[str, Any], fallback: str) -> str:
    for k in ("title", "name", "name_cn", "id"):
        v = d.get(k)
        if v: return str(v)
    return fallback

def infer_kind_from_path(path: Path) -> Optional[str]:
    names = [n.lower() for n in path.parts]
    for n in names:
        if n in ("area", "areas"): return "area"
        if n in ("objective", "objectives"): return "objective"
        if n in ("project", "projects"): return "project"
        if n in ("task", "tasks"): return "task"
    return None

def detect_roots(base: Path) -> Dict[str, Path]:
    def pick(*cands):
        for c in cands:
            p = base / c
            if p.exists() and p.is_dir(): return p
        return None
    return {
        "areas": pick("areas", "area"),
        "objectives": pick("objectives", "objective"),
        "projects": pick("projects", "project"),
        "tasks": pick("tasks", "task"),
    }

def collect_yaml(root: Optional[Path]) -> List[Path]:
    if not root: return []
    out: List[Path] = []
    for p in root.rglob("*"):
        if p.suffix.lower() in (".yml", ".yaml"):
            out.append(p)
    return out

def to_node(p: Path) -> Optional[Node]:
    data = read_yaml(p)
    if not isinstance(data, dict): return None
    uid = str(data.get("uid") or p.stem)
    kind = data.get("kind") or infer_kind_from_path(p) or "task"
    title = best_title(data, p.stem)
    status = data.get("status")
    start = data.get("start")
    end = data.get("end")
    due = data.get("due") or data.get("date") or data.get("deadline") or data.get("when")
    duration = data.get("duration_minutes")
    if duration is None and data.get("duration") is not None:
        duration = parse_duration_minutes(data.get("duration"))
    area_uid = data.get("area_uid")
    objective_uid = data.get("objective_uid")
    project_uid = data.get("project_uid")
    return Node(uid=uid, kind=kind, title=str(title), status=(status or None),
                start=(start or None), end=(end or None), due=(due or None),
                duration_minutes=(float(duration) if isinstance(duration, (int,float)) else duration),
                area_uid=(area_uid or None), objective_uid=(objective_uid or None),
                project_uid=(project_uid or None), file_path=str(p))

def attach_inferred_parents(node: Node, known: Dict[str, Dict[str, Node]]):
    if node.kind == "task":
        if not node.project_uid and node.file_path:
            for pp in [Path(node.file_path).parent] + list(Path(node.file_path).parents):
                cand = pp.name
                if cand in known["project"]:
                    node.project_uid = cand
                    break
        if not node.area_uid and node.project_uid:
            proj = known["project"].get(node.project_uid)
            if proj and proj.area_uid:
                node.area_uid = proj.area_uid
    elif node.kind == "project":
        if not node.area_uid and node.file_path:
            for pp in Path(node.file_path).parents:
                cand = pp.name
                if cand in known["area"]:
                    node.area_uid = cand
                    break

def compute_span_for_parent(parent: Node, children: List[Node]):
    starts = []
    ends = []
    for ch in children:
        s = parse_iso_datetime(ch.start) or (parse_iso_date(ch.due) and dt.datetime.combine(parse_iso_date(ch.due), dt.time(0,0)))
        e = parse_iso_datetime(ch.end) or (parse_iso_date(ch.due) and dt.datetime.combine(parse_iso_date(ch.due), dt.time(23,59)))
        if s: starts.append(s)
        if e: ends.append(e)
    if starts and ends:
        if not parent.start: parent.start = min(starts).isoformat(timespec="minutes")
        if not parent.end: parent.end = max(ends).isoformat(timespec="minutes")

def build_index(base: Path) -> Dict[str, Any]:
    roots = detect_roots(base)
    yamls = []
    for root in roots.values():
        yamls.extend(collect_yaml(root))
    nodes: List[Node] = []
    for p in yamls:
        n = to_node(p)
        if n: nodes.append(n)

    by_kind: Dict[str, Dict[str, Node]] = {k:{} for k in ("area","objective","project","task")}
    for n in nodes:
        by_kind.setdefault(n.kind, {})[n.uid] = n
    for n in nodes:
        attach_inferred_parents(n, by_kind)

    for obj in list(by_kind.get("objective", {}).values()):
        children = [t for t in by_kind.get("task", {}).values() if t.objective_uid == obj.uid]
        compute_span_for_parent(obj, children)
    for proj in list(by_kind.get("project", {}).values()):
        children = [t for t in by_kind.get("task", {}).values() if t.project_uid == proj.uid]
        compute_span_for_parent(proj, children)
    for area in list(by_kind.get("area", {}).values()):
        children = [p for p in by_kind.get("project", {}).values() if p.area_uid == area.uid]
        compute_span_for_parent(area, children)

    edges: List[Dict[str, str]] = []
    for t in by_kind.get("task", {}).values():
        if t.project_uid: edges.append({"from": t.uid, "to": t.project_uid, "type": "task->project"})
        if t.objective_uid: edges.append({"from": t.uid, "to": t.objective_uid, "type": "task->objective"})
        if t.area_uid: edges.append({"from": t.uid, "to": t.area_uid, "type": "task->area"})
    for p in by_kind.get("project", {}).values():
        if p.area_uid: edges.append({"from": p.uid, "to": p.area_uid, "type": "project->area"})
    for o in by_kind.get("objective", {}).values():
        if o.area_uid: edges.append({"from": o.uid, "to": o.area_uid, "type": "objective->area"})

    def to_span(n: Node):
        # AOPT convention:
        # - Most tasks are single-day. `due` 即是开始也是结束。
        # - 若有 duration_minutes，则以 due + duration 作为结束时间；
        # - 若无 duration，则给一个很小的可见跨度（默认 30 分钟）以便甘特图可视化。
        # - 若存在显式 start/end，则仍然优先使用。
        explicit_s = parse_iso_datetime(n.start) if n.start else None
        explicit_e = parse_iso_datetime(n.end) if n.end else None
        if explicit_s and explicit_e and explicit_e >= explicit_s:
            return explicit_s, explicit_e

        # use due as the base
        due_dt = None
        if n.due:
            # tolerate date-only strings
            try:
                d = dt.date.fromisoformat(str(n.due)[:10])
                due_dt = dt.datetime.combine(d, dt.time(0, 0))
            except Exception:
                # also try full datetime
                due_dt = parse_iso_datetime(n.due)
        if not due_dt:
            # nothing to plot
            return None, None

        dur_min = float(n.duration_minutes) if isinstance(n.duration_minutes, (int, float)) else None
        if dur_min and dur_min > 0:
            end_dt = due_dt + dt.timedelta(minutes=dur_min)
        else:
            # minimal visible bar for same-day tasks without explicit duration
            end_dt = due_dt + dt.timedelta(minutes=30)
        return due_dt, end_dt

    gantt_items: List[Dict[str, Any]] = []
    for n in nodes:
        s,e = to_span(n)
        gantt_items.append({
            "uid": n.uid,
            "label": f"[{n.kind}] {n.title}",
            "kind": n.kind,
            "parent_uid": n.project_uid or n.objective_uid or n.area_uid,
            "status": n.status,
            "start": s.isoformat(timespec="minutes") if s else None,
            "end": e.isoformat(timespec="minutes") if e else None,
            "file_path": n.file_path,
        })

    idx = {
        "meta": {
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "base": str(base),
            "counts": {k: len(v) for k,v in by_kind.items()},
        },
        "nodes": {
            "areas": [asdict(n) for n in by_kind.get("area", {}).values()],
            "objectives": [asdict(n) for n in by_kind.get("objective", {}).values()],
            "projects": [asdict(n) for n in by_kind.get("project", {}).values()],
            "tasks": [asdict(n) for n in by_kind.get("task", {}).values()],
        },
        "edges": edges,
        "gantt_items": gantt_items,
    }
    return idx

def dump_yaml(d: Dict[str, Any], out: Path):
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False), encoding="utf-8")

def main():
    base = Path("/Users/gongbaoming/Library/CloudStorage/OneDrive-个人/life_os")
    out = Path("/Users/gongbaoming/Library/CloudStorage/OneDrive-个人/life_os/reviews/aopt_index.yaml")
    idx = build_index(base)
    dump_yaml(idx, out)
    print(f"[OK] Index written to: {out}")

if __name__ == "__main__":
    main()
