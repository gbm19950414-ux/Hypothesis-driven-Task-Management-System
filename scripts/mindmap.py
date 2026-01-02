def _delete_entity(uid, level):
    # 删除当前文件
    if level == "area":
        path = area_path_uid.get(uid)
        if path and path.exists():
            path.unlink()
        # 删除其下 objectives / projects / tasks
        for o in [x for x in objs if x.get("area_uid") == uid]:
            _delete_entity(o.get("uid"), "objective")
        for p in [x for x in projs if x.get("area_uid") == uid]:
            _delete_entity(p.get("uid"), "project")
        for t in [x for x in tasks if x.get("area_uid") == uid]:
            _delete_entity(t.get("uid"), "task")

    elif level == "objective":
        path = obj_path_uid.get(uid)
        if path and path.exists():
            path.unlink()
        # 删除其下 projects / tasks
        for p in [x for x in projs if x.get("objective_uid") == uid]:
            _delete_entity(p.get("uid"), "project")
        for t in [x for x in tasks if x.get("objective_uid") == uid]:
            _delete_entity(t.get("uid"), "task")

    elif level == "project":
        path = proj_path_uid.get(uid)
        if path and path.exists():
            path.unlink()
        # 删除其下 tasks
        for t in [x for x in tasks if x.get("project_uid") == uid]:
            _delete_entity(t.get("uid"), "task")

    elif level == "task":
        path = task_path.get(uid)
        if path and path.exists():
            path.unlink()
import streamlit as st
from streamlit_markmap import markmap
from pathlib import Path
from ruamel.yaml import YAML
from collections import defaultdict
import threading, time
try:
    from streamlit_sortables import sort_items  # 多容器拖拽
except Exception:
    st.error("需要安装依赖：pip install -U streamlit-sortables")
    st.stop()
from datetime import datetime, date, timedelta
import copy
from uuid import uuid4
import re
# 插件依赖
try:
    from streamlit_calendar import calendar as st_calendar
except Exception:
    st.warning("未安装 streamlit-calendar，日历视图不可用。pip install streamlit-calendar")
    st_calendar = None

def _gen_uid():
    return uuid4().hex[:8]

# Helper to validate UID strings
def _is_valid_uid(v):
    return isinstance(v, str) and re.fullmatch(r"[0-9a-f]{8}", v)

def _slugify(text):
    s = re.sub(r'[^a-zA-Z0-9_-]+', '_', (text or '').strip().lower())[:32]
    return s or f"auto_{_gen_uid()}"
# Helper: compute index in (label, uid) options by uid
def _index_in_labeled_opts_by_uid(options, uid):
    if not uid:
        return 0
    for i, opt in enumerate(options):
        try:
            if isinstance(opt, (list, tuple)) and len(opt) >= 2 and opt[1] == uid:
                return i
        except Exception:
            pass
    return 0
# === duration/datetime helpers for plugins ===
def _parse_duration_to_minutes(s: str, default_min: int = 120) -> int:
    if not s:
        return default_min
    s = str(s).strip().lower()
    try:
        if s.endswith('min'):
            return int(s[:-3])
        if s.endswith('m') and s[:-1].isdigit():
            return int(s[:-1])
        if s.endswith('h'):
            return int(float(s[:-1]) * 60)
        if s.isdigit():
            return int(s)
    except Exception:
        pass
    return default_min

def _ensure_dt_from_due_and_time(due_str: str, default_hour: int = 9, default_minute: int = 0):
    try:
        d = datetime.fromisoformat(due_str)
        return d
    except Exception:
        try:
            d = datetime.fromisoformat(due_str).date()
            return datetime.combine(d, datetime.min.time()).replace(hour=default_hour, minute=default_minute)
        except Exception:
            try:
                d = date.fromisoformat(due_str)
                return datetime.combine(d, datetime.min.time()).replace(hour=default_hour, minute=default_minute)
            except Exception:
                return datetime.combine(date.today(), datetime.min.time()).replace(hour=default_hour, minute=default_minute)

# —— 颜色：基于 uid 生成稳定可读的 HEX 颜色（用于汇总日历分层上色）
def _hex_color_from_uid(uid: str) -> str:
    if not isinstance(uid, str) or len(uid) < 6:
        return "#8899aa"
    try:
        h = int(uid[:6], 16)
        r = (h >> 16) & 0xFF
        g = (h >> 8) & 0xFF
        b = h & 0xFF
        # 简单提亮，避免过暗
        r = int((r + 180) / 2)
        g = int((g + 180) / 2)
        b = int((b + 180) / 2)
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return "#8899aa"

# —— 点击日历事件 → 打开任务编辑（仅在“汇总日历视图（多层级）”启用） ——
def _select_task_and_focus(task_uid: str):
    """把单选/预选都切到点击的任务，并打开右侧任务编辑区。"""
    if not isinstance(task_uid, str):
        return
    t = task_by_uid.get(task_uid) or next((x for x in tasks if x.get("uid") == task_uid or x.get("id") == task_uid), None)
    if not t:
        return
    pu = t.get("project_uid")
    ou = t.get("objective_uid") or (proj_by_uid.get(pu, {}).get("objective_uid") if pu else None)
    au = t.get("area_uid") or (obj_by_uid.get(ou, {}).get("area_uid") if ou else None)
    if au: st.session_state["preselect_area_uid"] = au
    if ou: st.session_state["preselect_obj_uid"]  = ou
    if pu: st.session_state["preselect_proj_uid"] = pu
    st.session_state["preselect_task_uid"] = t.get("uid")
    st.session_state["op_mode_choice"] = "层级浏览编辑"
    st.session_state["last_focus"] = "task"
    st.toast("📝 已定位到任务编辑区")
    st.rerun()

def _handle_calendar_event_click(cal_state: dict):
    """仅在“汇总日历视图（多层级）”启用点击→编辑。"""
    try:
        if st.session_state.get("calendar_click_context") != "multi":
            return
        if isinstance(cal_state, dict) and cal_state.get("callback") == "eventClick":
            ev = (cal_state.get("eventClick") or {}).get("event", {})
            tuid = None
            if isinstance(ev.get("id"), str) and ev.get("id"):
                tuid = ev["id"]
            if not tuid:
                ext = ev.get("extendedProps") or {}
                if isinstance(ext, dict):
                    tuid = ext.get("task_uid")
            if tuid:
                _select_task_and_focus(tuid)
    except Exception:
        pass
st.set_page_config(page_title="life_os MindMap", layout="wide")
st.title("🧭 领域→目标→项目→任务 思维导图")

# Dashboard 基地址（用于点击任务跳转到 dashboard）
dash_base_url = st.text_input(
    "Dashboard 基地址（例如 http://localhost:8501）",
    value=st.session_state.get("dash_base_url", "http://localhost:8501")
)
st.session_state["dash_base_url"] = dash_base_url.strip()

# ---- calendar view state (persist across rerun) ----
# 我们把当前想看的日历视图（"dayGridMonth" / "timeGridWeek" / "timeGridDay"）写进 session_state
# 以后渲染 st_calendar 的时候不再写死默认，而是用这里的值。
st.session_state.setdefault("calendar_initial_view", "dayGridMonth")

_calendar_view_options = [
    ("月视图", "dayGridMonth"),
    ("周视图", "timeGridWeek"),
    ("日视图", "timeGridDay"),
]

# 找到当前值在列表里的位置，作为 radio 的默认
_current_view = st.session_state["calendar_initial_view"]
_default_idx = [v for (_, v) in _calendar_view_options].index(_current_view)

# 这个 radio 既是 UI 控件，也是我们同步 state 的地方
_calendar_view_choice = st.radio(
    "日历视图（记忆）",
    _calendar_view_options,
    index=_default_idx,
    format_func=lambda x: x[0],
    key="calendar_view_choice",
    horizontal=True,
    help="选择后会被记住。即使点击任务跳转触发 rerun，回到日历时依然保持这个视图。"
)

# 把选择结果（第二列是真正的 FullCalendar view 名称）写回 session_state
st.session_state["calendar_initial_view"] = _calendar_view_choice[1]

yaml = YAML()
root = Path(__file__).resolve().parents[1]
areas_dir = root / "01_areas"
objs_dir  = root / "02_objectives"
projs_dir = root / "03_projects"
tasks_dir = root / "tasks"

def _write_fields(path: Path, **updates):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        d = yaml.load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        d = {}
    for k, v in updates.items():
        if v in ("", None):
            d.pop(k, None)
        else:
            d[k] = v
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(d, f)
    st.toast(f"✅ 写回：{path.name}")
def load_yaml(folder: Path):
    data = []
    if folder.exists():
        for p in folder.glob("*.y*ml"):
            try:
                d = yaml.load(p.read_text(encoding="utf-8"))
                # 自动补齐 uid（双层 ID 体系：内部 uid，外部 title/alias）
                if isinstance(d, dict) and ("uid" not in d or not d.get("uid")):
                    d["uid"] = _gen_uid()
                    # 先把 __path 临时写入，便于 _write_fields 使用下面的 p
                    try:
                        _write_fields(p, uid=d["uid"])  # 立即写回
                    except Exception:
                        pass
                if isinstance(d, dict):
                    d["__path"] = str(p)
                    data.append(d)
            except Exception as e:
                st.warning(f"读取失败: {p.name} -> {e}")
    return data

areas = load_yaml(areas_dir)
objs  = load_yaml(objs_dir)
projs = load_yaml(projs_dir)
tasks = load_yaml(tasks_dir)

# —— 索引（UID-only）：为兼容旧变量名，*_by_id 实际以 uid 为 key
area_by_id = {a.get("uid"): a for a in areas if isinstance(a, dict) and a.get("uid")}
obj_by_id  = {o.get("uid"): o for o in objs  if isinstance(o, dict) and o.get("uid")}
proj_by_id = {p.get("uid"): p for p in projs if isinstance(p, dict) and p.get("uid")}

# —— 路径映射（UID-only）
obj_path  = {o.get("uid"): Path(o["__path"]) for o in objs  if o.get("uid") and o.get("__path")}
proj_path = {p.get("uid"): Path(p["__path"]) for p in projs if p.get("uid") and p.get("__path")}
area_path = {a.get("uid"): Path(a["__path"]) for a in areas if a.get("uid") and a.get("__path")}

# 新增：基于 uid 的路径映射
area_path_uid = {a.get("uid"): Path(a["__path"]) for a in areas if a.get("uid") and a.get("__path")}
obj_path_uid  = {o.get("uid"): Path(o["__path"]) for o in objs  if o.get("uid") and o.get("__path")}
proj_path_uid = {p.get("uid"): Path(p["__path"]) for p in projs if p.get("uid") and p.get("__path")}

# —— UID 索引与路径
area_by_uid = {a.get("uid"): a for a in areas if a.get("uid")}
obj_by_uid  = {o.get("uid"): o for o in objs  if o.get("uid")}
proj_by_uid = {p.get("uid"): p for p in projs if p.get("uid")}

task_by_uid  = {t.get("uid"): t for t in tasks if t.get("uid")}
task_path    = {t.get("uid"): Path(t["__path"]) for t in tasks if t.get("uid") and t.get("__path")}

# 旧 ID → UID 的兼容映射（用于读取旧数据与过渡期）

area_id2uid = {a.get("id"): a.get("uid") for a in areas if a.get("id") and a.get("uid")}
obj_id2uid  = {o.get("id"): o.get("uid") for o in objs  if o.get("id") and o.get("uid")}
proj_id2uid = {p.get("id"): p.get("uid") for p in projs if p.get("id") and p.get("uid")}

# === UID层级关系补齐（兼容旧字段 area/objective/project → 写入 *_uid） ===
def _migrate_uid_links():
    fix_obj = fix_proj = fix_task = 0
    fix_proj_uid_task = 0  # 新增计数器
    regen_obj = regen_proj = regen_task = 0
    # Objectives: 补齐 area_uid，并修复无效UID
    for o in objs:
        up = {}
        # Regenerate invalid uid
        if not _is_valid_uid(o.get("uid")):
            new_uid = _gen_uid()
            up["uid"] = new_uid
            o["uid"] = new_uid
            regen_obj += 1
        # Regenerate invalid area_uid
        if o.get("area_uid") is not None and not _is_valid_uid(o.get("area_uid")):
            new_area_uid = None
            old = o.get("area")
            if old:
                new_area_uid = area_id2uid.get(old)
            if not new_area_uid:
                new_area_uid = _gen_uid()
            up["area_uid"] = new_area_uid
            o["area_uid"] = new_area_uid
            regen_obj += 1
        # Fill missing area_uid
        if not o.get("area_uid"):
            old = o.get("area")
            if old:
                auid = area_id2uid.get(old)
                if auid:
                    up["area_uid"] = auid
                    o["area_uid"] = auid
                    fix_obj += 1
        if up:
            p = obj_path_uid.get(o.get("uid"))
            if p:
                _write_fields(p, **up)
                # Only count fix_obj if not already incremented above for fill
                if "area_uid" in up and not ("area_uid" in up and fix_obj > 0):
                    fix_obj += 1

    # Projects: 补齐 objective_uid / area_uid，并修复无效UID
    for p in projs:
        up = {}
        # Regenerate invalid uid
        if not _is_valid_uid(p.get("uid")):
            new_uid = _gen_uid()
            up["uid"] = new_uid
            p["uid"] = new_uid
            regen_proj += 1
        # Regenerate invalid objective_uid
        if p.get("objective_uid") is not None and not _is_valid_uid(p.get("objective_uid")):
            new_obj_uid = None
            oldo = p.get("objective")
            if oldo:
                new_obj_uid = obj_id2uid.get(oldo)
            if not new_obj_uid:
                new_obj_uid = _gen_uid()
            up["objective_uid"] = new_obj_uid
            p["objective_uid"] = new_obj_uid
            regen_proj += 1
        # Regenerate invalid area_uid
        if p.get("area_uid") is not None and not _is_valid_uid(p.get("area_uid")):
            new_area_uid = None
            olda = p.get("area")
            if olda:
                new_area_uid = area_id2uid.get(olda)
            if not new_area_uid:
                new_area_uid = _gen_uid()
            up["area_uid"] = new_area_uid
            p["area_uid"] = new_area_uid
            regen_proj += 1
        # Fill missing objective_uid
        if not p.get("objective_uid"):
            oldo = p.get("objective")
            if oldo:
                ouid = obj_id2uid.get(oldo)
                if ouid:
                    up["objective_uid"] = ouid
                    p["objective_uid"] = ouid
                    fix_proj += 1
        # Fill missing area_uid
        if not p.get("area_uid"):
            olda = p.get("area")
            if olda:
                auid = area_id2uid.get(olda)
                if auid:
                    up["area_uid"] = auid
                    p["area_uid"] = auid
                    fix_proj += 1
        if up:
            pp = proj_path_uid.get(p.get("uid"))
            if pp:
                _write_fields(pp, **up)
                # Only count fix_proj if not already incremented above for fill
                if (("objective_uid" in up or "area_uid" in up) and not ("objective_uid" in up and fix_proj > 0)):
                    fix_proj += 1

    # Tasks: 补齐 project_uid / objective_uid / area_uid，并修复无效UID
    for t in tasks:
        up = {}
        # Regenerate invalid uid
        if not _is_valid_uid(t.get("uid")):
            new_uid = _gen_uid()
            up["uid"] = new_uid
            t["uid"] = new_uid
            regen_task += 1
        # Regenerate invalid project_uid
        if t.get("project_uid") is not None and not _is_valid_uid(t.get("project_uid")):
            new_proj_uid = None
            oldp = t.get("project")
            if oldp:
                new_proj_uid = proj_id2uid.get(oldp)
            if not new_proj_uid:
                new_proj_uid = _gen_uid()
            up["project_uid"] = new_proj_uid
            t["project_uid"] = new_proj_uid
            regen_task += 1
            fix_proj_uid_task += 1  # 计数
        # Regenerate invalid objective_uid
        if t.get("objective_uid") is not None and not _is_valid_uid(t.get("objective_uid")):
            new_obj_uid = None
            oldo = t.get("objective")
            if oldo:
                new_obj_uid = obj_id2uid.get(oldo)
            if not new_obj_uid:
                new_obj_uid = _gen_uid()
            up["objective_uid"] = new_obj_uid
            t["objective_uid"] = new_obj_uid
            regen_task += 1
        # Regenerate invalid area_uid
        if t.get("area_uid") is not None and not _is_valid_uid(t.get("area_uid")):
            new_area_uid = None
            olda = t.get("area")
            if olda:
                new_area_uid = area_id2uid.get(olda)
            if not new_area_uid:
                new_area_uid = _gen_uid()
            up["area_uid"] = new_area_uid
            t["area_uid"] = new_area_uid
            regen_task += 1
        # Fill missing project_uid
        if not t.get("project_uid"):
            oldp = t.get("project")
            if oldp:
                puid = proj_id2uid.get(oldp)
                if puid:
                    up["project_uid"] = puid
                    t["project_uid"] = puid
                    fix_task += 1
        # Fill missing objective_uid
        if not t.get("objective_uid"):
            oldo = t.get("objective")
            if oldo:
                ouid = obj_id2uid.get(oldo)
                if ouid:
                    up["objective_uid"] = ouid
                    t["objective_uid"] = ouid
                    fix_task += 1
        # Fill missing area_uid
        if not t.get("area_uid"):
            olda = t.get("area")
            if olda:
                auid = area_id2uid.get(olda)
                if auid:
                    up["area_uid"] = auid
                    t["area_uid"] = auid
                    fix_task += 1
        if up:
            tp = None
            # 尝试多种方式定位 YAML 路径
            if t.get("uid") and t.get("uid") in task_path:
                tp = task_path[t.get("uid")]
            elif t.get("__path"):
                tp = Path(t["__path"])
            elif t.get("id"):
                # 兜底查找
                guess = tasks_dir / f"{t['id']}.yaml"
                if guess.exists():
                    tp = guess
            if tp:
                try:
                    _write_fields(tp, **up)
                except Exception as e:
                    st.warning(f"⚠️ 写入失败 {tp.name}: {e}")
            else:
                st.warning(f"⚠️ 未找到 Task 文件路径: {t.get('id')} ({t.get('uid')})")

    # —— Ensure referential consistency (Objective → Project/Task) ——
    prop_proj_area = prop_task_area = fill_task_obj = 0
    # Projects: area_uid should follow its Objective.area_uid
    for p in projs:
        ou = p.get("objective_uid")
        if ou and _is_valid_uid(ou):
            obj_area = obj_by_uid.get(ou, {}).get("area_uid")
            if obj_area and p.get("area_uid") != obj_area:
                pp = proj_path_uid.get(p.get("uid"))
                if pp:
                    _write_fields(pp, area_uid=obj_area)
                    p["area_uid"] = obj_area
                    prop_proj_area += 1
    # Tasks: fill missing objective from project, and align area with its Objective
    for t in tasks:
        exp_ou = t.get("objective_uid")
        if not exp_ou and t.get("project_uid"):
            exp_ou = proj_by_uid.get(t.get("project_uid"), {}).get("objective_uid")
            if exp_ou:
                tp = task_path.get(t.get("uid")) or (Path(t["__path"]) if t.get("__path") else None)
                if tp:
                    _write_fields(tp, objective_uid=exp_ou)
                t["objective_uid"] = exp_ou
                fill_task_obj += 1
        if exp_ou and _is_valid_uid(exp_ou):
            obj_area = obj_by_uid.get(exp_ou, {}).get("area_uid")
            if obj_area and t.get("area_uid") != obj_area:
                tp = task_path.get(t.get("uid")) or (Path(t["__path"]) if t.get("__path") else None)
                if tp:
                    _write_fields(tp, area_uid=obj_area)
                t["area_uid"] = obj_area
                prop_task_area += 1

    st.info(
        f"UID层级关系补齐完成：Objective {fix_obj} 个，Project {fix_proj} 个，Task {fix_task} 个（其中修复 project_uid {fix_proj_uid_task} 个）；"
        f"重新生成 UID：Objective {regen_obj} 个，Project {regen_proj} 个，Task {regen_task} 个。"
        f" 对齐层级：Project→Area {prop_proj_area} 个，Task 回填 Objective {fill_task_obj} 个，Task→Area {prop_task_area} 个。"
    )

