#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit_feasibility.py

极简“可行性审计”脚本（不做自动排程，只判断：在 deadline 前是否容量上可完成）

核心思想：全局（跨计划共享同一人力资源）按 deadline 做“前缀可行性检查”
- 对每个截止日期 d，统计所有 deadline<=d 的任务总需求 D(d)
- 统计从 today 到 d 的可用容量 S(d)
- 供给 S(d) 以工作日（周一到周五）计；周末默认不计入可用时间。
- 若任一 d 违反 D(d) <= S(d) 则必不可行（同一时间只能做一个任务）

同时检查“每天最多 N 个任务块”槽位约束：
- 对每个 d：Blocks(d) <= Days(d) * max_tasks_per_day

timebox 解析（重点）：
- h / m：按小时/分钟
- d / w：允许切块，并按“你的每天可用时间”换算
  - 1d = 1 天总可用分钟数（由 time_window 求和，通常≈10h）
  - 1w = 7d（按自然周计；如果你想改成 5d，只改 WEEK_DAYS 即可）

切块（仅用于槽位 blocks 下界估计，不做排程）：
- 对 timebox=d 或 w 的任务：blocks = ceil(minutes / max_window_capacity)
  （max_window_capacity = 单个 time_window 的最大容量；这是“最少需要多少次上手”的保守下界）
- 对 timebox=h/m 的任务：blocks = 1

