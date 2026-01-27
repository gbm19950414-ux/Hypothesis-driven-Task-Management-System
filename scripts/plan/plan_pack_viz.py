#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Pack tasks into a calendar (visual feasibility) — minimal logic.

Inputs:
- config yaml containing:
  plans: [{id, name, path}, ...]
  output.path (optional)

Plan YAML expects (either top-level or under key "plan"):
- constraints.deadline_or_budget: "YYYY-MM-DD" (plan-level deadline; fallback = latest task due end)
- tasks: list of tasks with fields:
  - id
  - status: todo/active/done/freeze
  - timebox: "1h" / "2h" / "1day" (1day = 10h by default)
  - due: "YYYY-MM-DD" or "YYYY-MM-DD~YYYY-MM-DD" or "today"
  - load: h/l (default h)
  - urgency: h/l (default l)

Calendar model (weekdays only):
- High-energy window: 07:00–11:00 => 4h, capacity type = h
- Low-energy  window: 12:00–18:00 => 6h, capacity type = l

Packing rules:
- Only tasks with status in {todo, active} are packed.
- Order: urgency=h first, then urgency=l; within each group sort by due (earliest end date), then id.
- load=h tasks go ONLY to high window.
- load=l tasks go to low window first; overflow is allowed to move up to high window.

Output:
- One PNG per plan + one combined PNG.
- A thin vertical line marks the plan deadline.
- Portions scheduled AFTER deadline are drawn as overflow (hatched).