# 执行一次迁移（仅对缺失 *_uid 的条目生效，幂等）
_migrate_uid_links()
# 重新加载 tasks 确保最新 UID 写入内存
tasks = load_yaml(tasks_dir)
# —— 任务索引与路径（重建，覆盖旧值）
task_by_uid  = {t.get("uid"): t for t in tasks if t.get("uid")}
task_path    = {t.get("uid"): Path(t["__path"]) for t in tasks if t.get("uid") and t.get("__path")}
# 任务索引（仅依赖 *_uid，新逻辑）
tasks_by_project   = defaultdict(list)
tasks_by_objective = defaultdict(list)
tasks_by_area      = defaultdict(list)
for t in tasks:
    pu, ou, au = t.get("project_uid"), t.get("objective_uid"), t.get("area_uid")
    if pu: tasks_by_project[pu].append(t)
    if ou: tasks_by_objective[ou].append(t)
    if au: tasks_by_area[au].append(t)

# 额外：为项目建立按 objective/area 的索引（仅依赖 *_uid）
projs_by_objective = defaultdict(list)
projs_by_area = defaultdict(list)
for p in projs:
    ou, au = p.get("objective_uid"), p.get("area_uid")
    if ou: projs_by_objective[ou].append(p)
    if au: projs_by_area[au].append(p)

def sort_tasks_by_due(tasks):
    def parse_due(t):
        from datetime import datetime
        d = t.get("due")
        if d:
            try:
                return datetime.fromisoformat(d)
            except:
                return datetime.max
        return datetime.max
    return sorted(tasks, key=parse_due)

# === Sorting helpers: order Area/Objective/Project by earliest contained task start ===

def _parse_task_start_dt(t):
    """Best-effort parse of a task's start datetime.
    Priority:
    1) explicit ISO `start`
    2) `due` + `start_time` (legacy)
    3) ISO `due` as date (fallback)
    """
    if not isinstance(t, dict):
        return None
    # 1) ISO start
    s = t.get("start")
    if isinstance(s, str) and s.strip():
        try:
            return datetime.fromisoformat(s.strip())
        except Exception:
            pass
    # 2) legacy: due + start_time
    due = t.get("due")
    stime = t.get("start_time")
    if isinstance(due, str) and due.strip() and isinstance(stime, str) and stime.strip():
        try:
            d = datetime.fromisoformat(due.strip()).date()
            hh, mm = stime.strip().split(":", 1)
            return datetime.combine(d, datetime.min.time()).replace(hour=int(hh), minute=int(mm))
        except Exception:
            pass
    # 3) fallback: treat due as date
    if isinstance(due, str) and due.strip():
        try:
            d = datetime.fromisoformat(due.strip()).date()
            return datetime.combine(d, datetime.min.time()).replace(hour=0, minute=0)
        except Exception:
            pass
    return None


def _earliest_start_in_tasks(task_list):
    """Return earliest datetime among tasks; None if no usable datetime."""
    if not isinstance(task_list, list) or not task_list:
        return None
    dts = []
    for t in task_list:
        dt = _parse_task_start_dt(t)
        if isinstance(dt, datetime):
            dts.append(dt)
    return min(dts) if dts else None


def _sort_uids_by_earliest_task_start(uids, uid_to_tasks_func):
    """Sort uid list by earliest contained task start.
    - If a uid has no tasks (or no parseable start), it is placed at the very top.
    - Otherwise sort by earliest start ascending.
    """
    def key(uid):
        ts = uid_to_tasks_func(uid) or []
        earliest = _earliest_start_in_tasks(ts)
        # no tasks => top
        if earliest is None:
            return (0, datetime.min)
        return (1, earliest)

    return sorted(list(uids), key=key)

from datetime import datetime, date
today = date.today()

def format_task_with_status(idx, t, link_base=None):
    title = t.get('title', t.get('uid',''))
    status = t.get('status','')
    due = t.get('due','')
    overdue = ''
    if due:
        try:
            ddate = datetime.fromisoformat(due).date()
            if ddate < today and status != 'done':
                overdue = '⚠️'
        except:
            pass
    text = f"{idx+1}.{title} [{status}|{due}]{overdue}"
    if link_base:
        url = f"{link_base.rstrip('/')}/?task_uid={t.get('uid')}&choose=1"
        return f"[{text}]({url})"
    return text

# 构建 markmap Markdown 语法
lines = ["# 我的科研项目思维导图"]
rendered_task_uids = set()  # 去重：基于 uid，确保任务只出现一次
for a in areas:
    lines.append(f"## 🌐 {a.get('title', a.get('uid'))}")
    for o in [x for x in objs if x.get("area_uid") == a.get("uid")]:
        lines.append(f"### 🎯 {o.get('title', o.get('uid'))}")
        # 该 Objective 下的项目（优先按项目自己的 objective 匹配，仅基于 uid）
        proj_list = list(projs_by_objective.get(o.get("uid"), []))

        # 若项目本身没有 objective，但属于当前 Area，且其任务中有指向该 Objective 的，也纳入
        for p in projs_by_area.get(a.get("uid"), []):
            if p in proj_list:
                continue
            ptasks = tasks_by_project.get(p.get("uid"), [])
            if any(t.get("objective_uid") == o.get("uid") for t in ptasks):
                proj_list.append(p)

        # 按截止日期排序 Project
        def parse_proj_due(p):
            d = p.get("due")
            if d:
                try:
                    return datetime.fromisoformat(d)
                except:
                    return datetime.max
            return datetime.max
        proj_list.sort(key=parse_proj_due)

        # 渲染项目和其下的任务（仅展示属于当前 objective 的任务，避免跨目标重复）
        for p in proj_list:
            due_disp = f" [{p['due']}]" if p.get("due") else ""
            lines.append(f"#### 📂 {p.get('title', p.get('uid'))}{due_disp}")
            p_uid = p.get("uid")
            ptasks = sort_tasks_by_due(tasks_by_project.get(p_uid, []))
            shown_i = 0
            for t in ptasks:
                t_uid = t.get("uid")
                # 仅在当前 Objective 下展示与其匹配的任务；若任务未绑定 objective_uid，也允许显示
                if ((not t.get("objective_uid")) or (t.get("objective_uid") == o.get("uid"))):
                    if t_uid and t_uid not in rendered_task_uids:
                        lines.append(f"- {format_task_with_status(shown_i, t, dash_base_url)}")
                        rendered_task_uids.add(t_uid)
                        shown_i += 1

        # 直接挂在该 objective 下、且没有项目归属的任务
        otasks = sort_tasks_by_due(tasks_by_objective.get(o.get("uid"), []))
        shown_i = 0
        for t in otasks:
            t_uid = t.get("uid")
            # 仅展示没有 project 归属的 objective 级任务，避免与项目下重复
            if not t.get("project_uid"):
                if t_uid and t_uid not in rendered_task_uids:
                    lines.append(f"#### - {format_task_with_status(shown_i, t, dash_base_url)}")
                    rendered_task_uids.add(t_uid)
                    shown_i += 1
    # 直接挂在该 area 下的任务
    atasks = sort_tasks_by_due(tasks_by_area.get(a.get("uid"), []))
    shown_i = 0
    for t in atasks:
        t_uid = t.get("uid")
        # 仅展示没有 project/objective 归属的 area 级任务，避免重复
        if (not t.get("project_uid")) and (not t.get("objective_uid")):
            if t_uid and t_uid not in rendered_task_uids:
                lines.append(f"### - {format_task_with_status(shown_i, t, dash_base_url)}")
                rendered_task_uids.add(t_uid)
                shown_i += 1

tab_map, tab_reorg = st.tabs(["🗺️ 思维导图", "🧰 层级重组（拖拽）"])

with tab_map:
    markmap("\n".join(lines))
    # 可保留调试输出
    st.write("Areas loaded:", areas)
    st.write("Objectives loaded:", objs)
    st.write("Projects loaded:", projs)
    st.write("Tasks loaded:", tasks)

