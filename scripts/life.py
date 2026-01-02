#!/usr/bin/env python3
import argparse, sys, os, re, uuid, glob, pathlib, datetime
from typing import Optional, List, Literal, Dict, Any
from ruamel.yaml import YAML
from pydantic import BaseModel, Field, field_validator
from datetime import date, datetime as dt, timezone

# ---------- Paths & YAML ----------
ROOT = pathlib.Path(os.environ.get("LIFE_ROOT", os.getcwd()))
CFG  = ROOT / "00_config" / "config.yml"
INBOX= ROOT / "tasks" / "inbox.yml"
TASKS= ROOT / "tasks"
REV  = ROOT / "04_reviews"

yaml = YAML(typ="rt")
yaml.preserve_quotes = True
yaml.default_flow_style = False

def load_yaml(p: pathlib.Path):
    with p.open("r", encoding="utf-8") as f:
        return yaml.load(f)

def dump_yaml(p: pathlib.Path, data: Any):
    with p.open("w", encoding="utf-8") as f:
        yaml.dump(data, f)

def ensure_dirs():
    for d in [ROOT/"00_config", ROOT/"01_areas", ROOT/"02_objectives", ROOT/"03_projects", ROOT/"04_reviews", ROOT/"tasks", ROOT/"scripts"]:
        d.mkdir(parents=True, exist_ok=True)

# ---------- Models ----------
class Config(BaseModel):
    areas: List[str]
    priorities: List[str]
    statuses: List[str]
    default_priority: str = "P2"
    timezone: Optional[str] = None

    @field_validator("default_priority")
    @classmethod
    def _check_default_priority(cls, v, info):
        pri = info.data.get("priorities", [])
        if v not in pri:
            raise ValueError(f"default_priority '{v}' not in priorities {pri}")
        return v

class Task(BaseModel):
    id: str
    title: str
    area: str
    status: Literal["backlog","todo","doing","waiting","blocked","done"]
    priority: str = "P2"
    objective: Optional[str] = None
    project: Optional[str] = None
    due: Optional[date] = None
    estimate_h: Optional[float] = Field(default=None, ge=0)
    tags: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    created_at: str
    updated_at: str

    @field_validator("created_at","updated_at")
    @classmethod
    def _iso_dt(cls, v):
        # 简单校验 ISO 日期时间
        dt.fromisoformat(v.replace("Z","+00:00"))
        return v

def now_iso():
    return dt.now(timezone.utc).isoformat(timespec="seconds")

def next_task_id() -> str:
    today = dt.now().strftime("%Y%m%d")
    # 同时匹配 .yaml 文件
    pattern_yaml = str(TASKS / f"t-{today}-*.yaml")
    existing = glob.glob(pattern_yaml)
    n = len(existing) + 1
    return f"t-{today}-{n:03d}"

def require_cfg() -> Config:
    if not CFG.exists():
        print(f"[ERR] config not found: {CFG}")
        sys.exit(1)
    return Config.model_validate(load_yaml(CFG))

def write_task(task: Task):
    path = TASKS / f"{task.id}.yaml"
    dump_yaml(path, task.model_dump())
    return path

def read_task(path: pathlib.Path) -> Task:
    data = load_yaml(path)
    return Task.model_validate(data)

def list_tasks() -> List[pathlib.Path]:
    return sorted([pathlib.Path(p) for p in glob.glob(str(TASKS / "t-*.yaml"))])

# ---------- Commands ----------
def cmd_capture(args):
    """Capture an idea into inbox.yml"""
    ensure_dirs()
    cfg = require_cfg()
    inbox = load_yaml(INBOX) if INBOX.exists() else {"ideas": []}
    idea = {
        "id": f"i-{dt.now().strftime('%Y%m%d-%H%M%S')}",
        "title": args.title,
        "area": args.area or None,
        "tags": args.tags or [],
        "notes": args.notes or "",
        "created_at": now_iso()
    }
    inbox.setdefault("ideas", []).append(idea)
    dump_yaml(INBOX, inbox)
    print(f"✓ Captured idea: {idea['id']} → {INBOX}")

