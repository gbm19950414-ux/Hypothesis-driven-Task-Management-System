#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
make_today_plan.py
- 读取 plans_config.yaml 指定的多个 plan YAML
- 只从 status in {todo, active} 的 tasks 中选
- Primary plan = urgency=h 的可执行任务数量最多的 plan
  tie-breaker:
    1) active 数量更多
    2) (urgency=h AND load=h) 数量更多
    3) 配置文件顺序更靠前
- 今日最多输出 3 个 task:
    A Must-do: Primary plan 中优先 (u=h,l=h) -> (u=h,l=l) -> any active -> any todo
    B Next:   Primary plan 中优先 (u=h,l=l) -> any todo(load=l) -> any todo
    C Filler: 任意 plan 中优先 todo/active 且 load=l （不抢占主线，只用于低精力）
- 输出 Markdown：Primary plan + 任务列表 + freeze 目标（done_if/output 优先，否则默认模板）
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


EXECUTABLE_STATUSES = {"todo", "active"}
NON_EXECUTABLE_STATUSES = {"done", "freeze"}
VALID_STATUSES = EXECUTABLE_STATUSES | NON_EXECUTABLE_STATUSES

VALID_URGENCY = {"h", "l"}
VALID_LOAD = {"h", "l"}


@dataclass
class Task:
    raw: Dict[str, Any]
    plan_id: str
    plan_name: str
    task_id: str
    description: str
    status: str
    urgency: str
    load: str
    timebox: Optional[str]
    done_if: Optional[str]
    output: Any  # str | list | dict | None


@dataclass
class Plan:
    plan_id: str
    name: str
    path: str
    tasks: List[Task]


def _read_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _normalize_str(x: Any) -> Optional[str]:
    if x is None:
        return None
    if isinstance(x, str):
        s = x.strip()
        return s if s else None
    return str(x).strip() or None


def _task_freeze_goal(t: Task) -> str:
    # 优先用 done_if/output；否则给一个默认 freeze 模板
    if t.done_if:
        return t.done_if
    if t.output:
        # output 可能是 str / list / dict
        if isinstance(t.output, str):
            return f"产出：{t.output}"
        if isinstance(t.output, list):
            items = [str(i) for i in t.output if i is not None]
            if items:
                return "产出：" + "；".join(items[:3])
        if isinstance(t.output, dict):
            keys = list(t.output.keys())
            if keys:
                return "产出：" + "；".join(keys[:3])
    return "产生一个可回写/可复用的证据（文件/段落/表格/commit），并将其置为 freeze 或 done"


def _parse_tasks(plan_cfg: Dict[str, str]) -> Plan:
    plan_id = plan_cfg["id"]
    plan_name = plan_cfg.get("name", plan_id)
    path = plan_cfg["path"]

    data = _read_yaml(path)

    # 兼容两种结构：
    # 1) 顶层就有 tasks: [...]
    # 2) 顶层有 plan: { tasks: [...] }
    tasks_block = None
    if isinstance(data.get("tasks"), list):
        tasks_block = data.get("tasks")
    elif isinstance(data.get("plan"), dict) and isinstance(data["plan"].get("tasks"), list):
        tasks_block = data["plan"]["tasks"]
    else:
        tasks_block = []

    tasks: List[Task] = []
    for i, raw in enumerate(tasks_block):
        if not isinstance(raw, dict):
            continue

        status = _normalize_str(raw.get("status")) or "todo"
        urgency = _normalize_str(raw.get("urgency")) or "l"
        load = _normalize_str(raw.get("load")) or "l"

        # 宽容：不在集合里就当作 todo/l/l，避免脚本崩
        if status not in VALID_STATUSES:
            status = "todo"
        if urgency not in VALID_URGENCY:
            urgency = "l"
        if load not in VALID_LOAD:
            load = "l"

        task_id = _normalize_str(raw.get("id")) or f"task_{i+1}"
        desc = _normalize_str(raw.get("description")) or ""

        t = Task(
            raw=raw,
            plan_id=plan_id,
            plan_name=plan_name,
            task_id=task_id,
            description=desc,
            status=status,
            urgency=urgency,
            load=load,
            timebox=_normalize_str(raw.get("timebox")),
            done_if=_normalize_str(raw.get("done_if")),
            output=raw.get("output"),
        )
        tasks.append(t)

    return Plan(plan_id=plan_id, name=plan_name, path=path, tasks=tasks)


def _executable_tasks(plan: Plan) -> List[Task]:
    return [t for t in plan.tasks if t.status in EXECUTABLE_STATUSES]


def _plan_score(plan: Plan) -> Tuple[int, int, int]:
    """
    返回用于选择 Primary plan 的排序分数（越大越优先）：
    1) urgency=h 的可执行 task 数量
    2) active 的数量
    3) urgency=h 且 load=h 的数量
    """
    exec_ts = _executable_tasks(plan)
    u_h = sum(1 for t in exec_ts if t.urgency == "h")
    active_n = sum(1 for t in exec_ts if t.status == "active")
    uh_lh = sum(1 for t in exec_ts if t.urgency == "h" and t.load == "h")
    return (u_h, active_n, uh_lh)


def _pick_primary(plans: List[Plan]) -> Optional[Plan]:
    if not plans:
        return None

    # 按分数排序，分数相同则保留配置顺序（稳定性）
    scored = [(idx, _plan_score(p), p) for idx, p in enumerate(plans)]
    scored.sort(key=lambda x: (x[1][0], x[1][1], x[1][2], -x[0]), reverse=True)
    return scored[0][2]