with tab_reorg:
    st.subheader("层级重组（拖拽即可，更改会立刻写回 YAML）")
    _op_mode_options = ["拖拽重组", "层级浏览编辑"]
    _default_mode_idx = 1 if st.session_state.get("force_edit_mode") else 0
    op_mode = st.radio("选择操作模式", _op_mode_options, horizontal=True, index=_default_mode_idx, key="op_mode_choice")

    if op_mode == "拖拽重组":
        reorg_mode = st.radio("选择重组模式", ["Objective ↔ Area", "Project ↔ Objective"], horizontal=True)

        if reorg_mode == "Objective ↔ Area":
            # 1) 组装多容器：每个 Area 是一列，items 是该 Area 下的 Objective（基于 area_uid）
            containers = []
            area_headers = []  # [(area_uid, header), ...]
            for a in areas:
                auid   = a.get("uid")
                title = a.get("title", a.get("uid"))
                header = f"{a.get('uid')} · {title}"
                items  = []
                for o in objs:
                    if o.get("area_uid") == auid:
                        label = f"{o.get('uid')} :: {o.get('title', o.get('uid'))}"
                        items.append(label)
                containers.append({"header": header, "items": items})
                area_headers.append((auid, header))

            # 未分配列
            unassigned = []
            for o in objs:
                if (o.get("area_uid") is None) or (o.get("area_uid") not in area_by_uid):
                    unassigned.append(f"{o.get('uid')} :: {o.get('title', o.get('uid'))}")
            containers.append({"header": "未分配 (Objective 无 area)", "items": unassigned})

            # 2) 拖拽交互（返回新布局）
            new_state = sort_items(containers, multi_containers=True, key="reorg_obj_area_sort")

            # 3) 计算新父级并写回
            if new_state:
                # header → area_uid 映射
                header_to_area_uid = {hdr: auid for auid, hdr in area_headers}
                header_to_area_uid["未分配 (Objective 无 area)"] = None

                # 逐列遍历新的 items，把每个 Objective 的 area_uid 设为该列的 area_uid
                for col in new_state:
                    target_area_uid = header_to_area_uid.get(col["header"])
                    for item in col.get("items", []):
                        oid = item.split("::", 1)[0].strip()  # objective uid
                        o   = obj_by_uid.get(oid)
                        if not o:
                            continue
                        cur = o.get("area_uid")
                        if cur != target_area_uid:
                            p = obj_path_uid.get(oid)
                            if p:
                                _write_fields(p, area_uid=target_area_uid)
                        # Cascade: when an Objective changes Area, align its Projects/Tasks
                        for _proj in projs:
                            if _proj.get("objective_uid") == oid:
                                _pp = proj_path_uid.get(_proj.get("uid"))
                                if _pp:
                                    _write_fields(_pp, area_uid=target_area_uid)
                        for _task in tasks:
                            if _task.get("objective_uid") == oid:
                                _tp = task_path.get(_task.get("uid")) or (Path(_task.get("__path")) if _task.get("__path") else None)
                                if _tp:
                                    _write_fields(_tp, area_uid=target_area_uid)

            st.caption("提示：把 Objective 从一列拖到另一列，即修改该 Objective 的 `area_uid` 字段；拖到“未分配”列会删除 `area_uid`。")

        else:  # Project ↔ Objective
            # 1) 每个 Objective 一列，items 是其下的 Project（基于 objective_uid）
            containers = []
            obj_headers = []  # [(obj_uid, header), ...]
            for o in objs:
                ouid   = o.get("uid")
                title = o.get("title", o.get("uid"))
                header = f"{o.get('uid')} · {title}"
                items  = []
                for p in projs:
                    if p.get("objective_uid") == ouid:
                        label = f"{p.get('uid')} :: {p.get('title', p.get('uid'))}"
                        items.append(label)
                containers.append({"header": header, "items": items})
                obj_headers.append((ouid, header))

            # 未分配列
            unassigned = []
            for p in projs:
                if (p.get("objective_uid") is None) or (p.get("objective_uid") not in obj_by_uid):
                    unassigned.append(f"{p.get('uid')} :: {p.get('title', p.get('uid'))}")
            containers.append({"header": "未分配 (Project 无 objective)", "items": unassigned})

            # 2) 拖拽交互
            new_state = sort_items(containers, multi_containers=True, key="reorg_proj_obj_sort")

            # 3) 写回 objective_uid
            if new_state:
                header_to_obj_uid = {hdr: ouid for ouid, hdr in obj_headers}
                header_to_obj_uid["未分配 (Project 无 objective)"] = None

                for col in new_state:
                    target_obj_uid = header_to_obj_uid.get(col["header"])
                    for item in col.get("items", []):
                        pid  = item.split("::", 1)[0].strip()  # project uid
                        proj = proj_by_uid.get(pid)
                        if not proj:
                            continue
                        cur = proj.get("objective_uid")
                        if cur != target_obj_uid:
                            p = proj_path_uid.get(pid)
                            if p:
                                _write_fields(p, objective_uid=target_obj_uid)

            st.caption("提示：把 Project 拖到另一列，即修改该 Project 的 `objective_uid` 字段；拖到“未分配”列会删除 `objective_uid`。")

    elif op_mode == "层级浏览编辑":
        st.session_state.setdefault("active_proj_for_calendar", None)
        st.markdown("#### 浏览+就地编辑（左侧选择，右侧立即写回 YAML）")
        # 显示已完成任务的总开关（默认关闭）
        st.session_state.setdefault("show_done", False)
        st.checkbox("显示已完成（done/canceled）", key="show_done")

        # 记录最后一次获得焦点的层级（area/obj/proj/task）
        def _mk_focus(level: str):
            def _cb():
                st.session_state['last_focus'] = level
            return _cb
        focus = st.session_state.get('last_focus')

        colA, colO, colP, colT, colE = st.columns([1, 1, 1, 1, 2])

        # —— 左一列：Area（不再默认选中任何下级）
        with colA:
            st.caption("区域 (Area)")
            # Area 排序：按其包含任务的“最早开始时间”升序；若不包含任务，则置顶
            areas_uids_sorted = _sort_uids_by_earliest_task_start(
                [a.get("uid") for a in areas if isinstance(a, dict) and a.get("uid")],
                lambda auid: tasks_by_area.get(auid, []),
            )
            areas.sort(key=lambda a: areas_uids_sorted.index(a.get("uid")) if a.get("uid") in areas_uids_sorted else 9999)
            def _area_label(a):
                return f"{a.get('title', a.get('uid'))} [{a.get('uid','')}]"
            area_opts = [("（未选择）", None)] \
                        + [(_area_label(a), a.get('uid')) for a in areas] \
                        + [("➕ 新建 Area", "__new__")]
            sel_area_opt = st.radio(
                "选择 Area",
                area_opts,
                index=_index_in_labeled_opts_by_uid(area_opts, st.session_state.get("preselect_area_uid")) if st.session_state.get("preselect_area_uid") else 0,
                format_func=lambda x: x[0],
                key="pick_area",
                label_visibility="collapsed",
                on_change=_mk_focus('area'),
            )
            current_area_id = None
            current_area_uid = None
            if sel_area_opt[1] not in (None, "__new__"):
                current_area_uid = sel_area_opt[1]
                current_area_id = area_by_uid.get(current_area_uid, {}).get("id")

        # —— 左二列：Objective（受 Area 过滤，不自动选中）
        with colO:
            st.caption("客观的 (Objective)")
            if current_area_uid:
                obj_list = [o for o in objs if o.get("area_uid") == current_area_uid]
                # Objective 排序：按其包含任务的“最早开始时间”升序；若不包含任务，则置顶
                obj_uids_sorted = _sort_uids_by_earliest_task_start(
                    [o.get("uid") for o in obj_list if isinstance(o, dict) and o.get("uid")],
                    lambda ouid: tasks_by_objective.get(ouid, []),
                )
                obj_list.sort(key=lambda o: obj_uids_sorted.index(o.get("uid")) if o.get("uid") in obj_uids_sorted else 9999)
            else:
                obj_list = []
            def _obj_label(o):
                return f"{o.get('title', o.get('uid'))} [{o.get('uid','')}]"
            obj_opts = [("（未选择）", None)] \
                    + [(_obj_label(o), o.get('uid')) for o in obj_list] \
                    + [("➕ 新建 Objective", "__new__")]
            sel_obj_opt = st.radio(
                "选择 Objective",
                obj_opts,
                index=_index_in_labeled_opts_by_uid(obj_opts, st.session_state.get("preselect_obj_uid")) if st.session_state.get("preselect_obj_uid") else 0,
                format_func=lambda x: x[0],
                key="pick_obj",
                label_visibility="collapsed",
                on_change=_mk_focus('obj'),
            )
            current_obj_id = None
            current_obj_uid = None
            if sel_obj_opt[1] not in (None, "__new__"):
                current_obj_uid = sel_obj_opt[1]
                current_obj_id = obj_by_uid.get(current_obj_uid, {}).get("id")

        # —— 左三列：Project（受 Objective 过滤，不自动选中）
        with colP:
            st.caption("项目 (Project)")
            if current_obj_uid:
                proj_list = [p for p in projs if p.get("objective_uid") == current_obj_uid]
                # Project 排序：按其包含任务的“最早开始时间”升序；若不包含任务，则置顶
                proj_uids_sorted = _sort_uids_by_earliest_task_start(
                    [p.get("uid") for p in proj_list if isinstance(p, dict) and p.get("uid")],
                    lambda puid: tasks_by_project.get(puid, []),
                )
                proj_list.sort(key=lambda p: proj_uids_sorted.index(p.get("uid")) if p.get("uid") in proj_uids_sorted else 9999)
            else:
                proj_list = []
            # 以 (label, uid) 作为选项，展示“标题 [uid前6位]”，内部仍用 UID
            def _proj_label_left(p):
                return f"{p.get('title', p.get('uid'))} [{p.get('uid','')[:6]}]"
            proj_opts = [("（未选择）", None)] \
                        + [(_proj_label_left(p), p.get('uid')) for p in proj_list] \
                        + [("➕ 新建 Project", "__new__")]
            sel_proj_opt = st.radio(
                "选择 Project",
                options=proj_opts,
                format_func=lambda x: x[0],
                index=_index_in_labeled_opts_by_uid(proj_opts, st.session_state.get("preselect_proj_uid")) if st.session_state.get("preselect_proj_uid") else 0,
                key="pick_proj",
                label_visibility="collapsed",
                on_change=_mk_focus('proj'),
            )
            current_proj_id = None
            current_proj_uid = None
            is_new_proj = (sel_proj_opt[1] == "__new__")
            if sel_proj_opt[1] not in (None, "__new__"):
                current_proj_uid = sel_proj_opt[1]
                current_proj_id = proj_by_uid.get(current_proj_uid, {}).get("id")

        # —— 左四列：Task（受 Project 过滤，不自动选中）
        with colT:
            st.caption("任务 (Task)")
            if current_proj_uid:
                # 按截止日期排序 Task
                task_list = [t for t in tasks if t.get("project_uid") == current_proj_uid]
                # 默认隐藏已完成（done/canceled）
                if not st.session_state.get("show_done"):
                    task_list = [t for t in task_list if (t.get("status","" ).strip().lower() not in ("done","canceled"))]
                def parse_due(t):
                    from datetime import datetime
                    d = t.get("due")
                    if d:
                        try:
                            return datetime.fromisoformat(d)
                        except:
                            return datetime.max
                    return datetime.max
                task_list.sort(key=parse_due)
            else:
                task_list = []
            def _task_label(t):
                tit = t.get('title', t.get('uid',''))
                tuid = t.get('uid','')
                # 进行中标记（存在 active 计时）
                active_mark = " ⏺" if isinstance(t.get("time_log_active"), dict) else ""
                return f"{tit}{active_mark} [{tuid}]"
            task_opts = [("（未选择）", None)] \
                        + [(_task_label(t), t.get('uid')) for t in task_list] \
                        + [("➕ 新建 Task", "__new__")]
            sel_task_opt = st.radio(
                "选择 Task",
                task_opts,
                index=_index_in_labeled_opts_by_uid(task_opts, st.session_state.get("preselect_task_uid")) if st.session_state.get("preselect_task_uid") else 0,
                format_func=lambda x: x[0],
                key="pick_task",
                label_visibility="collapsed",
                on_change=_mk_focus('task'),
            )
            current_task_uid = None
            if sel_task_opt[1] not in (None, "__new__"):
                current_task_uid = sel_task_opt[1]

        # —— 右侧编辑区：按“最后点击的层级”来决定编辑谁（而不是总是最深层）
        with colE:

            def _create_task_api(
                *,
                title: str,
                desc: str = "",
                status: str = "todo",
                due_date=None,              # datetime.date
                start_time=None,            # datetime.time
                duration: str = "2h",
                project_uid=None,
                objective_uid=None,
                area_uid=None,
                acceptance_criteria=None,
            ):
                """
                统一的任务创建入口：
                - 生成稳定 id/uid 与唯一文件名
                - 计算 start/end（若提供 due_date/start_time；否则按 08:00 兜底）
                - 写入 YAML，并将 uid 追加到 Project 的 order_tasks
                - 返回 {"uid": uid, "path": path}
                """
                from datetime import datetime, date as _date, time as _time, timedelta
                from pathlib import Path

                # 校验与默认
                t_title = (title or "").strip() or "新任务"
                t_desc  = desc or ""
                t_status = (status or "todo").strip().lower()
                ac_list = acceptance_criteria if isinstance(acceptance_criteria, list) else []

                # 推导归属：若未显式给出，则尽量从当前上下文继承（与“新建 Task”区域一致）
                tgt_proj_uid = project_uid or (current_proj_uid if 'current_proj_uid' in globals() else None)
                tgt_obj_uid  = objective_uid
                tgt_area_uid = area_uid
                if not tgt_obj_uid:
                    if tgt_proj_uid:
                        tgt_obj_uid = (proj_by_uid.get(tgt_proj_uid, {}) or {}).get("objective_uid") or (current_obj_uid if 'current_obj_uid' in globals() else None)
                    else:
                        tgt_obj_uid = (current_obj_uid if 'current_obj_uid' in globals() else None)
                if not tgt_area_uid:
                    if tgt_obj_uid:
                        tgt_area_uid = (obj_by_uid.get(tgt_obj_uid, {}) or {}).get("area_uid") or (current_area_uid if 'current_area_uid' in globals() else None)
                    else:
                        tgt_area_uid = (current_area_uid if 'current_area_uid' in globals() else None)

                # 计算 start/end
                base_date = due_date if due_date else (_date.today())
                base_time = start_time if start_time else _time(hour=8, minute=0)
                start_dt = datetime.combine(base_date, base_time)
                dur_min  = _parse_duration_to_minutes(duration or "2h", 120)
                end_dt   = start_dt + timedelta(minutes=dur_min)

                # 生成 id / uid / path（文件名基于 slug 确保稳定且唯一）
                gen_id = _slugify(t_title)
                # 先尝试以 id 命名，若存在则在 id 后拼短 uid 片段
                uid = _gen_uid()
                path = tasks_dir / f"{gen_id}.yaml"
                if path.exists():
                    gen_id = f"{gen_id}_{uid[:4]}"
                    path = tasks_dir / f"{gen_id}.yaml"

                # 若极端情况下仍冲突，则用 uid 命名兜底
                tries = 0
                while path.exists() and tries < 5:
                    uid = _gen_uid()
                    path = tasks_dir / f"{gen_id}_{uid[:4]}.yaml"
                    tries += 1
                if path.exists():
                    uid = _gen_uid()
                    path = tasks_dir / f"{uid}.yaml"

                payload = {
                    "id": gen_id,
                    "uid": uid,
                    "title": t_title,
                    "desc": t_desc,
                    "status": t_status,
                    "due": start_dt.date().isoformat(),
                    "start": start_dt.isoformat(),
                    "end": end_dt.isoformat(),
                    "duration": duration or "2h",
                    "project_uid": tgt_proj_uid,
                    "objective_uid": tgt_obj_uid,
                    "area_uid": tgt_area_uid,
                    "acceptance_criteria": ac_list,
                    "deliverable": [],
                    "repeat_rule": None,
                    "repeat_cycle": "不重复",
                    "created_at": _date.today().isoformat(),
                }
                _write_fields(path, **payload)

                # 追加到 Project 的顺序
                if tgt_proj_uid:
                    p_proj = proj_path_uid.get(tgt_proj_uid)
                    if p_proj:
                        order_tasks = proj_by_uid.get(tgt_proj_uid, {}).get("order_tasks", []) or []
                        if uid not in order_tasks:
                            _write_fields(p_proj, order_tasks=order_tasks + [uid])

                return {"uid": uid, "path": str(path)}

            focus = st.session_state.get('last_focus')
            # ====== Area 层 ======
            if focus == 'area':
                # 新建 Area
                if isinstance(sel_area_opt, (list, tuple)) and sel_area_opt[1] == "__new__":
                    st.markdown("### 新建 Area")
                    area_title_new = st.text_input("标题", key="new_area_title")
                    area_desc_new  = st.text_area("描述", key="new_area_desc")
                    if st.button("创建 Area", type="primary", key="btn_create_area"):
                        if not area_title_new.strip():
                            st.warning("请填写标题")
                        else:
                            new_uid = _gen_uid()
                            pth = areas_dir / f"{new_uid}.yaml"
                            while pth.exists():
                                new_uid = _gen_uid()
                                pth = areas_dir / f"{new_uid}.yaml"
                            payload = {
                                "uid": new_uid,
                                "title": area_title_new,
                                "desc": area_desc_new,
                            }
                            _write_fields(pth, **payload)
                            st.success("✅ 已创建 Area")
                            st.session_state["preselect_area_uid"] = new_uid
                            st.rerun()
                # 编辑 Area
                elif current_area_uid:
                    data_area = copy.deepcopy(area_by_uid.get(current_area_uid, {}))
                    st.markdown(f"### 编辑 Area: {current_area_uid} (UID)")
                    p_area = area_path_uid.get(current_area_uid) or (Path(data_area.get("__path")) if data_area.get("__path") else None)

                    # —— Per-area widget keys to avoid state collision when switching fast ——
                    area_title_key = f"edit_area_title_{current_area_uid}"
                    area_desc_key  = f"edit_area_desc_{current_area_uid}"

                    def auto_save_area(_p=p_area, _title_key=area_title_key, _desc_key=area_desc_key):
                        if _p:
                            _write_fields(
                                _p,
                                title=st.session_state.get(_title_key, ""),
                                desc=st.session_state.get(_desc_key,  ""),
                            )
                            st.toast("✅ 已自动保存 Area")

                    st.text_input("标题", value=data_area.get("title", ""), key=area_title_key, on_change=auto_save_area)
                    st.text_area("描述", value=data_area.get("desc", ""), key=area_desc_key, on_change=auto_save_area)
                    # 删除该 Area
                    if st.button("🗑 删除该 Area（及其下所有 Objective/Project/Task）", type="secondary"):
                        _delete_entity(current_area_uid, "area")
                        st.success("已删除该 Area 及其所有下属内容")
                        st.rerun()
            # ====== Objective 层 ======
            elif focus == 'obj':
                # 新建 Objective
                if isinstance(sel_obj_opt, (list, tuple)) and sel_obj_opt[1] == "__new__":
                    st.markdown("### 新建 Objective")
                    obj_title = st.text_input("标题", key="new_obj_title")
                    obj_desc  = st.text_area("描述", key="new_obj_desc")

                    # 选择归属 Area（默认当前 Area）
                    def _idx_by_uid_opts(opts, uid):
                        for i, opt in enumerate(opts):
                            if opt[1] == uid:
                                return i
                        return 0
                    def _area_label_obj(a):
                        return f"{a.get('title', a.get('uid'))} [{a.get('uid','')}]"
                    area_opts_all = [("(留空)", None)] + [(_area_label_obj(a), a.get('uid')) for a in areas]
                    sel_area_opt_obj = st.selectbox(
                        "归属 Area",
                        area_opts_all,
                        index=_idx_by_uid_opts(area_opts_all, current_area_uid),
                        format_func=lambda x: x[0],
                        key="new_obj_area"
                    )
                    sel_area_uid = sel_area_opt_obj[1]

                    if st.button("创建 Objective", type="primary", key="btn_create_obj"):
                        if not obj_title.strip():
                            st.warning("请填写标题")
                        else:
                            new_uid = _gen_uid()
                            path = objs_dir / f"{new_uid}.yaml"
                            while path.exists():
                                new_uid = _gen_uid()
                                path = objs_dir / f"{new_uid}.yaml"
                            payload = {
                                "uid": new_uid,
                                "title": obj_title,
                                "desc": obj_desc,
                                "area_uid": sel_area_uid,
                            }
                            _write_fields(path, **payload)
                            # 把新 Objective 追加到其 Area 的排序列表
                            if sel_area_uid:
                                a_path = area_path_uid.get(sel_area_uid)
                                if a_path:
                                    order_list = area_by_uid.get(sel_area_uid, {}).get("order_objectives", []) or []
                                    _write_fields(a_path, order_objectives=order_list + [new_uid])
                            st.success("✅ 已创建 Objective")
                            st.rerun()
                # 编辑 Objective（UID 版）
                elif current_obj_uid:
                    data_obj = copy.deepcopy(obj_by_uid.get(current_obj_uid, {}))
                    st.markdown(f"### 编辑 Objective: {current_obj_uid} (UID)")

                    p_obj = obj_path_uid.get(current_obj_uid) or (Path(data_obj.get("__path")) if data_obj.get("__path") else None)

                    area_opts_all = [("(留空)", None)] + [
                        (f"{a.get('title', a.get('uid'))} [{a.get('uid','')}]", a.get('uid')) for a in areas
                    ]
                    cur_area_uid_obj = data_obj.get("area_uid")

                    # —— Per-objective widget keys ——
                    obj_title_key = f"edit_obj_title_{current_obj_uid}"
                    obj_desc_key  = f"edit_obj_desc_{current_obj_uid}"
                    obj_area_key  = f"edit_obj_area_{current_obj_uid}"

                    def auto_save_objective(_p=p_obj, _title_key=obj_title_key, _desc_key=obj_desc_key, _area_key=obj_area_key):
                        if _p:
                            _sel = st.session_state.get(_area_key)
                            _area_uid = _sel[1] if isinstance(_sel, (list, tuple)) and len(_sel) >= 2 else None
                            _write_fields(
                                _p,
                                title=st.session_state.get(_title_key, ""),
                                desc=st.session_state.get(_desc_key,  ""),
                                area_uid=_area_uid,
                            )
                            st.toast("✅ 已自动保存 Objective")

                    sel_area_opt_obj_edit = st.selectbox(
                        "归属 Area（可留空）",
                        area_opts_all,
                        index=_index_in_labeled_opts_by_uid(area_opts_all, cur_area_uid_obj),
                        format_func=lambda x: x[0],
                        key=obj_area_key,
                        on_change=auto_save_objective,
                    )

                    st.text_input("标题", value=data_obj.get("title", ""), key=obj_title_key, on_change=auto_save_objective)
                    st.text_area("描述", value=data_obj.get("desc", ""), key=obj_desc_key, on_change=auto_save_objective)
                    # 删除该 Objective
                    if st.button("🗑 删除该 Objective（及其下所有 Project/Task）", type="secondary"):
                        _delete_entity(current_obj_uid, "objective")
                        st.success("已删除该 Objective 及其所有下属内容")
                        st.rerun()
            # ====== Project 层 ======
            elif focus == 'proj':
                # 新建 Project
                if 'is_new_proj' in locals() and is_new_proj:
                    st.markdown("### 新建 Project")
                    proj_title_new = st.text_input("标题", key="new_proj_title")
                    proj_desc_new  = st.text_area("描述", key="new_proj_desc")

                    # 归属 Objective（默认当前）
                    def _idx_by_uid_opts(opts, uid):
                        for i, opt in enumerate(opts):
                            if opt[1] == uid:
                                return i
                        return 0
                    def _obj_label_proj(o):
                        return f"{o.get('title', o.get('uid'))} [{o.get('uid','')}]"
                    obj_opts_all = [("(留空)", None)] + [(_obj_label_proj(o), o.get('uid')) for o in objs]
                    sel_obj_opt_new = st.selectbox(
                        "归属 Objective",
                        obj_opts_all,
                        index=_idx_by_uid_opts(obj_opts_all, current_obj_uid),
                        format_func=lambda x: x[0],
                        key="new_proj_obj"
                    )
                    sel_obj_uid_new = sel_obj_opt_new[1]

                    # 归属 Area（默认当前，显示 title[uid]，值为 uid）
                    def _area_label_proj(a):
                        return f"{a.get('title', a.get('uid'))} [{a.get('uid','')}]"
                    area_uid_opts = [("(留空)", None)] + [(_area_label_proj(a), a.get('uid')) for a in areas]
                    sel_area_opt_new = st.selectbox(
                        "归属 Area",
                        area_uid_opts,
                        index=_idx_by_uid_opts(area_uid_opts, current_area_uid),
                        format_func=lambda x: x[0],
                        key="new_proj_area"
                    )
                    sel_area_uid_new = sel_area_opt_new[1]

                    # 截止日期（可选）
                    proj_due_new = st.date_input("截止日期", value=None, key="new_proj_due")

                    if st.button("创建 Project", type="primary", key="btn_create_proj"):
                        if not proj_title_new.strip():
                            st.warning("请填写标题")
                        else:
                            new_uid = _gen_uid()
                            pth = projs_dir / f"{new_uid}.yaml"
                            while pth.exists():
                                new_uid = _gen_uid()
                                pth = projs_dir / f"{new_uid}.yaml"

                            # —— 兜底：未选 Objective/Area 时自动挂到当前上下文；Area 优先跟随 Objective ——
                            if not sel_obj_uid_new and current_obj_uid:
                                sel_obj_uid_new = current_obj_uid
                            if not sel_area_uid_new:
                                if sel_obj_uid_new:
                                    sel_area_uid_new = obj_by_uid.get(sel_obj_uid_new, {}).get("area_uid") or current_area_uid
                                else:
                                    sel_area_uid_new = current_area_uid

                            # 进一步兜底：若仍无 objective_uid，且当前 Area 下只有一个 Objective，则自动采用该 Objective
                            if not sel_obj_uid_new and current_area_uid:
                                _cands = [o for o in objs if o.get("area_uid") == current_area_uid]
                                if len(_cands) == 1:
                                    sel_obj_uid_new = _cands[0].get("uid")
                                    sel_area_uid_new = _cands[0].get("area_uid") or sel_area_uid_new or current_area_uid

                            # 最后兜底：仍无法确定 objective，则提示并中止，避免产生无归属的 Project
                            if not sel_obj_uid_new:
                                st.warning("未能确定 Project 的归属 Objective：请先选择一个 Objective。")
                                st.stop()

                            st.toast(f"📌 归属确认：objective_uid={sel_obj_uid_new}, area_uid={sel_area_uid_new}")

                            payload = {
                                "uid": new_uid,
                                "title": proj_title_new,
                                "desc": proj_desc_new,
                                "due": str(proj_due_new) if proj_due_new else "",
                                "objective_uid": sel_obj_uid_new,
                                "area_uid": sel_area_uid_new,
                            }
                            _write_fields(pth, **payload)
                            # 追加到 Objective 的排序
                            if sel_obj_uid_new:
                                obj_p = obj_path_uid.get(sel_obj_uid_new)
                                if obj_p:
                                    order_projects = obj_by_uid.get(sel_obj_uid_new, {}).get("order_projects", []) or []
                                    _write_fields(obj_p, order_projects=order_projects + [new_uid])
                            st.success("✅ 已创建 Project")
                            st.rerun()

                # 编辑 Project（UID 版）
                elif current_proj_uid:
                    data = copy.deepcopy(proj_by_uid.get(current_proj_uid, {}))
                    st.markdown(f"### 编辑 Project: {current_proj_uid} (UID)")

                    p = proj_path_uid.get(current_proj_uid) or (Path(data.get("__path")) if data.get("__path") else None)

                    cur_obj_uid = data.get("objective_uid")
                    obj_opts_all_edit = [("(留空)", None)] + [
                        (f"{o.get('title', o.get('uid'))} [{o.get('uid','')}]", o.get('uid')) for o in objs
                    ]

                    cur_area_uid = data.get("area_uid")
                    area_opts_all_edit = [("(留空)", None)] + [
                        (f"{a.get('title', a.get('uid'))} [{a.get('uid','')}]", a.get('uid')) for a in areas
                    ]

                    # —— Per-project widget keys ——
                    proj_title_key = f"edit_proj_title_{current_proj_uid}"
                    proj_desc_key  = f"edit_proj_desc_{current_proj_uid}"
                    proj_due_key   = f"edit_proj_due_{current_proj_uid}"
                    proj_obj_key   = f"edit_proj_obj_{current_proj_uid}"
                    proj_area_key  = f"edit_proj_area_{current_proj_uid}"

                    def auto_save_project(_p=p, _title_key=proj_title_key, _desc_key=proj_desc_key,
                                          _due_key=proj_due_key, _obj_key=proj_obj_key, _area_key=proj_area_key):
                        if not _p:
                            return
                        _obj_sel = st.session_state.get(_obj_key)
                        _area_sel = st.session_state.get(_area_key)
                        _obj_uid = _obj_sel[1] if isinstance(_obj_sel, (list, tuple)) and len(_obj_sel) >= 2 else None
                        _area_uid = _area_sel[1] if isinstance(_area_sel, (list, tuple)) and len(_area_sel) >= 2 else None
                        _due_val = st.session_state.get(_due_key)
                        _write_fields(
                            _p,
                            title=st.session_state.get(_title_key, ""),
                            desc=st.session_state.get(_desc_key,  ""),
                            due=str(_due_val) if isinstance(_due_val, date) else "",
                            objective_uid=_obj_uid,
                            area_uid=_area_uid,
                        )
                        st.toast("✅ 已自动保存 Project")

                    sel_obj_opt_edit = st.selectbox(
                        "归属 Objective（可留空）",
                        obj_opts_all_edit,
                        index=_index_in_labeled_opts_by_uid(obj_opts_all_edit, cur_obj_uid),
                        format_func=lambda x: x[0],
                        key=proj_obj_key,
                        on_change=auto_save_project,
                    )

                    sel_area_opt_edit = st.selectbox(
                        "归属 Area（可留空）",
                        area_opts_all_edit,
                        index=_index_in_labeled_opts_by_uid(area_opts_all_edit, cur_area_uid),
                        format_func=lambda x: x[0],
                        key=proj_area_key,
                        on_change=auto_save_project,
                    )

                    proj_due_val = None
                    if data.get("due"):
                        try:
                            proj_due_val = datetime.fromisoformat(data["due"]).date()
                        except Exception:
                            proj_due_val = None

                    st.text_input("标题", value=data.get("title", ""), key=proj_title_key, on_change=auto_save_project)
                    st.text_area("描述", value=data.get("desc", ""), key=proj_desc_key, on_change=auto_save_project)
                    st.date_input("截止日期", value=proj_due_val, key=proj_due_key, on_change=auto_save_project)
                    # 删除该 Project
                    if st.button("🗑 删除该 Project（及其下所有 Task）", type="secondary"):
                        _delete_entity(current_proj_uid, "project")
                        st.success("已删除该 Project 及其所有下属任务")
                        st.rerun()
            # ====== Task 层 ======
            elif focus == 'task':
                # 新建 Task
                if isinstance(sel_task_opt, (list, tuple)) and sel_task_opt[1] == "__new__":
                    st.markdown("### 新建 Task")
                    t_title = st.text_input("标题", key="new_task_title")
                    t_desc  = st.text_area("描述", key="new_task_desc")
                    t_status = st.selectbox("状态", ["todo", "in_progress", "done"], index=0, key="new_task_status")

                    # 归属 Project（默认当前 Objective 下的项目；若无则全部项目）
                    def _proj_label_new_task(p):
                        return f"{p.get('title', p.get('uid'))} [{p.get('uid','')}]"
                    cand_projs = [p for p in projs if (not current_obj_uid) or (p.get('objective_uid') == current_obj_uid)] or projs
                    proj_opts_new = [("(请选择 Project)", None)] + [(_proj_label_new_task(p), p.get('uid')) for p in cand_projs]
                    sel_proj_new = st.selectbox(
                        "归属 Project（必选）",
                        proj_opts_new,
                        index=_index_in_labeled_opts_by_uid(proj_opts_new, current_proj_uid) if current_proj_uid else 0,
                        format_func=lambda x: x[0],
                        key="new_task_proj"
                    )
                    target_proj_uid = sel_proj_new[1]

                    # 归属 Objective（默认当前或随 Project）
                    def _obj_label_new_task(o):
                        return f"{o.get('title', o.get('uid'))} [{o.get('uid','')}]"
                    obj_opts_new_task = [("(跟随/留空)", None)] + [(_obj_label_new_task(o), o.get('uid')) for o in objs]
                    sel_obj_new_task = st.selectbox(
                        "归属 Objective（可留空，默认跟随 Project）",
                        obj_opts_new_task,
                        index=_index_in_labeled_opts_by_uid(obj_opts_new_task, current_obj_uid) if current_obj_uid else 0,
                        format_func=lambda x: x[0],
                        key="new_task_obj"
                    )
                    target_obj_uid = sel_obj_new_task[1]

                    # 归属 Area（默认跟随 Objective）
                    def _area_label_new_task(a):
                        return f"{a.get('title', a.get('uid'))} [{a.get('uid','')}]"
                    area_opts_new_task = [("(跟随/留空)", None)] + [(_area_label_new_task(a), a.get('uid')) for a in areas]
                    sel_area_new_task = st.selectbox(
                        "归属 Area（可留空，默认跟随 Objective）",
                        area_opts_new_task,
                        index=_index_in_labeled_opts_by_uid(area_opts_new_task, current_area_uid) if current_area_uid else 0,
                        format_func=lambda x: x[0],
                        key="new_task_area"
                    )
                    target_area_uid = sel_area_new_task[1]

                    # —— 循环设置（对齐旧版：repeat_rule + repeat_cycle） ——
                    st.markdown("#### 🔁 循环设置")
                    repeat_mode = st.selectbox(
                        "循环频率",
                        ["不重复", "每天", "每周", "每N周", "每月", "每N月"],
                        index=0,
                        help="选择循环频率；选择周/每N周时可指定星期几；N 表示每 N 周或每 N 个月",
                        key="new_task_repeat_mode",
                    )
                    byweekday = []
                    if repeat_mode in ("每周", "每N周"):
                        wk_labels = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
                        wk_map = {wk_labels[i]: i for i in range(7)}
                        byweekday_labels = st.multiselect(
                            "重复的星期",
                            wk_labels,
                            default=[],
                            key="new_task_byweekday",
                            help="可多选；仅对每周/每N周生效"
                        )
                        byweekday = [wk_map[x] for x in byweekday_labels]
                    # 间隔（N）：仅在“每N周/每N月”时出现；默认 2
                    if repeat_mode in ("每N周", "每N月"):
                        interval = int(st.number_input(
                            "间隔（N）", min_value=1, max_value=52, value=2, step=1,
                            key="new_task_interval", help="每 N 周或每 N 个月"
                        ))
                    else:
                        interval = 1
                    repeat_rule = None
                    if repeat_mode != "不重复":
                        _freq_map = {"每天": "daily", "每周": "weekly", "每N周": "weekly", "每月": "monthly", "每N月": "monthly"}
                        repeat_rule = {"freq": _freq_map[repeat_mode], "interval": interval}
                        if repeat_mode in ("每周", "每N周"):
                            repeat_rule["byweekday"] = byweekday or []
                    repeat_cycle_compat = {"每天":"每天","每周":"每周","每N周":"每周","每月":"每月","每N月":"每月"}.get(repeat_mode, "不重复")

                    # —— 完成标准（简化编辑：每行一条） ——
                    st.markdown("#### 📋 完成标准 (Acceptance Criteria)")
                    ac_text = st.text_area("每行一条", value="", key="new_task_ac_text", placeholder="例：\n- 提交初稿\n- 通过导师审阅")
                    acceptance_criteria_list = [x.strip("- ") for x in ac_text.splitlines() if x.strip()]

                    # 时间与时长
                    due_val = st.date_input("截止日期", value=date.today(), key="new_task_due")
                    start_time_val = st.time_input("开始时间", value=datetime.strptime("08:00", "%H:%M").time(), key="new_task_time")
                    dur_str = st.text_input("时长（如 90min / 2h）", value="2h", key="new_task_dur")
                    # 循环截止日期（仅当设置了循环时出现）
                    loop_due_val = None
                    if repeat_mode != "不重复":
                        loop_due_val = st.date_input(
                            "循环截止日期",
                            value=None,
                            key="new_task_loop_due",
                            help="仅对循环任务：每个实例的截止日期（可选）。若不填，将不写入该字段。"
                        )
                        st.caption("提示：循环任务的“截止日期”字段将作为循环开始日期使用。")

                    if st.button("创建 Task", type="primary", key="btn_create_task"):
                        if not t_title.strip():
                            st.warning("请填写标题")
                        elif not target_proj_uid:
                            st.warning("请选择归属 Project")
                        else:
                            created = _create_task_api(
                                title=t_title,
                                desc=t_desc,
                                status=t_status,
                                due_date=due_val,
                                start_time=start_time_val,
                                duration=dur_str,
                                project_uid=target_proj_uid,
                                objective_uid=target_obj_uid,
                                area_uid=target_area_uid,
                                acceptance_criteria=acceptance_criteria_list,
                            )
                            # 将新建任务的循环设置按旧逻辑立即覆盖（若用户在创建时就设置了重复）
                            if repeat_mode != "不重复":
                                p_task_new = Path(created["path"])
                                _write_fields(
                                    p_task_new,
                                    repeat_rule=repeat_rule,
                                    repeat_cycle=repeat_cycle_compat,
                                    repeat_due=(str(loop_due_val) if loop_due_val else "")
                                )
                            st.success("✅ 已创建 Task")
                            st.session_state["preselect_task_uid"] = created["uid"]
                            st.rerun()

                # 编辑 Task
                elif current_task_uid:
                    data_task = copy.deepcopy(task_by_uid.get(current_task_uid, {}))
                    st.markdown(f"### 编辑 Task: {current_task_uid} (UID)")
                    p_task = task_path.get(current_task_uid) or (Path(data_task.get("__path")) if data_task.get("__path") else None)
                    # —— Per-task widget keys to avoid state collision when switching fast ——
                    title_key  = f"edit_task_title_{current_task_uid}"
                    desc_key   = f"edit_task_desc_{current_task_uid}"
                    status_key = f"edit_task_status_{current_task_uid}"
                    due_key    = f"edit_task_due_{current_task_uid}"
                    time_key   = f"edit_task_time_{current_task_uid}"
                    dur_key    = f"edit_task_dur_{current_task_uid}"

                    # 记录上一次状态（用于检测向 done 的跳变）
                    prev_status_key = f"prev_task_status_{current_task_uid}"
                    if prev_status_key not in st.session_state:
                        st.session_state[prev_status_key] = (data_task.get("status") or "todo")

                    # 归属 Project/Objective/Area 选择器
                    def _proj_label_edit_task(p):
                        return f"{p.get('title', p.get('uid'))} [{p.get('uid','')}]"
                    proj_opts_edit = [(_proj_label_edit_task(p), p.get('uid')) for p in projs]
                    cur_proj_uid_t = data_task.get("project_uid")
                    sel_proj_edit = st.selectbox(
                        "归属 Project",
                        [("(留空)", None)] + proj_opts_edit,
                        index=_index_in_labeled_opts_by_uid([("(留空)", None)] + proj_opts_edit, cur_proj_uid_t),
                        format_func=lambda x: x[0],
                        key="edit_task_proj"
                    )

                    def _obj_label_edit_task(o):
                        return f"{o.get('title', o.get('uid'))} [{o.get('uid','')}]"
                    obj_opts_edit = [(_obj_label_edit_task(o), o.get('uid')) for o in objs]
                    cur_obj_uid_t = data_task.get("objective_uid")
                    sel_obj_edit = st.selectbox(
                        "归属 Objective",
                        [("(留空)", None)] + obj_opts_edit,
                        index=_index_in_labeled_opts_by_uid([("(留空)", None)] + obj_opts_edit, cur_obj_uid_t),
                        format_func=lambda x: x[0],
                        key="edit_task_obj"
                    )

                    def _area_label_edit_task(a):
                        return f"{a.get('title', a.get('uid'))} [{a.get('uid','')}]"
                    area_opts_edit = [(_area_label_edit_task(a), a.get('uid')) for a in areas]
                    cur_area_uid_t = data_task.get("area_uid")
                    sel_area_edit = st.selectbox(
                        "归属 Area",
                        [("(留空)", None)] + area_opts_edit,
                        index=_index_in_labeled_opts_by_uid([("(留空)", None)] + area_opts_edit, cur_area_uid_t),
                        format_func=lambda x: x[0],
                        key="edit_task_area"
                    )

                    # 时间与时长
                    due_val = None
                    if data_task.get("due"):
                        try:
                            due_val = datetime.fromisoformat(data_task["due"]).date()
                        except Exception:
                            due_val = None
                    start_val = None
                    if data_task.get("start"):
                        try:
                            start_val = datetime.fromisoformat(data_task["start"])
                        except Exception:
                            start_val = None
                    if not start_val and due_val:
                        start_val = datetime.combine(due_val, datetime.strptime("08:00", "%H:%M").time())

                    def auto_save_task(_task_path=p_task, _title_key=title_key, _desc_key=desc_key, _status_key=status_key,
                                       _due_key=due_key, _time_key=time_key, _dur_key=dur_key,
                                       _proj_uid=sel_proj_edit[1], _obj_uid=sel_obj_edit[1], _area_uid=sel_area_edit[1],
                                       _task_uid=current_task_uid,
                                       _tlk=f"time_log_state_{current_task_uid}",
                                       _note_widget_key=f"time_log_note_{current_task_uid}",
                                       _repeat_due_key=f"edit_task_repeat_due_{current_task_uid}",
                                       _prev_status_key=None):
                        if not _task_path:
                            return
                        if _prev_status_key is None:
                            _prev_status_key = f"prev_task_status_{_task_uid}"
                        # 读取最新控件状态（按每个任务专属的 widget key 读取，避免串写）
                        new_title = st.session_state.get(_title_key, "")
                        new_desc  = st.session_state.get(_desc_key,  "")
                        new_status= (st.session_state.get(_status_key, "todo") or "").strip().lower()
                        new_due   = st.session_state.get(_due_key)
                        new_time  = st.session_state.get(_time_key)
                        new_dur_s = st.session_state.get(_dur_key, "2h")
                        # 计算 start/end
                        if isinstance(new_due, date) and new_time:
                            ns = datetime.combine(new_due, new_time)
                        elif isinstance(new_due, date):
                            ns = datetime.combine(new_due, datetime.strptime("08:00", "%H:%M").time())
                        else:
                            ns = None
                        dur_min = _parse_duration_to_minutes(new_dur_s, 120)
                        ne = (ns + timedelta(minutes=dur_min)) if ns else None
                        # 读取“循环截止日期”（仅循环任务使用；非循环时会被清除）
                        repeat_due_val = st.session_state.get(_repeat_due_key)
                        # 写回常规字段
                        _write_fields(
                            _task_path,
                            title=new_title,
                            desc=new_desc,
                            status=new_status,
                            due=new_due.isoformat() if isinstance(new_due, date) else "",
                            start=ns.isoformat() if ns else "",
                            end=ne.isoformat() if ne else "",
                            duration=new_dur_s,
                            project_uid=_proj_uid,
                            objective_uid=_obj_uid,
                            area_uid=_area_uid,
                            repeat_due=repeat_due_val.isoformat() if isinstance(repeat_due_val, date) else "",
                        )

                        # —— 若状态从非 done → done：自动完成处理 ——
                        prev = (st.session_state.get(_prev_status_key, "") or "").strip().lower()
                        transition_to_done = (prev != "done" and new_status == "done")
                        st.session_state[_prev_status_key] = new_status  # 更新“前状态”
                        if transition_to_done:
                            now = datetime.now()
                            # 读取 YAML 当前内容以确保日志合并准确
                            try:
                                cur_data = yaml.load(_task_path.read_text(encoding="utf-8")) or {}
                            except Exception:
                                cur_data = {}
                            logs_old = cur_data.get("time_logs", [])
                            if not isinstance(logs_old, list):
                                logs_old = []
                            # 处理 active 计时 → 结算为一条日志
                            start_dt = None
                            note_val = st.session_state.get(_note_widget_key, "")
                            # 先看会话态
                            _st = st.session_state.get(_tlk, {})
                            st_start = _st.get("start")
                            if isinstance(st_start, datetime):
                                start_dt = st_start
                            elif isinstance(st_start, str) and st_start:
                                try:
                                    start_dt = datetime.fromisoformat(st_start)
                                except Exception:
                                    start_dt = None
                            # 再看文件里的 active
                            if not start_dt:
                                act = cur_data.get("time_log_active") if isinstance(cur_data.get("time_log_active"), dict) else None
                                if act and isinstance(act.get("start"), str):
                                    try:
                                        start_dt = datetime.fromisoformat(act["start"])
                                    except Exception:
                                        start_dt = None
                                if not note_val and act:
                                    note_val = act.get("note", "")
                            # 若存在开始时间，则结算此段
                            if start_dt:
                                logs_old = list(logs_old) + [{
                                    "start": start_dt.isoformat(timespec="seconds"),
                                    "end": now.isoformat(timespec="seconds"),
                                    "note": note_val or "",
                                }]
                            # 计算累计分钟数
                            total_min = 0
                            for l in logs_old:
                                try:
                                    s = datetime.fromisoformat(str(l.get("start")))
                                    e = datetime.fromisoformat(str(l.get("end")))
                                    total_min += int((e - s).total_seconds() // 60)
                                except Exception:
                                    pass
                            # 写回完成信息、日志与清理 active
                            _write_fields(
                                _task_path,
                                time_logs=logs_old,
                                time_log_active=None,
                                time_log_note_draft="",
                                completed_at=now.isoformat(timespec="seconds"),
                                time_spent_min=total_min,
                            )
                            # 完成标准提示（不强制）
                            ac = cur_data.get("acceptance_criteria", [])
                            if isinstance(ac, list):
                                incomplete = any((not str(x).strip()) for x in ac)
                                if incomplete:
                                    st.toast("ℹ️ 已完成，但存在空的完成标准条目（可在完成标准处补充）")
                        st.toast("✅ 已自动保存 Task")

                    st.text_input("标题", value=data_task.get("title", ""), key=title_key, on_change=auto_save_task)
                    st.text_area("描述", value=data_task.get("desc", ""), key=desc_key, on_change=auto_save_task)
                    st.selectbox("状态", ["todo", "in_progress", "done"], index=["todo","in_progress","done"].index(data_task.get("status","todo")), key=status_key, on_change=auto_save_task)
                    st.date_input("截止日期", value=due_val, key=due_key, on_change=auto_save_task)
                    st.time_input("开始时间", value=(start_val.time() if start_val else datetime.strptime("08:00","%H:%M").time()), key=time_key, on_change=auto_save_task)
                    st.text_input("时长（如 90min / 2h）", value=data_task.get("duration","2h"), key=dur_key, on_change=auto_save_task)
                    st.markdown("---")
                    if st.button("🗑 删除该 Task", type="secondary", key=f"btn_del_task_{current_task_uid}"):
                        _delete_entity(current_task_uid, "task")
                        st.success("已删除该任务")
                        st.rerun()
                    # —— 循环设置（读取现状 → 编辑 → 保存）
                    st.markdown("#### 🔁 循环设置")
                    rr = data_task.get("repeat_rule") if isinstance(data_task.get("repeat_rule"), dict) else None
                    rc = data_task.get("repeat_cycle") or "不重复"
                    def _auto_save_repeat(_p=p_task, _task_uid=current_task_uid):
                        if not _p:
                            return
                        # 取控件值
                        mode = st.session_state.get(f"repeat_mode_edit_{_task_uid}", "不重复")
                        labels = st.session_state.get(f"byweekday_edit_{_task_uid}", []) or []
                        interval_val = st.session_state.get(f"interval_edit_{_task_uid}", 1)
                        try:
                            interval = int(interval_val or 1)
                        except Exception:
                            interval = 1

                        # 周几 → 索引 0-6
                        wk_labels = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
                        wk_map = {wk_labels[i]: i for i in range(7)}
                        byweekday = []
                        for x in labels:
                            if isinstance(x, int):
                                byweekday.append(x)
                            elif isinstance(x, str) and x in wk_map:
                                byweekday.append(wk_map[x])

                        # 组装 repeat_rule / repeat_cycle
                        repeat_rule_new = None
                        repeat_cycle_new = "不重复"
                        if mode != "不重复":
                            _freq_map = {"每天": "daily", "每周": "weekly", "每N周": "weekly", "每月": "monthly", "每N月": "monthly"}
                            repeat_rule_new = {"freq": _freq_map.get(mode, "daily"), "interval": interval}
                            if mode in ("每周", "每N周"):
                                repeat_rule_new["byweekday"] = byweekday
                            repeat_cycle_new = {"每天":"每天","每周":"每周","每N周":"每周","每月":"每月","每N月":"每月"}.get(mode, "不重复")

                        _write_fields(_p, repeat_rule=repeat_rule_new, repeat_cycle=repeat_cycle_new)
                        st.toast("✅ 循环设置已保存")
                    def _default_repeat_from(rr, rc, due):
                        if isinstance(rr, dict) and rr.get("freq"):
                            f = rr.get("freq")
                            itv = int(rr.get("interval", 1) or 1)
                            wk = rr.get("byweekday", []) if isinstance(rr.get("byweekday", []), list) else []
                            if f == "daily": return "每天", wk, itv
                            if f == "weekly": return ("每N周" if itv and itv > 1 else "每周"), wk, itv
                            if f == "monthly": return ("每N月" if itv and itv > 1 else "每月"), [], itv
                        # fallback from repeat_cycle
                        if rc == "每天":
                            return "每天", [], 1
                        if rc in ("每周", "每N周"):
                            # 默认选截止日或今天对应的星期
                            base_d = None
                            try:
                                base_d = date.fromisoformat(str(due)) if due else date.today()
                            except Exception:
                                base_d = date.today()
                            return "每周", [base_d.weekday()], 1
                        if rc in ("每月", "每N月"):
                            return "每月", [], 1
                        return "不重复", [], 1
                    _mode0, _wk0, _int0 = _default_repeat_from(rr, rc, data_task.get("due"))

                    repeat_mode_e = st.selectbox(
                        "循环频率",
                        ["不重复", "每天", "每周", "每N周", "每月", "每N月"],
                        index=["不重复", "每天", "每周", "每N周", "每月", "每N月"].index(_mode0),
                        key=f"repeat_mode_edit_{current_task_uid}",
                        help="选择循环频率；选择周/每N周时可指定星期几；N 表示每 N 周或每 N 个月",
                        on_change=_auto_save_repeat,
                    )
                    byweekday_e = []
                    if repeat_mode_e in ("每周", "每N周"):
                        wk_labels = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
                        wk_map = {wk_labels[i]: i for i in range(7)}
                        default_labels = [wk_labels[i] for i in (_wk0 or [])]
                        byweekday_labels_e = st.multiselect(
                            "重复的星期",
                            wk_labels,
                            default=default_labels,
                            key=f"byweekday_edit_{current_task_uid}",
                            on_change=_auto_save_repeat,
                        )
                        byweekday_e = [wk_map[x] for x in byweekday_labels_e]
                    interval_e = 1
                    if repeat_mode_e in ("每N周", "每N月"):
                        interval_e = int(st.number_input(
                            "间隔（N）", min_value=1, max_value=52, value=_int0 if _mode0 in ("每N周", "每N月") else 2, step=1,
                            key=f"interval_edit_{current_task_uid}", help="每 N 周或每 N 个月"
                        ))
                    repeat_rule_new = None
                    if repeat_mode_e != "不重复":
                        _freq_map = {"每天": "daily", "每周": "weekly", "每N周": "weekly", "每月": "monthly", "每N月": "monthly"}
                        repeat_rule_new = {"freq": _freq_map[repeat_mode_e], "interval": interval_e}
                        if repeat_mode_e in ("每周", "每N周"):
                            repeat_rule_new["byweekday"] = byweekday_e or (_wk0 or [])
                    repeat_cycle_new = {"每天":"每天","每周":"每周","每N周":"每周","每月":"每月","每N月":"每月"}.get(repeat_mode_e, "不重复")
                    # 循环截止日期（仅当有循环设置时显示；自动保存）
                    rep_due_val = None
                    if data_task.get("repeat_due"):
                        try:
                            rep_due_val = date.fromisoformat(str(data_task["repeat_due"]))
                        except Exception:
                            rep_due_val = None
                    is_repeating_edit = (repeat_mode_e != "不重复") or ((isinstance(rr, dict) and rr.get("freq")) or (rc and rc != "不重复"))
                    if is_repeating_edit:
                        st.date_input(
                            "循环截止日期",
                            value=rep_due_val,
                            key=f"edit_task_repeat_due_{current_task_uid}",
                            on_change=auto_save_task,
                            help="仅对循环任务：每个实例的截止日期（可选）。"
                        )
                        st.caption("提示：循环任务的“截止日期”字段作为循环开始日期使用。")


                    # —— 完成标准（每行一条，保存为列表） ——
                    st.markdown("#### 📋 完成标准 (Acceptance Criteria)")
                    ac_list_cur = data_task.get("acceptance_criteria", [])
                    if not isinstance(ac_list_cur, list):
                        ac_list_cur = []
                    ac_text_e = "\n".join([str(x) for x in ac_list_cur])
                    def _autosave_ac(_p=p_task, _key=f"ac_text_edit_{current_task_uid}"):
                        txt = st.session_state.get(_key, "")
                        lines = [x.strip("- ") for x in txt.splitlines() if x.strip()]
                        if _p:
                            _write_fields(_p, acceptance_criteria=lines)
                            st.toast("✅ 完成标准已自动保存")
                    ac_text_e = st.text_area(
                        "每行一条",
                        value=ac_text_e,
                        key=f"ac_text_edit_{current_task_uid}",
                        on_change=_autosave_ac,
                    )

                    # —— 报告/成果（与 aopt 系统关联：保存到 YAML: deliverable[]） ——
                    st.markdown("#### 📎 报告/成果")

                    # 读取现有 deliverable 列表（向后兼容：deliverables）
                    _deliv_list = data_task.get("deliverable")
                    if not isinstance(_deliv_list, list):
                        _deliv_list = data_task.get("deliverables") if isinstance(data_task.get("deliverables"), list) else []

                    # 显示已关联的报告/成果
                    if _deliv_list:
                        for i, it in enumerate(_deliv_list):
                            _title = (it.get("title") or it.get("path") or it.get("url") or f"报告 {i+1}")
                            _path  = it.get("path")
                            _url   = it.get("url")
                            cols = st.columns([6, 4, 2])
                            with cols[0]:
                                st.write(f"• {_title}")
                                if _path:
                                    st.code(_path)
                            with cols[1]:
                                if _url:
                                    st.markdown(f"[打开链接]({_url})")
                            # 若不需要在此支持删除按钮，可省略 cols[2]
                    # 方式二：手动关联已有文件/云端链接（例如 OneDrive/SharePoint/URL）
                    with st.expander("手动关联已有文件/链接"):
                        _rep_title = st.text_input("标题（可选）", value="", key=f"report_title_{current_task_uid}")
                        _rep_path  = st.text_input("本地/云盘路径（可相对仓库根目录）", value="", key=f"report_path_{current_task_uid}")
                        _rep_url   = st.text_input("外部链接 URL（可选）", value="", key=f"report_url_{current_task_uid}")
                        if st.button("➕ 关联报告", key=f"btn_link_report_{current_task_uid}"):
                            item = {
                                "kind": "report",
                                "title": _rep_title.strip() or (Path(_rep_path).name if _rep_path.strip() else (_rep_url.strip() or "报告")),
                                "created_at": datetime.now().isoformat(timespec="seconds"),
                            }
                            if _rep_path.strip():
                                _p = Path(_rep_path.strip())
                                if not _p.is_absolute():
                                    _p = (root / _p).resolve()
                                try:
                                    item["path"] = str(_p.relative_to(root))
                                except Exception:
                                    item["path"] = str(_p)
                            if _rep_url.strip():
                                item["url"] = _rep_url.strip()
                            _write_fields(p_task, deliverable=(_deliv_list + [item]))
                            st.toast("✅ 已关联报告")
                            st.rerun()

                    # —— 时间日志（start / stop，追加保存到 time_logs） ——
                    st.markdown("#### ⏱️ 时间日志")
                    time_log_key = f"time_log_state_{current_task_uid}"
                    active_log = data_task.get("time_log_active") if isinstance(data_task.get("time_log_active"), dict) else None
                    active_start_dt = None
                    if active_log and isinstance(active_log.get("start"), str):
                        try:
                            active_start_dt = datetime.fromisoformat(active_log["start"])  # seconds precision expected
                        except Exception:
                            active_start_dt = None
                    if time_log_key not in st.session_state:
                        st.session_state[time_log_key] = {
                            "recording": bool(active_log),
                            "start": active_start_dt,
                            "note": (active_log.get("note", "") if active_log else ""),
                        }
                    else:
                        # 若 YAML 中存在进行中记录，则与之对齐
                        if active_log:
                            st.session_state[time_log_key]["recording"] = True
                            if active_start_dt:
                                st.session_state[time_log_key]["start"] = active_start_dt
                            st.session_state[time_log_key]["note"] = active_log.get("note", st.session_state[time_log_key].get("note", ""))
                    # 1. 控制按钮
                    cols_btn = st.columns(2)
                    with cols_btn[0]:
                        if not st.session_state[time_log_key]["recording"]:
                            if st.button("▶️ 开始记录", key=f"btn_time_start_{current_task_uid}"):
                                _now = datetime.now()
                                st.session_state[time_log_key]["recording"] = True
                                st.session_state[time_log_key]["start"] = _now
                                st.session_state[time_log_key]["note"] = ""
                                st.toast("⏱️ 已开始时间记录")
                                # 清除任何之前的草稿备注
                                st.session_state.pop(f"time_log_note_{current_task_uid}", None)
                                if p_task:
                                    try:
                                        _write_fields(
                                            p_task,
                                            time_log_note_draft="",
                                            time_log_active={"start": _now.isoformat(timespec="seconds"), "note": ""},
                                        )
                                    except Exception:
                                        pass
                        else:
                            st.info(
                                f"已开始于 {st.session_state[time_log_key]['start'].strftime('%Y-%m-%d %H:%M:%S')}"
                                if st.session_state[time_log_key]['start'] else "已开始"
                            )
                    with cols_btn[1]:
                        if st.session_state[time_log_key]["recording"]:
                            if st.button("⏹ 结束并保存", key=f"btn_time_stop_{current_task_uid}"):
                                start_at = st.session_state[time_log_key]["start"] or datetime.now()
                                end_at = datetime.now()
                                widget_note_key = f"time_log_note_{current_task_uid}"
                                note = st.session_state.get(widget_note_key, st.session_state[time_log_key].get("note", "")) or ""
                                # 读取旧 logs
                                logs_old = data_task.get("time_logs", [])
                                if not isinstance(logs_old, list):
                                    logs_old = []
                                logs_to_save = list(logs_old) + [{
                                    "start": start_at.isoformat(timespec="seconds"),
                                    "end": end_at.isoformat(timespec="seconds"),
                                    "note": note,
                                }]
                                if p_task:
                                    _write_fields(p_task, time_logs=logs_to_save, time_log_active=None, time_log_note_draft="")
                                st.session_state.pop(widget_note_key, None)
                                st.session_state[time_log_key] = {"recording": False, "start": None, "note": ""}
                                st.success("✅ 时间日志已保存")
                                st.rerun()
                    # 2. 备注输入
                    if st.session_state[time_log_key]["recording"]:
                        def _autosave_time_note(_p=p_task, _uid=current_task_uid, _tlk=time_log_key):
                            _note_key = f"time_log_note_{_uid}"
                            _note_val = st.session_state.get(_note_key, "")
                            if _p:
                                # 取进行中开始时间（若存在）以便同步到 YAML 的 active 记录
                                _start_dt = st.session_state.get(_tlk, {}).get("start")
                                _start_iso = None
                                try:
                                    if isinstance(_start_dt, datetime):
                                        _start_iso = _start_dt.isoformat(timespec="seconds")
                                    elif isinstance(_start_dt, str) and _start_dt:
                                        _start_iso = datetime.fromisoformat(_start_dt).isoformat(timespec="seconds")
                                except Exception:
                                    _start_iso = None
                                updates = {"time_log_note_draft": _note_val}
                                if _start_iso:
                                    updates["time_log_active"] = {"start": _start_iso, "note": _note_val}
                                try:
                                    _write_fields(_p, **updates)
                                except Exception:
                                    pass
                            # 同步到嵌套状态，便于其它位置读取
                            st.session_state[_tlk]["note"] = _note_val

                        st.session_state[time_log_key]["note"] = st.text_area(
                            "备注（可选）",
                            value=st.session_state[time_log_key]["note"],
                            key=f"time_log_note_{current_task_uid}",
                            on_change=_autosave_time_note,
                        )
                    # 3. 历史日志列表
                    active_now = data_task.get("time_log_active") if isinstance(data_task.get("time_log_active"), dict) else None
                    if active_now:
                        st.caption("进行中：")
                        _s = active_now.get("start", "")
                        _n = active_now.get("note", "")
                        st.text(f"• {_s} → (进行中)  {_n}")
                    logs = data_task.get("time_logs", [])
                    st.markdown("##### 历史时间日志")
                    if logs:
                        if isinstance(logs[0], str):
                            for i, l in enumerate(logs):
                                st.text(f"{i+1}. {l}")
                        else:
                            for i, l in enumerate(logs):
                                s_ = l.get("start", "")
                                e_ = l.get("end", "")
                                n_ = l.get("note", "")
                                st.text(f"{i+1}. {s_} → {e_}  {n_}")
                    else:
                        st.caption("暂无时间日志")

                    # —— 交付物（当状态为 done 时显示；简单文本列表版） ——
                    _cur_status = (st.session_state.get(status_key, data_task.get("status", "")) or "").strip().lower()
                    if _cur_status == "done":
                        st.markdown("#### 📦 交付物 (Deliverables)")
                        delivs = data_task.get("deliverable", [])
                        if not isinstance(delivs, list):
                            delivs = []
                        deliv_text = "\n".join([ (d.get("name") if isinstance(d, dict) else str(d)) for d in delivs ])
                        deliv_text = st.text_area("每行一个交付物名称（简化版）", value=deliv_text, key=f"deliverable_text_{current_task_uid}")
                        if st.button("💾 保存交付物", key=f"btn_save_deliverable_{current_task_uid}"):
                            names = [x.strip() for x in deliv_text.splitlines() if x.strip()]
                            to_save = [{"name": n, "delivered_at": datetime.now().isoformat(timespec="seconds") } for n in names]
                            _write_fields(p_task, deliverable=to_save)
                            st.toast("✅ 交付物已保存")
                            st.rerun()

        # —— 插件二：🧮 从模板生成任务流
        cur_proj_uid = current_proj_uid
        st.markdown("---")
        st.markdown("### 🧮 从模板生成任务流")
        flows_dir = root / "flows"
        if not flows_dir.exists():
            st.caption("未检测到 flows/ 目录，可创建并放置模板 YAML 后使用。示例结构：\n\n- title: 步骤A\n  offset_days: 0\n  duration: 2h\n- title: 步骤B\n  offset_days: 2\n  duration: 1h")
        else:
            flow_files = list(flows_dir.glob("*.y*ml"))
            if not flow_files:
                st.caption("flows/ 目录为空。")
            else:
                flow_map = {f.name: f for f in sorted(flow_files)}
                pick_name = st.selectbox("选择模板", list(flow_map.keys()), key=f"pick_flow_{cur_proj_uid}")
                start_date = st.date_input("起始日期", value=date.today(), key=f"flow_start_{cur_proj_uid}")
                prefix = st.text_input("任务标题前缀（可选）", value="", key=f"flow_prefix_{cur_proj_uid}")
                if st.button("生成任务", type="primary", key=f"btn_gen_flow_{cur_proj_uid}"):
                    try:
                        tpl = yaml.load(flow_map[pick_name].read_text(encoding="utf-8"))
                        steps = tpl.get("tasks") if isinstance(tpl, dict) else tpl
                        if not isinstance(steps, list):
                            st.warning("模板格式应为列表或包含 tasks 列表的字典。")
                        else:
                            created_uids = []
                            from datetime import time as _time
                            for step in steps:
                                if not isinstance(step, dict):
                                    continue
                                t_title = f"{prefix}{step.get('title','新任务')}"
                                offset = int(step.get("offset_days", 0))
                                dur_s  = step.get("duration", "2h")
                                desc_s = step.get("desc", "")
                                # 约定：模板的到期时间固定为 09:00
                                due_base = datetime.combine(start_date, datetime.min.time()) + timedelta(days=offset)
                                created = _create_task_api(
                                    title=t_title,
                                    desc=desc_s,
                                    status="todo",
                                    due_date=due_base.date(),
                                    start_time=_time(hour=9, minute=0),
                                    duration=dur_s,
                                    project_uid=cur_proj_uid,
                                    objective_uid=current_obj_uid,
                                    area_uid=current_area_uid,
                                    acceptance_criteria=[],
                                )
                                created_uids.append(created["uid"])
                            st.success(f"✅ 已生成 {len(created_uids)} 条任务")
                            st.rerun()
                    except Exception as e:
                        st.warning(f"读取模板失败：{e}")

            # === 📅 汇总日历视图（多层级） ===
            from datetime import date  # 文件顶部已经有的话可忽略

            # —— 初始视图控制（默认本月/本周/今天）——
            default_view_choice = st.session_state.get("calendar_default_view_choice", "本月")
            default_view_choice = st.radio(
                "初始视图", ["本月", "本周", "今天"],
                index=["本月","本周","今天"].index(default_view_choice),
                horizontal=True,
                key="calendar_default_view_choice"
            )
            _view_map = {"本月": "dayGridMonth", "本周": "timeGridWeek", "今天": "timeGridDay"}
            _init_view = _view_map.get(default_view_choice, "dayGridMonth")
            _init_date = date.today().isoformat()  # 锚定今天，避免跳到最早任务

            # === 正式内容 ===
            st.markdown("---")
            st.markdown("## 📅 汇总日历视图（多层级）")
            agg_scope = st.radio(
                "显示范围",
                ["当前 Project", "当前 Objective", "当前 Area", "全部"],
                horizontal=True,
                key="agg_scope",
            )
            # 颜色分组依据 & 标题是否显示路径
            agg_color_basis = st.radio(
                "颜色分组依据",
                ["Project", "Objective", "Area"],
                horizontal=True,
                key="agg_color_basis",
                help="决定事件背景色按哪个层级分组。",
            )
            agg_show_path = st.checkbox(
                "在事件标题中显示层级来源（Area/Objective/Project）",
                value=True,
                key="agg_show_path",
            )
            # —— 一起拖动范围（联动同组任务移动） ——
            group_move_scope = st.selectbox(
                "一起拖动范围（拖动一个任务时，按所选范围联动同组任务移动）",
                ["关闭", "同 Project", "同 Objective", "同 Area"],
                index=0,
                key="group_move_scope",
                help="例如选择‘同 Area’，拖动任意该 Area 下的一个任务，会按照相同时间偏移联动该 Area 内所有（当前显示范围内的）任务。"
            )

            def _group_key_for_scope(t: dict, scope: str):
                if scope == "同 Project":
                    return t.get("project_uid")
                if scope == "同 Objective":
                    return t.get("objective_uid")
                if scope == "同 Area":
                    return t.get("area_uid")
                return None

            # 计算范围内任务
            sel_proj_uids: set[str] = set()
            sel_obj_uids: set[str] = set()
            sel_area_uids: set[str] = set()

            if agg_scope == "当前 Project" and current_proj_uid:
                sel_proj_uids = {current_proj_uid}
            elif agg_scope == "当前 Objective" and current_obj_uid:
                sel_obj_uids = {current_obj_uid}
            elif agg_scope == "当前 Area" and current_area_uid:
                sel_area_uids = {current_area_uid}
            elif agg_scope == "全部":
                pass
            else:
                st.info("请选择左侧层级，或切换到 ‘全部’。")

            def _in_scope(t: dict) -> bool:
                if sel_proj_uids:
                    return t.get("project_uid") in sel_proj_uids
                if sel_obj_uids:
                    return t.get("objective_uid") in sel_obj_uids
                if sel_area_uids:
                    return t.get("area_uid") in sel_area_uids
                return True  # 全部

            tasks_scope = [t for t in tasks if _in_scope(t)]
            # === 汇总视图辅助函数 ===
            def _group_uid_for(t: dict, basis: str = "Project") -> str | None:
                """按分组依据返回任务的上级 UID"""
                if basis == "Project":
                    return t.get("project_uid") or t.get("objective_uid") or t.get("area_uid")
                if basis == "Objective":
                    return t.get("objective_uid") or t.get("area_uid") or t.get("project_uid")
                if basis == "Area":
                    return t.get("area_uid") or t.get("objective_uid") or t.get("project_uid")
                return t.get("project_uid") or t.get("objective_uid") or t.get("area_uid")

            def _labels_for_task(t: dict) -> dict:
                """获取任务的层级标签名称"""
                au = t.get("area_uid")
                ou = t.get("objective_uid")
                pu = t.get("project_uid")
                area_t = area_by_uid.get(au, {}).get("title") if au else None
                obj_t  = obj_by_uid.get(ou, {}).get("title") if ou else None
                proj_t = proj_by_uid.get(pu, {}).get("title") if pu else None
                return {
                    "area_title": area_t or (au or ""),
                    "obj_title":  obj_t  or (ou or ""),
                    "proj_title": proj_t or (pu or ""),
                }
            # —— 渲染颜色图例，帮助识别任务来源 ——
            _legend = {}
            for t in tasks_scope:
                gid = _group_uid_for(t, st.session_state.get("agg_color_basis", "Project"))
                if not gid:
                    continue
                if gid not in _legend:
                    if st.session_state.get("agg_color_basis", "Project") == "Project":
                        lab = proj_by_uid.get(gid, {}).get("title") or gid
                    elif st.session_state.get("agg_color_basis") == "Objective":
                        lab = obj_by_uid.get(gid, {}).get("title") or gid
                    else:
                        lab = area_by_uid.get(gid, {}).get("title") or gid
                    _legend[gid] = {"label": lab, "color": _hex_color_from_uid(gid)}
            if _legend:
                chips = []
                for _gid, meta in _legend.items():
                    chips.append(
                        f"<span style='display:inline-block;margin:2px 6px;padding:2px 8px;border-radius:10px;background:{meta['color']};color:#fff;font-size:12px;'>■ {meta['label']}</span>"
                    )
                st.markdown("<div>" + " ".join(chips) + "</div>", unsafe_allow_html=True)
            # —— 循环任务展开（从 created_at 到 due，按 repeat_cycle 展开到多个日历事件） ——
            def _parse_date_any(s):
                if not s:
                    return None
                try:
                    return datetime.fromisoformat(str(s).replace("Z", "").split("+")[0]).date()
                except Exception:
                    try:
                        return date.fromisoformat(str(s))
                    except Exception:
                        return None

            def _add_months(d: date, months: int = 1) -> date:
                y = d.year + (d.month - 1 + months) // 12
                m = (d.month - 1 + months) % 12 + 1
                # 对齐到同日，若当月没有该日则取当月最后一天
                from calendar import monthrange
                last_day = monthrange(y, m)[1]
                day = min(d.day, last_day)
                return date(y, m, day)

            def _expand_repeats(t: dict, base_start_dt: datetime, dur_min: int, until_date: date):
                """返回 [(start_dt, end_dt), ...]。
                优先使用 repeat_rule（freq/interval/byweekday），无则回落到 repeat_cycle 旧字段。
                循环时间窗口：起始日优先使用 due（循环开始）；若无则 created_at；再无则 base_start_dt.date()。
                截止窗口优先使用 repeat_due（循环截止）；若无则由调用方传入的 until_date（或默认未来一年）。
                """
                def _occ(sdate: date, tpart, out_list: list):
                    sdt = datetime.combine(sdate, tpart)
                    edt = sdt + timedelta(minutes=dur_min)
                    out_list.append((sdt, edt))

                # 起始=due（优先）；否则 created_at；再否则 base_start_dt.date()
                start_date = _parse_date_any(t.get("due")) or _parse_date_any(t.get("created_at")) or base_start_dt.date()
                # 未设置截止窗口：默认视为“长期循环”，但为避免性能问题，限制到未来 12 个月
                if not until_date:
                    until_date = date.today() + timedelta(days=365)
                # 回溯限制：最多回溯 4 个月
                start_date = max(start_date, date.today() - timedelta(days=120))
                if start_date > until_date:
                    return [(base_start_dt, base_start_dt + timedelta(minutes=dur_min))]

                tpart = base_start_dt.time()
                out = []

                # —— 新规则：repeat_rule ——
                rule = t.get("repeat_rule") if isinstance(t.get("repeat_rule"), dict) else None
                if rule:
                    freq = (rule.get("freq") or "").strip()
                    interval = int(rule.get("interval") or 1)
                    if interval < 1:
                        interval = 1
                    byweekday = rule.get("byweekday") or []  # 仅对 weekly 生效，0=Mon..6=Sun

                    if freq == "daily":
                        cur = start_date
                        while cur <= until_date:
                            _occ(cur, tpart, out)
                            cur += timedelta(days=interval)
                        return out or [(base_start_dt, base_start_dt + timedelta(minutes=dur_min))]

                    if freq == "weekly":
                        cur = start_date
                        while cur <= until_date:
                            # 基于 start_date 的周序偏移计算：每 N 周生效
                            week_index = ((cur - start_date).days // 7)
                            if week_index % interval == 0:
                                if not byweekday or (cur.weekday() in byweekday):
                                    _occ(cur, tpart, out)
                            cur += timedelta(days=1)
                        return out or [(base_start_dt, base_start_dt + timedelta(minutes=dur_min))]

                    if freq == "monthly":
                        cur = start_date
                        while cur <= until_date:
                            _occ(cur, tpart, out)
                            cur = _add_months(cur, interval)
                        return out or [(base_start_dt, base_start_dt + timedelta(minutes=dur_min))]

                    # 未知 freq：回落旧逻辑

                # —— 旧逻辑：repeat_cycle ——
                cycle = (t.get("repeat_cycle") or "").strip()
                if not cycle or cycle == "不重复":
                    return [(base_start_dt, base_start_dt + timedelta(minutes=dur_min))]

                cur = start_date
                if cycle == "每天":
                    step = timedelta(days=1)
                    while cur <= until_date:
                        _occ(cur, tpart, out)
                        cur += step
                    return out or [(base_start_dt, base_start_dt + timedelta(minutes=dur_min))]
                if cycle == "每周":
                    while cur <= until_date:
                        _occ(cur, tpart, out)
                        cur += timedelta(days=7)
                    return out or [(base_start_dt, base_start_dt + timedelta(minutes=dur_min))]
                if cycle == "每月":
                    while cur <= until_date:
                        _occ(cur, tpart, out)
                        cur = _add_months(cur, 1)
                    return out or [(base_start_dt, base_start_dt + timedelta(minutes=dur_min))]

                # 兜底：不重复
                return [(base_start_dt, base_start_dt + timedelta(minutes=dur_min))]

            agg_events = []
            for t in tasks_scope:
                tuid = t.get("uid")
                if not tuid:
                    continue
                title = t.get("title", t.get("id", ""))
                due = t.get("due") or str(date.today())
                # 优先使用任务中保存的 start/end；否则回落到 due+默认时间 与 duration
                def _safe_parse_iso(dt_str: str):
                    try:
                        return datetime.fromisoformat(dt_str.replace("Z", "").split("+")[0])
                    except Exception:
                        return None

                start_dt = None
                if t.get("start"):
                    start_dt = _safe_parse_iso(t.get("start"))
                if not start_dt:
                    start_dt = _ensure_dt_from_due_and_time(due, default_hour=8)

                dur_min = _parse_duration_to_minutes(t.get("duration"), 120)
                if t.get("end"):
                    end_dt = _safe_parse_iso(t.get("end")) or (start_dt + timedelta(minutes=dur_min))
                else:
                    end_dt = start_dt + timedelta(minutes=dur_min)
                color_uid = _group_uid_for(t, st.session_state.get("agg_color_basis", "Project"))
                color = _hex_color_from_uid(color_uid) if color_uid else None
                # —— 根据循环设置展开多个实例（以 due 为开始，以 repeat_due 为截止窗口） ——
                until_d = _parse_date_any(t.get("repeat_due")) or (date.today() + timedelta(days=365))
                occs = _expand_repeats(t, start_dt, dur_min, until_d)
                for idx, (sdt_i, edt_i) in enumerate(occs):
                    ev = {
                        "id": f"{tuid}:{sdt_i.date().isoformat()}",  # 唯一 ID，便于渲染多个实例
                        "title": title,
                        "start": sdt_i.isoformat(),
                        "end": edt_i.isoformat(),
                        "editable": True,
                        "allDay": False,
                        "display": "block",
                        "extendedProps": {
                            "task_uid": tuid,
                            "project_uid": t.get("project_uid"),
                            "objective_uid": t.get("objective_uid"),
                            "area_uid": t.get("area_uid"),
                            "group_uid": color_uid,
                        },
                    }
                    if color:
                        ev["backgroundColor"] = color
                        ev["borderColor"] = color
                    agg_events.append(ev)

            agg_opts = {
                "initialView": st.session_state.get("calendar_initial_view", "dayGridMonth"),
                "initialDate": _init_date,
                "editable": True,
                "eventStartEditable": True,
                "eventDurationEditable": True,
                "selectable": True,
                # Let calendar grow to natural height (no inner scroll)
                "height": "auto",
                # Do not collapse events into "+N"
                "dayMaxEvents": False,
                "dayMaxEventRows": False,
                "handleWindowResize": True,
                "locale": "zh-cn",
                "headerToolbar": {
                    "left": "prev,next today",
                    "center": "title",
                    "right": "dayGridMonth,timeGridWeek,timeGridDay",
                },
            }
            if agg_events and not agg_opts.get("initialDate"):
                try:
                    agg_opts["initialDate"] = min(e["start"] for e in agg_events)
                except Exception:
                    pass

        # —— 事件后处理：补充状态/进行中标记，并按“显示已完成”过滤 ——
        def _postprocess_event_list(_events):
            def _pp(ev):
                # 优先从 extendedProps 取 uid；若无则从 id 拆分出 uid（形如 uid:YYYY-MM-DD 或 uid@YYYY-MM-DD）
                ext = ev.get("extendedProps") or {}
                tuid = ext.get("task_uid")
                if not tuid:
                    raw_id = ev.get("id")
                    if isinstance(raw_id, str):
                        tuid = raw_id.split(":", 1)[0].split("@", 1)[0]
                t = task_by_uid.get(tuid) if isinstance(tuid, str) else None
                stt = ((t or {}).get("status", "") or "").strip().lower()
                running = isinstance((t or {}).get("time_log_active"), dict)

                # —— 判定是否为循环任务：存在 repeat_rule.freq 或 repeat_cycle != "不重复"
                is_repeating = False
                if isinstance(t, dict):
                    rr = t.get("repeat_rule")
                    rc = (t.get("repeat_cycle") or "").strip()
                    is_repeating = (isinstance(rr, dict) and bool(rr.get("freq"))) or (rc and rc != "不重复")

                # —— 统一 start/end 校正（仅在缺失时补齐；不再用 repeat_due 拉长事件）
                try:
                    from datetime import datetime as _dt, time as _time, timedelta as _td, date as _date
                    def _parse_dt_or_date(s):
                        if not s:
                            return None
                        s = str(s)
                        try:
                            return _dt.fromisoformat(s)         # datetime
                        except Exception:
                            try:
                                d = _date.fromisoformat(s)      # date → 08:00
                                return _dt.combine(d, _time(8, 0))
                            except Exception:
                                return None
                    # 读取当前事件自带的起止
                    ev_start_dt = None
                    ev_end_dt = None
                    try:
                        if ev.get("start"):
                            ev_start_dt = _dt.fromisoformat(str(ev.get("start")).replace("Z", "").split("+")[0])
                    except Exception:
                        ev_start_dt = None
                    try:
                        if ev.get("end"):
                            ev_end_dt = _dt.fromisoformat(str(ev.get("end")).replace("Z", "").split("+")[0])
                    except Exception:
                        ev_end_dt = None
                    # 若缺失，则用任务字段+duration 补齐
                    if (ev_start_dt is None) or (ev_end_dt is None):
                        t_due_dt   = _parse_dt_or_date((t or {}).get("due"))
                        t_start_dt = _parse_dt_or_date((t or {}).get("start"))
                        t_end_dt   = _parse_dt_or_date((t or {}).get("end"))
                        if ev_start_dt is None:
                            ev_start_dt = t_start_dt or t_due_dt
                        if ev_end_dt is None:
                            if t_end_dt and ev_start_dt:
                                ev_end_dt = t_end_dt
                            elif ev_start_dt and isinstance((t or {}).get("duration"), str):
                                try:
                                    _mins = _parse_duration_to_minutes((t or {}).get("duration"), 120)
                                    ev_end_dt = ev_start_dt + _td(minutes=_mins)
                                except Exception:
                                    ev_end_dt = None
                    if ev_start_dt:
                        ev["start"] = ev_start_dt.isoformat()
                    if ev_end_dt:
                        ev["end"] = ev_end_dt.isoformat()
                except Exception:
                    pass

                # —— 循环任务的历史实例：在今天之前一律按 done 显示（仅用于视图，不写回 YAML）
                ev_date = None
                try:
                    from datetime import datetime as _dt, date as _date
                    ev_date = _dt.fromisoformat(str(ev.get("start")).replace("Z", "").split("+")[0]).date()
                except Exception:
                    ev_date = None
                stt_effective = stt
                try:
                    if is_repeating and ev_date and ev_date < _date.today():
                        stt_effective = "done"
                except Exception:
                    pass

                # 标注进行中
                if running and "⏺" not in (ev.get("title") or ""):
                    ev["title"] = f"{ev.get('title','')} ⏺"

                # extendedProps 补齐（透传 due/repeat_due，便于前端/回调判断）
                ex = ev.get("extendedProps") or {}
                if tuid:
                    ex["task_uid"] = tuid
                if isinstance(t, dict):
                    if "repeat_due" in t:
                        ex["repeat_due"] = t.get("repeat_due")
                    ex["due"] = t.get("due")
                    ex["is_repeating"] = bool(is_repeating)
                ex["status"] = stt_effective
                ev["extendedProps"] = ex

                # 已完成/取消 → 浅灰弱化
                if stt_effective in ("done", "canceled"):
                    ev["backgroundColor"] = "#c8c8c8"
                    ev["borderColor"] = "#c8c8c8"
                    ev["textColor"] = "#333333"
                return ev
            return [_pp(e) for e in _events]

        agg_events = _postprocess_event_list(agg_events)
        agg_events = [e for e in agg_events if isinstance(e, dict)]


        # 默认隐藏已完成/已取消；勾选“显示已完成”后才展示
        if not st.session_state.get("show_done"):
            agg_events = [e for e in agg_events if (e.get("extendedProps", {}).get("status") not in ("done", "canceled"))]

        st.markdown("<div style='width: 100%;'>", unsafe_allow_html=True)
        agg_h_key = "agg_cal_height_px"
        est_agg_height = st.session_state.get(agg_h_key, 2000)
        agg_opts["height"] = est_agg_height
        st.session_state["calendar_click_context"] = "multi"
        try:
            cal_state_agg = st_calendar(
                events=agg_events,
                options=agg_opts,
                key=f"cal_aggregate_{cur_proj_uid}_{est_agg_height}",
                callbacks=["eventChange", "eventsSet", "eventClick", "dateClick"],
                height=est_agg_height,
            )
        except TypeError:
            cal_state_agg = st_calendar(
                events=agg_events,
                options=agg_opts,
                key=f"cal_aggregate_{cur_proj_uid}_{est_agg_height}",
                callbacks=["eventChange", "eventsSet", "eventClick", "dateClick"],
            )
        try:
            if isinstance(cal_state_agg, dict) and cal_state_agg.get("callback") == "eventsSet":
                view = cal_state_agg.get("eventsSet", {}).get("view", {})
                from datetime import datetime as _dt
                weeks = 6
                try:
                    cs = view.get("currentStart") or view.get("activeStart")
                    ce = view.get("currentEnd") or view.get("activeEnd")
                    if cs and ce:
                        d1 = _dt.fromisoformat(str(cs).replace("Z", "").split("+")[0]).date()
                        d2 = _dt.fromisoformat(str(ce).replace("Z", "").split("+")[0]).date()
                        days = max(1, (d2 - d1).days)
                        weeks = max(5, min(6, (days + 6) // 7))
                except Exception:
                    weeks = 6
                from collections import Counter
                day_counts = Counter(ev["start"][:10] for ev in agg_events if isinstance(ev.get("start"), str))
                max_ev = max(day_counts.values()) if day_counts else 1
                row_h = 50 + max_ev * 24
                new_h = 140 + weeks * row_h
                new_h = int(min(6000, max(900, new_h)))
                if new_h != st.session_state.get(agg_h_key):
                    st.session_state[agg_h_key] = new_h
                    st.rerun()
        except Exception:
            pass

        # —— 点击事件：打开任务编辑界面（仅多层级汇总视图） ——
        try:
            if isinstance(cal_state_agg, dict) and cal_state_agg.get("callback") == "eventClick" and cal_state_agg.get("eventClick"):
                ev = cal_state_agg["eventClick"].get("event", {})
                # 任务 UID：优先 extendedProps.task_uid；回退从 id 拆分（uid:YYYY-MM-DD 或 uid@YYYY-MM-DD）
                tuid = None
                ext = ev.get("extendedProps") or {}
                if isinstance(ext, dict):
                    tuid = ext.get("task_uid")
                if not tuid:
                    ev_id_raw = ev.get("id")
                    if isinstance(ev_id_raw, str):
                        tuid = ev_id_raw.split(":", 1)[0].split("@", 1)[0]
                if tuid:
                    t = task_by_uid.get(tuid)
                    if t:
                        st.session_state["preselect_area_uid"] = t.get("area_uid")
                        st.session_state["preselect_obj_uid"]  = t.get("objective_uid")
                        st.session_state["preselect_proj_uid"] = t.get("project_uid")
                        st.session_state["preselect_task_uid"] = t.get("uid")
                        st.session_state["op_mode_choice"] = "层级浏览编辑"
                        st.session_state["last_focus"] = "task"
                        st.toast("📝 已定位到任务编辑区")
                        st.rerun()
        except Exception:
            pass

        # —— 写回：汇总日历视图中的拖拽/拉伸 ——
        try:
            if isinstance(cal_state_agg, dict) and cal_state_agg.get("callback") == "eventChange" and cal_state_agg.get("eventChange"):
                ec = cal_state_agg["eventChange"]
                e  = ec.get("event", {}) if isinstance(ec, dict) else {}
                oe = ec.get("oldEvent", {}) if isinstance(ec, dict) else {}

                def _piso(s):
                    if not s:
                        return None
                    return datetime.fromisoformat(str(s).replace("Z", "").split("+")[0])

                # 新旧起止时间
                new_start = _piso(e.get("start") or e.get("startStr"))
                new_end   = _piso(e.get("end")   or e.get("endStr")   or (e.get("start") or e.get("startStr")))
                old_start = _piso(oe.get("start") or oe.get("startStr"))
                if not (new_start and old_start):
                    raise ValueError("缺少起始时间，无法写回")
                delta = new_start - old_start

                # 任务 UID：优先从 extendedProps 取；回退从 id 拆分（形如 uid:YYYY-MM-DD）
                ev_uid = (e.get("extendedProps") or {}).get("task_uid")
                if not ev_uid:
                    ev_id_raw = e.get("id")
                    if ev_id_raw:
                        ev_uid = ev_id_raw.split(":", 1)[0].split("@", 1)[0]
                if not ev_uid:
                    raise ValueError("无法识别任务 UID")
                # —— 去重防抖：同一拖拽在 rerun 期间可能重复触发 eventChange，导致多次平移 ——
                fp = f"{ev_uid}|{old_start.isoformat()}->{new_start.isoformat()}|{st.session_state.get('group_move_scope', '关闭')}"
                last_fp = st.session_state.get("agg_last_ec_fp")
                last_ts = st.session_state.get("agg_last_ec_ts", 0.0)
                now_ts  = time.time()
                is_dup = (last_fp == fp and (now_ts - last_ts) < 2.0)
                if is_dup:
                    st.toast("⏭️ 已忽略重复回调")
                else:
                    # ↓↓↓ 把下面“平移并写回”的逻辑整体缩进到这个 else 里面 ↓↓↓
                    dragged_task = task_by_uid.get(ev_uid)
                    if not dragged_task:
                        raise ValueError(f"未找到任务: {ev_uid}")

                    def _shift_and_write(t: dict, _delta: timedelta):
                        # 读取当前 start/end（若缺省则从 due 推导），并按 _delta 平移后写回
                        sdt = None
                        if t.get("start"):
                            try:
                                sdt = datetime.fromisoformat(str(t["start"]).replace("Z", "").split("+" )[0])
                            except Exception:
                                sdt = None
                        if not sdt:
                            base_due = t.get("due") or date.today().isoformat()
                            sdt = _ensure_dt_from_due_and_time(base_due, default_hour=8)
                        dur_min = _parse_duration_to_minutes(t.get("duration"), 120)
                        edt = None
                        if t.get("end"):
                            try:
                                edt = datetime.fromisoformat(str(t["end"]).replace("Z", "").split("+" )[0])
                            except Exception:
                                edt = None
                        if not edt:
                            edt = sdt + timedelta(minutes=dur_min)

                        ns = sdt + _delta
                        ne = edt + _delta
                        duration_min = max(1, int((ne - ns).total_seconds() / 60))
                        duration_str = f"{duration_min}min" if duration_min < 120 else f"{duration_min // 60}h"
                        tp = task_path.get(t.get("uid"))
                        if tp:
                            _write_fields(tp,
                                due=ns.date().isoformat(),
                                start=ns.isoformat(),
                                end=ne.isoformat(),
                                duration=duration_str,
                            )

                    moved = 0
                    scope = st.session_state.get("group_move_scope", "关闭") or "关闭"
                    if scope != "关闭":
                        # 计算分组 key，并对当前显示范围内同组任务批量平移
                        ref_key = _group_key_for_scope(dragged_task, scope)
                        for t in tasks_scope:
                            if not t.get("uid"):
                                continue
                            if _group_key_for_scope(t, scope) == ref_key:
                                _shift_and_write(t, delta)
                                moved += 1
                    else:
                        # 仅移动当前被拖拽的任务
                        _shift_and_write(dragged_task, delta)
                        moved = 1
                    st.session_state["agg_last_ec_fp"] = fp
                    st.session_state["agg_last_ec_ts"] = time.time()
                    st.toast(f"✅ 已写回 {moved} 个任务时间")
                    st.rerun()
        except Exception as _e:
            # 提示但不中断页面
            st.warning(f"⚠️ 拖拽写回失败：{_e}")
        st.markdown("</div>", unsafe_allow_html=True)



        if is_new_proj:
            st.markdown("### 新建 Project")
            title  = st.text_input("标题", key="new_proj_title")
            desc   = st.text_area("描述", key="new_proj_desc")
            proj_due = st.date_input("截止日期", value=None, key="new_proj_due")
            # --- 自动继承当前层级的 UID（基于新体系）
            def_obj_uid = current_obj_uid
            def_area_uid = None
            if def_obj_uid:
                def_area_uid = obj_by_uid.get(def_obj_uid, {}).get("area_uid")
            else:
                def_area_uid = current_area_uid

            # 根据 UID 推回到 ID（用于 selectbox 默认值）
            def_obj_id = obj_by_uid.get(def_obj_uid, {}).get("id") if def_obj_uid else None
            def_area_id = area_by_uid.get(def_area_uid, {}).get("id") if def_area_uid else None
            new_obj = st.selectbox(
                "归属 Objective（可留空）",
                ["(留空)"] + [o["id"] for o in objs],
                index=(["(留空)"] + [o["id"] for o in objs]).index(def_obj_id) if def_obj_id in [o["id"] for o in objs] else 0,
                key="new_proj_obj"
            )

            new_area = st.selectbox(
                "归属 Area（可留空）",
                ["(留空)"] + [a["id"] for a in areas],
                index=(["(留空)"] + [a["id"] for a in areas]).index(def_area_id) if def_area_id in [a["id"] for a in areas] else 0,
                key="new_proj_area"
            )
            btn_label = "创建 Project（自动生成 ID/UID）"
            if st.button(btn_label, type="primary", key="btn_create_proj"):
                gen_id = _slugify(title)
                path = projs_dir / f"{gen_id}.yaml"
                if path.exists():
                    uid = _gen_uid()
                    gen_id = f"{gen_id}_{uid[:4]}"
                    path = projs_dir / f"{gen_id}.yaml"
                payload = {
                    "id": gen_id,
                    "title": title or gen_id,
                    "desc": desc or "",
                    "created_at": str(datetime.now().date()),
                    "uid": _gen_uid(),
                    "due": str(proj_due) if proj_due else "",
                }
                if def_obj_uid:
                    payload["objective_uid"] = def_obj_uid
                if def_area_uid:
                    payload["area_uid"] = def_area_uid
                _write_fields(path, **payload)
                # 新建 Project 后追加到排序
                if def_obj_uid:
                    obj_p = obj_path_uid.get(def_obj_uid)
                    if obj_p:
                        order_list = obj_by_uid.get(def_obj_uid, {}).get("order_projects", []) or []
                        _write_fields(obj_p, order_projects=order_list + [payload["uid"]])
                st.success("✅ 已创建 Project")
                st.rerun()

            # ====== Objective 层 ======
            elif focus == 'obj':
                if current_obj_id:
                    data = obj_by_id.get(current_obj_id, {})
                    st.markdown(f"### 编辑 Objective: {current_obj_id}")
                    title = st.text_input("标题", value=data.get("title", ""), key="edit_obj_title")
                    desc  = st.text_area("描述", value=data.get("desc", ""), key="edit_obj_desc")
                    cur_area_uid = data.get("area_uid")
                    cur_area_id = area_by_uid.get(cur_area_uid, {}).get("id") if cur_area_uid else None
                    new_area = st.selectbox(
                        "归属 Area（可留空）",
                        ["(留空)"] + [a["id"] for a in areas],
                        index=(["(留空)"] + [a["id"] for a in areas]).index(cur_area_id) if cur_area_id in [a["id"] for a in areas] else 0,
                        key="edit_obj_area",
                    )
                    if new_area == "(留空)":
                        new_area = None
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("💾 保存修改 (Objective)", type="primary", key="save_obj"):
                            p = obj_path.get(current_obj_id)
                            if p:
                                _write_fields(
                                    p,
                                    title=title,
                                    desc=desc,
                                    area_uid=area_id2uid.get(new_area) if new_area else None,
                                )
                                st.success("已更新 Objective")
                                st.rerun()
                    with c2:
                        if st.button("🗑️ 删除该 Objective", type="secondary", key="del_obj"):
                            p = obj_path.get(current_obj_id)
                            if p:
                                p.unlink(missing_ok=True)
                                st.warning("已删除 Objective（未级联处理其下 Project/Task）")
                                st.rerun()
                elif sel_obj_opt == "➕ 新建 Objective":
                    st.markdown("### 新建 Objective")
                    title  = st.text_input("标题", key="new_obj_title")
                    desc   = st.text_area("描述", key="new_obj_desc")
                    def_area = current_area_id
                    new_area = st.selectbox("归属 Area（可留空）", ["(留空)"] + [a["id"] for a in areas], index=( ["(留空)"] + [a["id"] for a in areas] ).index(def_area) if def_area in [a["id"] for a in areas] else 0, key="new_obj_area")
                    if new_area == "(留空)":
                        new_area = None
                    btn_label = "创建 Objective（自动生成 ID/UID）"
                    if st.button(btn_label, type="primary", key="btn_create_obj"):
                        gen_id = _slugify(title)
                        path = objs_dir / f"{gen_id}.yaml"
                        if path.exists():
                            uid = _gen_uid()
                            gen_id = f"{gen_id}_{uid[:4]}"
                            path = objs_dir / f"{gen_id}.yaml"
                        payload = {
                            "id": gen_id,
                            "title": title or gen_id,
                            "desc": desc or "",
                            "created_at": str(datetime.now().date()),
                            "uid": _gen_uid(),
                        }
                        if new_area is not None:
                            payload["area_uid"] = area_id2uid.get(new_area)
                        _write_fields(path, **payload)
                        # 新建 Objective 后追加到排序
                        if new_area is not None:
                            a_path = area_path_uid.get(area_id2uid.get(new_area))
                            if a_path:
                                order_list = area_by_uid.get(area_id2uid.get(new_area), {}).get("order_objectives", []) or []
                                _write_fields(a_path, order_objectives=order_list + [payload["uid"]])
                        st.success("✅ 已创建 Objective")
                        st.rerun()

            # ====== Area 层 ======
            else:  # 默认聚焦 Area
                if current_area_id:
                    data = area_by_id.get(current_area_id, {})
                    st.markdown(f"### 编辑 Area: {current_area_id}")
                    title = st.text_input("标题", value=data.get("title", ""), key="edit_area_title")
                    desc  = st.text_area("描述", value=data.get("desc", ""), key="edit_area_desc")
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("💾 保存修改 (Area)", type="primary", key="save_area"):
                            p = area_path.get(current_area_id)
                            if p:
                                _write_fields(p, title=title, desc=desc)
                                st.success("已更新 Area")
                                st.rerun()
                    with c2:
                        if st.button("🗑️ 删除该 Area", type="secondary", key="del_area"):
                            p = area_path.get(current_area_id)
                            if p:
                                p.unlink(missing_ok=True)
                                st.warning("已删除 Area（未级联处理其下 Objective/Project/Task）")
                                st.rerun()
                elif sel_area_opt == "➕ 新建 Area":
                    st.markdown("### 新建 Area")
                    title  = st.text_input("标题", key="new_area_title")
                    desc   = st.text_area("描述", key="new_area_desc")
                    btn_label = "创建 Area（自动生成 ID/UID）"
                    if st.button(btn_label, type="primary", key="btn_create_area"):
                        gen_id = _slugify(title)
                        path = areas_dir / f"{gen_id}.yaml"
                        if path.exists():
                            uid = _gen_uid()
                            gen_id = f"{gen_id}_{uid[:4]}"
                            path = areas_dir / f"{gen_id}.yaml"
                        payload = {
                            "id": gen_id,
                            "title": title or gen_id,
                            "desc": desc or "",
                            "created_at": str(datetime.now().date()),
                            "uid": _gen_uid(),
                        }
                        existing_areas = [a.get("uid") for a in areas]
                        _write_fields(path, **payload)
                        _write_fields(path, order_index=len(existing_areas))
                        st.success("✅ 已创建 Area")
                        st.rerun()
st.markdown("""
<style>
/* 给一些常见 Streamlit 容器描边，便于定位是谁在裁切 */
[data-testid="stVerticalBlock"] { outline: 1px dashed #999; }
[data-testid="stIFrame"]        { outline: 2px dashed #f90; }   /* 组件容器 */
[data-testid="stIFrame"] iframe { outline: 3px solid  #e00; }   /* 真正的“框” */
</style>
""", unsafe_allow_html=True)
