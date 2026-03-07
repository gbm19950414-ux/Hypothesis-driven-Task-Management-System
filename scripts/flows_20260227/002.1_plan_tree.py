#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
core_plan_tree.py
- Read YAML plan files
- Detect schema (phst / execution / accumulative)
- Normalize into a typed hierarchy tree (PlanDoc) + flat TaskIndex
- Output a single JSON (for render layer)

Design principles:
- strict schema detection; fail fast if unknown
- minimal node fields; extensible meta dict
- ONLY hierarchy tree here; no visualization logic
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml  # PyYAML


# -----------------------------
# Models
# -----------------------------
@dataclass
class Node:
    id: str
    type: str  # Plan/Hypothesis/Objective/Stream/Step/Task
    title: str
    meta: Dict[str, Any] = field(default_factory=dict)
    children: List["Node"] = field(default_factory=list)


@dataclass
class PlanDoc:
    name: str
    kind: str  # phst / execution / accum
    root: Node


@dataclass
class TaskRow:
    plan: str
    task_id: str
    title: str
    status: Optional[str] = None
    due: Optional[str] = None
    timebox: Optional[str] = None
    path: List[str] = field(default_factory=list)  # ancestor node ids, excluding Plan root


# -----------------------------
# Helpers
# -----------------------------
class UnrecognizedPlanSchema(Exception):
    pass


class InvalidPlanData(Exception):
    pass


def _iso_date(v: Any) -> Optional[str]:
    """Normalize due/date values into ISO string if possible; otherwise keep as string."""
    if v is None:
        return None
    # If already a string, keep it
    if isinstance(v, str):
        return v.strip() or None
    # Some YAML parsers may decode date types; represent them safely
    try:
        # datetime/date objects have isoformat()
        iso = v.isoformat()  # type: ignore[attr-defined]
        return str(iso)
    except Exception:
        return str(v)


def _as_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return str(v)


def _first_nonempty(*vals: Any) -> Optional[str]:
    for v in vals:
        s = _as_str(v)
        if s:
            return s
    return None


def _load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise InvalidPlanData(f"Top-level YAML must be a mapping/dict. Got: {type(data)} in {path}")
    return data


# -----------------------------
# Schema detection
# -----------------------------
def detect_kind(d: Dict[str, Any]) -> str:
    """
    Strict signatures:
      - phst: top-level keys include hypotheses, steps, tasks
      - execution: top-level key execution_plan
      - accum: top-level key ResearchAccumulativePlan
    """
    keys = set(d.keys())
    if {"hypotheses", "steps", "tasks"}.issubset(keys):
        return "phst"
    if "execution_plan" in keys:
        return "execution"
    if "ResearchAccumulativePlan" in keys:
        return "accum"
    raise UnrecognizedPlanSchema(
        "Unrecognized schema.\n"
        "Expected one of:\n"
        "  - PHST: top-level keys include hypotheses, steps, tasks\n"
        "  - Execution: top-level key execution_plan\n"
        "  - Accumulative: top-level key ResearchAccumulativePlan\n"
        f"Found keys: {sorted(list(keys))}"
    )


