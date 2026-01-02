import streamlit as st
from pathlib import Path
from datetime import datetime, timezone
from ruamel.yaml import YAML
from rapidfuzz import process, fuzz
import re

# =========================
# Paths & YAML helpers
# =========================
ROOT = Path(__file__).resolve().parent.parent
AREAS = ROOT / "01_areas"
OBJS  = ROOT / "02_objectives"
PROJS = ROOT / "03_projects"
TASKS = ROOT / "tasks"

for p in [AREAS, OBJS, PROJS, TASKS]:
    p.mkdir(parents=True, exist_ok=True)

yaml = YAML()
yaml.indent(mapping=2, sequence=2, offset=2)

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def read_yaml(path: Path):
    try:
        with path.open("r", encoding="utf-8") as f:
            return yaml.load(f) or {}
    except FileNotFoundError:
        return {}

def write_yaml(path: Path, data: dict):
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f)

# load a dir of yaml/yml

def load_dir(dirpath: Path):
    items = []
    for ext in ("*.yaml", "*.yml"):
        for p in sorted(dirpath.glob(ext)):
            d = read_yaml(p)
            if isinstance(d, dict):
                d["__path"] = str(p)
                items.append(d)
    return items

# =========================
# Index & ID helpers
# =========================

def ensure_id_title(items):
    out = []
    for d in items:
        if not isinstance(d, dict):
            continue
        if not d.get("id"):
            # derive id from filename
            fid = Path(d.get("__path", "")).stem
            d["id"] = fid
        if not d.get("title"):
            d["title"] = d["id"]
        out.append(d)
    return out

@st.cache_data(show_spinner=False)
def load_indexes():
    areas = ensure_id_title(load_dir(AREAS))
    objs  = ensure_id_title(load_dir(OBJS))
    projs = ensure_id_title(load_dir(PROJS))
    return areas, objs, projs

@st.cache_data(show_spinner=False)
def list_ids(items):
    return [d.get("id") for d in items]

@st.cache_data(show_spinner=False)
def next_task_id(today: str):
    # today format: YYYYMMDD
    pattern = re.compile(rf"^t-{today}-(\\d{{3}})$")
    max_n = 0
    for p in TASKS.glob("t-*.yaml"):
        m = pattern.match(p.stem)
        if m:
            try:
                max_n = max(max_n, int(m.group(1)))
            except ValueError:
                pass
    return f"t-{today}-{max_n+1:03d}"

# =========================
# Fuzzy search helpers
# =========================

def fuzzy_filter(items, query, key_fn=lambda d: f"{d.get('id')} {d.get('title')}"):
    if not query:
        return items
    corpus = {idx: key_fn(d) for idx, d in enumerate(items)}
    ranked = process.extract(
        query,
        corpus,
        scorer=fuzz.WRatio,
        limit=20,
    )
    # ranked: list of (matched_string, score, idx)
    keep_idx = [idx for _, score, idx in ranked if score >= 40]
    return [items[i] for i in keep_idx]

# =========================
# UI
# =========================
st.set_page_config(page_title="Promote Smart (UI)", layout="wide")
st.title("🧠 Promote Smart UI")
st.caption("把灵感快速提升为任务：Area → Objective → Project → 任务属性 → 保存到 tasks/")

if "sel" not in st.session_state:
    st.session_state.sel = {
        "title": "",
        "area": None,
        "objective": None,
        "project": None,
        "kind": "do",
        "importance": "P2",
        "difficulty": 3,
        "duration_minutes": 60,
        "due": None,
        "tags": [],
        "notes": "",
    }

# Reload data button
colR1, colR2 = st.columns([1, 3])
with colR1:
    if st.button("🔄 重新载入索引", use_container_width=True):
        load_indexes.clear()
        list_ids.clear()
        st.experimental_rerun()

areas, objs, projs = load_indexes()

# Step 0: Title
st.subheader("① 标题")
st.session_state.sel["title"] = st.text_input(
    "输入灵感标题（必填）",
    value=st.session_state.sel.get("title", ""),
    placeholder="例如：WB：验证抗体批次B；或：界定 Nlrp3 肥胖表型的证据标准",
)

# Step 1: Area
st.subheader("② 选择 Area（领域）")
q_area = st.text_input("筛选 Area（模糊）", key="q_area")
areas_filtered = fuzzy_filter(areas, q_area)
area_options = [f"{a['id']} — {a.get('title','')}" for a in areas_filtered] or ["（无可选项）"]
area_idx = 0
if st.session_state.sel.get("area"):
    for i, a in enumerate(areas_filtered):
        if a["id"] == st.session_state.sel["area"]:
            area_idx = i
            break
