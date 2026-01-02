
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml  # Requires: pip install pyyaml
except Exception as e:
    print("This script requires PyYAML. Please run: pip install pyyaml", file=sys.stderr)
    raise


BLOCK_MINUTES = 50

# --- helpers for parsing ISO datetimes & duration strings ---

def parse_iso_datetime(s: Optional[str]) -> Optional[dt.datetime]:
    if not s:
        return None
    try:
        # tolerate trailing Z
        s2 = s.replace("Z", "")
        return dt.datetime.fromisoformat(s2)
    except Exception:
        return None


def parse_duration_to_minutes(x: Any) -> Optional[float]:
    """Accepts numeric minutes or strings like '90', '1.5h', '3h', '45m', '1h30m', '01:20'."""
    if x is None:
        return None
    # numeric -> minutes
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        s = x.strip().lower()
        # HH:MM
        m = re.match(r"^(\d{1,2}):(\d{2})$", s)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            return float(hh * 60 + mm)
        # 1h30m / 1.5h / 90m / 90
        m = re.match(r"^(?:(\d+(?:\.\d+)?)h)?(?:(\d+)m)?$", s)
        if m and (m.group(1) or m.group(2)):
            h = float(m.group(1)) if m.group(1) else 0.0
            m_ = float(m.group(2)) if m.group(2) else 0.0
            return h * 60.0 + m_
        # simple forms like '3h' or '45m'
        m = re.match(r"^(\d+(?:\.\d+)?)\s*h$", s)
        if m:
            return float(m.group(1)) * 60.0
        m = re.match(r"^(\d+)\s*m$", s)
        if m:
            return float(m.group(1))
        # plain number string -> minutes
        try:
            return float(s)
        except Exception:
            return None
    return None


def parse_week_iso(week_str: Optional[str], today: dt.date) -> Tuple[dt.date, dt.date, str]:
    """
    Returns (monday, sunday, "YYYY-Www") for given ISO week string or for 'next week' relative to today.
    If week_str is None -> next ISO week.
    """
    if week_str:
        m = re.match(r"^(\d{4})-W(\d{2})$", week_str.strip())
        if not m:
            raise ValueError("Week must be in ISO format like 2025-W43")
        year, week = int(m.group(1)), int(m.group(2))
        monday = dt.date.fromisocalendar(year, week, 1)
        sunday = dt.date.fromisocalendar(year, week, 7)
        return monday, sunday, f"{year}-W{week:02d}"
    else:
        iso = today.isocalendar()
        year, week = iso.year, iso.week + 1
        try:
            monday = dt.date.fromisocalendar(year, week, 1)
        except ValueError:
            monday = dt.date.fromisocalendar(year + 1, 1, 1)
            year = monday.isocalendar().year
            week = monday.isocalendar().week
        sunday = dt.date.fromisocalendar(year, week, 7)
        return monday, sunday, f"{year}-W{week:02d}"


def parse_week_iso_current(week_str: Optional[str], today: dt.date) -> Tuple[dt.date, dt.date, str]:
    """
    Returns (monday, sunday, "YYYY-Www") for given ISO week string or for 'current week' when None.
    """
    if week_str:
        return parse_week_iso(week_str, today)  # reuse
    iso = today.isocalendar()
    monday = dt.date.fromisocalendar(iso.year, iso.week, 1)
    sunday = dt.date.fromisocalendar(iso.year, iso.week, 7)
    return monday, sunday, f"{iso.year}-W{iso.week:02d}"


def parse_cn_stamp(s: str) -> Optional[dt.datetime]:
    """
    Parse strings like "周四_2025-10-02_07:20" into datetime.
    Works even if only YYYY-MM-DD is present.
    """
    if not s:
        return None
    m = re.search(r"(\\d{4}-\\d{2}-\\d{2})(?:[_T](\\d{2}):(\\d{2}))?", s)
    if not m:
        return None
    date_part = m.group(1)
    hh = int(m.group(2)) if m.group(2) else 0
    mm = int(m.group(3)) if m.group(3) else 0
    try:
        y, mo, d = [int(x) for x in date_part.split("-")]
        return dt.datetime(y, mo, d, hh, mm)
    except Exception:
        return None