Run:
python plan_pack_viz.py /path/to/plans_config.yaml
"""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


# ---------------------------
# Helpers
# ---------------------------

def load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def parse_date(s: str) -> Optional[dt.date]:
    if not s:
        return None
    s = str(s).strip()
    try:
        return dt.date.fromisoformat(s)
    except Exception:
        return None


def parse_due(due: Any, today: dt.date) -> Tuple[Optional[dt.date], Optional[dt.date]]:
    """
    Return (start, end) as dates.
    Supports:
      - "today"
      - "YYYY-MM-DD"
      - "YYYY-MM-DD~YYYY-MM-DD"
    """
    if due is None:
        return (None, None)
    s = str(due).strip()
    if not s:
        return (None, None)
    if s.lower() == "today":
        return (today, today)
    if "~" in s:
        a, b = s.split("~", 1)
        return (parse_date(a.strip()), parse_date(b.strip()))
    d = parse_date(s)
    return (d, d)


def norm_status(x: Any) -> str:
    s = ("" if x is None else str(x)).strip().lower()
    if s == "frozen":
        return "freeze"
    return s


def norm_load(x: Any) -> str:
    s = ("" if x is None else str(x)).strip().lower()
    return s if s in ("h", "l") else "h"


def norm_urgency(x: Any) -> str:
    s = ("" if x is None else str(x)).strip().lower()
    return s if s in ("h", "l") else "l"


def parse_timebox_hours(x: Any) -> float:
    """
    Minimal parser:
      "1h", "1.5h", "2h" => hours
      "1day" / "1d"     => 10h (4+6)
      numeric           => hours
      empty             => 0
    """
    if x is None:
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().lower().replace(" ", "")
    if not s:
        return 0.0
    if s.endswith("day"):
        try:
            n = float(s[:-3]) if s[:-3] else 1.0
        except Exception:
            n = 1.0
        return 10.0 * n
    if s.endswith("d"):
        try:
            n = float(s[:-1]) if s[:-1] else 1.0
        except Exception:
            n = 1.0
        return 10.0 * n
    if s.endswith("h"):
        try:
            return float(s[:-1])
        except Exception:
            return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


def is_weekday(d: dt.date) -> bool:
    return d.weekday() < 5


# ---------------------------
# Calendar slots
# ---------------------------

@dataclass
class Slot:
    start: dt.datetime
    end: dt.datetime
    load: str  # "h" or "l"
    remaining_hours: float


def build_slots(today: dt.date, end_date: dt.date, extra_workdays: int = 0) -> List[Slot]:
    """
    Build slots from today to end_date (inclusive), weekdays only.
    Optionally extend extra_workdays beyond end_date to visualize overflow.
    """
    slots: List[Slot] = []

    def add_day(d: dt.date):
        if not is_weekday(d):
            return
        # high: 07-11 (4h)
        s1 = dt.datetime.combine(d, dt.time(7, 0))
        e1 = dt.datetime.combine(d, dt.time(11, 0))
        slots.append(Slot(s1, e1, "h", 4.0))
        # low: 12-18 (6h)
        s2 = dt.datetime.combine(d, dt.time(12, 0))
        e2 = dt.datetime.combine(d, dt.time(18, 0))
        slots.append(Slot(s2, e2, "l", 6.0))

    cur = today
    one = dt.timedelta(days=1)
    while cur <= end_date:
        add_day(cur)
        cur += one

    # extend overflow visualization
    wd_added = 0
    while wd_added < extra_workdays:
        if is_weekday(cur):
            add_day(cur)
            wd_added += 1
        cur += one

    return slots


# ---------------------------
# Packing
# ---------------------------

@dataclass
class TaskSeg:
    task_id: str
    task_label: str
    start: dt.datetime
    end: dt.datetime
    after_deadline: bool


def allocate_hours_into_slots(
    task_id: str,
    task_label: str,
    hours: float,
    slots: List[Slot],
    allow_load_upshift: bool,
    deadline_dt: dt.datetime,
    slot_filter: callable,
) -> Tuple[List[TaskSeg], float]:
    """
    Allocate 'hours' into slots matching slot_filter.
    Returns segments + remaining_unallocated_hours.
    """
    segs: List[TaskSeg] = []
    remaining = hours

    for sl in slots:
        if remaining <= 1e-9:
            break
        if sl.remaining_hours <= 1e-9:
            continue
        if not slot_filter(sl):
            continue

        take = min(remaining, sl.remaining_hours)

        # allocate within this slot proportionally in time
        slot_seconds = (sl.end - sl.start).total_seconds()
        # hours -> seconds
        take_seconds = take * 3600.0
        # if slot partially used already, start should be shifted by used portion
        used_hours = (4.0 if sl.load == "h" else 6.0) - sl.remaining_hours
        used_seconds = used_hours * 3600.0
        seg_start = sl.start + dt.timedelta(seconds=used_seconds)
        seg_end = seg_start + dt.timedelta(seconds=take_seconds)

        segs.append(TaskSeg(
            task_id=task_id,
            task_label=task_label,
            start=seg_start,
            end=seg_end,
            after_deadline=seg_start > deadline_dt,
        ))

        sl.remaining_hours -= take
        remaining -= take

    if remaining > 1e-9 and allow_load_upshift:
        # try upshift into high slots if allowed
        for sl in slots:
            if remaining <= 1e-9:
                break
            if sl.remaining_hours <= 1e-9:
                continue
            if sl.load != "h":
                continue

            take = min(remaining, sl.remaining_hours)
            slot_seconds = (sl.end - sl.start).total_seconds()
            take_seconds = take * 3600.0
            used_hours = 4.0 - sl.remaining_hours
            used_seconds = used_hours * 3600.0
            seg_start = sl.start + dt.timedelta(seconds=used_seconds)
            seg_end = seg_start + dt.timedelta(seconds=take_seconds)

            segs.append(TaskSeg(
                task_id=task_id,
                task_label=task_label,
                start=seg_start,
                end=seg_end,
                after_deadline=seg_start > deadline_dt,
            ))

            sl.remaining_hours -= take
            remaining -= take

    return segs, remaining


def pack_plan_tasks(
    tasks: List[Dict[str, Any]],
    today: dt.date,
    deadline: dt.date,
    overflow_days: int = 10,
) -> Tuple[List[TaskSeg], List[str], bool]:
    """
    Returns:
      segments, task_labels_in_order, fully_within_deadline (bool)
    """
    # pick tasks to schedule
    picked = []
    for t in tasks:
        st = norm_status(t.get("status"))
        if st not in ("todo", "active"):
            continue
        tb = parse_timebox_hours(t.get("timebox"))
        if tb <= 0:
            continue
        tid = str(t.get("id", "")).strip()
        due_s, due_e = parse_due(t.get("due"), today)
        due_end = due_e or due_s or deadline
        picked.append({
            "id": tid,
            "desc": str(t.get("description", "")).strip(),
            "hours": tb,
            "load": norm_load(t.get("load")),
            "urg": norm_urgency(t.get("urgency")),
            "due_end": due_end,
        })

    # order: urgent first, then by due_end, then id
    picked.sort(key=lambda x: (0 if x["urg"] == "h" else 1, x["due_end"], x["id"]))

    # build slots (include overflow visualization window)
    slots = build_slots(today, deadline, extra_workdays=overflow_days)
    deadline_dt = dt.datetime.combine(deadline, dt.time(23, 59, 59))

    all_segs: List[TaskSeg] = []
    labels: List[str] = []
    overflow = False

    for t in picked:
        tid = t["id"]
        label = f"{tid} ({t['hours']}h/{t['load']}/{t['urg']})"
        labels.append(label)

        hours = t["hours"]
        load = t["load"]

        if load == "h":
            # only high slots
            segs, rem = allocate_hours_into_slots(
                tid, label, hours,
                slots,
                allow_load_upshift=False,
                deadline_dt=deadline_dt,
                slot_filter=lambda sl: sl.load == "h",
            )
        else:
            # low first, overflow allowed to high
            segs, rem = allocate_hours_into_slots(
                tid, label, hours,
                slots,
                allow_load_upshift=True,
                deadline_dt=deadline_dt,
                slot_filter=lambda sl: sl.load == "l",
            )

        all_segs.extend(segs)
        if rem > 1e-9:
            # not even fit in extended overflow window
            overflow = True
        # mark overflow if any segment starts after deadline
        if any(s.after_deadline for s in segs):
            overflow = True

    fully_within_deadline = not overflow
    return all_segs, labels, fully_within_deadline


# ---------------------------
# Plotting
# ---------------------------

def plot_plan(
    plan_id: str,
    plan_name: str,
    segs: List[TaskSeg],
    labels: List[str],
    today: dt.date,
    deadline: dt.date,
    out_png: Path,
) -> None:
    n = max(1, len(labels))
    fig_h = max(4.0, 0.45 * n + 1.8)
    fig, ax = plt.subplots(figsize=(13.5, fig_h))

    # Map label -> y index
    y_map = {lab: i for i, lab in enumerate(labels)}

    # Plot segments as bars; within deadline = solid gray; after deadline = hatched
    for s in segs:
        y = y_map.get(s.task_label, None)
        if y is None:
            continue
        start_num = mdates.date2num(s.start)
        end_num = mdates.date2num(s.end)
        width = max(1e-6, end_num - start_num)

        bars = ax.barh(
            y=y,
            width=width,
            left=start_num,
            height=0.55,
            color="0.75",
            alpha=0.85 if not s.after_deadline else 0.35,
            linewidth=0.8,
            edgecolor="0.3",
        )
        if s.after_deadline:
            for b in bars:
                b.set_hatch("//")

    # y labels
    ax.set_yticks(range(n))
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()

    # x axis formatting
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax.xaxis.set_minor_locator(mdates.DayLocator())
    ax.grid(True, which="minor", axis="x", alpha=0.15)
    ax.grid(True, which="major", axis="x", alpha=0.35)

    # deadline line
    dl_dt = dt.datetime.combine(deadline, dt.time(0, 0))
    ax.axvline(mdates.date2num(dl_dt), linewidth=2.2, alpha=0.7)

    # bounds
    left = dt.datetime.combine(today, dt.time(0, 0))
    right = dt.datetime.combine(deadline + dt.timedelta(days=14), dt.time(23, 59))
    ax.set_xlim(mdates.date2num(left), mdates.date2num(right))

    ax.set_title(f"{plan_id} — {plan_name} | packed schedule (// = overflow after deadline)", fontsize=13)
    ax.set_xlabel("date")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_png, dpi=160)
    plt.close(fig)


def combine_summary_plot(
    plan_rows: List[Tuple[str, str, bool]],
    out_png: Path,
) -> None:
    """
    Minimal overview: one row per plan; feasible vs overflow.
    """
    n = max(1, len(plan_rows))
    fig_h = max(2.8, 0.4 * n + 1.3)
    fig, ax = plt.subplots(figsize=(10.5, fig_h))

    y = list(range(n))
    labels = [f"{pid} {name}" for pid, name, _ in plan_rows]
    vals = [1 if ok else 0 for _, _, ok in plan_rows]

    ax.barh(y, vals, height=0.6, color="0.75", alpha=0.8)
    for i, ok in enumerate(vals):
        ax.text(ok + 0.02, i, "OK" if ok else "OVERFLOW", va="center", fontsize=9)

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.25)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["overflow", "fits"])
    ax.grid(True, axis="x", alpha=0.2)
    ax.set_title("Plans feasibility (visual packing result)", fontsize=12)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_png, dpi=160)
    plt.close(fig)


# ---------------------------
# Main
# ---------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config", help="Path to plans_config.yaml")
    ap.add_argument(
        "--outdir",
        default="/Volumes/Samsung_SSD_990_PRO_2TB_Media/life_os/outputs",
        help="Output directory (default fixed)",
    )
    ap.add_argument(
        "--overflow_days",
        type=int,
        default=10,
        help="How many extra workdays to draw beyond deadline (to visualize overflow).",
    )
    args = ap.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        raise SystemExit(f"[ERROR] config file not found: {config_path}")

    cfg = load_yaml(str(config_path))
    plans = cfg.get("plans") or []
    if not plans:
        raise SystemExit("[ERROR] No plans found under key: plans")

    outdir = Path(args.outdir)
    today = dt.date.today()

    plan_rows: List[Tuple[str, str, bool]] = []

    for p in plans:
        pid = str(p.get("id", "")).strip()
        name = str(p.get("name", pid)).strip()
        path = str(p.get("path", "")).strip()
        if not pid or not path:
            continue

        plan_data = load_yaml(path)
        plan = plan_data.get("plan", plan_data)

        # deadline: prefer plan.constraints.deadline_or_budget; fallback = latest task due end
        constraints = plan.get("constraints") or {}
        deadline = parse_date(str(constraints.get("deadline_or_budget", "")).strip())

        tasks = plan.get("tasks") or []
        if deadline is None:
            # fallback: latest due end among tasks
            due_ends = []
            for t in tasks:
                ds, de = parse_due(t.get("due"), today)
                if de:
                    due_ends.append(de)
                elif ds:
                    due_ends.append(ds)
            deadline = max(due_ends) if due_ends else today

        segs, labels, ok = pack_plan_tasks(tasks, today, deadline, overflow_days=args.overflow_days)
        plan_rows.append((pid, name, ok))

        out_png = outdir / f"packed_{pid}_{today.isoformat()}_to_{deadline.isoformat()}.png"
        plot_plan(pid, name, segs, labels, today, deadline, out_png)

        print(f"[OK] {pid}: {'fits' if ok else 'OVERFLOW'} -> {out_png}")

    # combined overview
    overview_png = outdir / f"packed_overview_{today.isoformat()}.png"
    combine_summary_plot(plan_rows, overview_png)
    print(f"[OK] overview -> {overview_png}")


if __name__ == "__main__":
    main()