#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import streamlit as st
import yaml, os
from datetime import date, datetime, timedelta
import subprocess
from pathlib import Path
from collections import Counter, defaultdict
import hashlib
CONFIG_PATH = Path("/Users/gongbaoming/Library/CloudStorage/OneDrive-个人/life_os/00_config/config_weekly.yaml")
TASKS_PATH = Path("/Users/gongbaoming/Library/CloudStorage/OneDrive-个人/life_os/tasks/tasks.yaml")

OBJECTIVES_DIR = Path("/Users/gongbaoming/Library/CloudStorage/OneDrive-个人/life_os/objectives")
AREAS_DIR = Path("/Users/gongbaoming/Library/CloudStorage/OneDrive-个人/life_os/areas")
PROJECTS_DIR = Path("/Users/gongbaoming/Library/CloudStorage/OneDrive-个人/life_os/projects")
TASKS_DIR = Path("/Users/gongbaoming/Library/CloudStorage/OneDrive-个人/life_os/tasks")

def load_yaml(path):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}

def save_yaml(data, path):
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)

def load_all_yaml_from_dir(dir_path):
    data = []
    dir_path = Path(dir_path)
    if dir_path.exists() and dir_path.is_dir():
        for file in dir_path.iterdir():
            if file.suffix in (".yaml", ".yml"):
                try:
                    with open(file, "r", encoding="utf-8") as f:
                        content = yaml.safe_load(f)
                        if isinstance(content, list):
                            for c in content:
                                if isinstance(c, dict):
                                    c["__path"] = str(file)
                                    # Coerce weight to float if present
                                    if "weight" in c:
                                        try:
                                            c["weight"] = float(c["weight"])
                                        except (TypeError, ValueError):
                                            pass
                                    data.append(c)
                        elif isinstance(content, dict):
                            content["__path"] = str(file)
                            # Coerce weight to float if present
                            if "weight" in content:
                                try:
                                    content["weight"] = float(content["weight"])
                                except (TypeError, ValueError):
                                    pass
                            data.append(content)
                except Exception as e:
                    st.warning(f"无法读取文件 {file.name}：{e}")
    return data