area_choice = st.selectbox("Area", options=area_options, index=area_idx, key="area_choice")

colA1, colA2 = st.columns([3,1])
with colA1:
    st.caption("若不存在，可在右侧新建 Area")
with colA2:
    with st.popover("➕ 新建 Area"):
        new_area_id = st.text_input("新 Area id")
        new_area_title = st.text_input("新 Area title")
        if st.button("创建 Area"):
            if not new_area_id:
                st.warning("id 不能为空")
            else:
                outp = AREAS / f"{new_area_id}.yaml"
                if outp.exists():
                    st.error("该 id 已存在")
                else:
                    write_yaml(outp, {"id": new_area_id, "title": new_area_title or new_area_id, "created_at": now_iso()})
                    load_indexes.clear(); list_ids.clear()
                    st.success(f"已创建：{outp}")
                    st.experimental_rerun()

# resolve selected area id
if areas_filtered:
    sel_area_obj = areas_filtered[area_options.index(area_choice)]
    st.session_state.sel["area"] = sel_area_obj["id"]
else:
    st.session_state.sel["area"] = None

# Step 2: Objective（按 Area 过滤）
st.subheader("③ 选择 Objective（目标/候选章节）")
objs_pool = [o for o in objs if (not st.session_state.sel["area"] or o.get("area") == st.session_state.sel["area"])]
q_obj = st.text_input("筛选 Objective（模糊）", key="q_obj")
objs_filtered = fuzzy_filter(objs_pool, q_obj)
obj_options = [f"{o['id']} — {o.get('title','')}" for o in objs_filtered] or ["（无可选项）"]
obj_idx = 0
if st.session_state.sel.get("objective"):
    for i, o in enumerate(objs_filtered):
        if o["id"] == st.session_state.sel["objective"]:
            obj_idx = i
            break
obj_choice = st.selectbox("Objective", options=obj_options, index=obj_idx, key="obj_choice")

colO1, colO2 = st.columns([3,1])
with colO1:
    st.caption("若不存在，可在右侧新建 Objective")
with colO2:
    with st.popover("➕ 新建 Objective"):
        new_obj_id = st.text_input("新 Objective id")
        new_obj_title = st.text_input("新 Objective title")
        if st.button("创建 Objective"):
            if not st.session_state.sel.get("area"):
                st.warning("请先选择 Area 再创建 Objective")
            elif not new_obj_id:
                st.warning("id 不能为空")
            else:
                outp = OBJS / f"{new_obj_id}.yaml"
                if outp.exists():
                    st.error("该 id 已存在")
                else:
                    write_yaml(outp, {"id": new_obj_id, "title": new_obj_title or new_obj_id, "area": st.session_state.sel["area"], "created_at": now_iso()})
                    load_indexes.clear(); list_ids.clear()
                    st.success(f"已创建：{outp}")
                    st.experimental_rerun()

# resolve selected objective id
if objs_filtered:
    sel_obj_obj = objs_filtered[obj_options.index(obj_choice)]
    st.session_state.sel["objective"] = sel_obj_obj["id"]
else:
    st.session_state.sel["objective"] = None

# Step 3: Project（按 Area + Objective 过滤）
st.subheader("④ 选择 Project（方法线路）")
projs_pool = [p for p in projs if \
              (not st.session_state.sel["area"] or p.get("area") == st.session_state.sel["area"]) and \
              (not st.session_state.sel["objective"] or p.get("objective") == st.session_state.sel["objective"]) ]
q_proj = st.text_input("筛选 Project（模糊）", key="q_proj")
projs_filtered = fuzzy_filter(projs_pool, q_proj)
proj_options = [f"{p['id']} — {p.get('title','')}" for p in projs_filtered] or ["（无可选项）"]
proj_idx = 0
if st.session_state.sel.get("project"):
    for i, p in enumerate(projs_filtered):
        if p["id"] == st.session_state.sel["project"]:
            proj_idx = i
            break
proj_choice = st.selectbox("Project", options=proj_options, index=proj_idx, key="proj_choice")

colP1, colP2 = st.columns([3,1])
with colP1:
    st.caption("若不存在，可在右侧新建 Project")