def cmd_promote(args):
    """Promote an idea from inbox to a real task file"""
    ensure_dirs()
    cfg = require_cfg()
    inbox = load_yaml(INBOX)
    ideas = inbox.get("ideas", [])
    if not ideas:
        print("No ideas in inbox.")
        return
    # pick last by default
    idea = None
    if args.idea_id:
        for x in ideas:
            if x["id"] == args.idea_id:
                idea = x; break
    else:
        idea = ideas[-1]
    if not idea:
        print("Idea not found.")
        return
    area = args.area or idea.get("area") or cfg.areas[0]
    if area not in cfg.areas:
        print(f"[ERR] area must be one of {cfg.areas}")
        return
    tid = next_task_id()
    task = Task(
        id=tid,
        title=args.title or idea["title"],
        area=area,
        status="todo",
        priority=args.priority or cfg.default_priority,
        objective=args.objective,
        project=args.project,
        due=date.fromisoformat(args.due) if args.due else None,
        estimate_h=float(args.estimate) if args.estimate else None,
        tags=idea.get("tags", []),
        notes=idea.get("notes",""),
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    path = write_task(task)
    # remove idea
    inbox["ideas"] = [x for x in ideas if x["id"] != idea["id"]]
    dump_yaml(INBOX, inbox)
    print(f"✓ Promoted {idea['id']} → {path}")

def cmd_add(args):
    """Add a task directly (skip inbox)"""
    ensure_dirs()
    cfg = require_cfg()
    # Remove area check
    area_val = args.area if args.area else ""
    priority_val = args.priority or cfg.default_priority
    tid = next_task_id()
    task = Task(
        id=tid, title=args.title, area=area_val,
        status="todo", priority=priority_val,
        objective=args.objective, project=args.project,
        due=date.fromisoformat(args.due) if args.due else None,
        estimate_h=float(args.estimate) if args.estimate else None,
        tags=args.tags or [], notes=args.notes or "",
        created_at=now_iso(), updated_at=now_iso()
    )
    path = write_task(task)
    print(f"✓ Added task (area may be blank): {path}")

def cmd_set(args):
    """Update task fields"""
    ensure_dirs()
    cfg = require_cfg()
    path = TASKS / f"{args.id}.yaml"
    if not path.exists():
        print(f"[ERR] task not found: {path}"); return
    task = read_task(path)
    changed = False
    if args.title: task.title = args.title; changed=True
    if args.area:
        if args.area not in cfg.areas: print(f"[ERR] area∉{cfg.areas}"); return
        task.area = args.area; changed=True
    if args.status:
        if args.status not in cfg.statuses: print(f"[ERR] status∉{cfg.statuses}"); return
        task.status = args.status; changed=True
    if args.priority:
        if args.priority not in cfg.priorities: print(f"[ERR] priority∉{cfg.priorities}"); return
        task.priority = args.priority; changed=True
    if args.project is not None: task.project = args.project; changed=True
    if args.objective is not None: task.objective = args.objective; changed=True
    if args.due is not None: task.due = date.fromisoformat(args.due) if args.due else None; changed=True
    if args.estimate is not None: task.estimate_h = float(args.estimate) if args.estimate else None; changed=True
    if args.add_tag:
        task.tags = list(sorted(set(task.tags + args.add_tag))); changed=True
    if args.notes is not None: task.notes = args.notes; changed=True
    if not changed:
        print("No change."); return
    task.updated_at = now_iso()
    write_task(task)
    print(f"✓ Updated {path.name}")

def cmd_ls(args):
    """List tasks with filters"""
    ensure_dirs()
    _all = []
    for p in list_tasks():
        t = read_task(p)
        _all.append(t)
    def ok(t: Task):
        if args.area and t.area != args.area: return False
        if args.status and t.status != args.status: return False
        if args.project and t.project != args.project: return False
        if args.objective and t.objective != args.objective: return False
        return True
    rows = [t for t in _all if ok(t)]
    rows = sorted(rows, key=lambda x: (x.area, x.priority, x.due or date.max, x.id))
    for t in rows:
        due = t.due.isoformat() if t.due else "-"
        print(f"{t.id:>14} | {t.area:8} | {t.status:8} | {t.priority:2} | {due} | {t.title}")

def cmd_report_week(args):
    """Generate weekly markdown report"""
    ensure_dirs()
    week = dt.now().isocalendar().week
    year = dt.now().year
    out = REV / f"{year}-W{week:02d}.md"
    tasks = [read_task(p) for p in list_tasks()]
    start = dt.fromisocalendar(year, week, 1).date()
    end   = dt.fromisocalendar(year, week, 7).date()
    def in_week(t: Task):
        # 显示本周到期、或本周更新的任务
        updated = dt.fromisoformat(t.updated_at.replace("Z","+00:00")).date()
        return (t.due and start <= t.due <= end) or (start <= updated <= end)
    ts = [t for t in tasks if in_week(t)]
    # 简单统计
    done = [t for t in ts if t.status=="done"]
    todo = [t for t in ts if t.status in ("todo","doing","waiting","blocked","backlog")]
    # 写 Markdown
    lines = []
    lines += [f"# Week {year}-W{week:02d} 周报（{start} ~ {end}）", ""]
    lines += [f"**本周完成**：{len(done)}｜**活跃任务**：{len(todo)}", ""]
    def section(title, items):
        lines.append(f"## {title}")
        if not items:
            lines.append("- （无）"); lines.append(""); return
        for t in sorted(items, key=lambda x:(x.area, x.project or "", x.priority, x.due or date.max)):
            due = t.due.isoformat() if t.due else "-"
            proj = t.project or "-"
            lines.append(f"- [{t.area}] ({proj}) [{t.priority}] {t.title} 〔{t.status} | due {due} | {t.id}〕")
        lines.append("")
    section("已完成（done）", done)
    section("待办/进行中（todo/doing/...）", todo)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ Wrote {out}")

def cmd_validate(args):
    """Validate all tasks by schema, allowed values, and cross-file references"""
    ensure_dirs()
    cfg = require_cfg()

    # Load all valid area IDs from 01_areas/*.yml; if none, fallback to cfg.areas
    area_paths = list((ROOT / "01_areas").glob("*.yml"))
    if area_paths:
        valid_areas = set()
        for p in area_paths:
            try:
                data = load_yaml(p)
                if isinstance(data, dict) and "id" in data:
                    valid_areas.add(data["id"])
            except Exception:
                pass
    else:
        valid_areas = set(cfg.areas)

    # Load all valid project IDs from 03_projects/*.yml
    project_paths = list((ROOT / "03_projects").glob("*.yml"))
    valid_projects = set()
    for p in project_paths:
        try:
            data = load_yaml(p)
            if isinstance(data, dict) and "id" in data:
                valid_projects.add(data["id"])
        except Exception:
            pass

    # Load all valid objective IDs from 02_objectives/*.yml (free file names, require id field)
    objective_paths = list((ROOT / "02_objectives").glob("*.yml"))
    valid_objectives = set()
    for p in objective_paths:
        try:
            data = load_yaml(p)
            if isinstance(data, dict) and "id" in data:
                valid_objectives.add(data["id"])
        except Exception:
            pass

    ok=True
    for p in list_tasks():
        try:
            t = read_task(p)
            if t.area not in valid_areas:
                raise ValueError(f"area '{t.area}' ∉ {sorted(valid_areas)}")
            if t.priority not in cfg.priorities:
                raise ValueError(f"priority '{t.priority}' ∉ {cfg.priorities}")
            if t.status not in cfg.statuses:
                raise ValueError(f"status '{t.status}' ∉ {cfg.statuses}")
            if t.project is not None and t.project not in valid_projects:
                raise ValueError(f"project '{t.project}' ∉ {sorted(valid_projects)}")
            if t.objective is not None and t.objective not in valid_objectives:
                raise ValueError(f"objective '{t.objective}' ∉ {sorted(valid_objectives)}")
            print(f"OK {p.name}")
        except Exception as e:
            ok=False
            print(f"FAIL {p.name} -> {e}")
    sys.exit(0 if ok else 2)

def main():
    ensure_dirs()
    ap = argparse.ArgumentParser(prog="life")
    sp = ap.add_subparsers(dest="cmd")

    a = sp.add_parser("capture", help="快速捕捉想法到 inbox")
    a.add_argument("title")
    a.add_argument("--area")
    a.add_argument("--tags", nargs="+")
    a.add_argument("--notes")
    a.set_defaults(func=cmd_capture)

    b = sp.add_parser("promote", help="把 inbox 的想法转为任务")
    b.add_argument("--idea-id")
    b.add_argument("--title")
    b.add_argument("--area")
    b.add_argument("--priority")
    b.add_argument("--project")
    b.add_argument("--objective")
    b.add_argument("--due")
    b.add_argument("--estimate")
    b.set_defaults(func=cmd_promote)

    c = sp.add_parser("add", help="直接新增任务")
    c.add_argument("title")
    c.add_argument("--area", required=False, help="可选；不指定时 area 留空，稍后可在 Streamlit 中配置")
    c.add_argument("--priority", default=None, help="可选；未指定则使用 config.yml 中的 default_priority")
    c.add_argument("--project")
    c.add_argument("--objective")
    c.add_argument("--due")
    c.add_argument("--estimate")
    c.add_argument("--tags", nargs="+")
    c.add_argument("--notes")
    c.set_defaults(func=cmd_add)

    d = sp.add_parser("set", help="修改任务字段")
    d.add_argument("--id", required=True)
    d.add_argument("--title")
    d.add_argument("--area")
    d.add_argument("--status")
    d.add_argument("--priority")
    d.add_argument("--project")
    d.add_argument("--objective")
    d.add_argument("--due")
    d.add_argument("--estimate")
    d.add_argument("--add-tag", nargs="+")
    d.add_argument("--notes")
    d.set_defaults(func=cmd_set)

    e = sp.add_parser("ls", help="列出任务")
    e.add_argument("--area")
    e.add_argument("--status")
    e.add_argument("--project")
    e.add_argument("--objective")
    e.set_defaults(func=cmd_ls)

    f = sp.add_parser("report-week", help="生成本周 Markdown 周报")
    f.set_defaults(func=cmd_report_week)

    g = sp.add_parser("validate", help="校验所有任务")
    g.set_defaults(func=cmd_validate)

    args = ap.parse_args()
    if not hasattr(args, "func"):
        ap.print_help(); sys.exit(0)
    args.func(args)

if __name__ == "__main__":
    main()
