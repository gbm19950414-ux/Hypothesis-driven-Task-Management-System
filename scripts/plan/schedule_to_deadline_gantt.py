#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schedule_to_deadline_gantt.py

最终版可视化脚本（方案 A）：
- 排程：从今天开始排到 deadline（取所有 plans 中最晚的 deadline_or_budget）
- 排序：active 优先于 todo（可改），urgency=h 在前，其余保持 plan 内出现顺序（不看 timebox 长短，不看 due）
- 匹配：load=h 优先进 energy=h 窗口；load=l 优先进 energy=l 窗口；放不下则“向后/向低 energy”窗口；仍不行就顺延到下一天
- 超大 timebox：若单任务 timebox 超过任一 time_window 容量，则自动切成多个 chunk（每个 chunk 不超过最大窗口容量），可跨天排程；图上仍显示为同一任务的多段条
- 每天最多 max_tasks_per_day 个任务
- 可视化：x 轴=日期+时间（绝对时间），y 轴=任务（task lane）
  y 轴标签 = "{plan_name} · {task_id} | {short_description}"（description 自动截断单行）

用法：
- 直接运行（使用默认配置 scripts/plan/plans_config.yaml；从 today 开始；输出到 life_os/outputs；最长 28 天；active-first）：
  python schedule_to_deadline_gantt.py

- 指定配置/输出：
  python schedule_to_deadline_gantt.py --config /path/to/plans_config.yaml --out /path/to/output.png

- 调整最长天数与 description 截断长度：
  python schedule_to_deadline_gantt.py --max_days 21 --desc_max 24

- 关闭 active-first：
  python schedule_to_deadline_gantt.py --no_active_first
