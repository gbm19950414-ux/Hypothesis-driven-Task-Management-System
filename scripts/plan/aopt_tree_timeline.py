#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
aopt_tree_timeline.py
读取 aopt_index.yaml，绘制：
- 左侧：Area → Objective → Project 树
- 右侧：按 Project 分行的任务时间线（Gantt-like）
并用不同标记形状标注：
  - 上周已完成的 task（方形标记 's'）
  - 下周计划的 task（上三角 '^'）
  - 其他任务（圆点 'o'）
注意：遵循可移植性与简洁性，不手动设置颜色（使用 Matplotlib 默认配色），
用“标记形状/线型”区分不同类别，便于黑白打印。
"""

import argparse
import datetime as dt
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Set

import yaml
import zoneinfo
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.transforms as mtransforms
import matplotlib.dates as mdates



# networkx 可选：用于更好看的树状布局
try:
    import networkx as nx
    HAS_NX = True
except Exception:
    HAS_NX = False

# -------------------------
# Matplotlib 字体设置（中文）
# -------------------------
# 优先使用 macOS 常见中文字体；如果这些字体不存在，
# Matplotlib 会自动 fallback 到其它可用 sans-serif 字体。
matplotlib.rcParams['font.sans-serif'] = [
    "PingFang SC", "Heiti SC", "STHeiti", "Microsoft YaHei",
    "SimHei", "Arial Unicode MS"
]
# 避免坐标轴负号显示成方块
matplotlib.rcParams['axes.unicode_minus'] = False

# -------------------------
# 默认路径解析
# -------------------------
def resolve_default_yaml() -> Path:
    """尽量智能地找到 aopt_index.yaml，按以下优先级：
    1) 环境变量 AOPT_INDEX 指定的路径
    2) 当前工作目录下的 aopt_index.yaml
    3) 当前工作目录下的 reviews/aopt_index.yaml
    4) 用户 OneDrive 目录中的固定路径
    5) 脚本所在目录的上级/已知 reviews 路径
    若均不存在，返回当前工作目录下的 aopt_index.yaml（便于报错提示）。
    """
    candidates = []

    # 1) 环境变量
    env_path = os.environ.get("AOPT_INDEX")
    if env_path:
        candidates.append(Path(env_path))

    # 2) CWD 常见位置
    cwd = Path.cwd()
    candidates.append(cwd / "aopt_index.yaml")
    candidates.append(cwd / "reviews" / "aopt_index.yaml")

    # 3) 用户 OneDrive 常见固定路径
    home = Path.home()
    onedrive = home / "Library/CloudStorage/OneDrive-个人/life_os/reviews/aopt_index.yaml"
    candidates.append(onedrive)

    # 4) 脚本位置的相对路径
    try:
        script_dir = Path(__file__).resolve().parent
        candidates.append(script_dir.parent / "reviews" / "aopt_index.yaml")
        candidates.append(script_dir / "aopt_index.yaml")
    except Exception:
        pass

    for p in candidates:
        if p.is_file():
            return p

    # 都没找到，就返回 CWD/aopt_index.yaml 作为兜底
    return cwd / "aopt_index.yaml"


# -------------------------
# 数据结构
# -------------------------
@dataclass
class Node:
    uid: str
    kind: str
    title: str
    status: Optional[str]
    start: Optional[dt.datetime]
    end: Optional[dt.datetime]
    area_uid: Optional[str]
    objective_uid: Optional[str]
    project_uid: Optional[str]


@dataclass
class Task(Node):
    due: Optional[dt.date]
    duration_minutes: Optional[float]


# -------------------------
# 工具函数
# -------------------------
def parse_dt(x: Optional[str], tz: zoneinfo.ZoneInfo) -> Optional[dt.datetime]:
    if not x:
        return None
    # 支持形如 '2025-10-27T07:30' 或 '2025-10-27T07:30:00'
    try:
        # 标准：fromisoformat 支持缺省秒
        d = dt.datetime.fromisoformat(x)
    except Exception:
        return None
    # YAML 里看起来是“本地无时区”的时间；我们赋予本机时区
    if d.tzinfo is None:
        d = d.replace(tzinfo=tz)
    return d


def week_window(today: dt.date) -> Tuple[dt.date, dt.date, dt.date, dt.date]:
    """
    返回 (上周周一, 上周周日, 下周周一, 下周周日)，全部是日期，不含时区。
    以 ISO 周为准：周一=0，周日=6。
    """
    dow = today.weekday()  # 0..6
    this_monday = today - dt.timedelta(days=dow)
    last_monday = this_monday - dt.timedelta(days=7)
    last_sunday = this_monday - dt.timedelta(days=1)
    next_monday = this_monday + dt.timedelta(days=7)
    next_sunday = this_monday + dt.timedelta(days=13)
    return last_monday, last_sunday, next_monday, next_sunday


def load_index(path: str, tzname: str) -> Tuple[Dict[str, Node], Dict[str, Task], List[Tuple[str, str]], Dict[str, List[str]]]:
    tz = zoneinfo.ZoneInfo(tzname)

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    nodes: Dict[str, Node] = {}
    tasks: Dict[str, Task] = {}

    # 解析三类：areas / objectives / projects
    for group_name in ("areas", "objectives", "projects"):
        for item in raw.get("nodes", {}).get(group_name, []):
            uid = str(item.get("uid"))
            node = Node(
                uid=uid,
                kind=str(item.get("kind")),
                title=str(item.get("title")),
                status=(item.get("status") if item.get("status") is not None else None),
                start=parse_dt(item.get("start"), tz),
                end=parse_dt(item.get("end"), tz),
                area_uid=item.get("area_uid"),
                objective_uid=item.get("objective_uid"),
                project_uid=item.get("project_uid"),
            )
            nodes[uid] = node

    # 解析 tasks
    for item in raw.get("nodes", {}).get("tasks", []):
        uid = str(item.get("uid"))
        due_raw = item.get("due")
        due = None
        if due_raw:
            try:
                due = dt.date.fromisoformat(str(due_raw))
            except Exception:
                due = None

        t = Task(
            uid=uid,
            kind=str(item.get("kind")),
            title=str(item.get("title")),
            status=(item.get("status") if item.get("status") is not None else None),
            start=parse_dt(item.get("start"), tz),
            end=parse_dt(item.get("end"), tz),
            area_uid=item.get("area_uid"),
            objective_uid=item.get("objective_uid"),
            project_uid=item.get("project_uid"),
            due=due,
            duration_minutes=item.get("duration_minutes"),
        )
        tasks[uid] = t

    # 解析 edges（便于稳健建立父子关系）
    edges: List[Tuple[str, str]] = []
    for e in raw.get("edges", []):
        frm = str(e.get("from"))
        to = str(e.get("to"))
        edges.append((frm, to))

    # 建立 project -> task 映射（更快捷）
    proj_to_tasks: Dict[str, List[str]] = {}
    for t in tasks.values():
        if t.project_uid:
            proj_to_tasks.setdefault(t.project_uid, []).append(t.uid)

    return nodes, tasks, edges, proj_to_tasks


def is_active(node: Node, now: dt.datetime, project_has_focus_task: bool) -> bool:
    """
    定义“正在进行”的启发式：
      1) status == 'in_progress'；或
      2) 时间窗覆盖 now；或
      3) 对于 project：其任务中包含上周已完成或下周计划（project_has_focus_task）
    """
    if node.status and node.status.lower() == "in_progress":
        return True
    if node.start and node.end and node.start <= now <= node.end:
        return True
    if node.kind == "project" and project_has_focus_task:
        return True
    return False


def classify_task_week(t: Task, lw_beg: dt.date, lw_end: dt.date, nw_beg: dt.date, nw_end: dt.date) -> str:
    """
    返回 'last_week_done' / 'next_week_todo' / 'other'
    规则：
      - 上周：若 task.status == 'done' 且 (start 或 end 任一日期) 落在 [上周一, 上周日]
      - 下周：若 task.status in ('todo','in_progress') 且 start 日期 落在 [下周一, 下周日]
      - 其他：其余
    """
    def ddate(x: Optional[dt.datetime]) -> Optional[dt.date]:
        return x.date() if x else None

    s, e = ddate(t.start), ddate(t.end)

    # 上周已完成
    if (t.status or "").lower() == "done":
        for d_ in (s, e):
            if d_ and lw_beg <= d_ <= lw_end:
                return "last_week_done"

    # 下周将要进行
    if (t.status or "").lower() in ("todo", "in_progress"):
        if s and nw_beg <= s <= nw_end:
            return "next_week_todo"

    return "other"


def collect_aop_active(nodes: Dict[str, Node],
                       tasks: Dict[str, Task],
                       proj_to_tasks: Dict[str, List[str]],
                       today: dt.date,
                       now: dt.datetime) -> Tuple[Set[str], Dict[str, str]]:
    """
    返回需要展示的 A/O/P uid 集合，以及每个 task 的周分类
    """
    lw_beg, lw_end, nw_beg, nw_end = week_window(today)

    task_week_cls: Dict[str, str] = {}
    project_focus: Dict[str, bool] = {}

    # 统计每个 project 是否有“上周已完成或下周进行”的任务
    for pid, tids in proj_to_tasks.items():
        focus = False
        for tid in tids:
            cls = classify_task_week(tasks[tid], lw_beg, lw_end, nw_beg, nw_end)
            task_week_cls[tid] = cls
            if cls in ("last_week_done", "next_week_todo"):
                focus = True
        project_focus[pid] = focus

    # 选择 active 的 A/O/P
    active_uids: Set[str] = set()
    for n in nodes.values():
        if n.kind not in ("area", "objective", "project"):
            continue
        focus = project_focus.get(n.uid, False)
        if is_active(n, now, focus):
            active_uids.add(n.uid)

    # 同时把 active project 的父级 objective/area 补齐
    uid_to_parent: Dict[str, Optional[str]] = {}
    for n in nodes.values():
        parent = None
        if n.kind == "project":
            parent = n.objective_uid
        elif n.kind == "objective":
            parent = n.area_uid
        uid_to_parent[n.uid] = parent

    to_add = set()
    for uid in list(active_uids):
        p = uid_to_parent.get(uid)
        while p:
            to_add.add(p)
            p = uid_to_parent.get(p)
    active_uids |= to_add

    return active_uids, task_week_cls


# -------------------------
# 绘图
# -------------------------
def draw_tree(ax, nodes: Dict[str, Node], active_uids: Set[str]):
    """绘制 Area→Objective→Project 树"""
    # 只画 active 子图
    # 建立层次
    levels = {"area": 0, "objective": 1, "project": 2}

    # 构图
    edges = []
    for n in nodes.values():
        if n.uid not in active_uids:
            continue
        if n.kind == "objective" and n.area_uid and n.area_uid in active_uids:
            edges.append((n.area_uid, n.uid))
        if n.kind == "project" and n.objective_uid and n.objective_uid in active_uids:
            edges.append((n.objective_uid, n.uid))

    if HAS_NX:
        G = nx.DiGraph()
        for uid in active_uids:
            G.add_node(uid)
        G.add_edges_from(edges)

        # 分层位置：我们自定义 y=level, x 自动排列
        pos = {}
        x_slots = {0: 0, 1: 0, 2: 0}
        x_step = 1.0
        for uid in sorted(active_uids):
            level = levels.get(nodes[uid].kind, 0)
            pos[uid] = (x_slots[level] * x_step, -level)  # y 反向向下
            x_slots[level] += 1

        nx.draw_networkx_nodes(G, pos, ax=ax, node_size=400)
        labels = {uid: nodes[uid].title for uid in active_uids}
        nx.draw_networkx_labels(G, pos, labels=labels, font_size=8, ax=ax)
        nx.draw_networkx_edges(G, pos, ax=ax, arrows=True, arrowstyle="->", arrowsize=10)
        ax.set_axis_off()
    else:
        # 简单退化版：逐层打印成“文本树”，用竖向均匀排布
        y = 0
        line_h = 0.3
        for a in [n for n in nodes.values() if n.kind == "area" and n.uid in active_uids]:
            ax.text(0.0, y, f"[Area] {a.title}", fontsize=9, va="center")
            y -= line_h
            for o in [n for n in nodes.values() if n.kind == "objective" and n.area_uid == a.uid and n.uid in active_uids]:
                ax.text(0.2, y, f"↳ [Obj] {o.title}", fontsize=8, va="center")
                y -= line_h
                for p in [n for n in nodes.values() if n.kind == "project" and n.objective_uid == o.uid and n.uid in active_uids]:
                    ax.text(0.4, y, f"↳ [Prj] {p.title}", fontsize=8, va="center")
                    y -= line_h
        ax.set_xlim(0, 1)
        ax.set_ylim(y - 0.5, 1)
        ax.set_axis_off()


def draw_timelines(ax, nodes: Dict[str, Node], tasks: Dict[str, Task],
                   proj_to_tasks: Dict[str, List[str]],
                   active_uids: Set[str],
                   today: dt.date,
                   left_margin_axes: float,
                   project_filter: Optional[List[str]] = None) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """任务时间线总览（支持单 Project 输出和多 Project 输出）：
        - 若 project_filter 只包含 1 个 project：
            * 仅绘制该 project
            * 行距 / lane 间距加大
            * 直接为每个任务画 45° 的标签（不再做 label-lane 算法）
            * 不在左侧绘制 Area / Objective / Project 三列，而是交由上层在标题里展示
        - 若包含多个 project：
            * 每个 project 一行
            * 行按 area → objective → project 排序
            * 左侧绘制三列文字：Area / Objective / Project
            * X 轴仅显示 ~1 个月窗口（今天±15天）
            * 同一 project 内任务自动分多轨道 lane，避免重叠
            * 任务名标签使用像素偏移 + 多层 label lane，减少水平和垂直重叠
        - 所有模式：
            * 完全落在窗口外的任务用折叠标记 (“<” 或 “>”) 显示在边界
        返回:
            (area_title, obj_title, proj_title) 如果是单 project 模式，否则 (None, None, None)
    """

    # ==== 1. 预处理：窗口设为今天±15天 ====
    local_tz = dt.datetime.now().astimezone().tzinfo
    left = dt.datetime.combine(
        today - dt.timedelta(days=15),
        dt.time(0, 0),
        tzinfo=local_tz
    )
    right = dt.datetime.combine(
        today + dt.timedelta(days=15),
        dt.time(23, 59),
        tzinfo=local_tz
    )

    # ==== 2. 选择 active 的 projects ====
    active_projects = [n for n in nodes.values() if n.kind == "project" and n.uid in active_uids]
    # 如果传入了 project_filter，则仅保留该子集
    if project_filter is not None:
        filter_set = set(project_filter)
        active_projects = [n for n in active_projects if n.uid in filter_set]

    # 按 area → objective → project 名称排序
    def sort_key(p: Node):
        """用于对 project 行排序：
        先按 area，再按 objective，最后按 project 名称。
        """
        obj_title = ""
        area_title = ""
        if p.objective_uid and p.objective_uid in nodes:
            o = nodes[p.objective_uid]
            obj_title = o.title
            if o.area_uid and o.area_uid in nodes:
                area_title = nodes[o.area_uid].title
        return (area_title, obj_title, p.title)

    active_projects.sort(key=sort_key)

    # 是否是单 project 模式
    single_project = (len(active_projects) == 1)

    # 只有在多 project 模式下，我们才会在画布左边绘制 Area / Objective / Project 三列
    if not single_project:
        # 这些列将放置在轴坐标的负方向 (x<0)，因此不会和时间线/marker 重叠。
        # 列的 x 位置（轴坐标）基于经验和 left_margin_axes 的大小动态调整：
        col_x_area = -0.85 * left_margin_axes
        col_x_obj  = -0.45 * left_margin_axes
        col_x_prj  = -0.08 * left_margin_axes
    def clip_task_to_window(t: Task, left_win: dt.datetime, right_win: dt.datetime) -> Tuple[str, dt.datetime, Optional[dt.datetime]]:
        """
        根据任务时间决定绘制方式，返回 (mode, xs, xe)
        mode 取值：
          - 'segment'  : 任务有持续区间（或可当成区间）且与窗口有交集，xs/xe 是截断到窗口范围内的线段
          - 'point'    : 任务是时间点，且该点落在窗口范围内，xs 为该点
          - 'left_fold': 任务完全在窗口左侧（结束时间 < left_win），xs 为 left_win
          - 'right_fold':任务完全在窗口右侧（开始时间 > right_win），xs 为 right_win
        """
        # 解析开始和结束时间
        if t.start and t.end and t.end >= t.start:
            s = t.start
            e = t.end
        elif t.start:
            s = t.start
            e = t.start
        else:
            # 没有任何时间信息，视为 point 在窗口左边界
            return ("point", left_win, None)

        # 情况一：完全在窗口左侧
        if e < left_win:
            return ("left_fold", left_win, None)

        # 情况二：完全在窗口右侧
        if s > right_win:
            return ("right_fold", right_win, None)

        # 情况三：与窗口有重叠
        xs = s if s >= left_win else left_win
        xe = e if e <= right_win else right_win

        # 如果这是一个点状任务（xs==xe）
        if xs == xe:
            return ("point", xs, None)
        else:
            return ("segment", xs, xe)

    # ==== 3. 行高 / Y 轴准备 ====
    if single_project:
        # 单项目：让每个 task lane 有更大的垂直空间，避免重叠
        row_h = 2.0    # 只有一行，所以可以很大
        lane_h = 0.5   # lane 之间也拉开
    else:
        row_h = 0.9    # 多项目图：保持紧凑但可读
        lane_h = 0.22  # lane 之间的垂直分隔

    y0 = 0
    y_ticks: List[float] = []
    y_labels: List[str] = []  # 将保持为空，稍后我们不直接用它画文字
    row_meta: List[Dict[str, str]] = []  # 保存该行的 area/objective/project 文字和 y 值

    # ==== 4. 周窗（为了分类样式：上周完成/下周计划） ====
    lw_beg, lw_end, nw_beg, nw_end = week_window(today)

    # ==== 5. lane 分配工具 ====
    def interval_for_task(t: Task) -> Tuple[dt.datetime, dt.datetime]:
        """返回 (start, end)。若缺 end 则用 start；若缺 start 就放在 left 点上。"""
        if t.start and t.end:
            return (t.start, t.end)
        if t.start:
            return (t.start, t.start)
        return (left, left)

    def assign_lanes_for_project(tids_local: List[str]) -> Dict[str, int]:
        """
        对同一 project 的任务做简单贪心调度，避免重叠：
        新任务的开始时间 >= 该 lane 上最后一个任务的结束时间 才能放入该 lane。
        """
        lane_last_end: List[dt.datetime] = []
        lane_of_task: Dict[str, int] = {}
        # 按开始时间排序
        tids_sorted = sorted(
            tids_local,
            key=lambda tid: interval_for_task(tasks[tid])[0]
        )
        for tid in tids_sorted:
            s, e = interval_for_task(tasks[tid])
            placed = False
            for lane_idx, last_end in enumerate(lane_last_end):
                if s >= last_end:
                    lane_of_task[tid] = lane_idx
                    lane_last_end[lane_idx] = e
                    placed = True
                    break
            if not placed:
                lane_idx = len(lane_last_end)
                lane_of_task[tid] = lane_idx
                lane_last_end.append(e)
        return lane_of_task

    # 在正式绘制前先给 ax 一个“临时”的坐标范围，
    # 这样我们在计算标签像素坐标 (ax.transData.transform) 时，
    # Matplotlib 已经知道 x/y 轴的缩放关系。
    # y 轴的临时下界用项目数量估个下限，后面会再精确更新。
    tmp_bottom = -(len(active_projects) - 1) * row_h - 1.0
    ax.set_xlim(left, right)
    ax.set_ylim(tmp_bottom, y0 + 1.0)

    # ==== 6. 绘制每个 project 行 ====
    min_y_overall = y0
    for i, p in enumerate(active_projects):
        y = y0 - i * row_h
        y_ticks.append(y)

        proj_title = p.title
        obj_title = ""
        area_title = ""
        if p.objective_uid and p.objective_uid in nodes:
            o = nodes[p.objective_uid]
            obj_title = o.title
            if o.area_uid and o.area_uid in nodes:
                area_title = nodes[o.area_uid].title
        row_meta.append({
            "y": y,
            "area": area_title,
            "objective": obj_title,
            "project": proj_title,
        })

        # 画这行的基准参考线
        ax.hlines(y, left, right, linestyles="dotted", linewidth=0.8)

        # 该 project 下所有任务
        tids = proj_to_tasks.get(p.uid, [])

        # 任务按 lane 分层，避免线段重叠
        lane_of_task = assign_lanes_for_project(tids)
        max_lane_idx = 0 if not lane_of_task else max(lane_of_task.values())

        # 更新整体 Y 最低值，保证 ylim 能容纳所有 lanes
        y_bottom_this_project = y - max_lane_idx * lane_h
        if y_bottom_this_project < min_y_overall:
            min_y_overall = y_bottom_this_project

        if single_project:
            # ---------- 单项目模式 ----------
            # 直接画每个 task，并用 45° 标注名字；不做 label-lane
            for tid in tids:
                t = tasks.get(tid)
                if not t:
                    continue

                lane_idx = lane_of_task.get(tid, 0)
                yy = y - lane_idx * lane_h

                # 任务的类别 → 线型 / marker
                cls = classify_task_week(t, lw_beg, lw_end, nw_beg, nw_end)
                if cls == "last_week_done":
                    ls = "solid"         # 上周完成：实线
                    base_marker = "s"    # 方形
                elif cls == "next_week_todo":
                    ls = (0, (3, 2))     # 下周计划：虚线
                    base_marker = "^"    # 上三角
                else:
                    ls = (0, (1, 2))     # 其他：点划线
                    base_marker = "o"    # 圆点

                mode, xs, xe = clip_task_to_window(t, left, right)

                # 根据 mode 绘制线/marker，并决定文本的锚点
                if mode == "segment":
                    ax.hlines(yy, xs, xe, linestyles=ls, linewidth=2.0)
                    ax.plot([xs, xe], [yy, yy],
                            linestyle="None", marker=base_marker, markersize=5)
                    label_x = xs
                elif mode == "point":
                    ax.plot(xs, yy,
                            linestyle="None", marker=base_marker, markersize=6)
                    label_x = xs
                elif mode == "left_fold":
                    ax.plot(xs, yy,
                            linestyle="None", marker="<", markersize=6)
                    label_x = xs
                elif mode == "right_fold":
                    ax.plot(xs, yy,
                            linestyle="None", marker=">", markersize=6)
                    label_x = xs
                else:
                    label_x = xs  # 兜底

                # 在单项目模式下，直接用 45° 倾斜文字放在当前 lane 上方
                ax.text(
                    label_x,
                    yy + 0.25,  # 往上抬一些（lane_h 已经比较大）
                    t.title,
                    fontsize=7,
                    rotation=45,
                    ha="left",
                    va="bottom"
                )

        else:
            # ---------- 多项目模式 ----------
            # 收集 label_entries，之后统一用像素距离做 label-lane，避免水平重叠
            label_entries = []

            for tid in tids:
                t = tasks.get(tid)
                if not t:
                    continue

                lane_idx = lane_of_task.get(tid, 0)
                yy = y - lane_idx * lane_h

                # 任务的类别 → 线型 / marker
                cls = classify_task_week(t, lw_beg, lw_end, nw_beg, nw_end)
                if cls == "last_week_done":
                    ls = "solid"         # 上周完成：实线
                    base_marker = "s"    # 方形
                elif cls == "next_week_todo":
                    ls = (0, (3, 2))     # 下周计划：虚线
                    base_marker = "^"    # 上三角
                else:
                    ls = (0, (1, 2))     # 其他：点划线
                    base_marker = "o"    # 圆点

                mode, xs, xe = clip_task_to_window(t, left, right)

                # 根据 mode 绘制线/marker，并拿到 label_x
                if mode == "segment":
                    ax.hlines(yy, xs, xe, linestyles=ls, linewidth=2.0)
                    ax.plot([xs, xe], [yy, yy],
                            linestyle="None", marker=base_marker, markersize=5)
                    label_x = xs
                elif mode == "point":
                    ax.plot(xs, yy,
                            linestyle="None", marker=base_marker, markersize=6)
                    label_x = xs
                elif mode == "left_fold":
                    ax.plot(xs, yy,
                            linestyle="None", marker="<", markersize=6)
                    label_x = xs
                elif mode == "right_fold":
                    ax.plot(xs, yy,
                            linestyle="None", marker=">", markersize=6)
                    label_x = xs
                else:
                    label_x = xs  # 兜底

                label_entries.append({
                    "label_x": label_x,
                    "yy": yy,
                    "text": t.title,
                })

            # ---------- 多项目：为标签做 label-lane，避免水平重叠 ----------
            MIN_LABEL_X_GAP_PX = 30  # 同一 project 内，相邻标签在同一层至少要间隔的像素
            label_entries_sorted = sorted(label_entries, key=lambda e: e["label_x"])
            lane_last_px: List[float] = []

            for entry in label_entries_sorted:
                x_num = mdates.date2num(entry["label_x"])
                px, py = ax.transData.transform((x_num, entry["yy"]))

                placed = False
                for lane_idx, last_px in enumerate(lane_last_px):
                    if abs(px - last_px) > MIN_LABEL_X_GAP_PX:
                        entry["lane_idx"] = lane_idx
                        lane_last_px[lane_idx] = px
                        placed = True
                        break
                if not placed:
                    entry["lane_idx"] = len(lane_last_px)
                    lane_last_px.append(px)

            BASE_Y_OFFSET_PX = 4    # 第一层的上移像素
            DELTA_Y_OFFSET_PX = 10  # 每多一层，多抬这么多像素
            BASE_X_OFFSET_PX = 4    # 水平右移像素

            for entry in label_entries_sorted:
                lane_idx = entry["lane_idx"]
                pixel_y_offset = BASE_Y_OFFSET_PX + DELTA_Y_OFFSET_PX * lane_idx

                ax.annotate(
                    entry["text"],
                    xy=(entry["label_x"], entry["yy"]),
                    xytext=(BASE_X_OFFSET_PX, pixel_y_offset),
                    textcoords="offset points",
                    fontsize=6,
                    rotation=0,
                    ha="left",
                    va="bottom"
                )

    # ==== 7. 轴范围、刻度 ====
    ax.set_ylim(min_y_overall - 1.0, y0 + 1.0)
    ax.set_xlim(left, right)
    ax.set_yticks(y_ticks)
    ax.set_yticklabels([])

    # X 轴用日期
    ax.xaxis_date()
    ax.grid(axis="x", linestyle=":", linewidth=0.5)

    # ==== 8. 左侧列（仅多项目模式需要） ====
    if not single_project:
        trans = mtransforms.blended_transform_factory(ax.transAxes, ax.transData)

        # 表头
        ax.text(col_x_area, y0 + 0.4, "Area", transform=trans,
                ha="right", va="bottom", fontsize=7, fontweight="bold")
        ax.text(col_x_obj,  y0 + 0.4, "Objective", transform=trans,
                ha="right", va="bottom", fontsize=7, fontweight="bold")
        ax.text(col_x_prj,  y0 + 0.4, "Project", transform=trans,
                ha="right", va="bottom", fontsize=7, fontweight="bold")

        # 行值
        for row in row_meta:
            yy = row["y"]
            ax.text(col_x_area, yy, row["area"],
                    transform=trans,
                    ha="right", va="center",
                    fontsize=7)
            ax.text(col_x_obj, yy, row["objective"],
                    transform=trans,
                    ha="right", va="center",
                    fontsize=7)
            ax.text(col_x_prj, yy, row["project"],
                    transform=trans,
                    ha="right", va="center",
                    fontsize=7)

    # ==== 9. 图例（标记形状区分状态） ====
    from matplotlib.lines import Line2D
    legend_elems = [
        Line2D([0], [0], marker='s', linestyle='None', label='上周已完成'),
        Line2D([0], [0], marker='^', linestyle='None', label='下周将进行'),
        Line2D([0], [0], marker='o', linestyle='None', label='其他任务'),
    ]
    ax.legend(handles=legend_elems, loc="upper right", fontsize=8, frameon=False)

    # 返回单项目的 (Area, Objective, Project)，否则 (None, None, None)
    if single_project and row_meta:
        meta_first = row_meta[0]
        return (meta_first["area"], meta_first["objective"], meta_first["project"])
    else:
        return (None, None, None)


def main():
    parser = argparse.ArgumentParser(description="基于 aopt_index.yaml 生成 A/O/P 树 + Project 任务时间线图")
    parser.add_argument("yaml_path", nargs="?", default=None,
                        help="aopt_index.yaml 的路径（留空则自动查找常见位置或读取环境变量 AOPT_INDEX）")
    parser.add_argument("--tz", default="America/Los_Angeles", help="解析无时区时间的时区（默认 America/Los_Angeles）")
    parser.add_argument("--today", default=None, help="覆盖今天日期（YYYY-MM-DD），用于复现")
    parser.add_argument("--out", default=None, help="输出图片路径（.png），默认与 YAML 同目录下的 aopt_tree_timeline.png")
    args = parser.parse_args()

    # 今天/现在
    tz = zoneinfo.ZoneInfo(args.tz)
    if args.today:
        today = dt.date.fromisoformat(args.today)
        now = dt.datetime.combine(today, dt.time(12, 0)).replace(tzinfo=tz)
    else:
        now = dt.datetime.now(tz)
        today = now.date()

    # 解析默认 YAML 路径
    if args.yaml_path:
        yaml_path = Path(args.yaml_path)
    else:
        yaml_path = resolve_default_yaml()

    if not yaml_path.is_file():
        print("[ERROR] 未找到 aopt_index.yaml。请指定路径或设置环境变量 AOPT_INDEX。尝试过的路径示例：")
        print(f" - {yaml_path}")
        print("提示：可执行 `export AOPT_INDEX=\"/绝对路径/aopt_index.yaml\"` 后重试。")
        return

    # 输出路径：若未指定，则与 YAML 同目录
    out_path = Path(args.out) if args.out else (yaml_path.parent / "aopt_tree_timeline.png")

    nodes, tasks, edges, proj_to_tasks = load_index(str(yaml_path), args.tz)
    active_uids, task_week_cls = collect_aop_active(nodes, tasks, proj_to_tasks, today, now)

    # ==== 自适应 + 单 project 导出 ====
    # 我们对每个 active project 单独出一张图，避免标签重叠。
    active_projects = [
        n for n in nodes.values()
        if n.kind == "project" and n.uid in active_uids
    ]

    # 排序（area → objective → project）确保输出顺序稳定
    def sort_key_for_project(pnode: Node):
        obj_title = ""
        area_title = ""
        if pnode.objective_uid and pnode.objective_uid in nodes:
            o = nodes[pnode.objective_uid]
            obj_title = o.title
            if o.area_uid and o.area_uid in nodes:
                area_title = nodes[o.area_uid].title
        return (area_title, obj_title, pnode.title)

    active_projects.sort(key=sort_key_for_project)

    def get_titles_for_project(pnode: Node) -> Tuple[str, str, str]:
        proj_title = pnode.title
        obj_title = ""
        area_title = ""
        if pnode.objective_uid and pnode.objective_uid in nodes:
            o = nodes[pnode.objective_uid]
            obj_title = o.title
            if o.area_uid and o.area_uid in nodes:
                area_title = nodes[o.area_uid].title
        return area_title, obj_title, proj_title

    def sanitize_filename(name: str) -> str:
        # 将中文和空格等都允许保留为可见字符以便你识别，但把不适合做文件名的符号替换成下划线
        safe_chars = []
        for ch in name.strip():
            if ch.isalnum() or ch in ("_", "-", "，", "。", "（", "）"):
                safe_chars.append(ch)
            else:
                safe_chars.append("_")
        safe = "".join(safe_chars)
        if not safe:
            safe = "project"
        return safe

    # 针对每个 project 单独生成一张图
    for pnode in active_projects:
        # 这个 project 的标题信息
        area_t, obj_t, prj_t = get_titles_for_project(pnode)

        # 估计此 project 的 label 长度，用于宽度
        longest_label_len = max(len(area_t), len(obj_t), len(prj_t))

        # 单项目：高度可以更大，给 lane 更多垂直空间
        fig_h = max(4.5, 2.5 * 1 + 2.0)  # 至少 ~4.5
        # 宽度考虑左侧不画三列的时候可以略小，但我们保持 14 基础以免太挤
        fig_w = 14.0 + 0.12 * float(longest_label_len)

        # 单项目图时，我们不会绘制 Area/Objective/Project 三列在左侧，
        # 所以可以把 left margin 收紧。
        left_margin_axes = 0.15

        # 只画这个 project
        project_filter = [pnode.uid]

        fig, ax_tl = plt.subplots(figsize=(fig_w, fig_h))

        meta_area, meta_obj, meta_prj = draw_timelines(
            ax_tl,
            nodes,
            tasks,
            proj_to_tasks,
            active_uids,
            today,
            left_margin_axes,
            project_filter=project_filter
        )

        # 边距（左边可以更窄）
        fig.subplots_adjust(left=left_margin_axes, right=0.97, top=0.85, bottom=0.12)

        # 标题整合 Area / Objective / Project
        if meta_area is None:
            meta_area = area_t
        if meta_obj is None:
            meta_obj = obj_t
        if meta_prj is None:
            meta_prj = prj_t

        fig.suptitle(
            f"[{meta_area}] / [{meta_obj}] / [{meta_prj}]\n任务时间线（上周 & 下周标注） - {today.isoformat()}",
            fontsize=12
        )

        # 针对该 project 输出文件名
        project_slug = sanitize_filename(meta_prj)
        out_file = out_path.with_name(
            out_path.stem + f"_{project_slug}" + out_path.suffix
        )

        plt.savefig(out_file, dpi=200, bbox_inches="tight")

        print("[OK] 导出完成 (单项目)")
        print(f"YAML: {yaml_path}")
        print(f"AREA: {meta_area}")
        print(f"OBJ : {meta_obj}")
        print(f"PRJ : {meta_prj}")
        print(f"OUT : {out_file}")

        plt.close(fig)


if __name__ == "__main__":
    main()
