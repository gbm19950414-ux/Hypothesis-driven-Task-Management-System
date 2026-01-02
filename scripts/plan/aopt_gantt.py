from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
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

def load_index(path: Path) -> Dict[str, Any]:
    idx = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = idx.get("gantt_items") or []
    print(f"[DEBUG] gantt_items: {len(items)}")
    return idx

def pick_items(idx: Dict[str, Any], scope: List[str]) -> List[Dict[str, Any]]:
    items = list(idx.get("gantt_items") or [])
    if not items:
        # fallback: try to build lightweight items from nodes
        nodes = []
        for grp in ("areas","objectives","projects","tasks"):
            nodes.extend(idx.get("nodes", {}).get(grp, []))
        for n in nodes:
            items.append({
                "uid": n.get("uid"),
                "label": f"[{n.get('kind','node')}] {n.get('title')}",
                "kind": n.get("kind"),
                "parent_uid": n.get("project_uid") or n.get("objective_uid") or n.get("area_uid"),
                "status": n.get("status"),
                "start": n.get("start"),
                "end": n.get("end"),
                "due": n.get("due"),
                "duration_minutes": n.get("duration_minutes"),
                "file_path": n.get("file_path"),
            })
    kinds = {
        "areas": "area",
        "objectives": "objective",
        "projects": "project",
        "tasks": "task",
    }
    allow = {kinds.get(s, s) for s in scope}

    def span_ok(it):
        return bool(it.get("start") or it.get("end") or it.get("due") or it.get("duration_minutes"))

    picked = [it for it in items if (it.get("kind") in allow and span_ok(it))]
    print(f"[DEBUG] picked items: {len(picked)} (of {len(items)})")
    return picked

def to_spans(items: List[Dict[str, Any]]) -> List[Tuple[str, dt.datetime, dt.datetime, str]]:
    out: List[Tuple[str, dt.datetime, dt.datetime, str]] = []
    for it in items:
        label = it.get("label") or it.get("uid")
        status = it.get("status") or ""

        s = parse_iso_datetime(it.get("start"))
        e = parse_iso_datetime(it.get("end"))

        if not (s and e and e >= s):
            due_raw = it.get("due")
            due_dt = None
            if due_raw:
                try:
                    d = dt.date.fromisoformat(str(due_raw)[:10])
                    due_dt = dt.datetime.combine(d, dt.time(0, 0))
                except Exception:
                    due_dt = parse_iso_datetime(due_raw)
            if due_dt is not None:
                dur = it.get("duration_minutes")
                dur_min = float(dur) if isinstance(dur, (int, float)) else None
                s = due_dt
                e = due_dt + dt.timedelta(minutes=dur_min if (dur_min and dur_min > 0) else 30)

        if s and e and e >= s and label:
            out.append((label, s, e, status))
    out.sort(key=lambda x: (x[1], x[2]))
    return out
