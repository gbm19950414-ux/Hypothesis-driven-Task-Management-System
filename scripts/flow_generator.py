#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
flow_generator.py
-----------------
根据实验模板（如 Western blot）自动生成一系列任务文件到 tasks/。
兼容 dashboard.py 的任务结构，可直接在 dashboard 中显示。
"""

from pathlib import Path
from ruamel.yaml import YAML
from datetime import datetime, timedelta
from pathlib import Path
import os

# === 固定根路径到 life_os ===
# root = Path(__file__).resolve().parents[1]
# tasks_dir = root / "tasks"
# tasks_dir.mkdir(parents=True, exist_ok=True)

yaml = YAML()

def parse_duration(expr: str, params: dict = None) -> int:
    """解析字符串时长表达式（返回分钟数）"""
    params = params or {}
    expr = str(expr).strip()
    # 替换单位 m/h/d
    expr = (expr.replace("h", "*60")
                 .replace("m", "*1")
                 .replace("d", "*1440"))
    try:
        val = eval(expr, {"__builtins__": None}, params)
        return int(round(float(val)))
    except Exception:
        return 0

def load_template(template_path: Path):
    """载入流程模板 YAML"""
    return yaml.load(template_path.read_text(encoding="utf-8"))

def schedule_steps(template: dict, params: dict, anchor_dt: datetime):
    """根据模板计算每步开始/结束时间"""
    steps = []
    cur = anchor_dt
    for s in template.get("steps", []):
        dur_min = parse_duration(s.get("duration", "0m"), params)
        gap_min = parse_duration(s.get("gap_to_next", "0m"), params)
        step = {
            "key": s.get("key"),
            "title": s.get("title"),
            "duration_minutes": dur_min,
            "start": cur,
            "end": cur + timedelta(minutes=dur_min),
            "gap_to_next": gap_min,
            "difficulty": s.get("difficulty", 3)
        }
        steps.append(step)
        cur = cur + timedelta(minutes=dur_min + gap_min)
    return steps

def generate_tasks(template_path: str, project_id: str, params: dict, anchor_dt: datetime):

    """生成任务文件到 tasks/ 目录"""
    root = Path(__file__).resolve().parents[1]
    tasks_dir = root / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    print(f"[DEBUG] 当前写入目录: {tasks_dir.resolve()}")
    template = load_template(Path(template_path))
    steps = schedule_steps(template, params, anchor_dt)
    today_str = anchor_dt.strftime("%Y%m%d")
    base_id = template.get("id", Path(template_path).stem)
    instance_id = f"{base_id}_{today_str}_{int(anchor_dt.timestamp())}"
    for i, s in enumerate(steps, 1):
        tid = f"t-{today_str}-{i:03d}"
        data = {
            "id": tid,
            "title": f"[{base_id}] {s['title']}",
            "project": project_id,
            "area": "research",
            "objective": "okr-2025Q4-explore",
            "status": "todo",
            "difficulty": s["difficulty"],
            "duration_minutes": s["duration_minutes"],
            "start_time": s["start"].strftime("%H:%M"),
            "due": s["start"].date().isoformat(),
            "tags": ["auto_flow", base_id],
            "flow": {
                "instance_id": instance_id,
                "flow_id": base_id,
                "order": i
            },
        }
        with (tasks_dir / f"{tid}.yaml").open("w", encoding="utf-8") as f:
            yaml.dump(data, f)
    print(f"✅ 已生成 {len(steps)} 个任务，流程 ID: {instance_id}")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="从流程模板生成任务")
    ap.add_argument("--template", required=True, help="模板路径，如 flows/western_blot.yaml")
    ap.add_argument("--project", required=True, help="归属的项目 ID")
    ap.add_argument("--anchor", default=datetime.now().strftime("%Y-%m-%d_%H:%M"), help="锚点起始时间")
    ap.add_argument("--param", action="append", help="参数，如 n_samples=6，可多次提供")
    args = ap.parse_args()

    params = {}
    if args.param:
        for kv in args.param:
            if "=" in kv:
                k, v = kv.split("=", 1)
                try:
                    v = int(v)
                except:
                    pass
                params[k] = v

    anchor_dt = datetime.strptime(args.anchor, "%Y-%m-%d_%H:%M")
    generate_tasks(args.template, args.project, params, anchor_dt)