def _pick_first_match(candidates: List[Task], predicates: List) -> Optional[Task]:
    for pred in predicates:
        for t in candidates:
            if pred(t):
                return t
    return None


def _remove_task(tasks: List[Task], picked: Optional[Task]) -> List[Task]:
    if picked is None:
        return tasks
    return [t for t in tasks if not (t.plan_id == picked.plan_id and t.task_id == picked.task_id)]


def _pick_today_tasks(plans: List[Plan], primary: Plan, max_n: int) -> List[Task]:
    chosen: List[Task] = []

    # A Must-do（Primary plan）
    primary_exec = _executable_tasks(primary)
    must = _pick_first_match(
        primary_exec,
        predicates=[
            lambda t: t.urgency == "h" and t.load == "h",
            lambda t: t.urgency == "h" and t.load == "l",
            lambda t: t.status == "active",
            lambda t: t.status == "todo",
        ],
    )
    if must:
        chosen.append(must)

    if len(chosen) >= max_n:
        return chosen

    # B Next（仍从 Primary plan）
    primary_exec2 = _remove_task(primary_exec, must)
    nxt = _pick_first_match(
        primary_exec2,
        predicates=[
            lambda t: t.urgency == "h" and t.load == "l",
            lambda t: t.status == "todo" and t.load == "l",
            lambda t: t.status == "todo",
        ],
    )
    if nxt:
        chosen.append(nxt)

    if len(chosen) >= max_n:
        return chosen

    # C Filler（任何 plan，load=l）
    all_exec: List[Task] = []
    for p in plans:
        all_exec.extend(_executable_tasks(p))
    all_exec = _remove_task(all_exec, must)
    all_exec = _remove_task(all_exec, nxt)

    filler = _pick_first_match(
        all_exec,
        predicates=[
            lambda t: t.load == "l" and t.urgency == "h",
            lambda t: t.load == "l",
        ],
    )
    if filler:
        chosen.append(filler)

    return chosen[:max_n]


def _task_md(t: Task, role: str) -> str:
    window = "High-energy" if t.load == "h" else "Low-energy"
    timebox = f"（timebox: {t.timebox}）" if t.timebox else ""
    freeze_goal = _task_freeze_goal(t)
    desc = f" - {t.description}" if t.description else ""
    return (
        f"- **{role}** [{t.plan_name}] `{t.task_id}`{desc}  \n"
        f"  - status: `{t.status}` | urgency: `{t.urgency}` | load: `{t.load}` | window: **{window}** {timebox}  \n"
        f"  - freeze: {freeze_goal}\n"
    )


def _render_md(primary: Plan, tasks: List[Task], all_plans: List[Plan]) -> str:
    # 统计快照：每个 plan urgency=h 的可执行数量
    lines: List[str] = []
    lines.append("# Today Plan\n")
    lines.append(f"## Primary plan\n- **{primary.name}** (`{primary.plan_id}`)\n")

    lines.append("## Plan snapshot (executable urgency=h counts)\n")
    for p in all_plans:
        u_h = sum(1 for t in _executable_tasks(p) if t.urgency == "h")
        a_n = sum(1 for t in _executable_tasks(p) if t.status == "active")
        lines.append(f"- {p.name}: urgency=h={u_h}, active={a_n}\n")

    lines.append("\n## Tasks (max 3)\n")
    roles = ["Must-do", "Next", "Filler"]
    for i, t in enumerate(tasks):
        role = roles[i] if i < len(roles) else f"Task {i+1}"
        lines.append(_task_md(t, role))

    lines.append("\n## Rule reminder\n")
    lines.append("- Only pick from `todo/active`. `freeze/done` are not picked.\n")
    lines.append("- Goal is **freeze progress**, not perfect completion.\n")
    return "".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--config",
        required=False,
        default=None,
        help="Path to plans_config.yaml (default: plans_config.yaml in script directory)",
    )
    args = ap.parse_args()

    # Resolve config path
    if args.config:
        config_path = args.config
    else:
        # default: plans_config.yaml in the same directory as this script
        script_dir = Path(__file__).resolve().parent
        config_path = script_dir / "plans_config.yaml"

    if not os.path.exists(config_path):
        raise SystemExit(f"ERROR: plans_config.yaml not found: {config_path}")

    cfg = _read_yaml(str(config_path))
    max_tasks = int(cfg.get("max_tasks_per_day", 3))
    plans_cfg = cfg.get("plans", [])
    out_cfg = cfg.get("output", {}) or {}
    out_path = out_cfg.get("path", "today_plan.md")

    if not isinstance(plans_cfg, list) or not plans_cfg:
        raise SystemExit("ERROR: config 'plans' must be a non-empty list.")

    plans: List[Plan] = []
    for p in plans_cfg:
        if not isinstance(p, dict) or "id" not in p or "path" not in p:
            raise SystemExit("ERROR: each plan entry must have {id, path}.")
        if not os.path.exists(p["path"]):
            raise SystemExit(f"ERROR: plan file not found: {p['path']}")
        plans.append(_parse_tasks(p))

    # 只保留“有可执行任务”的 plans（否则 urgency 统计会把空计划也算进去没有意义）
    plans_with_exec = [p for p in plans if _executable_tasks(p)]
    if not plans_with_exec:
        md = "# Today Plan\n\nNo executable tasks found (all done/freeze or empty).\n"
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"Wrote: {out_path}")
        return 0

    primary = _pick_primary(plans_with_exec)
    assert primary is not None

    today_tasks = _pick_today_tasks(plans_with_exec, primary, max_n=max_tasks)
    md = _render_md(primary, today_tasks, plans_with_exec)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"Primary plan: {primary.name} ({primary.plan_id})")
    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())