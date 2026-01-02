# scripts/promote_smart.py
import sys, re, os, glob, uuid
from pathlib import Path
from datetime import datetime, timezone, timedelta
from ruamel.yaml import YAML
try:
    from rapidfuzz import process
    use_rf = True
except:
    from difflib import get_close_matches
    use_rf = False

ROOT = Path("/Users/gongbaoming/Library/CloudStorage/OneDrive-个人/life_os")
AREAS = ROOT/"01_areas"
OBJS  = ROOT/"02_objectives"
PROJS = ROOT/"03_projects"
TASKS = ROOT/"tasks"
ROUTES = ROOT/"routes.yml"
INBOX  = TASKS/"inbox.yml"

yaml = YAML()
yaml.indent(mapping=2, sequence=2, offset=2)

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def load_yaml_dir(d):
    items=[]
    for p in sorted(d.glob("*.y*ml")):
        data = yaml.load(p.read_text(encoding="utf-8")) or {}
        data["__path"]=str(p)
        items.append(data)
    return items

def next_uid(kind: str):
    # Each type has its own counter file
    counter_path = ROOT / f".uid_{kind}.txt"
    if not counter_path.exists():
        counter_path.write_text("1000")
    val = int(counter_path.read_text().strip())
    counter_path.write_text(str(val + 1))
    return val

# Helper to ensure UID on legacy items
def ensure_uid_in_item(d: dict, kind: str):
    if d.get("uid"):
        return d["uid"]
    uid = next_uid(kind)
    d["uid"] = uid
    # write back to file to persist
    p = Path(d.get("__path", ""))
    if p and p.exists():
        with p.open("w", encoding="utf-8") as f:
            yaml.dump(d, f)
    return uid

def load_index():
    areas = load_yaml_dir(AREAS)
    objs  = load_yaml_dir(OBJS)
    projs = load_yaml_dir(PROJS)
    # ensure legacy files have uid
    for a in areas:
        ensure_uid_in_item(a, "area")
    for o in objs:
        ensure_uid_in_item(o, "objective")
    for p in projs:
        ensure_uid_in_item(p, "project")
    idx = {
        "areas": [{
            "key": str(a["uid"]),
            "uid": a["uid"],
            "id": a.get("id"),
            "title": a.get("title", ""),
            "data": a,
        } for a in areas],
        "objs": [{
            "key": str(o["uid"]),
            "uid": o["uid"],
            "id": o.get("id"),
            "title": o.get("title", ""),
            # keep raw so we can resolve by either id or uid when filtering
            "data": o,
        } for o in objs],
        "projs": [{
            "key": str(p["uid"]),
            "uid": p["uid"],
            "id": p.get("id"),
            "title": p.get("title", ""),
            "data": p,
        } for p in projs],
    }
    return idx

def load_routes():
    if ROUTES.exists():
        r = yaml.load(ROUTES.read_text(encoding="utf-8")) or []
        return r
    return []

def match_routes(title, routes):
    for r in routes:
        when = r.get("when", [])
        if any(w.lower() in title.lower() for w in when):
            return r.get("set", {})
        if m:=r.get("when_regex"):
            if re.search(m, title):
                return r.get("set", {})
    return {}

def fuzzy_pick(cands, query, key="title", limit=5):
    corpus = {f"{c['key']} {c.get('title','')}": c for c in cands}
    keys = list(corpus.keys())
    if use_rf:
        res = process.extract(query, keys, limit=limit)
        ordered = [corpus[k] for k,score,idx in res]
    else:
        res = get_close_matches(query, keys, n=limit, cutoff=0.0)
        ordered = [corpus[k] for k in res]
    return ordered

def ensure_inbox():
    if not INBOX.exists():
        INBOX.write_text("ideas: []\n", encoding="utf-8")
    data = yaml.load(INBOX.read_text(encoding="utf-8")) or {}
    data.setdefault("ideas", [])
    return data

def save_inbox(data):
    with INBOX.open("w", encoding="utf-8") as f:
        yaml.dump(data, f)