def minutes_between(a: Optional[dt.datetime], b: Optional[dt.datetime]) -> float:
    if a and b:
        return max(0.0, (b - a).total_seconds() / 60.0)
    return 0.0


def read_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def gather_task_files(root: Path) -> List[Path]:
    if root.is_file():
        return [root]
    files: List[Path] = []
    for p in root.rglob("*"):
        if p.suffix.lower() in (".yml", ".yaml"):
            files.append(p)
    return files


@dataclasses.dataclass
class TimeLog:
    start: Optional[dt.datetime]
    end: Optional[dt.datetime]
    note: List[str]

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TimeLog":
        start = parse_cn_stamp(d.get("start") or "")
        end = parse_cn_stamp(d.get("end") or "")
        note = d.get("note") or []
        return cls(start=start, end=end, note=note)


@dataclasses.dataclass
class Task:
    uid: str
    title: str
    status: str
    due: Optional[dt.date]
    duration_minutes: Optional[float]
    difficulty: Optional[float]
    deliverable: Optional[str]
    acceptance: Optional[str]
    time_logs: List[TimeLog]
    start_dt: Optional[dt.datetime]
    end_dt: Optional[dt.datetime]
    repeat_rule: Dict[str, Any]
    created_at: Optional[dt.datetime]
    raw: Dict[str, Any]
    file_path: str

    @classmethod
    def from_yaml(cls, data: Dict[str, Any], file_path: str) -> "Task":
        uid = str(data.get("uid") or os.path.basename(file_path))
        title = str(data.get("title") or data.get("name") or "未命名任务")
        status = str(data.get("status") or "todo")
        due_raw = data.get("due")
        due = None
        if isinstance(due_raw, str):
            try:
                due = dt.date.fromisoformat(due_raw)
            except Exception:
                due = None

        # duration can be numeric minutes (duration_minutes) or string (duration)
        duration = data.get("duration_minutes")
        if duration is None and data.get("duration") is not None:
            duration = parse_duration_to_minutes(data.get("duration"))
        elif isinstance(duration, str):
            duration = parse_duration_to_minutes(duration)
        elif isinstance(duration, (int, float)):
            duration = float(duration)
        else:
            duration = None

        difficulty = None
        if isinstance(data.get("difficulty"), (int, float)):
            difficulty = float(data.get("difficulty"))

        deliverable = data.get("deliverable")
        acceptance = data.get("acceptance") or data.get("acceptance_criteria")

        logs_raw = data.get("time_logs") or []
        logs: List[TimeLog] = []
        if isinstance(logs_raw, list):
            for lr in logs_raw:
                if isinstance(lr, dict):
                    logs.append(TimeLog.from_dict(lr))

        # schedule-like fields
        start_dt = parse_iso_datetime(data.get("start"))
        end_dt = parse_iso_datetime(data.get("end"))
        repeat_rule = data.get("repeat_rule") or {}
        created_at = parse_iso_datetime(data.get("created_at"))

        return cls(
            uid=uid,
            title=title,
            status=status,
            due=due,
            duration_minutes=duration,
            difficulty=difficulty,
            deliverable=deliverable,
            acceptance=acceptance,
            time_logs=logs,
            start_dt=start_dt,
            end_dt=end_dt,
            repeat_rule=repeat_rule if isinstance(repeat_rule, dict) else {},
            created_at=created_at,
            raw=data,
            file_path=file_path,
        )


# --- expand planned occurrences for a week ---