with colP2:
    with st.popover("➕ 新建 Project"):
        new_proj_id = st.text_input("新 Project id")
        new_proj_title = st.text_input("新 Project title")
        if st.button("创建 Project"):
            if not st.session_state.sel.get("area") or not st.session_state.sel.get("objective"):
                st.warning("请先选择 Area 和 Objective 再创建 Project")
            elif not new_proj_id:
                st.warning("id 不能为空")
            else:
                outp = PROJS / f"{new_proj_id}.yaml"
                if outp.exists():
                    st.error("该 id 已存在")
                else:
                    write_yaml(outp, {
                        "id": new_proj_id,
                        "title": new_proj_title or new_proj_id,
                        "area": st.session_state.sel["area"],
                        "objective": st.session_state.sel["objective"],
                        "created_at": now_iso(),
                    })
                    load_indexes.clear(); list_ids.clear()
                    st.success(f"已创建：{outp}")
                    st.experimental_rerun()

# resolve selected project id
if projs_filtered:
    sel_proj_obj = projs_filtered[proj_options.index(proj_choice)]
    st.session_state.sel["project"] = sel_proj_obj["id"]
else:
    st.session_state.sel["project"] = None

# Step 4: 任务属性
st.subheader("⑤ 任务属性")
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.session_state.sel["kind"] = st.selectbox("任务类型", ["do", "think"], index=["do","think"].index(st.session_state.sel.get("kind","do")))
with col2:
    st.session_state.sel["importance"] = st.selectbox("重要性", ["P1","P2","P3"], index=["P1","P2","P3"].index(st.session_state.sel.get("importance","P2")))
with col3:
    st.session_state.sel["difficulty"] = st.slider("难度 (1-5)", 1, 5, int(st.session_state.sel.get("difficulty",3)))
with col4:
    st.session_state.sel["duration_minutes"] = st.number_input("预计时长(分钟)", min_value=15, step=15, value=int(st.session_state.sel.get("duration_minutes",60)))

col5, col6 = st.columns([1,3])
with col5:
    due_date = st.date_input("到期日(可选)", value=None, format="YYYY-MM-DD")
    st.session_state.sel["due"] = due_date.isoformat() if due_date else None
with col6:
    tags_str = st.text_input("标签(逗号分隔，可选)", value=",".join(st.session_state.sel.get("tags", [])))
    st.session_state.sel["tags"] = [t.strip() for t in tags_str.split(",") if t.strip()]

notes = st.text_area("备注/说明(可选)", value=st.session_state.sel.get("notes", ""))
st.session_state.sel["notes"] = notes

# Step 5: 保存
st.subheader("⑥ 保存到 tasks/")
can_save = bool(st.session_state.sel.get("title")) and bool(st.session_state.sel.get("area"))
st.caption("保存条件：已填写标题，且至少选择 Area。Objective/Project 可选但推荐。")

colS1, colS2 = st.columns([1,3])
with colS1:
    do_save = st.button("💾 保存任务", type="primary", use_container_width=True, disabled=not can_save)
with colS2:
    if st.button("🧹 清空表单", use_container_width=True):
        st.session_state.sel = {
            "title": "",
            "area": None,
            "objective": None,
            "project": None,
            "kind": "do",
            "importance": "P2",
            "difficulty": 3,
            "duration_minutes": 60,
            "due": None,
            "tags": [],
            "notes": "",
        }
        st.experimental_rerun()

if do_save:
    title = st.session_state.sel["title"].strip()
    area  = st.session_state.sel["area"]
    obj   = st.session_state.sel.get("objective")
    proj  = st.session_state.sel.get("project")
    kind  = st.session_state.sel.get("kind", "do")
    importance = st.session_state.sel.get("importance", "P2")
    difficulty = int(st.session_state.sel.get("difficulty", 3))
    duration   = int(st.session_state.sel.get("duration_minutes", 60))
    due_str    = st.session_state.sel.get("due")
    tags       = st.session_state.sel.get("tags", [])
    notes_val  = st.session_state.sel.get("notes", "")

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    tid   = next_task_id(today)

    task = {
        "id": tid,
        "title": title,
        "status": "todo",
        "kind": kind,
        "importance": importance,
        "difficulty": difficulty,
        "duration_minutes": duration,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "tags": tags,
    }
    if area: task["area"] = area
    if obj:  task["objective"] = obj
    if proj: task["project"] = proj
    if due_str: task["due"] = due_str
    if notes_val: task["notes"] = notes_val

    outp = TASKS / f"{tid}.yaml"
    write_yaml(outp, task)

    st.success(f"✓ 已生成任务：{outp}")
    st.balloons()

    # 清空标题，保留 A/O/P 以便连续创建同类任务
    st.session_state.sel["title"] = ""
    st.experimental_rerun()
