#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Render plan yaml into:
1) Step timeline (grouped by hypothesis)  -> PNG + Mermaid
2) Task swimlane (grouped by step)       -> PNG + Mermaid

Usage:
  python render_plan_viz.py \
    --plan "/mnt/data/20260114_20260214_博士学位.yaml" \
    --out "/Volumes/Samsung_SSD_990_PRO_2TB_Media/life_os/outputs"
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import font_manager
import yaml

# 固定输出目录（按你的 life_os 结构钉死）
FIXED_OUTDIR = "/Volumes/Samsung_SSD_990_PRO_2TB_Media/life_os/outputs"


# ---- Matplotlib 字体设置：避免中文显示为方框 ----
def setup_cjk_font() -> None:
    """
    尝试在 macOS 上优先使用常见中文字体；若不存在则回退系统默认字体。
    """
    candidates = [
        "PingFang SC",
        "Heiti SC",
        "Songti SC",
        "STHeiti",
        "STSong",
        "Noto Sans CJK SC",
        "Microsoft YaHei",
        "SimHei",
        "WenQuanYi Zen Hei",
    ]
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name]
            break
    # 兼容负号显示
    plt.rcParams["axes.unicode_minus"] = False


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RANGE_RE = re.compile(r"^(?P<start>\d{4}-\d{2}-\d{2})~(?P<end>\d{4}-\d{2}-\d{2})$")


def parse_iso_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def parse_due(due: str, today: date) -> Optional[Tuple[date, date]]:
    """
    due supports:
      - "YYYY-MM-DD"
      - "YYYY-MM-DD~YYYY-MM-DD"
      - "today"
      - "" (None)
    """
    if not due:
        return None
    due = due.strip()
    if due.lower() == "today":
        return (today, today)
    m = RANGE_RE.match(due)
    if m:
        return (parse_iso_date(m.group("start")), parse_iso_date(m.group("end")))
    if DATE_RE.match(due):
        d = parse_iso_date(due)
        return (d, d)
    # Unknown format -> ignore (but keep visible in summary)
    return None


@dataclass
class Hypothesis:
    id: str
    action: str
    expected_effect: str
    time_to_observe: str
    falsified_if: str


@dataclass
class Step:
    id: str
    description: str
    supports_hypothesis: str
    done_if: str
    output: str


@dataclass
class Task:
    id: str
    supports_step: str
    description: str
    output: str
    done_if: str
    due: str
    timebox: str


def safe_get(d: dict, key: str, default: str = "") -> str:
    v = d.get(key, default)
    return "" if v is None else str(v)