def occurrences_in_week(task: Task, week_start: dt.date, week_end: dt.date) -> List[TimeLog]:
    occ: List[TimeLog] = []

    # 1) explicit time_logs
    for lg in task.time_logs:
        if lg.start is None and lg.end is None:
            continue
        s = lg.start.date() if lg.start else None
        e = lg.end.date() if lg.end else s
        if s and s <= week_end and (e or s) >= week_start:
            # clamp inside week if needed (but keep original for display)
            occ.append(lg)

    # 2) single scheduled start/end inside week
    if task.start_dt is not None:
        d = task.start_dt.date()
        if week_start <= d <= week_end:
            end_dt = task.end_dt
            if end_dt is None:
                dur = task.duration_minutes or BLOCK_MINUTES
                end_dt = task.start_dt + dt.timedelta(minutes=dur)
            occ.append(TimeLog(start=task.start_dt, end=end_dt, note=[]))

    # 3) repeat_rule (weekly)
    rr = task.repeat_rule or {}
    if isinstance(rr, dict) and (rr.get("freq") == "weekly" or rr.get("freq") == "WEEKLY"):
        interval = int(rr.get("interval", 1) or 1)
        byweekday = rr.get("byweekday") or rr.get("by_weekday") or []
        # normalize weekdays to ints 0-6
        days_idx: List[int] = []
        for w in byweekday:
            try:
                days_idx.append(int(w))
            except Exception:
                pass
        # time-of-day from start_dt if available, else 09:00 default
        start_time = dt.time(9, 0)
        if task.start_dt is not None:
            start_time = dt.time(task.start_dt.hour, task.start_dt.minute)
        # duration from field or default block
        dur_min = task.duration_minutes or None
        if dur_min is None and task.end_dt is not None and task.start_dt is not None:
            dur_min = max(0.0, (task.end_dt - task.start_dt).total_seconds()/60.0)
        if dur_min is None:
            dur_min = BLOCK_MINUTES

        # anchor for interval parity: created_at week if available, else week_start
        anchor_date = (task.created_at.date() if task.created_at else week_start)
        anchor_iso = anchor_date.isocalendar()
        for i in range(7):
            day = week_start + dt.timedelta(days=i)
            if days_idx and day.weekday() not in days_idx:
                continue
            # check interval parity based on ISO week distance
            weeks_apart = (day.isocalendar().week + 52* (day.isocalendar().year-anchor_iso.year)) - anchor_iso.week
            if weeks_apart % interval != 0:
                continue
            sdt = dt.datetime.combine(day, start_time)
            edt = sdt + dt.timedelta(minutes=dur_min)
            occ.append(TimeLog(start=sdt, end=edt, note=[]))

    return occ



def tasks_in_week(tasks: List[Task], week_start: dt.date, week_end: dt.date) -> List[Task]:
    selected: List[Task] = []
    for t in tasks:
        occ = occurrences_in_week(t, week_start, week_end)
        due_inside = t.due and week_start <= t.due <= week_end
        if occ or due_inside:
            selected.append(t)
    return selected



def group_logs_by_day(task: Task, week_start: dt.date, week_end: dt.date) -> Dict[str, List[Tuple[str, float]]]:
    by_day: Dict[str, List[Tuple[str, float]]] = {}
    occ = occurrences_in_week(task, week_start, week_end)
    for log in occ:
        if not log.start:
            continue
        day = log.start.date()
        if not (week_start <= day <= week_end):
            continue
        mins = minutes_between(log.start, log.end)
        if mins <= 0.0:
            mins = task.duration_minutes or BLOCK_MINUTES
        date_key = day.isoformat()
        disp = f"{task.title}"
        if log.start and log.end:
            disp += f" ({log.start.strftime('%H:%M')}-{log.end.strftime('%H:%M')})"
        else:
            disp += f" (~{int(round(mins))}m)"
        by_day.setdefault(date_key, []).append((disp, mins))
    return by_day