# -----------------------------
# Adapters
# -----------------------------
def parse_phst(plan_name: str, d: Dict[str, Any]) -> PlanDoc:
    """
    Expected minimal structure:
      hypotheses: list[ {id, action|description|title, status?, ...} ]
      steps:      list[ {id, description|title, supports_hypothesis, status?, ...} ]
      tasks:      list[ {id, supports_step, description|title, status?, due?, timebox? ...} ]
    """
    hyps = d.get("hypotheses", [])
    steps = d.get("steps", [])
    tasks = d.get("tasks", [])

    if not isinstance(hyps, list) or not isinstance(steps, list) or not isinstance(tasks, list):
        raise InvalidPlanData("PHST expects hypotheses/steps/tasks to be lists.")

    # Index steps by hypothesis
    steps_by_h = {}
    for s in steps:
        if not isinstance(s, dict):
            continue
        sid = _first_nonempty(s.get("id"))
        hid = _first_nonempty(s.get("supports_hypothesis"))
        if not sid or not hid:
            continue
        steps_by_h.setdefault(hid, []).append(s)

    # Index tasks by step
    tasks_by_s = {}
    for t in tasks:
        if not isinstance(t, dict):
            continue
        tid = _first_nonempty(t.get("id"))
        sid = _first_nonempty(t.get("supports_step"))
        if not tid or not sid:
            continue
        tasks_by_s.setdefault(sid, []).append(t)

    root = Node(id=plan_name, type="Plan", title=plan_name)

    for h in hyps:
        if not isinstance(h, dict):
            continue
        hid = _first_nonempty(h.get("id"))
        if not hid:
            continue
        htitle = _first_nonempty(h.get("action"), h.get("description"), h.get("title"), hid) or hid
        hmeta = {"status": _as_str(h.get("status"))}
        hnode = Node(id=hid, type="Hypothesis", title=htitle, meta={k: v for k, v in hmeta.items() if v})

        # steps under hypothesis
        for s in steps_by_h.get(hid, []):
            sid = _first_nonempty(s.get("id")) or ""
            stitle = _first_nonempty(s.get("description"), s.get("title"), sid) or sid
            smeta = {"status": _as_str(s.get("status"))}
            snode = Node(id=sid, type="Step", title=stitle, meta={k: v for k, v in smeta.items() if v})

            # tasks under step
            for t in tasks_by_s.get(sid, []):
                tid = _first_nonempty(t.get("id")) or ""
                ttitle = _first_nonempty(t.get("description"), t.get("title"), tid) or tid
                tmeta = {
                    "status": _as_str(t.get("status")),
                    "due": _iso_date(t.get("due")),
                    "timebox": _as_str(t.get("timebox")),
                }
                tnode = Node(id=tid, type="Task", title=ttitle, meta={k: v for k, v in tmeta.items() if v})
                snode.children.append(tnode)

            hnode.children.append(snode)

        root.children.append(hnode)

    return PlanDoc(name=plan_name, kind="phst", root=root)


def parse_execution(plan_name: str, d: Dict[str, Any]) -> PlanDoc:
    """
    Expected minimal structure:
      execution_plan:
        objective: list[{id, description|title, due?, status?, ...}]
        tasks:     list[{id, objective_id, description|title, status?, due?, timebox? ...}]
    """
    ep = d.get("execution_plan")
    if not isinstance(ep, dict):
        raise InvalidPlanData("Execution expects execution_plan to be a dict.")

    objectives = ep.get("objective", [])
    tasks = ep.get("tasks", [])

    if not isinstance(objectives, list) or not isinstance(tasks, list):
        raise InvalidPlanData("Execution expects execution_plan.objective/tasks to be lists.")

    # Build objective index
    obj_by_id: Dict[str, Dict[str, Any]] = {}
    for o in objectives:
        if not isinstance(o, dict):
            continue
        oid = _first_nonempty(o.get("id"))
        if not oid:
            continue
        obj_by_id[oid] = o

    # Group tasks by objective_id
    tasks_by_o: Dict[str, List[Dict[str, Any]]] = {}
    for t in tasks:
        if not isinstance(t, dict):
            continue
        tid = _first_nonempty(t.get("id"))
        oid = _first_nonempty(t.get("objective_id"))
        if not tid or not oid:
            continue
        tasks_by_o.setdefault(oid, []).append(t)

    # Minimal validation: all objective_id referenced must exist
    missing = [oid for oid in tasks_by_o.keys() if oid not in obj_by_id]
    if missing:
        raise InvalidPlanData(
            f"Execution tasks reference objective_id not found in objectives: {missing}"
        )

    root = Node(id=plan_name, type="Plan", title=plan_name)

    for oid, o in obj_by_id.items():
        otitle = _first_nonempty(o.get("description"), o.get("title"), oid) or oid
        ometa = {
            "status": _as_str(o.get("status")),
            "due": _iso_date(o.get("due")),
        }
        onode = Node(id=oid, type="Objective", title=otitle, meta={k: v for k, v in ometa.items() if v})

        for t in tasks_by_o.get(oid, []):
            tid = _first_nonempty(t.get("id")) or ""
            ttitle = _first_nonempty(t.get("description"), t.get("title"), tid) or tid
            tmeta = {
                "status": _as_str(t.get("status")),
                "due": _iso_date(t.get("due")),
                "timebox": _as_str(t.get("timebox")),
            }
            tnode = Node(id=tid, type="Task", title=ttitle, meta={k: v for k, v in tmeta.items() if v})
            onode.children.append(tnode)

        root.children.append(onode)

    return PlanDoc(name=plan_name, kind="execution", root=root)