def load_plan(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def date_to_num(d: date) -> float:
    # matplotlib date numbers (for proper date axes)
    return mdates.date2num(datetime(d.year, d.month, d.day))


# --- Helper: Chinese-safe text truncation ---
def truncate_text(s: str, max_chars: int, placeholder: str = "…") -> str:
    """
    Truncate by character count (Chinese-safe). This avoids textwrap.shorten() collapsing
    Chinese strings to only '…' due to lack of whitespace word boundaries.
    """
    s = (s or "").strip()
    if max_chars <= 0:
        return ""
    if len(s) <= max_chars:
        return s
    # keep room for placeholder
    keep = max(0, max_chars - len(placeholder))
    return (s[:keep] + placeholder) if keep > 0 else placeholder


def make_step_ranges(
    hypotheses: Dict[str, Hypothesis],
    steps: List[Step],
    tasks: List[Task],
    today: date,
) -> Dict[str, Tuple[Optional[date], Optional[date]]]:
    """
    Infer each step's [start, end] from tasks due ranges.
    Fallback: hypothesis time_to_observe as end if step has no tasks.
    """
    step_to_task_ranges: Dict[str, List[Tuple[date, date]]] = {}
    unknown_due_tasks: List[str] = []

    for t in tasks:
        rng = parse_due(t.due, today)
        if rng is None and t.due:
            unknown_due_tasks.append(f"{t.id}:{t.due}")
            continue
        if rng is None:
            continue
        step_to_task_ranges.setdefault(t.supports_step, []).append(rng)

    step_ranges: Dict[str, Tuple[Optional[date], Optional[date]]] = {}
    for s in steps:
        ranges = step_to_task_ranges.get(s.id, [])
        if ranges:
            start = min(r[0] for r in ranges)
            end = max(r[1] for r in ranges)
            step_ranges[s.id] = (start, end)
        else:
            # fallback to hypothesis time_to_observe as end, start left None
            h = hypotheses.get(s.supports_hypothesis)
            end = None
            if h and DATE_RE.match(h.time_to_observe.strip()):
                end = parse_iso_date(h.time_to_observe.strip())
            step_ranges[s.id] = (None, end)

    return step_ranges


def render_steps_timeline_png(
    outdir: str,
    hypotheses: Dict[str, Hypothesis],
    steps: List[Step],
    step_ranges: Dict[str, Tuple[Optional[date], Optional[date]]],
    deadline: Optional[date],
) -> str:
    """
    Standard Gantt style (Solution #1):
    - X axis: time (dates)
    - Y axis: names (each Step is a row)
    This avoids text overlap by not placing long labels on bars.
    """
    # Order steps by hypothesis id, then step id (stable & readable)
    def step_sort_key(s: Step) -> Tuple[str, str]:
        return (s.supports_hypothesis or "", s.id or "")

    steps_sorted = sorted(steps, key=step_sort_key)

    # Collect x limits
    all_dates: List[date] = []
    for _, (st, en) in step_ranges.items():
        if st:
            all_dates.append(st)
        if en:
            all_dates.append(en)
    if deadline:
        all_dates.append(deadline)
    if not all_dates:
        all_dates = [date.today()]

    xmin = min(all_dates)
    xmax = max(all_dates)

    n = len(steps_sorted)
    fig_h = max(4.0, 0.45 * n + 1.8)
    fig, ax = plt.subplots(figsize=(12.5, fig_h))

    # Plot each step as one barh row
    y_positions = list(range(n))
    y_labels: List[str] = []
    for i, s in enumerate(steps_sorted):
        st, en = step_ranges.get(s.id, (None, None))

        # Fallbacks: if missing start, place at end; if both missing, place at xmin
        if en is None and st is None:
            st = xmin
            en = xmin
        elif st is None and en is not None:
            st = en
        elif st is not None and en is None:
            en = st

        left = date_to_num(st)
        width = max(1, date_to_num(en) - date_to_num(st) + 1)  # inclusive day
        ax.barh(y=i, width=width, left=left, height=0.55)

        short_desc = truncate_text(s.description, max_chars=28, placeholder="…")
        # Put hypothesis id in label for quick grouping (no extra legend needed)
        y_labels.append(f"{s.id} [{s.supports_hypothesis}]: {short_desc}")

    ax.set_yticks(y_positions)
    ax.set_yticklabels(y_labels, fontsize=9)
    ax.invert_yaxis()

    ax.set_title("Plan Timeline (Steps Gantt)", fontsize=13)
    ax.set_xlabel("Date")

    ax.set_xlim(date_to_num(xmin) - 1, date_to_num(xmax) + 7)

    # --- 日期刻度与网格：每天淡网格线；每周重网格线 ---
    # 每周作为主刻度（重网格线），每天作为次刻度（淡网格线）
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO, interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax.xaxis.set_minor_locator(mdates.DayLocator(interval=1))

    # 网格线：按 x 轴画
    ax.grid(which="minor", axis="x", linestyle="-", linewidth=0.5, alpha=0.20)
    ax.grid(which="major", axis="x", linestyle="-", linewidth=1.2, alpha=0.45)

    # 让日期标签更紧凑
    fig.autofmt_xdate(rotation=0)

    # Deadline vertical line
    if deadline:
        ax.axvline(date_to_num(deadline), linewidth=1.0)
        ax.text(
            date_to_num(deadline) + 0.2,
            -0.8,
            f"deadline {deadline.isoformat()}",
            fontsize=9,
            va="top",
        )

    # 留出更多左侧空间给很长的 y 轴标签（避免被裁切）
    fig.tight_layout()
    fig.subplots_adjust(left=0.32)
    outpath = os.path.join(outdir, "plan_steps_timeline.png")
    fig.savefig(outpath, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return outpath


def render_tasks_swimlane_png(
    outdir: str,
    steps: List[Step],
    tasks: List[Task],
    today: date,
    deadline: Optional[date],
) -> str:
    """
    Standard Gantt style (Solution #1):
    - X axis: time (dates)
    - Y axis: names (each Task is a row)
    This is the most widely accepted static format for timelines.
    """
    # Parse task ranges and keep only tasks with parseable due
    parsed: List[Tuple[Task, date, date]] = []
    unknown_due: List[Tuple[str, str]] = []
    for t in tasks:
        rng = parse_due(t.due, today)
        if rng is None and t.due:
            unknown_due.append((t.id, t.due))
        if rng is None:
            continue
        parsed.append((t, rng[0], rng[1]))

    # Order by step then task id for readability
    def task_sort_key(item: Tuple[Task, date, date]) -> Tuple[str, str]:
        t, _, _ = item
        return (t.supports_step or "", t.id or "")

    parsed_sorted = sorted(parsed, key=task_sort_key)

    # x limits
    all_dates: List[date] = []
    for _, st, en in parsed_sorted:
        all_dates += [st, en]
    if deadline:
        all_dates.append(deadline)
    if not all_dates:
        all_dates = [today]

    xmin = min(all_dates)
    xmax = max(all_dates)

    n = len(parsed_sorted)
    fig_h = max(4.5, 0.40 * n + 2.0)
    fig, ax = plt.subplots(figsize=(12.5, fig_h))

    y_positions = list(range(n))
    y_labels: List[str] = []
    for i, (t, st, en) in enumerate(parsed_sorted):
        left = date_to_num(st)
        width = max(1, date_to_num(en) - date_to_num(st) + 1)
        ax.barh(y=i, width=width, left=left, height=0.55)

        short_desc = truncate_text(t.description, max_chars=34, placeholder="…")
        y_labels.append(f"{t.id} [{t.supports_step}]: {short_desc}")

    ax.set_yticks(y_positions)
    ax.set_yticklabels(y_labels, fontsize=8)
    ax.invert_yaxis()

    ax.set_title("Tasks Timeline (Tasks Gantt)", fontsize=13)
    ax.set_xlabel("Date")
    ax.set_xlim(date_to_num(xmin) - 1, date_to_num(xmax) + 7)

    # --- 日期刻度与网格：每天淡网格线；每周重网格线 ---
    # 每周作为主刻度（重网格线），每天作为次刻度（淡网格线）
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO, interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax.xaxis.set_minor_locator(mdates.DayLocator(interval=1))

    # 网格线：按 x 轴画
    ax.grid(which="minor", axis="x", linestyle="-", linewidth=0.5, alpha=0.20)
    ax.grid(which="major", axis="x", linestyle="-", linewidth=1.2, alpha=0.45)

    # 让日期标签更紧凑
    fig.autofmt_xdate(rotation=0)

    if deadline:
        ax.axvline(date_to_num(deadline), linewidth=1.0)
        ax.text(
            date_to_num(deadline) + 0.2,
            -0.8,
            f"deadline {deadline.isoformat()}",
            fontsize=9,
            va="top",
        )

    if unknown_due:
        ax.text(
            date_to_num(xmin),
            n + 0.8,
            "Unparsed due: " + ", ".join([f"{i}:{d}" for i, d in unknown_due]),
            fontsize=8,
            va="top",
        )

    # 留出更多左侧空间给很长的 y 轴标签（避免被裁切）
    fig.tight_layout()
    fig.subplots_adjust(left=0.34)
    outpath = os.path.join(outdir, "plan_tasks_swimlane.png")
    fig.savefig(outpath, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return outpath


def render_mermaid_steps(
    hypotheses: Dict[str, Hypothesis],
    steps: List[Step],
) -> str:
    # simple graph: Hypothesis --> Step
    lines = ["flowchart LR"]
    for hid, h in sorted(hypotheses.items()):
        lines.append(f'  {hid}["{hid}: {h.action}"]')
    for s in steps:
        sid = s.id
        lines.append(f'  {sid}["{sid}: {s.description}"]')
        lines.append(f"  {s.supports_hypothesis} --> {sid}")
    return "\n".join(lines) + "\n"


def render_mermaid_tasks(
    steps: List[Step],
    tasks: List[Task],
) -> str:
    # Step --> Task
    lines = ["flowchart LR"]
    for s in steps:
        lines.append(f'  {s.id}["{s.id}: {s.description}"]')
    for t in tasks:
        tid = t.id
        label = t.description.replace('"', "'")
        lines.append(f'  {tid}["{tid}: {label}"]')
        lines.append(f"  {t.supports_step} --> {tid}")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(
        description="Render plan YAML into timeline/swimlane figures. Output dir is fixed.",
    )
    # 兼容两种调用方式：
    # 1) python render_plan_viz.py /path/to/plan.yaml
    # 2) python render_plan_viz.py --plan /path/to/plan.yaml
    ap.add_argument("plan_path", nargs="?", default="", help="Path to plan yaml (positional)")
    ap.add_argument("--plan", default="", help="Path to plan yaml (optional, overrides positional)")
    ap.add_argument("--today", default="", help='Override today date "YYYY-MM-DD" (optional)')
    ap.add_argument("--run_id", default="", help="Optional suffix for output folder (e.g., v2, 20260114a)")
    args = ap.parse_args()

    plan_path = (args.plan or args.plan_path).strip()
    if not plan_path:
        ap.error("Missing plan path. Usage: python render_plan_viz.py /path/to/plan.yaml")

    # 输出根目录固定，但每个 plan 用独立子目录，避免覆盖
    plan_base = os.path.splitext(os.path.basename(plan_path))[0]
    run_id = args.run_id.strip()
    folder = f"{plan_base}__{run_id}" if run_id else plan_base
    outdir = os.path.join(FIXED_OUTDIR, folder)

    # 设置中文字体，避免输出图中中文变方框
    setup_cjk_font()

    today = date.today() if not args.today else parse_iso_date(args.today)

    data = load_plan(plan_path)
    if "plan" not in data:
        raise ValueError("YAML root must contain 'plan:'")

    p = data["plan"]
    constraints = p.get("constraints", {}) or {}
    deadline = None
    dl = str(constraints.get("deadline_or_budget", "")).strip()
    if dl and DATE_RE.match(dl):
        deadline = parse_iso_date(dl)

    hypotheses_list = p.get("hypotheses", []) or []
    steps_list = p.get("steps", []) or []
    tasks_list = p.get("tasks", []) or []

    hypotheses: Dict[str, Hypothesis] = {}
    for h in hypotheses_list:
        hid = safe_get(h, "id")
        hypotheses[hid] = Hypothesis(
            id=hid,
            action=safe_get(h, "action"),
            expected_effect=safe_get(h, "expected_effect"),
            time_to_observe=safe_get(h, "time_to_observe"),
            falsified_if=safe_get(h, "falsified_if"),
        )

    steps: List[Step] = []
    for s in steps_list:
        steps.append(
            Step(
                id=safe_get(s, "id"),
                description=safe_get(s, "description"),
                supports_hypothesis=safe_get(s, "supports_hypothesis"),
                done_if=safe_get(s, "done_if"),
                output=safe_get(s, "output"),
            )
        )

    tasks: List[Task] = []
    for t in tasks_list:
        tasks.append(
            Task(
                id=safe_get(t, "id"),
                supports_step=safe_get(t, "supports_step"),
                description=safe_get(t, "description"),
                output=safe_get(t, "output"),
                done_if=safe_get(t, "done_if"),
                due=safe_get(t, "due"),
                timebox=safe_get(t, "timebox"),
            )
        )

    ensure_dir(outdir)

    step_ranges = make_step_ranges(hypotheses, steps, tasks, today)

    # Render PNGs
    step_png = render_steps_timeline_png(outdir, hypotheses, steps, step_ranges, deadline)
    task_png = render_tasks_swimlane_png(outdir, steps, tasks, today, deadline)

    # Render Mermaid
    mer_steps = render_mermaid_steps(hypotheses, steps)
    mer_tasks = render_mermaid_tasks(steps, tasks)

    mer_steps_path = os.path.join(outdir, "plan_steps_timeline.mmd")
    mer_tasks_path = os.path.join(outdir, "plan_tasks_swimlane.mmd")
    with open(mer_steps_path, "w", encoding="utf-8") as f:
        f.write(mer_steps)
    with open(mer_tasks_path, "w", encoding="utf-8") as f:
        f.write(mer_tasks)

    # Summary JSON (debuggable, useful)
    summary = {
        "plan_path": plan_path,
        "out_dir": outdir,
        "today": today.isoformat(),
        "deadline": deadline.isoformat() if deadline else "",
        "hypotheses": [h.__dict__ for h in hypotheses.values()],
        "steps": [s.__dict__ for s in steps],
        "tasks": [t.__dict__ for t in tasks],
        "step_ranges_inferred": {
            sid: {
                "start": st.isoformat() if st else "",
                "end": en.isoformat() if en else "",
            }
            for sid, (st, en) in step_ranges.items()
        },
    }
    with open(os.path.join(outdir, "plan_visualization_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("OK")
    print("step timeline png:", step_png)
    print("task swimlane png:", task_png)
    print("mermaid steps:", mer_steps_path)
    print("mermaid tasks:", mer_tasks_path)
    print("summary json:", os.path.join(outdir, "plan_visualization_summary.json"))


if __name__ == "__main__":
    main()