def next_task_id():
    today = datetime.now().strftime("%Y%m%d")
    existing = sorted(TASKS.glob(f"t-{today}-*.yaml"))
    n = 1 if not existing else int(existing[-1].stem.split("-")[-1])+1
    return f"t-{today}-{n:03d}"

def slugify(text):
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    text = re.sub(r"^-+|-+$", "", text)
    return text

def prompt_pick(title, cands, label):
    print(f"\n{label} 候选（按回车接受 #1，或输入序号）：")
    for i,c in enumerate(cands,1):
        print(f"  {i}. {c['key']} — {c.get('title','')}")
    print("  n. ➕ 新建")
    print("  d. ❌ 删除此灵感")
    print("  b. ↩️ 返回上一步")
    print("  s. ✅ 停在此层级并生成此层对象")
    sel = input("> ").strip()
    if sel=="":
        return cands[0] if cands else None
    if sel.lower() == "d":
        return "DELETE"
    if sel.lower() == "b":
        return "BACK"
    if sel.lower() == "n":
        return "NEW"
    if sel.lower() == "s":
        return "STOP"
    if sel.isdigit() and 1<=int(sel)<=len(cands):
        return cands[int(sel)-1]
    # 手输关键字再匹配
    typed = sel
    return fuzzy_pick(cands, typed, limit=1)[0] if cands else None

