#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
最简 Plan 可视化（卡片 + 树状图 + depends_on 虚线）
输入:  plan/plan_毕业答辩.yaml, plan/plan_学位证书.yaml
输出: outputs/<plan_name>_graph.html  + outputs/<plan_name>_graph.mmd

依赖: PyYAML (pip install pyyaml)
打开: 用浏览器打开 outputs/*.html
"""

from __future__ import annotations
import html
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


ROOT = Path("/Volumes/Samsung_SSD_990_PRO_2TB_Media/life_os")
PLAN_DIR = ROOT / "plan"
files = list(PLAN_DIR.glob("plan_*.yaml")) + list(PLAN_DIR.glob("meta_plan_*.yaml"))
PLAN_FILES = sorted([p.name for p in files])
OUT_DIR = ROOT / "outputs"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TRUNC = 44  # description 截断长度（可自行调）

# Layout direction for Mermaid flowchart:
# - "LR": left-to-right (left is earlier)
# - "TB": top-to-bottom (top is earlier)
FLOW_DIR = "LR"


def _get(d: Dict[str, Any], path: List[str], default=None):
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _truncate(s: str, n: int = TRUNC) -> str:
    s = (s or "").strip().replace("\n", " ")
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"



def _node_label(title: str, due: Optional[str], desc: str) -> str:
    # Mermaid 节点可用 <br/> 换行；注意转义
    parts = [title]
    if due:
        parts.append(f"due: {due}")
    if desc:
        parts.append(_truncate(desc))
    safe = "<br/>".join(html.escape(p) for p in parts)
    return safe


# --- Ordering helpers ---
def _parse_date_yyyy_mm_dd(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    s = str(s).strip()
    # allow "YYYY-MM-DD" or "YYYY/MM/DD"
    s = s.replace("/", "-")
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except Exception:
        return None


def _id_order_key(id_str: Optional[str]) -> Tuple[int, str]:
    """
    Order IDs like o1, o2, t10 naturally; fall back to lexicographic.
    Returns (numeric_part_or_big, original_lower).
    """
    if not id_str:
        return (10**9, "")
    s = str(id_str).strip()
    m = re.search(r"(\d+)", s)
    if m:
        return (int(m.group(1)), s.lower())
    return (10**9, s.lower())


def _due_then_id_key(due: Optional[str], id_str: Optional[str]) -> Tuple[int, datetime, int, str]:
    """
    Sort primarily by due date if present, otherwise by natural id order.
    Items WITH due come first, ordered by date ascending.
    Items WITHOUT due come after, ordered by id (o1, o2, ...).
    """
    dt = _parse_date_yyyy_mm_dd(due)
    if dt is not None:
        # (has_due=0) sorts first
        num, sid = _id_order_key(id_str)
        return (0, dt, num, sid)
    # (has_due=1) sorts after
    num, sid = _id_order_key(id_str)
    return (1, datetime.max, num, sid)


def _normalize_plan(doc: Dict[str, Any]) -> Tuple[str, str, str, Optional[str], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Return (plan_name, plan_context, ep_name, tw_end, hypotheses, objectives, tasks)
    Supports:
      - Execution plans: execution_plan.objective + execution_plan.tasks
      - PHST plans: steps + tasks (group tasks under steps via task.step_id)
    """
    # --- Plan meta ---
    plan_name = _get(doc, ["plan_init", "meta", "name"], None)
    plan_context = _get(doc, ["plan_init", "meta", "context"], "")

    # PHST plans may not have plan_init/meta/name; use desired_state.statement as fallback
    if not plan_name:
        plan_name = _get(doc, ["desired_state", "statement"], None) or _get(doc, ["desired_state"], None)
    if not plan_name:
        plan_name = "Plan"

    # --- Execution plan defaults ---
    ep_name = _get(doc, ["execution_plan", "name"], None) or _get(doc, ["generator", "type"], None) or "execution_plan"
    time_window = _get(doc, ["execution_plan", "time_window"], {})
    tw_end = time_window.get("end") if isinstance(time_window, dict) else None

    # --- Objectives / Tasks (top-level only) ---
    # We standardize future plans to keep `objective(s)` and `tasks` at YAML top level.
    objectives: List[Dict[str, Any]] = []
    tasks: List[Dict[str, Any]] = []

    top_obj = doc.get("objective", None)
    if top_obj is None:
        top_obj = doc.get("objectives", None)
    if isinstance(top_obj, list):
        objectives = top_obj
    elif isinstance(top_obj, dict):
        objectives = [top_obj]

    top_tasks = doc.get("tasks", None)
    if isinstance(top_tasks, list):
        tasks = top_tasks
    elif isinstance(top_tasks, dict):
        tasks = [top_tasks]

    hypotheses: List[Dict[str, Any]] = _get(doc, ["hypotheses"], []) or []
    if not isinstance(hypotheses, list):
        hypotheses = []

    # Ensure objectives/tasks are always lists of dicts
    if not isinstance(objectives, list):
        objectives = []
    if not isinstance(tasks, list):
        tasks = []

    # --- PHST fallback: steps + tasks ---
    if not objectives and not tasks:
        # hypotheses for PHST plans
        hypotheses = doc.get("hypotheses", []) or []
        if not isinstance(hypotheses, list):
            hypotheses = []

        steps = doc.get("steps", [])
        phst_tasks = doc.get("tasks", [])

        if isinstance(steps, list) and steps:
            # Map steps -> objectives
            objectives = []
            for s in steps:
                if not isinstance(s, dict):
                    continue
                sid = s.get("id")
                if not sid:
                    continue
                objectives.append(
                    {
                        "id": sid,
                        "description": s.get("description", ""),
                        "due": s.get("due", None),
                        "status": s.get("status", ""),
                        # keep extra fields if needed later
                        "kind": s.get("kind", None),
                        "hypothesis_id": s.get("hypothesis_id", None),
                    }
                )

        if isinstance(phst_tasks, list) and phst_tasks:
            tasks = []
            for t in phst_tasks:
                if not isinstance(t, dict):
                    continue
                tid = t.get("id")
                if not tid:
                    continue
                # group under step_id as objective_id
                oid = t.get("objective_id")
                if not oid:
                    oid = t.get("step_id")
                tasks.append(
                    {
                        **t,
                        "objective_id": oid,
                    }
                )

        # Make ep_name more meaningful for PHST
        if steps or phst_tasks:
            ep_name = "PHST"

    return str(plan_name), str(plan_context or ""), str(ep_name), tw_end, hypotheses, objectives, tasks


def main() -> None:
    # A方案：生成一个目录页（index.html），集中打开各计划图
    registry: List[Dict[str, Any]] = []
    for plan_file in PLAN_FILES:
        in_path = ROOT / "plan" / plan_file
        if not in_path.exists():
            raise FileNotFoundError(f"Input not found: {in_path}")

        with in_path.open("r", encoding="utf-8") as f:
            doc = yaml.safe_load(f)

        plan_name, plan_context, ep_name, tw_end, hypotheses, objectives, tasks = _normalize_plan(doc)

        # Build indices
        obj_by_id = {o.get("id"): o for o in objectives if isinstance(o, dict) and o.get("id")}
        tasks_by_id = {t.get("id"): t for t in tasks if isinstance(t, dict) and t.get("id")}

        # Mermaid graph
        lines: List[str] = []
        lines.append(f"flowchart {FLOW_DIR}")

        # Styles
        lines.append("classDef card fill:#ffffff,stroke:#111,stroke-width:1px,rx:10,ry:10;")
        lines.append("classDef plan fill:#f6f8ff,stroke:#111,stroke-width:1px,rx:12,ry:12;")
        lines.append("classDef obj fill:#f7fff6,stroke:#111,stroke-width:1px,rx:12,ry:12;")
        lines.append("classDef hyp fill:#fff6fb,stroke:#111,stroke-width:1px,rx:12,ry:12;")
        lines.append("classDef task fill:#fffdf2,stroke:#111,stroke-width:1px,rx:12,ry:12;")
        lines.append("classDef done fill:#e0e0e0,stroke:#666,stroke-width:1px,color:#666,rx:12,ry:12;")

        # Nodes
        plan_node = "PLAN"
        plan_desc = plan_context or ""
        plan_due = tw_end
        plan_label = _node_label(f"PLAN: {plan_name}", plan_due, plan_desc)
        lines.append(f'{plan_node}["{plan_label}"]:::plan')

        ep_node = "EP"
        ep_label = _node_label(f"EXEC: {ep_name}", tw_end, "")
        lines.append(f'{ep_node}["{ep_label}"]:::plan')
        lines.append(f"{plan_node} --> {ep_node}")

        # Hypothesis layer (optional): EP -> Hypotheses -> Objectives
        hyp_by_id: Dict[str, Dict[str, Any]] = {}
        hyp_ids_in_order: List[str] = []

        if isinstance(hypotheses, list) and hypotheses:
            hypotheses_sorted = sorted(
                [h for h in hypotheses if isinstance(h, dict)],
                key=lambda h: _due_then_id_key(h.get("due"), h.get("id")),
            )

            for h in hypotheses_sorted:
                hid = h.get("id")
                if not hid:
                    continue
                hid = str(hid)
                hyp_by_id[hid] = h
                hyp_ids_in_order.append(hid)

                h_node = f"HYP_{hid}"
                h_due = h.get("due")
                h_desc = h.get("description", "") or h.get("statement", "") or ""
                h_status = str(h.get("status", "")).lower()

                title = f"{hid}"
                if h_status == "done":
                    title = f"✓ {hid}"

                h_label = _node_label(title, h_due, h_desc)

                if h_status == "done":
                    lines.append(f'{h_node}["{h_label}"]:::done')
                else:
                    lines.append(f'{h_node}["{h_label}"]:::hyp')

                lines.append(f"{ep_node} --> {h_node}")

            # Encourage ordering among hypotheses
            for a, b in zip(hyp_ids_in_order, hyp_ids_in_order[1:]):
                lines.append(f"HYP_{a} ~~~ HYP_{b}")

        # Objective nodes + tree edges (ordered left-to-right)
        objectives_sorted = sorted(
            [o for o in objectives if isinstance(o, dict)],
            key=lambda o: _due_then_id_key(o.get("due"), o.get("id")),
        )

        objective_ids_in_order: List[str] = []
        for o in objectives_sorted:
            oid = o.get("id")
            if not oid:
                continue
            objective_ids_in_order.append(str(oid))
            o_node = f"OBJ_{oid}"
            o_due = o.get("due")
            o_desc = o.get("description", "")
            o_label = _node_label(f"{oid}", o_due, o_desc)
            o_status = str(o.get("status", "")).lower()
            if o_status == "done":
                # show completed steps/objectives as done style
                lines.append(f'{o_node}["{_node_label("✓ " + str(oid), o_due, o_desc)}"]:::done')
            else:
                lines.append(f'{o_node}["{o_label}"]:::obj')
            parent_hid = o.get("hypothesis_id")
            if parent_hid is not None:
                parent_hid = str(parent_hid)
            if parent_hid and parent_hid in hyp_by_id:
                lines.append(f"HYP_{parent_hid} --> {o_node}")
            else:
                lines.append(f"{ep_node} --> {o_node}")

        # Invisible links to encourage left-to-right ordering
        for a, b in zip(objective_ids_in_order, objective_ids_in_order[1:]):
            lines.append(f"OBJ_{a} ~~~ OBJ_{b}")

        # Task nodes + tree edges (objective_id) + ordering
        tasks_list = [t for t in tasks if isinstance(t, dict)]
        tasks_sorted = sorted(
            tasks_list,
            key=lambda t: _due_then_id_key(t.get("due"), t.get("id")),
        )

        # Track task order per objective to add invisible links
        tasks_in_obj_order: Dict[str, List[str]] = {}
        tasks_no_obj_order: List[str] = []

        for t in tasks_sorted:
            tid = t.get("id")
            if not tid:
                continue
            t_node = f"TASK_{tid}"
            t_due = t.get("due")
            t_desc = t.get("description", "")
            status = str(t.get("status", "")).lower()

            title = f"{tid}"
            if status == "done":
                title = f"✓ {tid}"

            t_label = _node_label(title, t_due, t_desc)

            if status == "done":
                lines.append(f'{t_node}["{t_label}"]:::done')
            else:
                lines.append(f'{t_node}["{t_label}"]:::task')

            oid = t.get("objective_id")
            if oid and oid in obj_by_id:
                lines.append(f"OBJ_{oid} --> {t_node}")
                tasks_in_obj_order.setdefault(str(oid), []).append(str(tid))
            else:
                # 如果缺 objective_id，就挂到 EP
                lines.append(f"{ep_node} --> {t_node}")
                tasks_no_obj_order.append(str(tid))

        # Invisible links to encourage left-to-right ordering of tasks within each objective
        for oid, tids in tasks_in_obj_order.items():
            for a, b in zip(tids, tids[1:]):
                lines.append(f"TASK_{a} ~~~ TASK_{b}")

        # Also order tasks that are directly under EP (no objective_id)
        for a, b in zip(tasks_no_obj_order, tasks_no_obj_order[1:]):
            lines.append(f"TASK_{a} ~~~ TASK_{b}")

        # depends_on edges (dashed)
        # 用不同线型：-.-> 作为 depends_on
        for t in tasks:
            tid = t.get("id")
            if not tid:
                continue
            deps = t.get("depends_on")
            if not deps:
                continue
            if not isinstance(deps, list):
                continue
            for dep in deps:
                if not dep or dep not in tasks_by_id:
                    continue
                lines.append(f"TASK_{tid} -. depends_on .-> TASK_{dep}")

        mermaid = "\n".join(lines)

        stem = Path(plan_file).stem
        # Write .mmd
        mmd_path = OUT_DIR / f"{stem}_graph.mmd"
        mmd_path.write_text(mermaid, encoding="utf-8")

        # Write HTML (self-contained, using mermaid CDN)
        html_path = OUT_DIR / f"{stem}_graph.html"
        html_doc = f"""<!doctype html>
<html lang="zh">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Plan Graph - {html.escape(str(plan_name))}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
           margin: 0; padding: 16px; background:#fafafa; }}
    .wrap {{ max-width: 1400px; margin: 0 auto; }}
    .hint {{ color:#555; font-size: 13px; margin: 0 0 10px 0; }}
    .box {{ background:#fff; border:1px solid #ddd; border-radius: 12px; padding: 12px; overflow:auto; }}
    .legend {{ font-size: 13px; color:#333; margin: 10px 0 0; }}
    code {{ background:#f3f3f3; padding:2px 6px; border-radius: 6px; }}
  </style>
  <script type="module">
    import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";
    mermaid.initialize({{ startOnLoad: true, flowchart: {{ curve: "linear" }} }});
  </script>
</head>
<body>
  <div class="wrap">
    <p class="hint">
      实线箭头：层级树（Plan → Objective → Task）｜
      虚线箭头：<code>depends_on</code> ｜ 排列方向：<code>{html.escape(FLOW_DIR)}</code>
      （TB：上为先；LR：左为先）
    </p>
    <div class="box">
      <pre class="mermaid">
{html.escape(mermaid)}
      </pre>
    </div>
    <p class="legend">输出文件：{html.escape(str(html_path))}</p>
  </div>
</body>
</html>
"""
        html_path.write_text(html_doc, encoding="utf-8")

        print(f"[OK] {plan_file} ->\n- {mmd_path}\n- {html_path}")

        # Collect metadata for index page
        obj_count = len([o for o in objectives if isinstance(o, dict) and o.get("id")])
        task_count = len([t for t in tasks if isinstance(t, dict) and t.get("id")])
        obj_done = len([o for o in objectives if isinstance(o, dict) and str(o.get("status", "")).lower() == "done"])
        task_done = len([t for t in tasks if isinstance(t, dict) and str(t.get("status", "")).lower() == "done"])

        registry.append(
            {
                "plan_file": plan_file,
                "stem": stem,
                "plan_name": plan_name,
                "ep_name": ep_name,
                "tw_end": tw_end,
                "obj_count": obj_count,
                "task_count": task_count,
                "obj_done": obj_done,
                "task_done": task_done,
                # relative filename so opening outputs/index.html works
                "html_file": html_path.name,
            }
        )


    # --- Index page (A方案)：目录页 + 链接到每个计划的独立 HTML ---
    if registry:
        registry_sorted = sorted(registry, key=lambda r: str(r.get("plan_name", "")).lower())

        rows: List[str] = []
        for r in registry_sorted:
            plan_title = html.escape(str(r.get("plan_name", "Plan")))
            ep_title = html.escape(str(r.get("ep_name", "execution_plan")))
            tw_end = r.get("tw_end")
            due_part = f" · end: {html.escape(str(tw_end))}" if tw_end else ""
            stats = f"O {r.get('obj_done', 0)}/{r.get('obj_count', 0)} · T {r.get('task_done', 0)}/{r.get('task_count', 0)}"
            stats = html.escape(stats)
            href = html.escape(str(r.get("html_file", "")))
            rows.append(
                f"<tr>"
                f"<td><a href=\"{href}\">{plan_title}</a></td>"
                f"<td>{ep_title}</td>"
                f"<td>{stats}</td>"
                f"<td>{html.escape(due_part[3:]) if due_part else ''}</td>"
                f"</tr>"
            )

        index_path = OUT_DIR / "index.html"
        index_doc = f"""<!doctype html>
<html lang=\"zh\">
<head>
  <meta charset=\"utf-8\"/>
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>
  <title>Plan Index</title>
  <style>
    body {{ margin: 0; background: #fafafa; color: #111;
           font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial,
                        "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif; }}
    .wrap {{ max-width: 1200px; margin: 0 auto; padding: 16px; }}
    .top {{ background: #fff; border: 1px solid #e5e5e5; border-radius: 12px; padding: 12px 14px; }}
    .hint {{ color: #666; font-size: 13px; margin-top: 6px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 12px; background: #fff;
            border: 1px solid #e5e5e5; border-radius: 12px; overflow: hidden; }}
    th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #eee; font-size: 14px; }}
    th {{ font-size: 13px; color: #444; background: #fafafa; }}
    tr:last-child td {{ border-bottom: 0; }}
    a {{ color: #111; text-decoration: underline; }}
    a:hover {{ color: #000; }}
    .muted {{ color: #666; font-size: 12px; }}
  </style>
</head>
<body>
  <div class=\"wrap\">
    <div class=\"top\">
      <div><strong>Plan Index</strong> · outputs/</div>
      <div class=\"hint\">点击计划名称打开对应的图页面（每个计划仍是独立 HTML）。</div>
    </div>

    <table>
      <thead>
        <tr>
          <th>Plan</th>
          <th>Exec</th>
          <th>Stats</th>
          <th>End</th>
        </tr>
      </thead>
      <tbody>
        {"\n".join(rows)}
      </tbody>
    </table>

    <p class=\"muted\">提示：把本页加入浏览器书签即可“一键打开所有计划入口”。</p>
  </div>
</body>
</html>
"""

        index_path.write_text(index_doc, encoding="utf-8")
        print(f"[OK] index ->\n- {index_path}")


if __name__ == "__main__":
    main()