def parse_accum(plan_name: str, d: Dict[str, Any]) -> PlanDoc:
    """
    Accumulative plans may not produce actionable tasks.
    Expected minimal structure:
      ResearchAccumulativePlan:
        streams: list[{id|name, description?, tasks? ...}]
    If streams contain tasks, they will be normalized under Stream node.
    """
    ap = d.get("ResearchAccumulativePlan")
    if not isinstance(ap, dict):
        raise InvalidPlanData("Accumulative expects ResearchAccumulativePlan to be a dict.")

    streams = ap.get("streams", [])
    if not isinstance(streams, list):
        raise InvalidPlanData("Accumulative expects ResearchAccumulativePlan.streams to be a list.")

    root = Node(id=plan_name, type="Plan", title=plan_name)

    for s in streams:
        if not isinstance(s, dict):
            continue
        sid = _first_nonempty(s.get("id"), s.get("name"))
        if not sid:
            continue
        stitle = _first_nonempty(s.get("description"), s.get("title"), s.get("name"), sid) or sid
        snode = Node(id=sid, type="Stream", title=stitle)

        # Optional tasks under stream (if you choose to unify accumulative to support "now what")
        stasks = s.get("tasks")
        if isinstance(stasks, list):
            for t in stasks:
                if not isinstance(t, dict):
                    continue
                tid = _first_nonempty(t.get("id"))
                if not tid:
                    continue
                ttitle = _first_nonempty(t.get("description"), t.get("title"), tid) or tid
                tmeta = {
                    "status": _as_str(t.get("status")),
                    "due": _iso_date(t.get("due")),
                    "timebox": _as_str(t.get("timebox")),
                }
                snode.children.append(Node(id=tid, type="Task", title=ttitle, meta={k: v for k, v in tmeta.items() if v}))

        root.children.append(snode)

    return PlanDoc(name=plan_name, kind="accum", root=root)


def parse_plan_doc(plan_name: str, d: Dict[str, Any]) -> PlanDoc:
    kind = detect_kind(d)
    if kind == "phst":
        return parse_phst(plan_name, d)
    if kind == "execution":
        return parse_execution(plan_name, d)
    if kind == "accum":
        return parse_accum(plan_name, d)
    raise UnrecognizedPlanSchema(f"Unsupported kind: {kind}")


# -----------------------------
# Task Index builder
# -----------------------------
def build_task_index(plan: PlanDoc) -> List[TaskRow]:
    out: List[TaskRow] = []

    def walk(node: Node, path_ids: List[str]):
        next_path = path_ids + ([node.id] if node.type != "Plan" else [])
        if node.type == "Task":
            out.append(
                TaskRow(
                    plan=plan.name,
                    task_id=node.id,
                    title=node.title,
                    status=_as_str(node.meta.get("status")),
                    due=_as_str(node.meta.get("due")),
                    timebox=_as_str(node.meta.get("timebox")),
                    path=path_ids,  # ancestors only (excluding this task)
                )
            )
        for ch in node.children:
            walk(ch, next_path)

    walk(plan.root, [])
    return out


# -----------------------------
# CLI
# -----------------------------
def main():
    p = argparse.ArgumentParser()
    p.add_argument("inputs", nargs="+", help="YAML plan files or directories")
    p.add_argument("-o", "--out", default="plans.bundle.json", help="Output JSON path")
    p.add_argument("--glob", default="*.yaml", help="When input is a directory, glob pattern (default *.yaml)")
    args = p.parse_args()

    # Collect YAML files
    paths: List[Path] = []
    for inp in args.inputs:
        ip = Path(inp)
        if ip.is_dir():
            paths.extend(sorted(ip.glob(args.glob)))
        else:
            paths.append(ip)

    if not paths:
        print("No input files found.", file=sys.stderr)
        sys.exit(2)

    plans: List[PlanDoc] = []
    all_tasks: List[TaskRow] = []
    errors: List[Tuple[str, str]] = []

    for path in paths:
        try:
            data = _load_yaml(path)
            plan_name = path.stem  # filename without suffix
            plan = parse_plan_doc(plan_name, data)
            plans.append(plan)
            all_tasks.extend(build_task_index(plan))
        except (UnrecognizedPlanSchema, InvalidPlanData) as e:
            errors.append((str(path), str(e)))

    if errors:
        # Strict mode: if any file fails, exit non-zero
        print("ERROR: Some plan files could not be parsed.\n", file=sys.stderr)
        for fp, msg in errors:
            print(f"- {fp}\n{msg}\n", file=sys.stderr)
        sys.exit(1)

    bundle = {
        "version": "1.0",
        "plans": [asdict(pl) for pl in plans],
        "tasks": [asdict(t) for t in all_tasks],
    }

    out_path = Path(args.out)
    out_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote: {out_path} (plans={len(plans)}, tasks={len(all_tasks)})")


if __name__ == "__main__":
    main()