def pick_outcomes(planned_tasks: List[Task], top_k: int = 3) -> List[Dict[str, Any]]:
    def score(t: Task) -> Tuple:
        due_score = t.due.toordinal() if t.due else 9999999
        dur = t.duration_minutes or 0.0
        diff = t.difficulty or 0.0
        has_deliv = 0 if (t.deliverable or t.acceptance) else 1
        return (due_score, -dur, -diff, has_deliv, t.title)

    sorted_tasks = sorted(planned_tasks, key=score)
    outs = []
    for t in sorted_tasks[:top_k]:
        outs.append({
            "name": t.deliverable or t.title,
            "acceptance": t.acceptance or "完成并有可验证的证据（文件/链接/图稿/PR）",
            "due": t.due.isoformat() if t.due else None,
            "source_task": t.uid
        })
    return outs


def minutes_to_blocks(mins: float) -> float:
    return mins / BLOCK_MINUTES if mins else 0.0


def build_plan(tasks: List[Task], week_start: dt.date, week_end: dt.date) -> Dict[str, Any]:
    planned = tasks_in_week(tasks, week_start, week_end)
    schedule: Dict[str, List[str]] = {}
    total_planned_mins = 0.0
    for t in planned:
        by_day = group_logs_by_day(t, week_start, week_end)
        for d, items in by_day.items():
            items_sorted = sorted(items, key=lambda x: x[0])
            for disp, mins in items_sorted:
                schedule.setdefault(d, []).append(disp)
                total_planned_mins += mins

    outcomes = pick_outcomes(planned, top_k=3)
    plan = {
        "week": f"{week_start.isocalendar().year}-W{week_start.isocalendar().week:02d}",
        "period": {"start": week_start.isoformat(), "end": week_end.isoformat()},
        "outcomes": outcomes,
        "wip_limit": 3,
        "capacity": {
            "plan_minutes": int(round(total_planned_mins)),
            "plan_blocks": round(minutes_to_blocks(total_planned_mins), 1)
        },
        "daily_plan": {k: v for k, v in sorted(schedule.items())},
    }
    return plan