"""

import argparse
import re
from datetime import datetime, date, timedelta, time
from pathlib import Path
import yaml

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
from matplotlib import font_manager


# ------------------------
# Utilities
# ------------------------
def parse_hl(x, default="l"):
    return x if x in ("h", "l") else default

def parse_timebox(tb):
    """Supports: '1h', '1.5h', '90m', '45min', '2' (treated as hours), '7d', '2w'.

    Notes:
    - 'd' (day) is interpreted as 8 hours of focused work by default.
    - 'w' (week) is interpreted as 5 workdays (5 * 8h).
    """
    if tb is None:
        return 0
    s = str(tb).strip().lower()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*h$", s)
    if m:
        return int(round(float(m.group(1)) * 60))
    m = re.match(r"^(\d+)\s*m(?:in)?$", s)
    if m:
        return int(m.group(1))
    # days: '7d', '1.5d' (interpreted as 8h per day)
    m = re.match(r"^(\d+(?:\.\d+)?)\s*d(?:ay|ays)?$", s)
    if m:
        days = float(m.group(1))
        return int(round(days * 8 * 60))

    # weeks: '2w', '1.5w' (interpreted as 5 workdays per week)
    m = re.match(r"^(\d+(?:\.\d+)?)\s*w(?:eek|eeks)?$", s)
    if m:
        weeks = float(m.group(1))
        return int(round(weeks * 5 * 8 * 60))
    m = re.match(r"^(\d+(?:\.\d+)?)$", s)
    if m:
        return int(round(float(m.group(1)) * 60))
    raise ValueError(f"Unrecognized timebox: {tb}")

def parse_hhmm(s):
    hh, mm = map(int, s.split(":"))
    return hh * 60 + mm

def to_datetime(d: date, minutes_from_midnight: int) -> datetime:
    hh = minutes_from_midnight // 60
    mm = minutes_from_midnight % 60
    return datetime.combine(d, time(hh, mm))

def short_desc(desc: str, max_chars: int = 28) -> str:
    """Single-line, trimmed, truncated with ellipsis."""
    if not desc:
        return ""
    s = " ".join(desc.strip().split())  # normalize whitespace, remove newlines
    if len(s) <= max_chars:
        return s
    return s[: max(0, max_chars - 1)] + "…"

def pick_cjk_font() -> str | None:
    """Try common CJK fonts; fallback to AR PL UMing CN if present."""
    preferred = [
        "PingFang SC",
        "Heiti SC",
        "Songti SC",
        "Microsoft YaHei",
        "Noto Sans CJK SC",
        "Noto Sans SC",
        "Source Han Sans SC",
        "SimHei",
        "WenQuanYi Zen Hei",
        "WenQuanYi Micro Hei",
        "Arial Unicode MS",
        "AR PL UMing CN",  # often available in minimal linux envs
    ]
    available = {f.name for f in font_manager.fontManager.ttflist}
    for fn in preferred:
        if fn in available:
            return fn
    return None


# ------------------------
# Task collection
# ------------------------
def collect_tasks_anywhere(node):
    """
    Recursively collect lists under any key 'tasks' in a nested YAML structure.
    This matches your current plan style where tasks appear under plan.steps[*].tasks etc.
    """
    out = []
    if isinstance(node, dict):
        if "tasks" in node and isinstance(node["tasks"], list):
            out.extend(node["tasks"])
        for v in node.values():
            out.extend(collect_tasks_anywhere(v))
    elif isinstance(node, list):
        for it in node:
            out.extend(collect_tasks_anywhere(it))
    return out


# ------------------------
# Scheduling
# ------------------------
def candidate_windows_for(task, windows):
    """
    Preference order:
    - match load->energy first (h->h, l->l), windows by earlier to later ("向后")
    - then fallback other energy ("向低 energy")
    """
    pref_energy = "h" if task["load"] == "h" else "l"
    same = [w for w in windows if w["energy"] == pref_energy]
    other = [w for w in windows if w["energy"] != pref_energy]
    return same + other

def schedule_tasks(
    tasks_sorted,
    windows,
    today: date,
    deadline: date,
    max_tasks_per_day: int,
):
    """
    Returns:
      schedule_by_task: dict(parent_uid -> list of segments)
          where segment = {start_dt, end_dt, window_index, chunk_idx, n_chunks}
      schedule_by_day: dict(date -> list of placements)
      remaining_tasks: list (schedulable items not scheduled by deadline)
    """
    remaining = tasks_sorted[:]
    schedule_by_task = {}
    schedule_by_day = {}

    day = today
    while remaining and day <= deadline:
        caps = [w["end_min"] - w["start_min"] for w in windows]
        used = [0 for _ in windows]  # minutes used inside each window, packed from start
        placements = []
        count = 0

        # iterate over a snapshot, remove from 'remaining' when scheduled
        for t in list(remaining):
            if count >= max_tasks_per_day:
                break

            tmin = t["timebox_min"]
            placed = False

            for w in candidate_windows_for(t, windows):
                wi = w["index"]
                if caps[wi] - used[wi] >= tmin:
                    start_min = w["start_min"] + used[wi]
                    end_min = start_min + tmin
                    start_dt = to_datetime(day, start_min)
                    end_dt = to_datetime(day, end_min)

                    parent_uid = t.get("parent_uid") or t["uid"]
                    seg = {
                        "start_dt": start_dt,
                        "end_dt": end_dt,
                        "window_index": wi,
                        "chunk_idx": t.get("chunk_idx", 0),
                        "n_chunks": t.get("n_chunks", 1),
                    }
                    schedule_by_task.setdefault(parent_uid, []).append(seg)

                    placements.append(
                        {
                            "task": t,
                            "start_dt": start_dt,
                            "end_dt": end_dt,
                            "window_index": wi,
                        }
                    )

                    used[wi] += tmin
                    remaining.remove(t)
                    count += 1
                    placed = True
                    break

            if not placed:
                continue

        schedule_by_day[day] = placements
        day += timedelta(days=1)

    return schedule_by_task, schedule_by_day, remaining


# ------------------------
# Chunking helper
# ------------------------

def split_into_chunks(total_minutes: int, max_chunk_minutes: int):
    """Split a large task into chunks that each fit within a single window capacity.

    Returns a list of chunk minutes, e.g. 520 with max 240 -> [240, 240, 40].
    """
    if total_minutes <= 0:
        return []
    if max_chunk_minutes <= 0:
        return [total_minutes]
    chunks = []
    remaining = total_minutes
    while remaining > 0:
        m = min(max_chunk_minutes, remaining)
        chunks.append(m)
        remaining -= m
    return chunks


# ------------------------
# Plotting (方案 A)
# ------------------------
def plot_gantt_task_lanes(
    tasks_in_lane_order,
    schedule_by_task,
    windows,
    today: date,
    deadline: date,
    out_png: str,
    desc_max: int = 28,
    dpi: int = 220,
):
    # Fonts for Chinese
    font_name = pick_cjk_font()
    if font_name:
        plt.rcParams["font.family"] = font_name
    plt.rcParams["axes.unicode_minus"] = False

    # Define x-range
    x_start = datetime.combine(today, time(0, 0))
    x_end = datetime.combine(deadline, time(23, 59))

    # Figure size: scale with number of tasks
    n = len(tasks_in_lane_order)
    fig_h = max(4.5, 0.35 * n + 2.0)
    fig, ax = plt.subplots(figsize=(16, fig_h))

    # Background: time windows as vertical bands per day
    # We draw translucent rectangles covering each window time range for each day.
    d = today
    while d <= deadline:
        for w in windows:
            ws = to_datetime(d, w["start_min"])
            we = to_datetime(d, w["end_min"])
            # energy-based alpha; keep neutral color (no explicit color choices)
            ax.add_patch(
                Rectangle(
                    (mdates.date2num(ws), -0.5),
                    mdates.date2num(we) - mdates.date2num(ws),
                    n,
                    alpha=0.06 if w["energy"] == "l" else 0.10,
                    fill=True,
                    linewidth=0,
                )
            )
        d += timedelta(days=1)

    # Draw tasks (each lane may have multiple scheduled segments if auto-chunked)
    for yi, t in enumerate(tasks_in_lane_order):
        parent_uid = t["uid"]
        if parent_uid not in schedule_by_task:
            continue

        segments = sorted(schedule_by_task[parent_uid], key=lambda s: s["start_dt"])
        for seg_i, seg in enumerate(segments):
            start_dt = seg["start_dt"]
            end_dt = seg["end_dt"]
            # task bar (again: no explicit colors)
            ax.add_patch(
                Rectangle(
                    (mdates.date2num(start_dt), yi - 0.35),
                    mdates.date2num(end_dt) - mdates.date2num(start_dt),
                    0.7,
                    alpha=0.35,
                    fill=True,
                )
            )

            # Label only once for chunked long tasks (show on the first segment only)
            n_chunks = int(seg.get("n_chunks", 1))
            if seg_i == 0:
                total_minutes = int(t.get("timebox_min", 0) or 0)
                total_hours = total_minutes / 60 if total_minutes else 0
                total_htxt = (
                    f"{int(total_hours)}h" if total_hours and abs(total_hours - round(total_hours)) < 1e-9 else (f"{total_hours:g}h" if total_hours else "")
                )
                if n_chunks > 1:
                    chunk_note = f"，分{n_chunks}段"
                else:
                    chunk_note = ""
                inner = f"{t['id']} ({total_htxt}{chunk_note}，负荷:{t['load']}，紧急:{t['urgency']})".strip()
                ax.text(
                    start_dt + (end_dt - start_dt) * 0.01,
                    yi,
                    inner,
                    va="center",
                    fontsize=9,
                    alpha=0.9,
                )

    # Y axis labels: 方案 A（task_id + short description）
    ylabels = []
    for t in tasks_in_lane_order:
        sdesc = short_desc(t["desc"], max_chars=desc_max)
        if sdesc:
            ylabels.append(f"{t['plan_name']} · {t['id']} | {sdesc}")
        else:
            ylabels.append(f"{t['plan_name']} · {t['id']}")
    ax.set_yticks(range(n))
    ax.set_yticklabels(ylabels, fontsize=9)
    ax.invert_yaxis()

    # X axis formatting
    ax.set_xlim(x_start, x_end)
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax.xaxis.set_minor_locator(mdates.HourLocator(byhour=[7, 9, 11, 12, 14, 16, 18]))
    ax.xaxis.set_minor_formatter(mdates.DateFormatter("%H:%M"))

    ax.tick_params(axis="x", which="major", labelsize=10, pad=6)
    ax.tick_params(axis="x", which="minor", labelsize=8, rotation=90)

    ax.set_xlabel("日期 / 时间（绝对时间轴）")
    ax.set_title(f"任务甘特图（从 {today} 到 {deadline}）")

    # deadline marker
    ax.axvline(datetime.combine(deadline, time(0, 0)), alpha=0.35, linestyle="--")

    ax.grid(True, axis="x", which="major", alpha=0.15)
    ax.grid(True, axis="x", which="minor", alpha=0.08)
    fig.tight_layout()
    fig.savefig(out_png, dpi=dpi)
    plt.close(fig)


# ------------------------
# Main
# ------------------------
def main():
    ap = argparse.ArgumentParser()

    # Defaults: config next to this script; output under life_os/outputs; start from today; active-first enabled.
    script_dir = Path(__file__).resolve().parent
    life_os_dir = script_dir.parent.parent  # .../life_os
    default_config = script_dir / "plans_config.yaml"
    default_outputs_dir = life_os_dir / "outputs"

    ap.add_argument(
        "--config",
        default=str(default_config),
        help="plans_config.yaml path (default: scripts/plan/plans_config.yaml)",
    )
    ap.add_argument(
        "--today",
        default=None,
        help="YYYY-MM-DD; default=today (local)",
    )
    ap.add_argument(
        "--out",
        default=None,
        help="output png path (default: life_os/outputs/schedule_gantt_<today>_<end>.png)",
    )
    ap.add_argument(
        "--outputs_dir",
        default=str(default_outputs_dir),
        help="output directory when --out is not provided (default: life_os/outputs)",
    )
    ap.add_argument(
        "--max_days",
        type=int,
        default=28,
        help="maximum number of days to schedule/plot starting from today (default: 28)",
    )
    ap.add_argument(
        "--desc_max",
        type=int,
        default=28,
        help="max chars for description in y labels (default: 28)",
    )
    ap.add_argument(
        "--no_active_first",
        action="store_true",
        help="disable active-first sorting (active-first is enabled by default)",
    )

    args = ap.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    # Determine "today"
    if args.today:
        today = datetime.strptime(args.today, "%Y-%m-%d").date()
    else:
        today = date.today()

    outputs_dir = Path(args.outputs_dir).expanduser().resolve()
    outputs_dir.mkdir(parents=True, exist_ok=True)

    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    max_tasks_per_day = int(cfg.get("max_tasks_per_day", 3))
    time_windows = cfg.get("time_window", [])
    plans = cfg.get("plans", [])

    # Build windows
    windows = []
    for i, w in enumerate(sorted(time_windows, key=lambda x: parse_hhmm(x["start"]))):
        windows.append(
            {
                "index": i,
                "start_min": parse_hhmm(w["start"]),
                "end_min": parse_hhmm(w["end"]),
                "energy": parse_hl(w.get("energy"), "l"),
                "start": w["start"],
                "end": w["end"],
            }
        )

    max_window_capacity_min = max((w["end_min"] - w["start_min"]) for w in windows) if windows else 0

    # Collect tasks from all plans.
    # - tasks_lanes: one entry per original task (y-axis lanes)
    # - tasks_schedulables: items actually scheduled (may be chunked parts)
    tasks_lanes = []
    tasks_schedulables = []
    deadlines = []

    for p_i, p in enumerate(plans):
        plan_name = p.get("name") or f"plan_{p_i}"
        plan_path = Path(p["path"])
        with plan_path.open("r", encoding="utf-8") as f:
            plan_doc = yaml.safe_load(f)

        # deadline
        try:
            dl = plan_doc["plan"]["constraints"]["deadline_or_budget"]
            deadlines.append(datetime.strptime(dl, "%Y-%m-%d").date())
        except Exception:
            pass

        raw_tasks = collect_tasks_anywhere(plan_doc.get("plan", {}))

        # de-dup by id within this plan, keep first occurrence
        seen = set()
        ordered = []
        for t in raw_tasks:
            tid = t.get("id")
            if tid and tid not in seen:
                seen.add(tid)
                ordered.append(t)

        for order, t in enumerate(ordered):
            status = t.get("status", "todo")
            if status not in ("todo", "active"):
                continue
            base_uid = f"{plan_name}::{t.get('id', order)}"
            tmin_total = parse_timebox(t.get("timebox", "1h"))

            lane = {
                "uid": base_uid,
                "plan_name": plan_name,
                "id": t.get("id", f"task_{order}"),
                "desc": (t.get("description") or "").strip(),
                "status": status,
                "urgency": parse_hl(t.get("urgency"), "l"),
                "load": parse_hl(t.get("load"), "l"),
                "timebox_min": tmin_total,
                "plan_order": p_i,
                "source_order": order,
            }
            tasks_lanes.append(lane)

            # If the task is too large to fit into any single window, auto-split into chunks.
            # Each chunk is a schedulable item; all chunks share the same parent_uid (lane).
            if max_window_capacity_min > 0 and tmin_total > max_window_capacity_min:
                chunks = split_into_chunks(tmin_total, max_window_capacity_min)
                n_chunks = len(chunks)
                for ci, cm in enumerate(chunks):
                    tasks_schedulables.append(
                        {
                            **lane,
                            "uid": f"{base_uid}::chunk{ci+1}",
                            "parent_uid": base_uid,
                            "chunk_idx": ci,
                            "n_chunks": n_chunks,
                            "timebox_min": cm,
                            # keep original order, but ensure chunks keep their internal order
                            "source_order": order * 1000 + ci,
                        }
                    )
            else:
                tasks_schedulables.append({**lane, "parent_uid": base_uid, "chunk_idx": 0, "n_chunks": 1})

    if not deadlines:
        raise RuntimeError("No deadline_or_budget found in any plan. Please add plan.constraints.deadline_or_budget.")

    deadline = max(deadlines)
    # Cap horizon to at most max_days starting from today (inclusive)
    cap_end = today + timedelta(days=max(0, args.max_days - 1))
    end_date = min(deadline, cap_end)

    # Sorting: urgency=h first; keep plan order + source order.
    # Optionally active before todo (recommended).
    def sort_key(t):
        active_first = not args.no_active_first
        status_rank = 0 if (active_first and t["status"] == "active") else 1
        if not active_first:
            status_rank = 0  # stable by default (no status re-order)
        urgency_rank = 0 if t["urgency"] == "h" else 1
        return (status_rank, urgency_rank, t["plan_order"], t["source_order"])

    lanes_sorted = sorted(tasks_lanes, key=sort_key)
    sched_sorted = sorted(tasks_schedulables, key=sort_key)

    schedule_by_task, _, remaining = schedule_tasks(
        tasks_sorted=sched_sorted,
        windows=windows,
        today=today,
        deadline=end_date,
        max_tasks_per_day=max_tasks_per_day,
    )

    # Y lanes order: one lane per original task (so you scan from top to bottom = priority)
    tasks_in_lane_order = lanes_sorted

    if args.out:
        out_png = str(Path(args.out).expanduser().resolve())
    else:
        out_png = str(outputs_dir / f"schedule_gantt_{today.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.png")

    plot_gantt_task_lanes(
        tasks_in_lane_order=tasks_in_lane_order,
        schedule_by_task=schedule_by_task,
        windows=windows,
        today=today,
        deadline=end_date,
        out_png=out_png,
        desc_max=args.desc_max,
    )

    # Optional: print summary to stdout
    print(f"Saved: {out_png}")
    print(f"Horizon: {today} -> {end_date} (max_days={args.max_days})")
    print(f"Scheduled tasks: {len(schedule_by_task)}")
    print(f"Unscheduled by deadline: {len(remaining)} (schedulable chunks)")
    if remaining:
        # Aggregate remaining chunks by parent_uid for a more human-readable summary
        rem_by_parent = {}
        for t in remaining:
            pu = t.get("parent_uid") or t["uid"]
            rem_by_parent.setdefault(pu, {"task": t, "minutes": 0, "n": 0})
            rem_by_parent[pu]["minutes"] += int(t.get("timebox_min", 0))
            rem_by_parent[pu]["n"] += 1

        # Preserve priority by iterating in sched_sorted order and picking unique parents
        seen = set()
        top = []
        for t in sched_sorted:
            pu = t.get("parent_uid") or t["uid"]
            if pu in rem_by_parent and pu not in seen:
                seen.add(pu)
                top.append(pu)
            if len(top) >= 10:
                break

        print("Top unscheduled (by priority, aggregated):")
        for pu in top:
            info = rem_by_parent[pu]
            tt = info["task"]
            hrs = info["minutes"] / 60
            htxt = f"{int(hrs)}h" if abs(hrs - round(hrs)) < 1e-9 else f"{hrs:g}h"
            print(f"  - {tt['plan_name']} {tt['id']} | {short_desc(tt['desc'], 40)} | remaining={htxt} across {info['n']} chunk(s)")


if __name__ == "__main__":
    main()