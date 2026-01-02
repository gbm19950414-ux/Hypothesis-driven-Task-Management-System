import streamlit as st
import pandas as pd
from pathlib import Path
from ruamel.yaml import YAML
from datetime import datetime as _dt
import uuid

# === 自动恢复上次选择的项目 ===
import json
_project_cache = Path.home() / ".lifeos_last_project.json"

if _project_cache.exists():
    try:
        saved_proj = json.loads(_project_cache.read_text()).get("current_project")
        if saved_proj:
            st.session_state["current_project"] = saved_proj
    except Exception:
        pass

def save_current_project(pid):
    try:
        _project_cache.write_text(json.dumps({"current_project": pid}))
    except Exception:
        pass

# —— 数据版本：用于触发缓存失效（每次写回文件后 +1） ——
if "data_ver" not in st.session_state:
    st.session_state["data_ver"] = 0

st.set_page_config(page_title="life_os Dashboard", layout="wide")
st.title("📊 A→O→P→T 层级与快速重归类")

yaml = YAML()
root = Path(__file__).resolve().parents[1]  # life_os/
areas_dir = root / "01_areas"
objs_dir  = root / "02_objectives"
projs_dir = root / "03_projects"
tasks_dir = root / "tasks"


@st.cache_data(show_spinner=False)
def load_yaml_files(folder_str: str, version: int):
    folder = Path(folder_str)
    out = []
    if not folder.exists():
        return out
    for p in folder.glob("*.y*ml"):
        try:
            d = yaml.load(p.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                d["__path"] = str(p)
                # 读取 uid 字段
                if "uid" not in d:
                    # 若无 uid，自动生成并写回
                    d["uid"] = uuid.uuid4().hex[:8]
                    with p.open("w", encoding="utf-8") as f:
                        yaml.dump(d, f)
            out.append(d)
        except Exception as e:
            st.warning(f"读取失败: {p.name} -> {e}")
    return out

_ver = st.session_state.get("data_ver", 0)
areas = {d["uid"]: d for d in load_yaml_files(str(areas_dir), _ver) if d.get("uid")}
objs  = {d["uid"]: d for d in load_yaml_files(str(objs_dir),  _ver) if d.get("uid")}
projs = {d["uid"]: d for d in load_yaml_files(str(projs_dir), _ver) if d.get("uid")}


rows = []
for p in tasks_dir.glob("t-*.y*ml"):
    d = yaml.load(p.read_text(encoding="utf-8"))
    # 读取 uid 字段
    if "uid" not in d:
        d["uid"] = uuid.uuid4().hex[:8]
        with p.open("w", encoding="utf-8") as f:
            yaml.dump(d, f)
    # 处理 time_logs 字段为字符串格式
    time_logs = d.get("time_logs", [])
    if isinstance(time_logs, list) and time_logs and isinstance(time_logs[0], dict):
        time_logs_str = "\n".join([
            f"{log.get('start','')}|{log.get('end','')}|{';'.join(log.get('note',[]) if isinstance(log.get('note',[]),list) else [str(log.get('note', '') )])}"
            for log in time_logs
        ])
    else:
        time_logs_str = ""
    rows.append({
        "__path": str(p),
        "id": d.get("id"),
        "uid": d.get("uid"),
        "title": d.get("title", ""),
        "area_uid": d.get("area_uid", d.get("area", "")),
        "objective_uid": d.get("objective_uid", d.get("objective", "")),
        "project_uid": d.get("project_uid", d.get("project", "")),
        "status": d.get("status", ""),
        "importance": d.get("importance", ""),
        "difficulty": int(d.get("difficulty", 0) or 0),
        "start_time": d.get("start_time", ""),
        "duration_minutes": int(d.get("duration_minutes", 0) or 0),
        "due": d.get("due", "") or "",
        "tags": ", ".join(d.get("tags", []) or []),
        "time_logs": time_logs_str,
    })
df = pd.DataFrame(rows)
# === 全局项目选择 ===
# === 📁 分层选择（区域→目标→项目→任务） ===
st.markdown("---")
st.subheader("📁 浏览 + 就地编辑（左边选择，右边立刻写回 YAML）")

col_area, col_obj, col_proj, col_task = st.columns(4)
_ver = st.session_state.get("data_ver", 0)

# --- 区域选择 ---
with col_area:
    st.markdown("**区域（Area）**")
    area_opts = ["（未选择）"] + sorted([a["title"] for a in areas.values()]) + ["＋新建区域"]
    sel_area = st.radio("", area_opts, key="sel_area")
    if sel_area == "＋新建区域":
        new_name = st.text_input("新区域名称", key="new_area_name")
        if st.button("创建区域"):
            aid = f"a-{int(_dt.now().timestamp())}"
            auid = uuid.uuid4().hex[:8]
            data = {"id": aid, "uid": auid, "title": new_name}
            with (areas_dir / f"{aid}.yaml").open("w", encoding="utf-8") as f:
                yaml.dump(data, f)
            st.session_state["data_ver"] += 1
            st.success(f"已创建新区域：{new_name}")
            st.rerun()

# --- 目标选择 ---
with col_obj:
    st.markdown("**目标（Objective）**")
    # 先找出所选区域的 uid
    sel_area_uid = ""
    if sel_area and sel_area != "（未选择）" and sel_area != "＋新建区域":
        for auid, a in areas.items():
            if a.get("title") == sel_area:
                sel_area_uid = auid
                break
    # 只显示属于当前区域的目标
    if not sel_area_uid:
        st.caption("请先选择区域")
        obj_opts = ["（未选择）", "＋新建目标"]
    else:
        # 过滤属于该区域的目标
        obj_titles = [o["title"] for o in objs.values() if o.get("area_uid", o.get("area")) == sel_area_uid]
        obj_opts = ["（未选择）"] + sorted(obj_titles) + ["＋新建目标"]
    sel_obj = st.radio("", obj_opts, key="sel_obj")
    if sel_obj == "＋新建目标":
        new_name = st.text_input("新目标名称", key="new_obj_name")
        if st.button("创建目标"):
            oid = f"o-{int(_dt.now().timestamp())}"
            ouid = uuid.uuid4().hex[:8]
            # 新目标自动挂到当前区域
            data = {"id": oid, "uid": ouid, "title": new_name, "area_uid": sel_area_uid}
            with (objs_dir / f"{oid}.yaml").open("w", encoding="utf-8") as f:
                yaml.dump(data, f)
            st.session_state["data_ver"] += 1
            st.success(f"已创建新目标：{new_name}")
            st.rerun()

# --- 项目选择 ---
with col_proj:
    st.markdown("**项目（Project）**")
    # 找出所选目标的 uid
    sel_obj_uid = ""
    if sel_obj and sel_obj != "（未选择）" and sel_obj != "＋新建目标":
        for ouid, o in objs.items():
            if o.get("title") == sel_obj:
                sel_obj_uid = ouid
                break
    if not sel_obj_uid:
        st.caption("请先选择目标")
        proj_opts = ["（未选择）", "＋新建项目"]
    else:
        # 只显示属于该目标的项目
        proj_titles = [p["title"] for p in projs.values() if p.get("objective_uid", p.get("objective")) == sel_obj_uid]
        proj_opts = ["（未选择）"] + sorted(proj_titles) + ["＋新建项目"]
    sel_proj = st.radio("", proj_opts, key="sel_proj")

    if sel_proj == "＋新建项目":
        new_name = st.text_input("新项目名称", key="new_proj_name")
        if st.button("创建项目"):
            pid = f"p-{int(_dt.now().timestamp())}"
            puid = uuid.uuid4().hex[:8]
            # 新项目自动挂到当前目标和区域（用 uid）
            data = {"id": pid, "uid": puid, "title": new_name, "area_uid": sel_area_uid, "objective_uid": sel_obj_uid}
            with (projs_dir / f"{pid}.yaml").open("w", encoding="utf-8") as f:
                yaml.dump(data, f)
            st.session_state["data_ver"] += 1
            st.success(f"已创建新项目：{new_name}")
            st.rerun()
    elif sel_proj and sel_proj != "（未选择）":
        # 保存当前项目选择
        sel_proj_uid = next((puid for puid, p in projs.items() if p["title"] == sel_proj), "")
        st.session_state["current_project"] = sel_proj_uid
        save_current_project(sel_proj_uid)
        st.success(f"当前项目：**{sel_proj}**")

# --- 任务选择 ---
with col_task:
    st.markdown("**任务（Task）**")
    # 任务依赖于项目选择
    sel_proj_uid = ""
    if sel_proj and sel_proj != "（未选择）" and sel_proj != "＋新建项目":
        for puid, p in projs.items():
            if p.get("title") == sel_proj:
                sel_proj_uid = puid
                break
    # 优先使用 current_project
    chosen_proj_uid = st.session_state.get("current_project", "")
    if not sel_proj_uid and not chosen_proj_uid:
        st.caption("请先选择项目")
    else:
        # 选择顺序优先 current_project
        use_proj_uid = chosen_proj_uid or sel_proj_uid
        task_files = list(tasks_dir.glob("t-*.yaml"))
        tasks_in_proj = []
        for p in task_files:
            d = yaml.load(p.read_text(encoding="utf-8")) or {}
            if "uid" not in d:
                d["uid"] = uuid.uuid4().hex[:8]
                with p.open("w", encoding="utf-8") as f:
                    yaml.dump(d, f)
            if d.get("project_uid", d.get("project")) == use_proj_uid:
                tasks_in_proj.append(d.get("title", d.get("id", "")))
        task_opts = ["（未选择）"] + tasks_in_proj + ["＋新建任务"]
        sel_task = st.radio("", task_opts, key="sel_task")

        if sel_task == "＋新建任务":
            new_task_name = st.text_input("新任务标题", key="new_task_name")
            if st.button("创建任务"):
                today = _dt.now().strftime("%Y%m%d")
                tid = f"t-{today}-{len(task_files)+1:03d}"
                tuid = uuid.uuid4().hex[:8]
                # 查找项目的 area_uid/objective_uid
                proj_item = projs.get(use_proj_uid, {})
                data = {
                    "id": tid,
                    "uid": tuid,
                    "title": new_task_name,
                    "project_uid": use_proj_uid,
                    "area_uid": proj_item.get("area_uid", proj_item.get("area", "")),
                    "objective_uid": proj_item.get("objective_uid", proj_item.get("objective", "")),
                    "status": "todo",
                }
                with (tasks_dir / f"{tid}.yaml").open("w", encoding="utf-8") as f:
                    yaml.dump(data, f)
                st.session_state["data_ver"] += 1
                st.success(f"已创建新任务：{new_task_name}")
                st.rerun()

st.markdown("---")

# --- 🕒 项目时间线（Beta）：基于 streamlit-calendar ---
try:
    from streamlit_calendar import calendar
    st.markdown("---")
    st.subheader("🕒 项目时间线（Beta）")
    view_mode = st.radio("选择日历视图", ["月度", "周度"], horizontal=True, index=0)
    # 1) 选择项目，只看该项目的任务
    chosen_proj = st.session_state.get("current_project", "")
    if not chosen_proj:
        st.info("请先在页面顶部选择一个项目。")
    else:
        # 2) 将该项目的任务转为 FullCalendar 的 events
        # 规则：优先使用任务的 start_time + due（日期部分）来构造 start；
        #       没有 start_time 则用 09:00；没有 duration_minutes 则 60 分钟；
        #       若无 due，则用今天。
        def _event_from_task(row):
            import datetime as dt
            tid   = row["id"]
            title = row["title"] or tid
            dur   = int(row.get("duration_minutes") or 60)
            due_s = row.get("due") or pd.Timestamp.now().date().isoformat()
            date  = pd.to_datetime(due_s, errors="coerce").date()
            stime = (row.get("start_time") or "09:00")
            try:
                hh, mm = [int(x) for x in stime.split(":")[:2]]
            except Exception:
                hh, mm = 9, 0
            start = dt.datetime.combine(date, dt.time(hh, mm))
            end   = start + dt.timedelta(minutes=dur)
            return {
                "id": tid,
                "title": title,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "editable": True,
                "allDay": (view_mode == "月度"),
                "extendedProps": {
                    "task_id": tid,
                    "task_path": row["__path"],
                }
            }

        proj_df = df[(df["project_uid"] == chosen_proj)]
        events = [ _event_from_task(r._asdict() if hasattr(r, "_asdict") else r)
                   for _, r in proj_df.iterrows() ]

        # 3) FullCalendar 配置
        cal_options = {
            "initialView": "timeGridWeek",
            "slotMinTime": "07:00:00",
            "slotMaxTime": "18:00:00",
            "nowIndicator": True,
            "allDaySlot": False,
            "locale": "zh-cn",
            "editable": True,
            "eventStartEditable": True,
            "eventDurationEditable": True,
            "selectable": True,
            "selectMirror": True,
            "firstDay": 1,  # 周一
            # 选中空白区域用于创建新任务
            "selectConstraint": {"start": "07:00:00", "end":"18:00:00"},
            # 只允许在工作时间内拖拽
            "businessHours": [
                {"daysOfWeek": [1,2,3,4,5,6,0], "startTime": "07:00", "endTime": "10:30"},
                {"daysOfWeek": [1,2,3,4,5,6,0], "startTime": "11:30", "endTime": "18:00"},
            ],
        }

        # 新增月份视图配置
        cal_options_month = cal_options.copy()
        cal_options_month.update({
            "initialView": "dayGridMonth",
            "allDaySlot": True,
            "slotMinTime": None,
            "slotMaxTime": None,
        })

        # 只渲染一个视图以提升性能
        if view_mode == "月度":
            cal_key = f"cal_month_{chosen_proj}"
            state = calendar(events=events, options=cal_options_month, key=cal_key)
        else:
            cal_key = f"cal_week_{chosen_proj}"
            state = calendar(events=events, options=cal_options, key=cal_key)

        _state = state  # <-- define here once, immediately after state assignment

        # === 调试：开关与日志函数 ===
        st.session_state.setdefault("__cal_logs", [])
        cal_debug = st.checkbox("🛠 调试模式", value=False, key="cal_debug")

        def _log(msg):
            if cal_debug:
                st.session_state["__cal_logs"].append(str(msg))
                st.caption(f"DEBUG: {msg}")

        # === 统一解析工具：FullCalendar 回调数据 → Python ===
        def _parse_dt(val):
            try:
                return pd.to_datetime(val).to_pydatetime() if val else None
            except Exception as e:
                _log(f"_parse_dt 失败: {e} ({val})")
                return None

        def _extract_event_fields(edict: dict):
            """从回调对象中统一抽取 tid/path/start/end/allDay。
            兼容 eventChange/eventDrop/eventResize 形状差异。"""
            ev = edict.get("event", {}) or edict
            props = ev.get("extendedProps", {}) or {}
            tid = props.get("task_id") or ev.get("id")
            tpath = props.get("task_path") or props.get("__path")
            start_val = ev.get("start") or ev.get("startStr")
            end_val = ev.get("end") or ev.get("endStr")
            all_day = bool(ev.get("allDay", False))
            return tid, tpath, _parse_dt(start_val), _parse_dt(end_val), all_day

        def _write_task_fields(path: Path, **kwargs):
            d = yaml.load(path.read_text(encoding="utf-8")) or {}
            for k, v in kwargs.items():
                if v in ("", None):
                    d.pop(k, None)
                else:
                    d[k] = v
            with path.open("w", encoding="utf-8") as f:
                yaml.dump(d, f)
            # 写回后触发缓存失效
            st.session_state["data_ver"] = st.session_state.get("data_ver", 0) + 1

        # === 统一处理 eventChange（拖拽/拉伸后的官方回调）→ 立即写回 YAML ===
        if _state.get("eventChange"):
            ch = _state["eventChange"]
            new_ev = ch.get("event", {}) or {}
            old_ev = ch.get("oldEvent", {}) or {}

            tid, tpath, new_start, new_end, all_day = _extract_event_fields({"event": new_ev})
            _,   _,   old_start, old_end, _        = _extract_event_fields({"event": old_ev})

            # ===== Flow-wide block shift on eventChange (date drag) =====
            def _normalize_id(s):
                return str(s).strip().replace("\ufeff", "") if s else ""

            # Only when date actually changed (avoid hour-only moves here)
            _date_changed = (new_start is not None and old_start is not None and new_start.date() != old_start.date())

            if tpath and _date_changed:
                path = Path(tpath)
                try:
                    tdata_ec = yaml.load(path.read_text(encoding="utf-8")) or {}
                except Exception:
                    tdata_ec = {}
                flow_meta_ec = tdata_ec.get("flow") or {}
                flow_instance_id_ec = _normalize_id(
                    flow_meta_ec.get("instance_id") or tdata_ec.get("flow_instance_id") or tdata_ec.get("instance_id")
                )
                flow_id_ec = flow_meta_ec.get("flow_id") or tdata_ec.get("flow_id")

                if flow_instance_id_ec:
                    delta_days_ec = (new_start.date() - old_start.date()).days
                    if delta_days_ec != 0:
                        delta_ec = pd.Timedelta(days=delta_days_ec)
                        base_dir_ec = path.parent
                        moved_ec = 0
                        for fp in base_dir_ec.glob("t-*.y*ml"):
                            try:
                                d_ec = yaml.load(fp.read_text(encoding="utf-8")) or {}
                            except Exception:
                                continue
                            fmeta_ec = d_ec.get("flow") or {}
                            fid_ec = _normalize_id(
                                fmeta_ec.get("instance_id") or d_ec.get("flow_instance_id") or d_ec.get("instance_id")
                            )
                            if fid_ec == flow_instance_id_ec:
                                if d_ec.get("due"):
                                    new_due_ec = pd.to_datetime(d_ec["due"], errors="coerce") + delta_ec
                                    if not pd.isna(new_due_ec):
                                        d_ec["due"] = new_due_ec.date().isoformat()
                                with fp.open("w", encoding="utf-8") as f:
                                    yaml.dump(d_ec, f)
                                moved_ec += 1

                        # bump version & toast then rerun, so per-task fallback below won't double-apply
                        st.session_state["data_ver"] = st.session_state.get("data_ver", 0) + 1
                        st.toast(f"✅ 已整体移动流程 {flow_id_ec or flow_instance_id_ec}（{delta_days_ec:+d} 天，{moved_ec} 条任务）")
                        st.rerun()
            # ===== end flow-wide shift on eventChange =====

            # 回退到 DataFrame 路径
            if not tpath:
                row = df[df["id"] == tid]
                if not row.empty:
                    tpath = row["__path"].iloc[0]

            if tid and tpath and new_start is not None:
                path = Path(tpath)
                try:
                    old_data = yaml.load(path.read_text(encoding="utf-8")) or {}
                except Exception:
                    old_data = {}
                old_dur = int(old_data.get("duration_minutes") or 60)

                changed_end = (new_end != old_end) and (new_end is not None)

                if view_mode == "月度" or all_day:
                    # 月视图/全天：拖拽→更新 due；拉伸→更新 duration_minutes（以天计）
                    if (old_start is None) or (new_start.date() != getattr(old_start, "date", lambda: None)()):
                        _write_task_fields(path, due=new_start.date().isoformat())
                        st.toast(f"📅 已更新 {tid} → {new_start.date().isoformat()}")
                    if changed_end and new_end is not None:
                        dur_days = max(0, (new_end.date() - new_start.date()).days)
                        _write_task_fields(path, duration_minutes=dur_days * 24 * 60)
                        st.toast(f"⏱️ 已调整 {tid} 时长 → {dur_days} 天")
                else:
                    # 周视图：同时更新 due/start_time/duration_minutes（分钟）
                    dur = int((new_end - new_start).total_seconds() // 60) if new_end else old_dur
                    _write_task_fields(
                        path,
                        due=new_start.date().isoformat(),
                        start_time=new_start.strftime("%H:%M"),
                        duration_minutes=max(0, dur),
                    )
                    st.toast(f"🕘 已更新 {tid} → {new_start.strftime('%Y-%m-%d %H:%M')}（{max(0, dur)} 分钟）")
                # st.rerun()  # Removed to avoid race/unregistered-component errors
            else:
                _log("eventChange: 缺少 tid/path 或 new_start 为空，未写回")

        # === 移动模式控制条 ===
        move_key = "__move_task_id"
        # 如果刚刚点击了事件，提供“设为移动目标”快捷键
        if _state.get("eventClick"):
            clicked_tid = _state["eventClick"]["event"]["extendedProps"]["task_id"]
            with st.container():
                c1, c2, c3 = st.columns([1,1,4])
                if c1.button("📌 设为移动目标", key=f"set_move_{clicked_tid}"):
                    st.session_state[move_key] = clicked_tid
                    st.info(f"已进入移动模式：请选择日历上的日期/时间，把 {clicked_tid} 移动过去。")
                if c2.button("❎ 退出移动模式", key="cancel_move_any"):
                    st.session_state.pop(move_key, None)
                    st.experimental_rerun() if hasattr(st, 'experimental_rerun') else st.rerun()

        # 若已在移动模式，显示提示条
        if st.session_state.get(move_key):
            _mtid = st.session_state[move_key]
            st.warning(f"移动模式中：点击日历空白处选择新时间/日期 → 将任务 **{_mtid}** 移到该处。")

        # 4) 处理交互回调：点击、移动模式、日期点击创建
        def _write_task_fields(path: Path, **kwargs):
            d = yaml.load(path.read_text(encoding="utf-8")) or {}
            for k, v in kwargs.items():
                if v in ("", None):
                    d.pop(k, None)
                else:
                    d[k] = v
            with path.open("w", encoding="utf-8") as f:
                yaml.dump(d, f)
            # 写回后触发缓存失效
            st.session_state["data_ver"] = st.session_state.get("data_ver", 0) + 1


        # b) 拖动（eventDrop）：月视图只改 due；周视图改 due/start_time/duration
        if (not _state.get("eventChange")) and _state.get("eventDrop"):
            ed = _state["eventDrop"].get("event", {})
            props = ed.get("extendedProps", {}) or {}
            tid   = props.get("task_id") or ed.get("id")
            tpath = props.get("task_path") or props.get("__path")
            start_val = ed.get("start") or ed.get("startStr")
            end_val   = ed.get("end") or ed.get("endStr")
            _log({"eventDrop": {"tid": tid, "start": start_val, "end": end_val, "path": tpath}})
            try:
                new_start = pd.to_datetime(start_val).to_pydatetime() if start_val else None
                new_end   = pd.to_datetime(end_val).to_pydatetime() if end_val else None
            except Exception as e:
                new_start, new_end = None, None
                _log(f"eventDrop 解析失败: {e}")
            if not tpath:
                row = df[df["id"] == tid]
                if not row.empty:
                    tpath = row["__path"].iloc[0]
            if tid and tpath and new_start is not None:
                # ======= 新增：整体平移任务流 =======
                try:
                    tdata = yaml.load(Path(tpath).read_text(encoding="utf-8")) or {}
                    flow_info = tdata.get("flow", {})
                    flow_id = flow_info.get("flow_id")
                    instance_id = flow_info.get("instance_id")
                    order = int(flow_info.get("order", 0))
                except Exception:
                    flow_id = instance_id = order = None

                if instance_id:
                    # === normalize_id 清洗函数，去除 YAML 中隐藏字符（BOM、换行、空格） ===
                    def normalize_id(s):
                        """去除 YAML 中隐藏字符（BOM、换行、空格）"""
                        return str(s).strip().replace("\ufeff", "") if s else ""

                    # === 改进版整体平移任务流（按天偏移） ===
                    old_due_date = pd.to_datetime(tdata.get("due"), errors="coerce").date() if tdata.get("due") else None
                    new_due_date = new_start.date() if new_start else None
                    if old_due_date and new_due_date:
                        delta_days = (new_due_date - old_due_date).days
                        delta = pd.Timedelta(days=delta_days)
                        _log(f"整体平移任务流: {instance_id}, Δ={delta_days} 天")

                        # 批量更新同一 instance_id 下所有任务（仅平移日期）
                        for fp in tasks_dir.glob("t-*.y*ml"):
                            d = yaml.load(fp.read_text(encoding="utf-8")) or {}
                            flow_d = d.get("flow") or {}
                            if normalize_id(flow_d.get("instance_id")) == normalize_id(instance_id):
                                if d.get("due"):
                                    d["due"] = (pd.to_datetime(d["due"], errors="coerce") + delta).date().isoformat()
                                with open(fp, "w", encoding="utf-8") as f:
                                    yaml.dump(d, f)

                        # === (① 同步当前任务写回) ===
                        path = Path(tpath)
                        try:
                            old = yaml.load(path.read_text(encoding="utf-8")) or {}
                        except Exception:
                            old = {}
                        old_dur = int(old.get("duration_minutes") or 60)
                        _write_task_fields(
                            path,
                            due=new_start.date().isoformat(),
                            start_time=new_start.strftime("%H:%M"),
                            duration_minutes=int((new_end - new_start).total_seconds() // 60) if new_end else old_dur
                        )

                        # === (③ 重新加载数据并刷新界面) ===
                        st.session_state["data_ver"] += 1
                        # Show toast after all YAML updates, before rerun
                        st.toast(f"✅ 已整体移动流程 {flow_id or instance_id}（{delta_days:+d} 天）")
                        st.rerun()
                else:
                    # ======= 原 eventDrop 单任务逻辑继续执行 =======
                    path = Path(tpath)
                    # 读取旧时长兜底
                    try:
                        old = yaml.load(path.read_text(encoding="utf-8")) or {}
                    except Exception:
                        old = {}
                    old_dur = int(old.get("duration_minutes") or 60)
                    if view_mode == "月度" or ed.get("allDay", False):
                        _write_task_fields(path, due=new_start.date().isoformat())
                        st.toast(f"📅 已更新 {tid} → {new_start.date().isoformat()}")
                    else:
                        dur = int((new_end - new_start).total_seconds()//60) if new_end else old_dur
                        _write_task_fields(path,
                                           due=new_start.date().isoformat(),
                                           start_time=new_start.strftime("%H:%M"),
                                           duration_minutes=dur)
                        st.toast(f"🕘 已更新 {tid} → {new_start.strftime('%Y-%m-%d %H:%M')}（{dur} 分钟）")
                # st.rerun()  # Removed to avoid race/unregistered-component errors
            else:
                _log("eventDrop: 缺少 tid/path 或无法解析新时间，未写回")

        # c) 拉伸（eventResize）：更新 duration（周视图同步 start/due）
        if (not _state.get("eventChange")) and _state.get("eventResize"):
            ed = _state["eventResize"].get("event", {})
            props = ed.get("extendedProps", {}) or {}
            tid   = props.get("task_id") or ed.get("id")
            tpath = props.get("task_path") or props.get("__path")
            start_val = ed.get("start") or ed.get("startStr")
            end_val   = ed.get("end") or ed.get("endStr")
            _log({"eventResize": {"tid": tid, "start": start_val, "end": end_val, "path": tpath}})
            try:
                new_start = pd.to_datetime(start_val).to_pydatetime() if start_val else None
                new_end   = pd.to_datetime(end_val).to_pydatetime() if end_val else None
            except Exception as e:
                new_start, new_end = None, None
                _log(f"eventResize 解析失败: {e}")
            if not tpath:
                row = df[df["id"] == tid]
                if not row.empty:
                    tpath = row["__path"].iloc[0]
            if tid and tpath and new_start is not None and new_end is not None:
                path = Path(tpath)
                dur = max(0, int((new_end - new_start).total_seconds()//60))
                if view_mode == "月度":
                    _write_task_fields(path, duration_minutes=dur)
                    st.toast(f"⏱️ 已调整 {tid} 时长 → {dur} 分钟")
                else:
                    _write_task_fields(path,
                                       duration_minutes=dur,
                                       start_time=new_start.strftime("%H:%M"),
                                       due=new_start.date().isoformat())
                    st.toast(f"⏱️ 已调整 {tid} → {new_start.strftime('%Y-%m-%d %H:%M')}（{dur} 分钟）")
                # st.rerun()  # Removed to avoid race/unregistered-component errors
            else:
                _log("eventResize: 缺少必要字段，未写回")

        # a) 点击事件：直接跳到单条编辑，并记录以便删除等操作
        if _state.get("eventClick"):
            tid = _state["eventClick"]["event"]["extendedProps"]["task_id"]
            st.session_state["single_task_time_logs_select"] = tid
            st.session_state["__clicked_task_for_ops"] = tid
            st.info(f"已选中任务：{tid}")

        # e) 点击空白时间：优先“移动模式”；支持月视图(dateClick) 与 周视图(select)
        _dc_obj = _state.get("dateClick")
        _sel_obj = _state.get("select")  # timeGrid 选择通常走 select
        if _dc_obj or _sel_obj:
            if _dc_obj:
                raw_dt = _dc_obj.get("date")
            else:
                raw_dt = (_sel_obj.get("start") or _sel_obj.get("startStr"))
            try:
                click_dt = pd.to_datetime(raw_dt).to_pydatetime()
            except Exception:
                _log(f"无法解析点击时间: {raw_dt}")
                click_dt = None
            move_key = "__move_task_id"
            moving_tid = st.session_state.get(move_key)

            if click_dt is not None and moving_tid:
                # —— 移动模式 ——
                row = df[df["id"] == moving_tid]
                if row.empty:
                    st.error("未找到要移动的任务。")
                    _log("move: 未找到任务行")
                else:
                    path = Path(row["__path"].iloc[0])
                    try:
                        tdata = yaml.load(path.read_text(encoding="utf-8")) or {}
                    except Exception as e:
                        tdata = {}
                        _log(f"move: 读取任务失败 {e}")
                    old_dur = int(tdata.get("duration_minutes") or 60)
                    if view_mode == "月度":
                        _write_task_fields(path, due=click_dt.date().isoformat())
                        st.success(f"📅 已移动 {moving_tid} → {click_dt.date().isoformat()}")
                        _log(f"move-month: {moving_tid} -> {click_dt.date().isoformat()}")
                    else:
                        _write_task_fields(
                            path,
                            due=click_dt.date().isoformat(),
                            start_time=click_dt.strftime("%H:%M"),
                            duration_minutes=old_dur,
                        )
                        st.success(f"🕘 已移动 {moving_tid} → {click_dt.strftime('%Y-%m-%d %H:%M')}（{old_dur} 分钟）")
                        _log(f"move-week: {moving_tid} -> {click_dt}")
                st.session_state.pop(move_key, None)
                st.rerun()

            elif click_dt is not None:
                # —— 非移动模式：创建新任务（保留你原来的表单） ——
                default_title = f"{chosen_proj} 新任务"
                form_key = f"create_task_on_date_{'month' if view_mode=='月度' else 'week'}"
                with st.form(form_key, clear_on_submit=True):
                    ttitle = st.text_input("任务标题", value=default_title)
                    tdiff  = st.number_input("难度 (1-5)", min_value=1, max_value=5, value=3, key=form_key+"_diff")
                    tdur   = st.number_input("持续时间（分钟）", min_value=0, value=60, step=30, key=form_key+"_dur")
                    stime  = st.text_input("开始时间（HH:MM）", value="09:00", key=form_key+"_stime")
                    ok = st.form_submit_button("在该日期创建任务")
                    if ok and ttitle.strip():
                        try:
                            hh, mm = [int(x) for x in stime.split(":")[:2]]
                        except Exception:
                            hh, mm = 9, 0
                        from datetime import timedelta
                        start_dt = click_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
                        end_dt   = start_dt + timedelta(minutes=int(tdur))
                        today_str = pd.Timestamp.now().strftime("%Y%m%d")
                        seq = len(list(tasks_dir.glob(f"t-{today_str}-*.yaml"))) + 1
                        tid = f"t-{today_str}-{seq:03d}"
                        tuid = uuid.uuid4().hex[:8]
                        proj_item = projs.get(chosen_proj, {})
                        data = {
                            "id": tid,
                            "uid": tuid,
                            "title": ttitle.strip(),
                            "area_uid": proj_item.get("area_uid", proj_item.get("area", "")),
                            "objective_uid": proj_item.get("objective_uid", proj_item.get("objective", "")),
                            "project_uid": chosen_proj,
                            "status": "todo",
                            "difficulty": int(tdiff),
                            "duration_minutes": int((end_dt - start_dt).total_seconds()//60),
                            "start_time": start_dt.strftime("%H:%M"),
                            "due": start_dt.date().isoformat(),
                            "tags": [],
                            "created_at": pd.Timestamp.now(tz='UTC').isoformat(timespec="seconds"),
                        }
                        with (tasks_dir / f"{tid}.yaml").open("w", encoding="utf-8") as f:
                            yaml.dump(data, f)
                        st.session_state["data_ver"] = st.session_state.get("data_ver", 0) + 1
                        st.success(f"已在 {start_dt.strftime('%Y-%m-%d %H:%M')} 创建任务：{tid} — {ttitle}")
                        _log(f"create: {tid} @ {start_dt}")
                        st.rerun()

        # === 任务操作面板（点击任务后可删除/编辑） ===
        _clicked_tid = st.session_state.get("__clicked_task_for_ops")
        if _clicked_tid:
            st.markdown("### 🧰 对所选任务的操作")
            # 小提示：移动模式
            st.caption("提示：若要在日历上移动该任务，请先点击上面的 \"📌 设为移动目标\"，再在日历上点击新时间/日期。")
            # 读取任务 YAML
            _row = df[df["id"] == _clicked_tid]
            _task_path = None
            _task_data = {}
            if not _row.empty:
                _task_path = Path(_row["__path"].iloc[0])
                if _task_path.exists():
                    try:
                        _task_data = yaml.load(_task_path.read_text(encoding="utf-8")) or {}
                    except Exception as e:
                        st.error(f"读取任务文件失败: {e}")
                        _task_data = {}
            # 任务字段编辑表单
            with st.form(f"edit_task_fields_{_clicked_tid}"):
                _title = st.text_input("任务标题", value=_task_data.get("title", ""), key=f"title_{_clicked_tid}")
                _difficulty = st.number_input("难度 (1-5)", min_value=1, max_value=5, value=int(_task_data.get("difficulty", 3) or 3), key=f"diff_{_clicked_tid}")
                _duration = st.number_input("持续时间（分钟）", min_value=0, value=int(_task_data.get("duration_minutes", 60) or 60), key=f"dur_{_clicked_tid}")
                _due = st.text_input("到期日期（YYYY-MM-DD）", value=_task_data.get("due", ""), key=f"due_{_clicked_tid}")
                _start_time = st.text_input("开始时间（HH:MM）", value=_task_data.get("start_time", ""), key=f"stime_{_clicked_tid}")
                _status = st.selectbox("状态", options=["todo", "doing", "done", "cancelled"], index=["todo", "doing", "done", "cancelled"].index(str(_task_data.get("status", "todo")) if _task_data.get("status", "todo") in ["todo", "doing", "done", "cancelled"] else 0), key=f"status_{_clicked_tid}")
                col_save, col_del, col_cancel = st.columns([1,1,1])
                with col_save:
                    save_clicked = st.form_submit_button("💾 保存")
                with col_del:
                    del_clicked = st.form_submit_button("🗑️ 删除该任务")
                with col_cancel:
                    cancel_clicked = st.form_submit_button("取消")
            if save_clicked:
                # 写回 YAML
                if _task_path:
                    _task_data["title"] = _title
                    _task_data["difficulty"] = int(_difficulty)
                    _task_data["duration_minutes"] = int(_duration)
                    _task_data["due"] = _due
                    _task_data["start_time"] = _start_time
                    _task_data["status"] = _status
                    try:
                        with _task_path.open("w", encoding="utf-8") as f:
                            yaml.dump(_task_data, f)
                        st.session_state["data_ver"] = st.session_state.get("data_ver", 0) + 1
                        st.success("任务已保存")
                        st.session_state.pop("__clicked_task_for_ops", None)
                        st.rerun()
                    except Exception as e:
                        st.error(f"保存失败: {e}")
            if del_clicked:
                if _task_path and _task_path.exists():
                    _task_path.unlink()
                    st.session_state["data_ver"] = st.session_state.get("data_ver", 0) + 1
                    st.success(f"已删除任务：{_clicked_tid}")
                    st.session_state.pop("__clicked_task_for_ops", None)
                    st.rerun()
            if cancel_clicked:
                st.session_state.pop("__clicked_task_for_ops", None)
                st.rerun()

            # === 调试输出区 ===
            with st.expander("📋 调试输出", expanded=False):
                if st.session_state.get("__cal_logs"):
                    for ln in st.session_state["__cal_logs"][-50:]:
                        st.text(ln)
                else:
                    st.caption("暂无日志。勾选上方“🛠 调试模式”以记录事件明细。")

except Exception as e:
    st.info("（可选）你可以 `pip install streamlit-calendar` 启用可拖拽的项目时间线。")
    st.caption(f"加载日历组件失败：{e}")
# === 🧮 从模板生成任务流 ===
st.markdown("---")
st.subheader("🧮 从模板生成任务流")

flows_dir = root / "flows"
flows_dir.mkdir(exist_ok=True)
flow_files = list(flows_dir.glob("*.yaml"))
if not flow_files:
    st.info("请在 flows/ 目录下放入流程模板（例如 western_blot.yaml）")
else:
    flow_map = {f.stem: f for f in flow_files}
    chosen = st.selectbox("选择流程模板", list(flow_map.keys()))
    n_samples = st.number_input("样本数", 1, 20, 6)
    anchor_date = st.date_input("起始日期")
    anchor_time = st.time_input("起始时间")
    chosen_proj = st.session_state.get("current_project", "")
    if not chosen_proj:
        st.info("请先在页面顶部选择一个项目。")
    else:
        if st.button("生成任务流"):
            from flow_generator import generate_tasks
            import datetime as dt
            anchor_dt = dt.datetime.combine(anchor_date, anchor_time)
            generate_tasks(
                str(flow_map[chosen]),
                chosen_proj,
                {"n_samples": n_samples},
                anchor_dt
            )
            st.session_state["data_ver"] += 1
            st.success("✅ 已生成任务流！即将自动刷新显示新任务…")
            # 🔄 立即重载任务数据以验证生成结果
            import time
            import pandas as pd
            from ruamel.yaml import YAML
            yaml = YAML()
            rows = []
            for p in tasks_dir.glob("t-*.y*ml"):
                d = yaml.load(p.read_text(encoding="utf-8"))
                if "uid" not in d:
                    d["uid"] = uuid.uuid4().hex[:8]
                    with p.open("w", encoding="utf-8") as f:
                        yaml.dump(d, f)
                rows.append({
                    "__path": str(p),
                    "id": d.get("id"),
                    "uid": d.get("uid"),
                    "title": d.get("title", ""),
                    "project_uid": d.get("project_uid", d.get("project", "")),
                    "due": d.get("due", ""),
                    "status": d.get("status", ""),
                })
            df = pd.DataFrame(rows)
            st.dataframe(df[df["project_uid"] == chosen_proj])
            time.sleep(1.5)
            st.rerun()
# --- 单条任务 time_logs 编辑区 & “现在该做什么”推荐跳转 ---


# 文件结尾可用 st.success 或 st.rerun() 作为收尾