def build_summary(tasks: List[Task], week_start: dt.date, week_end: dt.date, plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    worked_mins_by_task: Dict[str, float] = {}
    planned_titles: set[str] = set()
    if plan:
        for _, items in plan.get("daily_plan", {}).items():
            planned_titles.update([re.sub(r"\\s*\\([^)]+\\)$", "", it) for it in items])

    completed_tasks: List[str] = []
    delayed_tasks: List[str] = []

    planned_count = 0
    for t in tasks_in_week(tasks, week_start, week_end):
        if t.title in planned_titles or t.due and week_start <= t.due <= week_end:
            planned_count += 1
        mins = 0.0
        for log in t.time_logs:
            if not log.start:
                continue
            day = log.start.date()
            if not (week_start <= day <= week_end):
                continue
            m = minutes_between(log.start, log.end)
            if m <= 0.0:
                m = t.duration_minutes or BLOCK_MINUTES
            mins += m
        if mins > 0:
            worked_mins_by_task[t.title] = worked_mins_by_task.get(t.title, 0.0) + mins

        if (t.status or "").lower() in ("done", "complete", "closed"):
            completed_tasks.append(t.title)
        else:
            if t.due and t.due <= week_end and (t.status or "").lower() not in ("done", "complete", "closed"):
                delayed_tasks.append(t.title)

    achievements = sorted(worked_mins_by_task.items(), key=lambda kv: kv[1], reverse=True)[:3]
    achievements = [f"{t}（{int(round(m))} 分钟）" for t, m in achievements] or completed_tasks[:3]

    completed_count = len([t for t in tasks if (t.status or "").lower() in ("done", "complete", "closed") and (t.due is None or t.due <= week_end)])
    delay_rate = round((len(delayed_tasks) / planned_count) * 100, 1) if planned_count else 0.0

    summary = {
        "week": f"{week_start.isocalendar().year}-W{week_start.isocalendar().week:02d}",
        "period": {"start": week_start.isoformat(), "end": week_end.isoformat()},
        "achievements_top3": achievements,
        "deviations": {
            "delayed": delayed_tasks[:5],
            "delay_rate_percent": delay_rate,
            "notes": [
                "完成度基于 status 和 time_logs 的启发式判断；可按需要定制规则。"
            ]
        },
        "risks_next_14d": [],
        "metrics": {
            "planned_tasks": planned_count,
            "completed_tasks": completed_count,
            "worked_minutes_total": int(round(sum(worked_mins_by_task.values()))),
            "worked_blocks_total": round(minutes_to_blocks(sum(worked_mins_by_task.values())), 1),
        }
    }
    return summary


def load_tasks_from_path(path: Path) -> List["Task"]:
    tasks: List[Task] = []
    for fp in gather_task_files(path):
        try:
            data = read_yaml(fp)
            if not isinstance(data, dict):
                continue
            tasks.append(Task.from_yaml(data, str(fp)))
        except Exception as e:
            print(f"[WARN] failed to parse {fp}: {e}", file=sys.stderr)
    return tasks


def dump_yaml(data: Dict[str, Any], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


# ---------- Defaults & helpers ----------

def default_paths(base: Path, iso_week: str) -> Tuple[Path, Path, Path]:
    """
    Returns (tasks_dir, plan_out, summary_out) for given base dir and week.
    """
    tasks_dir = base / "tasks"
    reviews_dir = base / "reviews"
    plan_out = reviews_dir / f"plan_{iso_week}.yaml"
    summary_out = reviews_dir / f"summary_{iso_week}.yaml"
    return tasks_dir, plan_out, summary_out


def iso_from_plan_filename(p: Path) -> Optional[str]:
    m = re.search(r"plan_(\\d{4}-W\\d{2})\\.ya?ml$", p.name)
    return m.group(1) if m else None


def find_latest_finished_plan_iso(reviews_dir: Path, today: dt.date) -> Optional[str]:
    """
    Find the most recent plan_YYYY-Www.yaml whose week has ended (Sunday <= today).
    If none, return the latest plan_* regardless of completion.
    """
    candidates = []
    for p in reviews_dir.glob("plan_*.y*ml"):
        iso = iso_from_plan_filename(p)
        if not iso:
            continue
        try:
            y, w = iso.split("-W")
            monday = dt.date.fromisocalendar(int(y), int(w), 1)
            sunday = dt.date.fromisocalendar(int(y), int(w), 7)
        except Exception:
            continue
        candidates.append((sunday, iso))
    if not candidates:
        return None
    # Prefer completed weeks first
    completed = [iso for sunday, iso in candidates if sunday <= today]
    if completed:
        completed.sort()
        return completed[-1]
    # else pick the latest planned
    all_iso = [iso for _, iso in candidates]
    all_iso.sort()
    return all_iso[-1]


def main():
    parser = argparse.ArgumentParser(description="AOPT Weekly Planner & Summarizer")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # plan
    p_plan = sub.add_parser("plan", help="Generate weekly plan from scheduled tasks")
    p_plan.add_argument("--tasks", default=None, help="Path to task YAML or directory (default: ./tasks)")
    p_plan.add_argument("--week", default=None, help="ISO week like 2025-W43 (default: next week)")
    p_plan.add_argument("--tz", default="America/Los_Angeles", help="Timezone label (informational)")
    p_plan.add_argument("--out", default=None, help="Output plan YAML path (default: ./reviews/plan_<ISO>.yaml)")

    # summarize
    p_sum = sub.add_parser("summarize", help="Summarize a week based on logs")
    p_sum.add_argument("--tasks", default=None, help="Path to task YAML or directory (default: ./tasks)")
    p_sum.add_argument("--week", default=None, help="ISO week like 2025-W43 (default: infer from latest plan or current week)")
    p_sum.add_argument("--tz", default="America/Los_Angeles", help="Timezone label (informational)")
    p_sum.add_argument("--plan", default=None, help="Optional: the plan YAML generated earlier (default: ./reviews/plan_<ISO>.yaml)")
    p_sum.add_argument("--out", default=None, help="Output summary YAML path (default: ./reviews/summary_<ISO>.yaml)")

    args = parser.parse_args()
    cwd = Path.cwd()
    today = dt.date.today()

    if args.cmd == "plan":
        # Decide week defaults
        week_start, week_end, iso = parse_week_iso(args.week, today)  # next week if None
        # Defaults for paths
        default_tasks_dir, default_plan_out, _ = default_paths(cwd, iso)
        tasks_path = Path(args.tasks) if args.tasks else default_tasks_dir
        out_path = Path(args.out) if args.out else default_plan_out

        tasks = load_tasks_from_path(tasks_path)
        plan = build_plan(tasks, week_start, week_end)
        plan["timezone"] = args.tz
        plan["checklist"] = [
            "WIP≤3；每天保留缓冲块",
            "Outcomes 使用可验收措辞；有证据链接",
            "优先排 O1→O2→O3；高能时段做最难任务",
        ]
        dump_yaml(plan, out_path)
        print(f"[OK] Plan written: {out_path}")

    elif args.cmd == "summarize":
        reviews_dir = cwd / "reviews"

        # Figure out ISO week for summary if not given:
        iso_for_sum = args.week
        if not iso_for_sum:
            iso_detected = find_latest_finished_plan_iso(reviews_dir, today)
            if iso_detected:
                iso_for_sum = iso_detected
            else:
                # fallback to current ISO week
                _, _, iso_for_sum = parse_week_iso_current(None, today)

        # Compute dates for that ISO
        y, w = iso_for_sum.split("-W")
        week_start = dt.date.fromisocalendar(int(y), int(w), 1)
        week_end = dt.date.fromisocalendar(int(y), int(w), 7)

        # Defaults for paths
        default_tasks_dir, default_plan_out, default_summary_out = default_paths(cwd, iso_for_sum)
        tasks_path = Path(args.tasks) if args.tasks else default_tasks_dir
        plan_path = Path(args.plan) if args.plan else default_plan_out
        out_path = Path(args.out) if args.out else default_summary_out

        tasks = load_tasks_from_path(tasks_path)
        plan = None
        if plan_path.exists():
            try:
                plan = read_yaml(plan_path)
            except Exception:
                plan = None
        else:
            print(f"[WARN] Plan file not found: {plan_path} (continuing without plan)")

        summary = build_summary(tasks, week_start, week_end, plan=plan)
        summary["timezone"] = args.tz

        # Also render a concise markdown string for convenience
        md_lines = [
            f"# 周总结（{summary['week']}）",
            f"周期：{summary['period']['start']} — {summary['period']['end']}",
            "## 本周 3 件成果：",
        ]
        for a in summary["achievements_top3"]:
            md_lines.append(f"- {a}")
        md_lines.extend([
            "## 偏差：",
            f"- 延期率：{summary['deviations']['delay_rate_percent']}%",
        ] + [f"- 延期：{x}" for x in summary['deviations']['delayed']])
        md_lines.extend([
            "## 指标：",
            f"- 计划任务数：{summary['metrics']['planned_tasks']}",
            f"- 完成任务数：{summary['metrics']['completed_tasks']}",
            f"- 工作总时长（分钟）：{summary['metrics']['worked_minutes_total']}",
            f"- 工作总区块（50m）：{summary['metrics']['worked_blocks_total']}",
        ])
        summary["markdown"] = "\n".join(md_lines)

        dump_yaml(summary, out_path)
        print(f"[OK] Summary written: {out_path}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
