#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
render_plan_tree.py
- Read JSON bundle produced by core_plan_tree.py
- Render:
  1) tree text per plan
  2) columns HTML per plan (recommended primary view)
  3) mermaid flow per plan (optional)
  4) tasks table HTML (global)
"""

from __future__ import annotations

import argparse
import html
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# -----------------------------
# Models (mirror of core output)
# -----------------------------
@dataclass
class Node:
    id: str
    type: str
    title: str
    meta: Dict[str, Any] = field(default_factory=dict)
    children: List["Node"] = field(default_factory=list)


@dataclass
class PlanDoc:
    name: str
    kind: str
    root: Node


@dataclass
class TaskRow:
    plan: str
    task_id: str
    title: str
    status: Optional[str] = None
    due: Optional[str] = None
    timebox: Optional[str] = None
    path: List[str] = field(default_factory=list)


def node_from_dict(d: Dict[str, Any]) -> Node:
    return Node(
        id=d["id"],
        type=d["type"],
        title=d.get("title", d["id"]),
        meta=d.get("meta", {}) or {},
        children=[node_from_dict(ch) for ch in (d.get("children") or [])],
    )


def plan_from_dict(d: Dict[str, Any]) -> PlanDoc:
    return PlanDoc(name=d["name"], kind=d["kind"], root=node_from_dict(d["root"]))


def task_from_dict(d: Dict[str, Any]) -> TaskRow:
    return TaskRow(
        plan=d["plan"],
        task_id=d["task_id"],
        title=d["title"],
        status=d.get("status"),
        due=d.get("due"),
        timebox=d.get("timebox"),
        path=d.get("path") or [],
    )


# -----------------------------
# Render: tree text
# -----------------------------
def render_tree_text(plan: PlanDoc) -> str:
    lines: List[str] = []

    def walk(n: Node, indent: int):
        prefix = " " * indent
        meta_bits = []
        if n.type == "Task":
            if n.meta.get("status"):
                meta_bits.append(f"status={n.meta['status']}")
            if n.meta.get("due"):
                meta_bits.append(f"due={n.meta['due']}")
            if n.meta.get("timebox"):
                meta_bits.append(f"timebox={n.meta['timebox']}")
        meta_str = f" [{' | '.join(meta_bits)}]" if meta_bits else ""
        lines.append(f"{prefix}- {n.type}: {n.id} :: {n.title}{meta_str}")
        for ch in n.children:
            walk(ch, indent + 2)

    lines.append(f"Plan: {plan.name} (kind={plan.kind})")
    walk(plan.root, 0)
    return "\n".join(lines)


# -----------------------------
# Render: columns table (HTML)
# -----------------------------
def _collect_rows_for_columns(plan: PlanDoc) -> List[Dict[str, Any]]:
    """
    Row format:
      mid: Hypothesis/Objective/Stream
      step: Step or ''
      task: Task or ''
      task_meta: status/due/timebox (if task exists)
    """
    rows: List[Dict[str, Any]] = []

    def add_mid(mid: Node):
        # If mid has Step children, expand them; else show mid only
        step_children = [c for c in mid.children if c.type == "Step"]
        task_children = [c for c in mid.children if c.type == "Task"]

        if step_children:
            for st in step_children:
                # tasks under step
                tchildren = [c for c in st.children if c.type == "Task"]
                if tchildren:
                    for t in tchildren:
                        rows.append(
                            {
                                "mid_type": mid.type,
                                "mid_id": mid.id,
                                "mid_title": mid.title,
                                "step_id": st.id,
                                "step_title": st.title,
                                "task_id": t.id,
                                "task_title": t.title,
                                "status": t.meta.get("status"),
                                "due": t.meta.get("due"),
                                "timebox": t.meta.get("timebox"),
                            }
                        )
                else:
                    rows.append(
                        {
                            "mid_type": mid.type,
                            "mid_id": mid.id,
                            "mid_title": mid.title,
                            "step_id": st.id,
                            "step_title": st.title,
                            "task_id": "",
                            "task_title": "",
                            "status": "",
                            "due": "",
                            "timebox": "",
                        }
                    )
        elif task_children:
            for t in task_children:
                rows.append(
                    {
                        "mid_type": mid.type,
                        "mid_id": mid.id,
                        "mid_title": mid.title,
                        "step_id": "",
                        "step_title": "",
                        "task_id": t.id,
                        "task_title": t.title,
                        "status": t.meta.get("status"),
                        "due": t.meta.get("due"),
                        "timebox": t.meta.get("timebox"),
                    }
                )
        else:
            rows.append(
                {
                    "mid_type": mid.type,
                    "mid_id": mid.id,
                    "mid_title": mid.title,
                    "step_id": "",
                    "step_title": "",
                    "task_id": "",
                    "task_title": "",
                    "status": "",
                    "due": "",
                    "timebox": "",
                }
            )

    # root children should be mid-level nodes
    for mid in plan.root.children:
        add_mid(mid)

    return rows


def render_columns_html(plan: PlanDoc) -> str:
    rows = _collect_rows_for_columns(plan)

    def esc(x: Any) -> str:
        return html.escape("" if x is None else str(x))

    # Simple inline style for readability
    style = """
    <style>
      body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; padding: 16px; }
      h1 { font-size: 20px; margin: 0 0 12px 0; }
      .meta { color: #666; font-size: 12px; margin-bottom: 16px; }
      table { border-collapse: collapse; width: 100%; }
      th, td { border: 1px solid #ddd; padding: 8px; vertical-align: top; }
      th { background: #f6f6f6; text-align: left; }
      .id { color: #666; font-size: 12px; }
      .taskmeta { color: #444; font-size: 12px; margin-top: 4px; }
      .empty { color: #aaa; }
    </style>
    """

    html_out = [f"<!doctype html><html><head><meta charset='utf-8'>{style}</head><body>"]
    html_out.append(f"<h1>{esc(plan.name)}</h1>")
    html_out.append(f"<div class='meta'>kind={esc(plan.kind)} | view=columns</div>")

    html_out.append("<table>")
    html_out.append("<thead><tr><th>Mid (Hypothesis/Objective/Stream)</th><th>Step</th><th>Task</th></tr></thead>")
    html_out.append("<tbody>")

    for r in rows:
        mid = f"<div><b>{esc(r['mid_title'])}</b><div class='id'>{esc(r['mid_type'])}:{esc(r['mid_id'])}</div></div>"
        step = (
            f"<div><b>{esc(r['step_title'])}</b><div class='id'>Step:{esc(r['step_id'])}</div></div>"
            if r["step_id"]
            else "<div class='empty'>—</div>"
        )
        if r["task_id"]:
            taskmeta_parts = []
            if r.get("status"):
                taskmeta_parts.append(f"status={esc(r['status'])}")
            if r.get("due"):
                taskmeta_parts.append(f"due={esc(r['due'])}")
            if r.get("timebox"):
                taskmeta_parts.append(f"timebox={esc(r['timebox'])}")
            meta_line = f"<div class='taskmeta'>{' | '.join(taskmeta_parts)}</div>" if taskmeta_parts else ""
            task = f"<div><b>{esc(r['task_title'])}</b><div class='id'>Task:{esc(r['task_id'])}</div>{meta_line}</div>"
        else:
            task = "<div class='empty'>—</div>"

        html_out.append(f"<tr><td>{mid}</td><td>{step}</td><td>{task}</td></tr>")

    html_out.append("</tbody></table></body></html>")
    return "".join(html_out)


# -----------------------------
# Render: Mermaid flow
# -----------------------------
def render_mermaid(plan: PlanDoc, direction: str = "LR") -> str:
    """
    Pure hierarchy edges only (no dependency edges).
    direction: LR / TD
    """
    lines = [f"flowchart {direction}"]
    # Use safe node labels
    def nid(n: Node) -> str:
        # Mermaid id: alnum + underscores; keep stable
        safe = "".join(ch if ch.isalnum() else "_" for ch in f"{n.type}_{n.id}")
        return safe

    def label(n: Node) -> str:
        return f"{n.type}:{n.id}\\n{n.title}"

    def walk(n: Node):
        n_id = nid(n)
        lines.append(f'{n_id}["{label(n)}"]')
        for ch in n.children:
            c_id = nid(ch)
            lines.append(f'{c_id}["{label(ch)}"]')
            lines.append(f"{n_id} --> {c_id}")
            walk(ch)

    walk(plan.root)
    return "\n".join(dict.fromkeys(lines))  # de-dup while preserving order


# -----------------------------
# Render: Global tasks table (HTML)
# -----------------------------
def render_tasks_table_html(tasks: List[TaskRow]) -> str:
    def esc(x: Any) -> str:
        return html.escape("" if x is None else str(x))

    style = """
    <style>
      body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; padding: 16px; }
      h1 { font-size: 20px; margin: 0 0 12px 0; }
      table { border-collapse: collapse; width: 100%; }
      th, td { border: 1px solid #ddd; padding: 8px; vertical-align: top; }
      th { background: #f6f6f6; text-align: left; }
      .id { color: #666; font-size: 12px; }
    </style>
    """
    out = [f"<!doctype html><html><head><meta charset='utf-8'>{style}</head><body>"]
    out.append("<h1>All Tasks</h1>")
    out.append("<table><thead><tr>"
               "<th>Plan</th><th>Task</th><th>Status</th><th>Due</th><th>Timebox</th><th>Path</th>"
               "</tr></thead><tbody>")
    for t in tasks:
        out.append("<tr>"
                   f"<td>{esc(t.plan)}</td>"
                   f"<td><b>{esc(t.title)}</b><div class='id'>{esc(t.task_id)}</div></td>"
                   f"<td>{esc(t.status)}</td>"
                   f"<td>{esc(t.due)}</td>"
                   f"<td>{esc(t.timebox)}</td>"
                   f"<td>{esc(' > '.join(t.path))}</td>"
                   "</tr>")
    out.append("</tbody></table></body></html>")
    return "".join(out)


# -----------------------------
# CLI
# -----------------------------
def main():
    p = argparse.ArgumentParser()
    p.add_argument("bundle", help="JSON bundle produced by core_plan_tree.py")
    p.add_argument("-o", "--outdir", default="outputs", help="Output directory")
    p.add_argument("--direction", default="LR", choices=["LR", "TD"], help="Mermaid direction")
    args = p.parse_args()

    bundle_path = Path(args.bundle)
    data = json.loads(bundle_path.read_text(encoding="utf-8"))

    plans = [plan_from_dict(x) for x in data.get("plans", [])]
    tasks = [task_from_dict(x) for x in data.get("tasks", [])]

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # Per plan outputs
    for pl in plans:
        # Tree text
        (outdir / f"{pl.name}.tree.txt").write_text(render_tree_text(pl), encoding="utf-8")

        # Columns HTML
        (outdir / f"{pl.name}.columns.html").write_text(render_columns_html(pl), encoding="utf-8")

        # Mermaid
        (outdir / f"{pl.name}.flow.mmd").write_text(render_mermaid(pl, direction=args.direction), encoding="utf-8")

    # Global tasks table
    (outdir / "all_tasks.html").write_text(render_tasks_table_html(tasks), encoding="utf-8")

    print(f"Wrote outputs to: {outdir.resolve()} (plans={len(plans)}, tasks={len(tasks)})")


if __name__ == "__main__":
    main()