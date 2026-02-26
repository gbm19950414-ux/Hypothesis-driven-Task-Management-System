#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Extract tasks from multiple plan YAML files and write a consolidated task list in CSV format.

Supported plan shapes:
1) execution_plan.tasks:  - typical execution generator plans
2) top-level tasks:       - PHST plans (tasks at top level)

Defaults (important for life_os layout):
- Scans `--plans-dir` for YAML files whose filenames start with any prefix in `--prefixes`
  (default: "plan_,meta_plan_")
- Always writes output as CSV to a sibling `outputs/` directory:
    <plans_dir>/../outputs/task_list.csv

Usage:
  python 001_extract_tasks.py
  python 001_extract_tasks.py --plans-dir "/path/to/life_os/plan" --include-done
  python 001_extract_tasks.py --plans-dir "/path/to/life_os/plan" --prefixes "plan_,custom_"
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


def _safe_load_yaml(path: Path) -> Optional[Dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return None
        data = yaml.safe_load(text)
        if isinstance(data, dict):
            return data
        return None
    except Exception as e:
        print(f"[WARN] Failed to load {path}: {e}")
        return None


def _get_plan_name(doc: Dict[str, Any], fallback: str) -> str:
    # Prefer execution_plan.name
    ep = doc.get("execution_plan")
    if isinstance(ep, dict):
        name = ep.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()

    # Then plan_init.meta.name
    pi = doc.get("plan_init")
    if isinstance(pi, dict):
        meta = pi.get("meta")
        if isinstance(meta, dict):
            name = meta.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()

    # Then plan_init.desired_state.statement
    if isinstance(pi, dict):
        ds = pi.get("desired_state")
        if isinstance(ds, dict):
            st = ds.get("statement")
            if isinstance(st, str) and st.strip():
                return st.strip()

    return fallback


def _extract_tasks(doc: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str]:
    """
    Returns (tasks, location_hint)
    location_hint: 'execution_plan.tasks' | 'tasks' | 'none'
    """
    # 1) execution_plan.tasks
    ep = doc.get("execution_plan")
    if isinstance(ep, dict) and isinstance(ep.get("tasks"), list):
        return ep["tasks"], "execution_plan.tasks"

    # 2) top-level tasks (PHST plan style)
    if isinstance(doc.get("tasks"), list):
        return doc["tasks"], "tasks"

    return [], "none"


def _extract_objectives_index(doc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    For execution plans: index objectives by id so we can lift due/done_if if needed.
    """
    out: Dict[str, Dict[str, Any]] = {}
    ep = doc.get("execution_plan")
    if not isinstance(ep, dict):
        return out
    obj = ep.get("objective")
    if isinstance(obj, list):
        for it in obj:
            if isinstance(it, dict) and isinstance(it.get("id"), str):
                out[it["id"]] = it
    return out


def _normalize_status(s: Any) -> str:
    if isinstance(s, str) and s.strip():
        return s.strip()
    return "todo"


def _get_dep_ids(depends_on: Any) -> List[str]:
    if isinstance(depends_on, str):
        dep = depends_on.strip()
        return [dep] if dep else []
    if isinstance(depends_on, list):
        return [str(x).strip() for x in depends_on if str(x).strip()]
    return []


def _as_str(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if isinstance(x, (int, float)):
        return str(x)
    return str(x)


def _parse_due(due: str) -> Optional[dt.date]:
    due = due.strip()
    if not due:
        return None
    try:
        return dt.date.fromisoformat(due)
    except Exception:
        return None


def _best_due(task: Dict[str, Any], obj_index: Dict[str, Dict[str, Any]]) -> str:
    # task.due > objective.due > ""
    due = task.get("due")
    if isinstance(due, str) and due.strip():
        return due.strip()

    oid = task.get("objective_id")
    if isinstance(oid, str) and oid in obj_index:
        odue = obj_index[oid].get("due")
        if isinstance(odue, str) and odue.strip():
            return odue.strip()

    return ""


def _best_done_if(task: Dict[str, Any], obj_index: Dict[str, Dict[str, Any]]) -> str:
    # task.done_if > objective.done_if > ""
    di = task.get("done_if")
    if isinstance(di, str) and di.strip():
        return di.strip()

    oid = task.get("objective_id")
    if isinstance(oid, str) and oid in obj_index:
        odi = obj_index[oid].get("done_if")
        if isinstance(odi, str) and odi.strip():
            return odi.strip()

    return ""


def _flatten_result_ref(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        parts: List[str] = []
        for it in v:
            parts.append(it if isinstance(it, str) else str(it))
        return "; ".join(parts)
    return str(v)


@dataclass
class TaskRow:
    plan_name: str
    plan_file: str
    generator: str
    task_id: str
    status: str
    due: str
    description: str
    objective_id: str
    step_id: str
    condition: str
    done_if: str
    output: str
    verdict: str
    result_ref: str
    result_summary: str


def _to_row(
    plan_name: str,
    plan_file: Path,
    generator: str,
    task: Dict[str, Any],
    obj_index: Dict[str, Dict[str, Any]],
) -> TaskRow:
    return TaskRow(
        plan_name=plan_name,
        plan_file=str(plan_file),
        generator=generator,
        task_id=_as_str(task.get("id", "")).strip(),
        status=_normalize_status(task.get("status")),
        due=_best_due(task, obj_index),
        description=_as_str(task.get("description", "")).strip(),
        objective_id=_as_str(task.get("objective_id", "")).strip(),
        step_id=_as_str(task.get("step_id", "")).strip(),
        condition=_as_str(task.get("condition", "")).strip(),
        done_if=_best_done_if(task, obj_index),
        output=_as_str(task.get("output", "")).strip(),
        verdict=_as_str(task.get("verdict", "")).strip(),
        result_ref=_flatten_result_ref(task.get("result_ref")),
        result_summary=_as_str(task.get("result_summary", "")).strip(),
    )


def _sort_key(row: TaskRow) -> Tuple[int, dt.date, str, str, str]:
    # due empty goes last
    due_date = _parse_due(row.due) or dt.date(9999, 12, 31)
    due_missing = 1 if not row.due.strip() else 0
    return (due_missing, due_date, row.status, row.plan_name, row.task_id)


def write_csv(out_path: Path, rows: List[TaskRow]) -> None:
    fields = [
        "plan_name",
        "plan_file",
        "generator",
        "task_id",
        "status",
        "due",
        "description",
        "objective_id",
        "step_id",
        "condition",
        "done_if",
        "output",
        "verdict",
        "result_ref",
        "result_summary",
    ]
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: getattr(r, k) for k in fields})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans-dir", default="", help="Directory containing plan YAML files (default: <script_dir>/../../plan)")
    ap.add_argument("--glob", default="*.y*ml", help="Glob pattern to find plans (default: *.y*ml)")
    ap.add_argument("--include-done", action="store_true", help="Include tasks with status done")
    ap.add_argument(
        "--prefixes",
        default="plan_,meta_plan_",
        help="Comma-separated filename prefixes to include (default: plan_,meta_plan_)",
    )
    args = ap.parse_args()

    if args.plans_dir.strip():
        plans_dir = Path(args.plans_dir).expanduser().resolve()
    else:
        # default to <script_dir>/../../plan
        script_dir = Path(__file__).resolve().parent
        plans_dir = (script_dir / ".." / ".." / "plan").resolve()

    if not plans_dir.exists():
        raise SystemExit(f"[ERR] plans-dir does not exist: {plans_dir}")

    prefixes = [x.strip() for x in args.prefixes.split(",") if x.strip()]
    plan_files = sorted([p for p in plans_dir.glob(args.glob) if p.is_file() and any(p.name.startswith(px) for px in prefixes)])
    if not plan_files:
        raise SystemExit(f"[ERR] No plan files found in {plans_dir} with glob={args.glob} and prefixes={prefixes}")

    outputs_dir = plans_dir.parent / "outputs"
    out_path = (outputs_dir / "task_list.csv").resolve()

    rows: List[TaskRow] = []

    for pf in plan_files:
        doc = _safe_load_yaml(pf)
        if not doc:
            continue

        plan_name = _get_plan_name(doc, fallback=pf.stem)
        tasks, _loc = _extract_tasks(doc)
        if not tasks:
            continue

        generator = "unknown"
        pi = doc.get("plan_init")
        if isinstance(pi, dict):
            gen = pi.get("generator")
            if isinstance(gen, dict):
                gtype = gen.get("type")
                if isinstance(gtype, str) and gtype.strip():
                    generator = gtype.strip()

        obj_index = _extract_objectives_index(doc)

        task_status_map: Dict[str, str] = {}
        for task in tasks:
            if not isinstance(task, dict):
                continue
            tid = task.get("id")
            if not isinstance(tid, str) or not tid.strip():
                continue
            status = _normalize_status(task.get("status"))
            task_status_map[tid.strip()] = status

        for t in tasks:
            if not isinstance(t, dict):
                continue

            status = _normalize_status(t.get("status"))
            if status == "suspend":
                continue

            if status != "done":
                depends_on = t.get("depends_on")
                dep_ids = _get_dep_ids(depends_on)
                if dep_ids:
                    if any(task_status_map.get(dep_id, "") != "done" for dep_id in dep_ids):
                        continue

            if (not args.include_done) and status == "done":
                continue

            row = _to_row(plan_name, pf, generator, t, obj_index)
            if not row.task_id and not row.description:
                continue
            rows.append(row)

    rows.sort(key=_sort_key)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    write_csv(out_path, rows)

    print(f"[OK] Exported {len(rows)} tasks -> {out_path}")


if __name__ == "__main__":
    main()