默认行为（符合你 life_os 习惯）：
- 直接运行：读取 scripts/plan/plans_config.yaml
- 从 today 开始
- 最长 28 天（--max_days 28）
- 输出到 life_os/outputs/feasibility_audit_YYYYMMDD_YYYYMMDD.md
"""

import argparse
import math
import re
from datetime import datetime, date, timedelta
from pathlib import Path
import yaml

WEEK_DAYS = 5  # 如果你想 1w=5d，把这里改成 5


# ------------------------
# Helpers
# ------------------------
def parse_hl(x, default="l"):
    return x if x in ("h", "l") else default

def parse_hhmm(s: str) -> int:
    hh, mm = map(int, s.split(":"))
    return hh * 60 + mm

def collect_tasks_anywhere(node):
    """Recursively collect lists under any key 'tasks' in a nested YAML structure."""
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

def short(s: str, n=60) -> str:
    s = " ".join((s or "").strip().split())
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"

def parse_timebox_to_minutes(tb, day_capacity_min: int) -> tuple[int, str]:
    """
    Return (minutes, unit_kind) where unit_kind in {"hm","d","w","unknown"}.
    - 'd' / 'w' are interpreted using day_capacity_min.
    """
    if tb is None:
        return 0, "unknown"
    s = str(tb).strip().lower()

    m = re.match(r"^(\d+(?:\.\d+)?)\s*h$", s)
    if m:
        return int(round(float(m.group(1)) * 60)), "hm"

    m = re.match(r"^(\d+)\s*m(?:in)?$", s)
    if m:
        return int(m.group(1)), "hm"

    # days: 1d = 1 day capacity (from time_window sum)
    m = re.match(r"^(\d+(?:\.\d+)?)\s*d(?:ay|ays)?$", s)
    if m:
        days = float(m.group(1))
        return int(round(days * day_capacity_min)), "d"

    # weeks: 1w = WEEK_DAYS * day capacity
    m = re.match(r"^(\d+(?:\.\d+)?)\s*w(?:eek|eeks)?$", s)
    if m:
        weeks = float(m.group(1))
        return int(round(weeks * WEEK_DAYS * day_capacity_min)), "w"

    # fallback: plain number treated as hours
    m = re.match(r"^(\d+(?:\.\d+)?)$", s)
    if m:
        return int(round(float(m.group(1)) * 60)), "hm"

    raise ValueError(f"Unrecognized timebox: {tb}")

def ceil_div(a: int, b: int) -> int:
    return int(math.ceil(a / b)) if b > 0 else 0


def count_workdays_inclusive(start: date, end: date) -> int:
    """Count workdays (Mon–Fri) from start to end inclusive."""
    if end < start:
        return 0
    n = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


# ------------------------
# Core audit logic
# ------------------------
def main():
    ap = argparse.ArgumentParser()

    script_dir = Path(__file__).resolve().parent
    life_os_dir = script_dir.parent.parent  # .../life_os
    default_config = script_dir / "plans_config.yaml"
    default_outputs_dir = life_os_dir / "outputs"

    ap.add_argument("--config", default=str(default_config))
    ap.add_argument("--today", default=None, help="YYYY-MM-DD; default=today")
    ap.add_argument("--max_days", type=int, default=28)
    ap.add_argument("--out", default=None, help="output md path (optional)")
    ap.add_argument("--outputs_dir", default=str(default_outputs_dir))

    args = ap.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    if args.today:
        today = datetime.strptime(args.today, "%Y-%m-%d").date()
    else:
        today = date.today()

    outputs_dir = Path(args.outputs_dir).expanduser().resolve()
    outputs_dir.mkdir(parents=True, exist_ok=True)

    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    max_tasks_per_day = int(cfg.get("max_tasks_per_day", 3))
    time_windows_cfg = cfg.get("time_window", [])
    plans_cfg = cfg.get("plans", [])

    # Build windows + per-day capacity
    windows = []
    for w in time_windows_cfg:
        smin = parse_hhmm(w["start"])
        emin = parse_hhmm(w["end"])
        windows.append({"start_min": smin, "end_min": emin, "energy": parse_hl(w.get("energy"), "l")})

    if not windows:
        raise RuntimeError("No time_window found in config.")

    day_capacity_total = sum(w["end_min"] - w["start_min"] for w in windows)
    day_capacity_h = sum((w["end_min"] - w["start_min"]) for w in windows if w["energy"] == "h")
    day_capacity_l = sum((w["end_min"] - w["start_min"]) for w in windows if w["energy"] == "l")
    max_window_capacity = max((w["end_min"] - w["start_min"]) for w in windows)

    # Collect all tasks globally with plan deadline as task deadline
    tasks = []
    deadlines = []

    def resolve_path(p: str) -> Path:
        pp = Path(p).expanduser()
        if pp.is_absolute():
            return pp.resolve()
        # relative paths treated as relative to life_os_dir
        return (life_os_dir / pp).resolve()

    for p_i, p in enumerate(plans_cfg):
        plan_name = p.get("name") or f"plan_{p_i}"
        plan_path = resolve_path(p["path"])
        if not plan_path.exists():
            raise FileNotFoundError(f"Plan file not found: {plan_path}")

        with plan_path.open("r", encoding="utf-8") as f:
            plan_doc = yaml.safe_load(f)

        try:
            dl = plan_doc["plan"]["constraints"]["deadline_or_budget"]
            plan_deadline = datetime.strptime(dl, "%Y-%m-%d").date()
            deadlines.append(plan_deadline)
        except Exception:
            raise RuntimeError(f"Missing plan.constraints.deadline_or_budget in: {plan_path}")

        raw_tasks = collect_tasks_anywhere(plan_doc.get("plan", {}))

        # de-dup by id within plan, keep first occurrence
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

            tb = t.get("timebox", "1h")
            minutes, unit_kind = parse_timebox_to_minutes(tb, day_capacity_total)

            load = parse_hl(t.get("load"), "l")
            urgency = parse_hl(t.get("urgency"), "l")
            desc = (t.get("description") or "").strip()
            tid = t.get("id", f"task_{order}")

            # blocks (slot feasibility lower bound)
            # - d/w: chunkable -> minimal blocks = ceil(minutes / max_window_capacity)
            # - hm: treat as 1 block (you'll pick it as one item from task pool)
            if unit_kind in ("d", "w") and max_window_capacity > 0:
                blocks = max(1, ceil_div(minutes, max_window_capacity))
            else:
                blocks = 1

            tasks.append(
                {
                    "plan": plan_name,
                    "id": tid,
                    "desc": desc,
                    "status": status,
                    "urgency": urgency,
                    "load": load,
                    "minutes": int(minutes),
                    "blocks": int(blocks),
                    "deadline": plan_deadline,
                }
            )

    global_deadline = max(deadlines)
    cap_end = today + timedelta(days=max(0, args.max_days - 1))
    end_date = min(global_deadline, cap_end)

    # Consider only tasks whose plan-deadline is within horizon end_date
    tasks_h = [t for t in tasks if t["deadline"] <= end_date]

    # Unique cut dates = sorted unique deadlines within horizon
    cut_dates = sorted({t["deadline"] for t in tasks_h})
    if not cut_dates:
        cut_dates = [end_date]

    # Sort tasks for reporting (not for scheduling): active first, urgency h first, then earlier deadline
    def rep_key(t):
        return (0 if t["status"] == "active" else 1, 0 if t["urgency"] == "h" else 1, t["deadline"], t["plan"], t["id"])
    tasks_h_sorted = sorted(tasks_h, key=rep_key)

    # Prefix feasibility check
    results = []
    first_fail = None

    for d in cut_dates:
        if d < today:
            continue
        workdays = count_workdays_inclusive(today, d)
        supply_total = workdays * day_capacity_total
        supply_h = workdays * day_capacity_h
        supply_l = workdays * day_capacity_l
        slots = workdays * max_tasks_per_day

        prefix = [t for t in tasks_h_sorted if t["deadline"] <= d]
        demand_total = sum(t["minutes"] for t in prefix)
        demand_h = sum(t["minutes"] for t in prefix if t["load"] == "h")
        demand_l = sum(t["minutes"] for t in prefix if t["load"] == "l")
        blocks = sum(t["blocks"] for t in prefix)

        # Feasibility modes
        ok_relaxed = demand_total <= supply_total  # allow h->l
        ok_strict_energy = (demand_h <= supply_h) and (demand_l <= supply_l)  # no energy mixing
        ok_slots = blocks <= slots

        # deficits (positive means lacking)
        deficit_total = max(0, demand_total - supply_total)
        deficit_h = max(0, demand_h - supply_h)
        deficit_l = max(0, demand_l - supply_l)
        deficit_slots = max(0, blocks - slots)

        row = {
            "date": d,
            "workdays": workdays,
            "supply_total": supply_total,
            "supply_h": supply_h,
            "supply_l": supply_l,
            "demand_total": demand_total,
            "demand_h": demand_h,
            "demand_l": demand_l,
            "blocks": blocks,
            "slots": slots,
            "ok_relaxed": ok_relaxed,
            "ok_strict_energy": ok_strict_energy,
            "ok_slots": ok_slots,
            "deficit_total": deficit_total,
            "deficit_h": deficit_h,
            "deficit_l": deficit_l,
            "deficit_slots": deficit_slots,
            "n_tasks": len(prefix),
        }
        results.append(row)

        if first_fail is None and (not ok_relaxed or not ok_slots or (not ok_strict_energy)):
            first_fail = row

    # Output markdown
    def fmt_h(mins: int) -> str:
        h = mins / 60
        return f"{h:.1f}h"

    if args.out:
        out_md = Path(args.out).expanduser().resolve()
    else:
        out_md = outputs_dir / f"feasibility_audit_{today.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.md"

    lines = []
    lines.append(f"# Feasibility Audit\n")
    lines.append(f"- Today: `{today}`\n")
    lines.append(f"- Horizon: `{today}` → `{end_date}` (max_days={args.max_days})\n")
    lines.append(f"- Daily capacity (from time_window): total={fmt_h(day_capacity_total)}, h={fmt_h(day_capacity_h)}, l={fmt_h(day_capacity_l)}\n")
    lines.append(f"- Max window capacity: {fmt_h(max_window_capacity)} (used for chunkable d/w blocks lower bound)\n")
    lines.append(f"- Max tasks per day (slots): {max_tasks_per_day}\n")
    lines.append(f"- Week definition for `w`: {WEEK_DAYS} days\n")

    if first_fail is None:
        lines.append("\n## Verdict\n")
        lines.append("✅ **Feasible (within horizon)** under relaxed time capacity and slots; strict energy feasibility is reported below.\n")
    else:
        lines.append("\n## Verdict\n")
        lines.append("❌ **Infeasible (within horizon)** — at least one deadline prefix exceeds capacity and/or slots.\n")
        lines.append(f"- First failure at: `{first_fail['date']}` (workdays={first_fail['workdays']})\n")
        lines.append(f"  - Total deficit: {fmt_h(first_fail['deficit_total'])}\n")
        lines.append(f"  - Slots deficit: {first_fail['deficit_slots']} block(s)\n")
        lines.append(f"  - Energy strict deficits: h={fmt_h(first_fail['deficit_h'])}, l={fmt_h(first_fail['deficit_l'])}\n")

    lines.append("\n## Prefix Check by Deadline\n")
    lines.append("| deadline | workdays | demand_total | supply_total | ok_total(relaxed) | blocks | slots | ok_slots | ok_energy(strict) |\n")
    lines.append("|---|---:|---:|---:|:---:|---:|---:|:---:|:---:|\n")

    for r in results:
        lines.append(
            f"| {r['date']} | {r['workdays']} | {fmt_h(r['demand_total'])} | {fmt_h(r['supply_total'])} | "
            f"{'✅' if r['ok_relaxed'] else '❌'} | {r['blocks']} | {r['slots']} | "
            f"{'✅' if r['ok_slots'] else '❌'} | {'✅' if r['ok_strict_energy'] else '❌'} |\n"
        )

    lines.append("\n## Notes\n")
    lines.append("- `ok_total(relaxed)` 允许高能任务占用低能窗口（只看总分钟）。\n")
    lines.append("- `ok_energy(strict)` 不允许能量混用（分别检查 h/l 供需）。\n")
    lines.append("- `blocks/slots` 是“每天最多 N 个任务块”的可执行性下界检查：\n")
    lines.append("  - 对 `d/w` 任务：允许切块，blocks≈ceil(minutes / max_window_capacity)\n")
    lines.append("  - 对 `h/m` 任务：blocks=1\n")
    lines.append("  这不是排程，只是判断你是否在注意力切换层面上必然超载。\n")

    # If infeasible, list top contributors up to first_fail date
    if first_fail is not None:
        d = first_fail["date"]
        prefix = [t for t in tasks_h_sorted if t["deadline"] <= d]
        prefix = sorted(prefix, key=lambda t: (t["deadline"], 0 if t["urgency"] == "h" else 1, 0 if t["status"] == "active" else 1))
        lines.append(f"\n## Tasks contributing to first failure (deadline<= {d})\n")
        lines.append("| plan | id | status | urgency | load | time | blocks | desc |\n")
        lines.append("|---|---|---|---|---|---:|---:|---|\n")
        for t in prefix[:40]:
            lines.append(
                f"| {t['plan']} | {t['id']} | {t['status']} | {t['urgency']} | {t['load']} | {fmt_h(t['minutes'])} | {t['blocks']} | {short(t['desc'], 50)} |\n"
            )
        if len(prefix) > 40:
            lines.append(f"\n(… truncated, showing first 40 / {len(prefix)} tasks)\n")

    out_md.write_text("".join(lines), encoding="utf-8")

    # Console summary
    print(f"Saved: {out_md}")
    print(f"Horizon: {today} -> {end_date} (max_days={args.max_days})")
    if first_fail is None:
        print("Verdict: FEASIBLE within horizon (relaxed total + slots). See strict energy in report table.")
    else:
        print(f"Verdict: INFEASIBLE. First fail at {first_fail['date']}.")
        print(f"  Workdays available: {first_fail['workdays']}")
        print(f"  Total deficit: {fmt_h(first_fail['deficit_total'])}")
        print(f"  Slots deficit: {first_fail['deficit_slots']}")
        print(f"  Energy deficits (strict): h={fmt_h(first_fail['deficit_h'])}, l={fmt_h(first_fail['deficit_l'])}")


if __name__ == "__main__":
    main()