def load_all_tasks(task_dir):
    tasks = []
    task_dir_path = Path(task_dir)
    if task_dir_path.exists() and task_dir_path.is_dir():
        for file in task_dir_path.glob("*.yaml"):
            with open(file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if isinstance(data, list):
                    tasks.extend(data)
                elif isinstance(data, dict):
                    tasks.append(data)
    return tasks

# --- Helpers for Calendar Sync (merge with canonical task files) ---
from typing import Tuple, Dict, Any

def index_tasks_by_uid(task_dir: Path) -> Tuple[Dict[str, dict], Dict[str, list]]:
    """Index tasks by uid and by title from YAML files under task_dir.
    Returns (uid_index, title_index).
    """
    uid_index: Dict[str, dict] = {}
    title_index: Dict[str, list] = defaultdict(list)
    tdir = Path(task_dir)
    if not (tdir.exists() and tdir.is_dir()):
        return uid_index, title_index
    for file in tdir.iterdir():
        if file.suffix not in (".yaml", ".yml"):
            continue
        try:
            with open(file, "r", encoding="utf-8") as f:
                content = yaml.safe_load(f)
        except Exception:
            continue
        def _add_one(item: Dict[str, Any]):
            if not isinstance(item, dict):
                return
            item = dict(item)
            item.setdefault("__path", str(file))
            if "uid" in item and item["uid"]:
                uid_index[str(item["uid"])] = item
            if "title" in item and item["title"]:
                title_index[str(item["title"])].append(item)
        if isinstance(content, list):
            for c in content: _add_one(c)
        elif isinstance(content, dict):
            _add_one(content)
    return uid_index, title_index

def merge_task_fields(weekly_task: dict, uid_index: Dict[str, dict], title_index: Dict[str, list]) -> dict:
    """Merge a weekly-plan task with canonical task fields from /tasks.
    Preference order: weekly_task field -> task_file field -> fallback.
    """
    uid = str(weekly_task.get("uid") or "").strip()
    canon = uid_index.get(uid)
    if not canon and weekly_task.get("title"):
        matches = title_index.get(str(weekly_task["title"])) or []
        canon = matches[0] if matches else None

    def pick(*keys, default=""):
        for k in keys:
            v = weekly_task.get(k)
            if v not in (None, ""):
                return v
        if canon:
            for k in keys:
                v = canon.get(k)
                if v not in (None, ""):
                    return v
        return default

    merged = {
        "uid": uid or (canon.get("uid") if canon else ""),
        "title": pick("title"),
        "deliverable": pick("deliverable", "dod", default=""),
        "estimate_hours": float(pick("estimate_hours", default= (float(canon.get("estimate_hours", 0)) if canon else 0) or (float(canon.get("estimate_blocks", 0))*2 if canon and canon.get("estimate_blocks") else 1.0))),
        "due": pick("due", default= canon.get("due", "") if canon else ""),
        "area": pick("area"),
        "project": pick("project"),
        "status": pick("status", default= canon.get("status", "todo") if canon else "todo"),
    }
    return merged

def compute_time_window(due_str: str, est_hours: float) -> Tuple[datetime, datetime]:
    """Return (start_time, end_time) from due string and estimated hours.
    If due is date-only (YYYY-MM-DD), default end time to 22:00 local.
    """
    if not due_str:
        # fallback: now as end
        end_time = datetime.now().replace(second=0, microsecond=0)
    else:
        try:
            if len(due_str) == 10 and due_str.count("-") == 2:  # YYYY-MM-DD
                end_time = datetime.fromisoformat(due_str + "T22:00:00")
            else:
                end_time = datetime.fromisoformat(due_str)
        except Exception:
            end_time = datetime.now().replace(second=0, microsecond=0)
    start_time = end_time - timedelta(hours=float(est_hours or 1.0))
    return start_time, end_time

# --- Helper: write area weight back to YAML file immediately ---
def write_area_weight(area_title, new_val, areas):
    """Write updated weight back to the area YAML file immediately."""
    area_entry = next((a for a in areas if a.get("title") == area_title), None)
    if area_entry and "__path" in area_entry:
        area_path = Path(area_entry["__path"])
        try:
            with open(area_path, "r", encoding="utf-8") as f:
                content = yaml.safe_load(f) or {}
            if isinstance(content, dict):
                content["weight"] = round(new_val, 4)
            elif isinstance(content, list):
                for c in content:
                    if c.get("title") == area_title:
                        c["weight"] = round(new_val, 4)
            with open(area_path, "w", encoding="utf-8") as f:
                yaml.dump(content, f, allow_unicode=True, sort_keys=False)
            st.toast(f"✅ {area_title} 权重已更新为 {new_val:.2f}")
        except Exception as e:
            st.warning(f"⚠️ 无法写回 {area_title} 的权重：{e}")

def get_week_id():
    today = date.today()
    y, w, _ = today.isocalendar()
    return f"{y}-W{w:02d}"

def build_hierarchy(objectives, areas, projects, tasks):
    """Build UID-based hierarchy mappings for area → objective → project → task."""
    from collections import defaultdict

    # Build UID lookup tables
    area_by_uid = {a.get("uid"): a for a in areas if a.get("uid")}
    obj_by_uid = {o.get("uid"): o for o in objectives if o.get("uid")}
    proj_by_uid = {p.get("uid"): p for p in projects if p.get("uid")}

    # area_uid → objectives
    area_to_objs = defaultdict(list)
    for o in objectives:
        aid = o.get("area_uid")
        if aid:
            area_to_objs[aid].append(o)

    # objective_uid → projects
    obj_to_projs = defaultdict(list)
    for p in projects:
        oid = p.get("objective_uid")
        if oid:
            obj_to_projs[oid].append(p)

    # project_uid → tasks
    proj_to_tasks = defaultdict(list)
    for t in tasks:
        pid = t.get("project_uid")
        if pid:
            proj_to_tasks[pid].append(t)

    return area_by_uid, obj_by_uid, proj_by_uid, area_to_objs, obj_to_projs, proj_to_tasks

def main():
    cfg = load_yaml(CONFIG_PATH)
    weekly_dir = Path(os.path.expanduser(cfg.get("paths", {}).get("weekly_dir", "~/life_os/weekly").replace("tasks/","")))
    weekly_dir.mkdir(parents=True, exist_ok=True)
    week_id = get_week_id()

    st.set_page_config(page_title="Weekly Planner", layout="wide")
    st.title(f"📅 周计划系统 ({week_id})")

    mode = st.sidebar.radio("模式选择", ["计划生成", "周总结"])

    if mode == "计划生成":
        st.sidebar.header("参数配置")
        hours = st.sidebar.slider("可支配时长 (小时)", 10, 50, cfg["default_capacity"]["hours_available"])
        blocks = st.sidebar.slider("深度工作块目标", 3, 10, cfg["default_capacity"]["deep_blocks_target"])
        buffer = st.sidebar.slider("缓冲比例", 0.0, 0.5, cfg["default_capacity"]["buffer_ratio"])
        not_do = st.sidebar.text_area("不做清单", "\n".join(cfg["defaults"]["not_do"]))

        # Load hierarchy
        objectives = load_all_yaml_from_dir(OBJECTIVES_DIR)
        areas = load_all_yaml_from_dir(AREAS_DIR)
        projects = load_all_yaml_from_dir(PROJECTS_DIR)
        # Load all tasks once
        all_tasks = load_all_tasks(TASKS_DIR)
        area_by_uid, obj_by_uid, proj_by_uid, area_to_objs, obj_to_projs, proj_to_tasks = build_hierarchy(objectives, areas, projects, all_tasks)

        st.subheader("领域权重分配")
        area_weights = {}

        # Prefer weights defined in area YAML files; others default to 0
        area_titles_sorted = sorted([a.get('title') for a in areas if a.get('title')])

        for a in areas:
            title = a.get('title')
            if not title:
                continue
            w = a.get('weight', 0.0)
            try:
                w = float(w)
            except (TypeError, ValueError):
                w = 0.0
            area_weights[title] = w

        # Any missing areas default to 0
        for area_title in area_titles_sorted:
            if area_title not in area_weights:
                area_weights[area_title] = 0.0

        st.markdown("调整各领域的权重（初始值为0，存在weight字段则使用其值）")
        sliders = {}
        for area_title in area_titles_sorted:
            init_val = float(area_weights.get(area_title, 0.0))
            init_val = min(max(init_val, 0.0), 1.0)
            new_val = st.slider(f"{area_title}", 0.0, 1.0, init_val, 0.01, key=f"weight_{area_title}")
            if abs(new_val - init_val) > 1e-6:
                write_area_weight(area_title, new_val, areas)
            sliders[area_title] = new_val

        st.markdown("### 领域总时长分配")
        for area_title in area_titles_sorted:
            area_hours = hours * sliders[area_title] * (1 - buffer)
            st.write(f"{area_title}: {area_hours:.1f} 小时")

        st.subheader("任务选择（按领域限制时长）")

        # --- Hierarchical Task Selection UI ---
        st.markdown("### 🧱 层级任务选择池")
        selected_tasks = []
        selected_tasks_by_area = defaultdict(list)
        total_estimated_hours = 0.0
        area_selected_hours = defaultdict(float)

        # Build hierarchical selection (Area -> Objective -> Project -> Task) using UID-based mappings
        for area in areas:
            area_title = area.get("title")
            if not area_title:
                continue
            with st.expander(f"🌐 {area_title}", expanded=False):
                area_hours = hours * sliders[area_title] * (1 - buffer)
                st.markdown(f"**可用时长：{area_hours:.1f} 小时**")

                objs = area_to_objs.get(area.get("uid"), [])
                if not objs:
                    st.caption("无关联 Objective")
                    continue

                area_task_selected = []
                area_task_selected_hours = 0.0

                # Sort projects by due date if available
                def parse_due(d):
                    try:
                        return datetime.fromisoformat(str(d))
                    except Exception:
                        return datetime.max

                for obj in objs:
                    st.markdown(f"#### 🎯 {obj.get('title','未命名目标')}")
                    projs = obj_to_projs.get(obj.get("uid"), [])
                    if not projs:
                        st.caption("无项目")
                        continue

                    projs = sorted(projs, key=lambda p: parse_due(p.get("due")))

                    for proj in projs:
                        st.markdown(f"##### 📂 {proj.get('title','未命名项目')}")
                        tasks = proj_to_tasks.get(proj.get("uid"), [])
                        if not tasks:
                            st.caption("无任务")
                            continue

                        # Sort tasks by due date if available
                        tasks = sorted(tasks, key=lambda t: parse_due(t.get("due")))

                        for t in tasks:
                            est = t.get("estimate_hours", 1)
                            key = str(t.get("uid") or f"{proj.get('title')}_{t.get('title')}_{hash(t.get('title'))}")
                            status = t.get("status", "todo")
                            due = t.get("due", "")
                            label = f"{t.get('title')} ({est}h) [{status}{' | ' + due if due else ''}]"
                            checked = st.checkbox(label, key=key)

                            if checked:
                                if area_task_selected_hours + est <= area_hours:
                                    area_task_selected.append(t)
                                    area_task_selected_hours += est
                                else:
                                    st.warning(f"领域时长上限已达，无法选择：{t.get('title')}")
                                    st.session_state[key] = False

                selected_tasks.extend(area_task_selected)
                selected_tasks_by_area[area_title].extend(area_task_selected)
                area_selected_hours[area_title] = area_task_selected_hours
                total_estimated_hours += area_task_selected_hours

                st.markdown(f"🕒 已选任务时长：{area_task_selected_hours:.1f}h / 可用 {area_hours:.1f}h")

        st.markdown(f"**所有领域已选任务总时长：{total_estimated_hours:.1f}h / 可支配 {(hours*(1-buffer)):.1f}h**")

        if selected_tasks:
            # Compose theme from selected tasks' projects' areas
            selected_projects = [t.get('project') for t in selected_tasks if t.get('project')]
            selected_areas = []
            # area_dict may not be defined above, so let's build it here
            area_dict = {a.get('uid'): a for a in areas if a.get('uid')}
            for proj_title in selected_projects:
                proj = next((p for p in projects if p.get('title') == proj_title), None)
                if proj:
                    area_id = proj.get('area')
                    if area_id and area_id in area_dict:
                        area_title = area_dict[area_id].get('title')
                        if area_title:
                            selected_areas.append(area_title)
            if selected_areas:
                theme = " & ".join(sorted(set(selected_areas)))
            else:
                theme = "综合推进"
            st.text_input("本周主题", value=theme, key="theme_input")

            st.subheader("任务定义与产出确认")
            defined_tasks = []
            for area_title in area_titles_sorted:
                tasks_in_area = selected_tasks_by_area.get(area_title, [])
                if not tasks_in_area:
                    continue
                st.markdown(f"##### 领域：{area_title}")
                for t in tasks_in_area:
                    col1, col2, col3 = st.columns([2,1,1])
                    with col1:
                        dod = st.text_input(f"交付内容 - {t['title']}", t.get('deliverable', '明确交付成果'), key=f"dod_{t['title']}")
                    with col2:
                        est_hours = st.number_input(f"预计工时 - {t['title']}", 0.5, 10.0, float(t.get('estimate_hours', 2)), 0.5, key=f"hours_{t['title']}")
                    with col3:
                        due = st.date_input(f"截止日期 - {t['title']}", date.today(), key=f"due_{t['title']}")
                    defined_tasks.append({
                        "uid": t.get("uid"),
                        "title": t["title"],
                        "deliverable": dod,
                        "estimate_hours": est_hours,
                        "due": due.isoformat(),
                        "area": area_title,
                        "project": t.get("project", ""),
                        "status": t.get("status", "todo")
                    })

            if st.button("✅ 生成周计划"):
                # Update area_weights in cfg with the latest sliders values (no normalization)
                final_area_weights = dict(sliders)
                if "default_capacity" not in cfg:
                    cfg["default_capacity"] = {}
                cfg["default_capacity"]["area_weights"] = final_area_weights
                save_yaml(cfg, CONFIG_PATH)

                # Debug output: show which area weights are being written back
                st.write("🧭 写回权重：", final_area_weights)

                # --- 写回 areas 文件的权重 ---
                for area_title, weight in final_area_weights.items():
                    area_entry = next((a for a in areas if a.get("title") == area_title), None)
                    if area_entry and "__path" in area_entry:
                        # Debug output: show which file is being written
                        st.write(f"写回 {area_title} → {area_entry.get('__path')} (weight={round(weight,4)})")
                        area_path = Path(area_entry["__path"])
                        try:
                            with open(area_path, "r", encoding="utf-8") as f:
                                content = yaml.safe_load(f) or {}
                            if isinstance(content, dict):
                                content["weight"] = round(weight, 4)
                            elif isinstance(content, list):
                                for c in content:
                                    if c.get("title") == area_title:
                                        c["weight"] = round(weight, 4)
                            with open(area_path, "w", encoding="utf-8") as f:
                                yaml.dump(content, f, allow_unicode=True, sort_keys=False)
                        except Exception as e:
                            st.warning(f"⚠️ 无法写回 {area_title} 的权重：{e}")

                data = {
                    "week": week_id,
                    "created_at": datetime.now().isoformat(timespec="minutes"),
                    "theme": st.session_state["theme_input"],
                    "capacity": dict(hours_available=hours, deep_blocks_target=blocks, buffer_ratio=buffer),
                    "tasks": defined_tasks,
                    "not_do": not_do.split("\n"),
                    "metrics": cfg["defaults"]["metrics"],
                    "evidence": {"method": cfg["defaults"]["evidence_method"]},
                }
                outpath = weekly_dir / f"{week_id}.yaml"
                save_yaml(data, outpath)
                st.success(f"✅ 已保存到 {outpath}")
                st.info("🔄 权重已写回各领域文件，正在刷新界面以加载最新配置…")
                # --- Attempt to refresh the interface safely ---
                st.success("✅ 周计划已生成并保存成功！")

                # --- macOS Calendar Sync Feature ---
                st.markdown("---")
                st.subheader("📆 macOS 日历同步")
                sync_to_calendar = st.checkbox("同步任务到本地日历 (macOS Calendar)", value=False)

                if sync_to_calendar:
                    st.info("将把所有任务写入系统日历（默认日历）。")
                    if st.button("🗓️ 执行同步"):
                        uid_index, title_index = index_tasks_by_uid(TASKS_DIR)
                        for t in defined_tasks:
                            merged = merge_task_fields(t, uid_index, title_index)
                            title = (merged.get("title") or "(未命名任务)")
                            deliverable = merged.get("deliverable", "")
                            est_hours = float(merged.get("estimate_hours", 1))
                            due_str = merged.get("due", "")
                            start_time, end_time = compute_time_window(due_str, est_hours)

                            # Prepare AppleScript-safe strings
                            title_safe = str(title).replace('"', '\\"')
                            notes = f"可交付内容: {deliverable}\\n预计工时: {est_hours} 小时"
                            notes_safe = notes.replace('"', '\\"')

                            try:
                                import locale
                                try:
                                    locale.setlocale(locale.LC_TIME, "en_US.UTF-8")
                                except locale.Error:
                                    pass
                                start_str = start_time.strftime("%a %b %d %H:%M:%S %Y")
                                end_str = end_time.strftime("%a %b %d %H:%M:%S %Y")
                                apple_script = (
                                    'tell application "Calendar" '
                                    'to tell calendar "日历" '
                                    f'to make new event with properties {{summary:"{title_safe}", description:"{notes_safe}", '
                                    f'start date:(date "{start_str}"), end date:(date "{end_str}")}}'
                                )
                                env = os.environ.copy()
                                env["LANG"] = "en_US.UTF-8"
                                env["LC_ALL"] = "en_US.UTF-8"
                                env["LANGUAGE"] = "en_US"
                                subprocess.run(["osascript", "-e", apple_script], check=True, env=env)
                                st.success(f"已同步任务到日历：{title}")
                            except Exception as e:
                                st.warning(f"⚠️ 无法同步任务 {title}：{e}")

                # Add a short pause to ensure UI feedback before rerun
                import time
                time.sleep(0.8)

                # Use Streamlit rerun depending on version
                try:
                    if hasattr(st, "rerun"):
                        st.rerun()
                    elif hasattr(st, "experimental_rerun"):
                        st.experimental_rerun()
                    else:
                        st.info("请手动刷新页面以加载最新配置。")
                except Exception as e:
                    st.warning(f"⚠️ 无法自动刷新，请手动刷新页面。错误详情：{e}")

        # --- Additional Feature: Sync existing weekly plan to Calendar ---
        existing_plan = weekly_dir / f"{week_id}.yaml"
        if existing_plan.exists():
            st.markdown("---")
            st.subheader("📅 已生成的周计划")
            st.info(f"检测到本周计划文件：{existing_plan.name}")
            if st.button("🗓️ 从周计划文件同步到日历"):
                plan_data = load_yaml(existing_plan)
                defined_tasks = plan_data.get("tasks", [])
                # Backward-compat: support old milestone-based files
                if not defined_tasks and "milestones" in plan_data:
                    defined_tasks = []
                    for m in plan_data["milestones"]:
                        defined_tasks.append({
                            "uid": m.get("uid"),
                            "title": m.get("title"),
                            "deliverable": m.get("dod", ""),
                            "estimate_hours": float(m.get("estimate_blocks", 1)) * 2,
                            "due": m.get("due", ""),
                            "area": m.get("area", ""),
                            "project": m.get("project", ""),
                            "status": m.get("status", "todo"),
                        })

                if not defined_tasks:
                    st.warning("周计划文件中未找到任务。")
                else:
                    uid_index, title_index = index_tasks_by_uid(TASKS_DIR)
                    for t in defined_tasks:
                        merged = merge_task_fields(t, uid_index, title_index)
                        title = (merged.get("title") or "(未命名任务)")
                        deliverable = merged.get("deliverable", "")
                        est_hours = float(merged.get("estimate_hours", 1))
                        due_str = merged.get("due", "")
                        start_time, end_time = compute_time_window(due_str, est_hours)

                        # Prepare AppleScript-safe strings
                        title_safe = str(title).replace('"', '\\"')
                        notes = f"可交付内容: {deliverable}\\n预计工时: {est_hours} 小时"
                        notes_safe = notes.replace('"', '\\"')

                        try:
                            import locale
                            try:
                                locale.setlocale(locale.LC_TIME, "en_US.UTF-8")
                            except locale.Error:
                                pass
                            start_str = start_time.strftime("%a %b %d %H:%M:%S %Y")
                            end_str = end_time.strftime("%a %b %d %H:%M:%S %Y")
                            apple_script = (
                                'tell application "Calendar" '
                                'to tell calendar "日历" '
                                f'to make new event with properties {{summary:"{title_safe}", description:"{notes_safe}", '
                                f'start date:(date "{start_str}"), end date:(date "{end_str}")}}'
                            )
                            env = os.environ.copy()
                            env["LANG"] = "en_US.UTF-8"
                            env["LC_ALL"] = "en_US.UTF-8"
                            env["LANGUAGE"] = "en_US"
                            subprocess.run(["osascript", "-e", apple_script], check=True, env=env)
                            st.success(f"已同步任务到日历：{title}")
                        except Exception as e:
                            st.warning(f"⚠️ 无法同步任务 {title}：{e}")
    else:
        st.header("📊 周总结")
        week_id = get_week_id()
        infile = weekly_dir / f"{week_id}.yaml"
        summary_file = weekly_dir / f"{week_id}_summary.yaml"

        if not infile.exists():
            st.error(f"未找到本周计划文件：{infile}")
            return

        plan = load_yaml(infile)
        completed, missed = [], []
        for t in plan.get("tasks", []):
            done = st.checkbox(f"完成：{t['title']}")
            if done:
                completed.append(t)
            else:
                reason = st.text_input(f"未完成原因 - {t['title']}", key=f"r_{t['title']}")
                t["reason"] = reason
                missed.append(t)

        sys_change = st.text_input("系统级改动（模板/流程/协作/环境）")
        habit_change = st.text_input("习惯级改动（行为节奏）")
        next_candidates = st.text_area("下周候选任务（每行一条）")

        if st.button("💾 保存总结"):
            summary = {
                "week": week_id,
                "completed": [t["title"] for t in completed],
                "missed": missed,
                "system_change": sys_change,
                "habit_change": habit_change,
                "next_candidates": next_candidates.splitlines(),
                "updated_at": datetime.now().isoformat(timespec="minutes"),
            }
            save_yaml(summary, summary_file)
            st.success(f"已保存总结：{summary_file}")

if __name__ == "__main__":
    main()