def promote_one(idea):
    idx = load_index()
    routes = load_routes()

    title = idea.get("title", "").strip()
    area_uid = None; area_id = None
    obj_uid = None;  obj_id  = None
    proj_uid = None; proj_id = None

    def clear_and_print_context():
        # Clear screen and print title and context
        print("\033c", end="")
        print(f"当前任务：{title}")
        print(f"已选择：Area={area_id or area_uid or '-'} | Objective={obj_id or obj_uid or '-'} | Project={proj_id or proj_uid or '-'}")

    print("\n==== 提升想法为任务 ====")
    print("标题：", title)  # 将标题提前打印

    preset = match_routes(title, routes)

    while True:
        clear_and_print_context()
        # Area
        area_cands = fuzzy_pick(idx["areas"], title, limit=5)
        if preset.get("area"):
            area_cands = [next((a for a in idx["areas"] if a["key"] == str(preset["area"])), area_cands[0])] + area_cands
        area = prompt_pick(title, area_cands, "Area")
        if area == "BACK":
            print("已经是最上层，无法回退。")
            continue
        if area == "DELETE":
            print("✗ 已标记删除此灵感")
            return "DELETE"
        if area == "NEW":
            while True:
                clear_and_print_context()
                print("新建 Area")
                new_id = input("新 Area id（输入 b 返回）：").strip()
                if new_id.lower() == "b":
                    # 返回到 Area 候选列表
                    area = "BACK"
                    break
                if not new_id:
                    print("id 不能为空")
                    continue
                new_file = AREAS / f"{new_id}.yaml"
                if new_file.exists():
                    print("⚠️ 该 id 已存在，请重新输入或加后缀")
                    continue
                new_title = input("新 Area title（输入 b 返回）：").strip()
                if new_title.lower() == "b":
                    area = "BACK"
                    break
                with new_file.open("w", encoding="utf-8") as f:
                    yaml.dump({"id": new_id, "uid": next_uid("area"), "title": new_title, "created_at": now_iso()}, f)
                print(f"✓ 已创建新 Area: {new_file}")
                # 更新索引，并把 area_uid/area_id 设置为新建 area
                idx = load_index()
                area = next(a for a in idx["areas"] if a["id"] == new_id)
                area_uid = area.get("uid") or int(area.get("key"))
                area_id  = area.get("id") or area["data"].get("id")
                break
            if area == "BACK":
                continue
        if area == "STOP":
            # 生成 Objective 并返回路径
            slug = slugify(title)
            new_id = f"okr-{slug}"
            # 如果文件存在，添加随机后缀避免冲突
            new_file = OBJS / f"{new_id}.yaml"
            if new_file.exists():
                new_id = f"okr-{slug}-{uuid.uuid4().hex[:6]}"
                new_file = OBJS / f"{new_id}.yaml"
            data = {
                "id": new_id,
                "uid": next_uid("objective"),
                "title": title,
                "area": None,
                "area_uid": None,  # 顶层停下不绑定具体 area
                "created_at": now_iso(),
            }
            with new_file.open("w", encoding="utf-8") as f:
                yaml.dump(data, f)
            print(f"✓ 已生成 Objective 文件（停在 Area 层级）：{new_file}")
            return new_file

        if area:
            area_uid = area.get("uid") or int(area.get("key"))
            area_id  = area.get("id") or area["data"].get("id")
        else:
            area_uid = area_id = None

        while True:
            clear_and_print_context()
            # Objective（同区过滤，优先用 area_uid 过滤）
            obj_pool = []
            for o in idx["objs"]:
                od = o["data"]
                if not area_uid:
                    obj_pool.append(o)
                else:
                    if (od.get("area_uid") == area_uid) or (od.get("area") == area_id):
                        obj_pool.append(o)
            obj_cands = fuzzy_pick(obj_pool, title, limit=5)
            if preset.get("objective"):
                obj_cands = [next((o for o in obj_pool if o["key"] == str(preset["objective"])), obj_cands[0])] + obj_cands
            objective = prompt_pick(title, obj_cands, "Objective")
            if objective == "BACK":
                # 回退到 Area 重新选择
                break
            if objective == "DELETE":
                print("✗ 已标记删除此灵感")
                return "DELETE"
            if objective == "NEW":
                while True:
                    clear_and_print_context()
                    print("新建 Objective")
                    new_id = input("新 Objective id（输入 b 返回）：").strip()
                    if new_id.lower() == "b":
                        # 返回到 Objective 候选列表
                        objective = "BACK"
                        break
                    if not new_id:
                        print("id 不能为空")
                        continue
                    new_file = OBJS / f"{new_id}.yaml"
                    if new_file.exists():
                        print("⚠️ 该 id 已存在，请重新输入或加后缀")
                        continue
                    new_title = input("新 Objective title（输入 b 返回）：").strip()
                    if new_title.lower() == "b":
                        objective = "BACK"
                        break
                    with new_file.open("w", encoding="utf-8") as f:
                        yaml.dump({
                            "id": new_id,
                            "uid": next_uid("objective"),
                            "title": new_title,
                            "area": area_id,
                            "area_uid": area_uid,
                            "created_at": now_iso()
                        }, f)
                    print(f"✓ 已创建新 Objective: {new_file}")
                    idx = load_index()
                    objective = next(o for o in idx["objs"] if o["id"] == new_id)
                    obj_uid = objective.get("uid") or int(objective.get("key"))
                    obj_id  = objective.get("id") or objective["data"].get("id")
                    break
                if objective == "BACK":
                    continue
            if objective == "STOP":
                # 生成 Project 并返回路径
                slug = slugify(title)
                new_id = f"prj-{slug}"
                new_file = PROJS / f"{new_id}.yaml"
                if new_file.exists():
                    new_id = f"prj-{slug}-{uuid.uuid4().hex[:6]}"
                    new_file = PROJS / f"{new_id}.yaml"
                data = {
                    "id": new_id,
                    "uid": next_uid("project"),
                    "title": title,
                    "area": area_id,
                    "area_uid": area_uid,
                    "objective": None,
                    "objective_uid": None,
                    "created_at": now_iso(),
                }
                with new_file.open("w", encoding="utf-8") as f:
                    yaml.dump(data, f)
                print(f"✓ 已生成 Project 文件（停在 Objective 层级）：{new_file}")
                return new_file

            if objective:
                obj_uid = objective.get("uid") or int(objective.get("key"))
                obj_id  = objective.get("id") or objective["data"].get("id")
            else:
                obj_uid = obj_id = None

            while True:
                clear_and_print_context()
                # Project 过滤：优先 area_uid/objective_uid, fallback id, 允许没有 objective 的项目
                proj_pool = []
                for p in idx["projs"]:
                    pd = p["data"]
                    if area_uid and not (pd.get("area_uid") == area_uid or pd.get("area") == area_id):
                        continue
                    if obj_uid:
                        if pd.get("objective_uid") == obj_uid or pd.get("objective") == obj_id or pd.get("objective") in (None, ""):
                            proj_pool.append(p)
                    else:
                        proj_pool.append(p)
                proj_cands = fuzzy_pick(proj_pool, title, limit=5)
                if preset.get("project"):
                    proj_cands = [next((p for p in proj_pool if p["key"] == str(preset["project"])), proj_cands[0])] + proj_cands
                project = prompt_pick(title, proj_cands, "Project")
                if project == "BACK":
                    # 回退到 Objective 重新选择
                    break
                if project == "DELETE":
                    print("✗ 已标记删除此灵感")
                    return "DELETE"
                if project == "NEW":
                    while True:
                        clear_and_print_context()
                        print("新建 Project")
                        new_id = input("新 Project id（输入 b 返回）：").strip()
                        if new_id.lower() == "b":
                            # 返回到 Project 候选列表
                            project = "BACK"
                            break
                        if not new_id:
                            print("id 不能为空")
                            continue
                        new_file = PROJS / f"{new_id}.yaml"
                        if new_file.exists():
                            print("⚠️ 该 id 已存在，请重新输入或加后缀")
                            continue
                        new_title = input("新 Project title（输入 b 返回）：").strip()
                        if new_title.lower() == "b":
                            project = "BACK"
                            break
                        with new_file.open("w", encoding="utf-8") as f:
                            yaml.dump({
                                "id": new_id,
                                "uid": next_uid("project"),
                                "title": new_title,
                                "area": area_id,
                                "area_uid": area_uid,
                                "objective": obj_id,
                                "objective_uid": obj_uid,
                                "created_at": now_iso()
                            }, f)
                        print(f"✓ 已创建新 Project: {new_file}")
                        idx = load_index()
                        project = next(p for p in idx["projs"] if p["id"] == new_id)
                        proj_uid = project.get("uid") or int(project.get("key"))
                        proj_id  = project.get("id") or project["data"].get("id")
                        break
                    if project == "BACK":
                        continue
                if project == "STOP":
                    # 生成 Project 并返回路径
                    slug = slugify(title)
                    new_id = f"prj-{slug}"
                    new_file = PROJS / f"{new_id}.yaml"
                    if new_file.exists():
                        new_id = f"prj-{slug}-{uuid.uuid4().hex[:6]}"
                        new_file = PROJS / f"{new_id}.yaml"
                    data = {
                        "id": new_id,
                        "uid": next_uid("project"),
                        "title": title,
                        "area": area_id,
                        "area_uid": area_uid,
                        "objective": obj_id,
                        "objective_uid": obj_uid,
                        "created_at": now_iso(),
                    }
                    with new_file.open("w", encoding="utf-8") as f:
                        yaml.dump(data, f)
                    print(f"✓ 已生成 Project 文件（停在 Project 层级）：{new_file}")

                    # 自动生成初始任务
                    tid = next_task_id()
                    task = {
                        "id": tid,
                        "uid": next_uid("task"),
                        "title": f"规划第一步：细化项目【{title}】",
                        "status": "todo",
                        "importance": "P2",
                        "difficulty": 3,
                        "duration_minutes": 60,
                        "created_at": now_iso(),
                        "updated_at": now_iso(),
                        "due": (datetime.now() + timedelta(days=7)).date().isoformat(),
                        "area_uid": area_uid,
                        "objective_uid": obj_uid,
                        "project_uid": data["uid"],
                    }
                    # legacy id references for compatibility
                    if area_id is not None:
                        task["area"] = area_id
                    if obj_id is not None:
                        task["objective"] = obj_id
                    # always set project as new string id
                    task["project"] = data["id"]
                    outp = TASKS / f"{tid}.yaml"
                    with outp.open("w", encoding="utf-8") as f:
                        yaml.dump(task, f)
                    print(f"✓ 已自动生成初始任务：{outp}")
                    return new_file
                elif project:
                    proj_uid = project.get("uid") or int(project.get("key"))
                    proj_id  = project.get("id") or project["data"].get("id")

                # 任务类型选择（在 Project 选择后，生成任务前）
                while True:
                    clear_and_print_context()
                    kind_sel = input("选择任务类型： t = 执行型（do） | q = 思考型（think） | d = 删除此灵感 | b = 返回上一步\n> ").strip().lower()
                    if kind_sel in ["t", "q", "d", "b", ""]:
                        break
                    print("请输入 t / q / d / b")
                if kind_sel == "d":
                    print("✗ 已标记删除此灵感")
                    return "DELETE"
                if kind_sel == "b":
                    # 回到 Project 选择
                    continue
                kind = "think" if kind_sel == "q" else "do"

                # 其他字段（可用预设/默认）
                importance = preset.get("importance") or "P2"
                difficulty = int(preset.get("difficulty") or 3)
                duration = int(preset.get("duration_minutes") or 60)
                # 到期日：允许 b 返回上一层（Project 选择），并做格式校验
                while True:
                    clear_and_print_context()
                    due_raw = input("到期日 (YYYY-MM-DD，可空回车，b 返回)：").strip()
                    if due_raw.lower() == "b":
                        # 回到 Project 选择
                        break
                    if due_raw:
                        try:
                            datetime.strptime(due_raw, "%Y-%m-%d")
                            due = due_raw
                            break
                        except ValueError:
                            print("⚠️ 日期格式无效，请重新输入")
                    else:
                        due = None
                        break
                if due_raw.lower() == "b":
                    continue

                # 生成任务
                tid = next_task_id()
                task = {
                    "id": tid,
                    "uid": next_uid("task"),
                    "title": title,
                    "status": "todo",
                    "importance": importance,
                    "difficulty": difficulty,
                    "duration_minutes": duration,
                    "kind": kind,
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                }
                # uid-based refs
                if area_uid is not None: task["area_uid"] = area_uid
                if obj_uid  is not None: task["objective_uid"] = obj_uid
                if proj_uid is not None: task["project_uid"] = proj_uid
                # legacy ids kept for compatibility (optional)
                if area_id: task["area"] = area_id
                if obj_id:  task["objective"] = obj_id
                if proj_id: task["project"] = proj_id
                if due:     task["due"] = due

                outp = TASKS / f"{tid}.yaml"
                with outp.open("w", encoding="utf-8") as f:
                    yaml.dump(task, f)
                print("✓ 已生成任务：", outp)
                return outp

def main():
    mode = "--one" if "--one" in sys.argv else "--all" if "--all" in sys.argv else ""
    inbox = ensure_inbox()
    ideas = inbox["ideas"]
    if not ideas:
        print("Inbox 为空。先用 `l capture \"标题\"` 把灵感记进去。")
        return
    if mode=="--one":
        ideas = [ideas[-1]]
    # 逐条处理，并在每条完成后立即从 inbox 移除并保存
    for idea in ideas[:]:
        original_idea = idea
        if isinstance(idea, str):
            idea = {"title": idea}
        try:
            out = promote_one(idea)
            if out == "DELETE":
                inbox["ideas"].remove(original_idea)
                save_inbox(inbox)
                continue
            # 成功生成任务文件后立即移除
            inbox["ideas"].remove(original_idea)
            save_inbox(inbox)
            print(f"✓ 已从 Inbox 立即移除 1 条想法：{idea.get('title','')}")
        except KeyboardInterrupt:
            print("\n中断。已保存进度，下次运行将从剩余灵感继续。")
            break

if __name__ == "__main__":
    main()
