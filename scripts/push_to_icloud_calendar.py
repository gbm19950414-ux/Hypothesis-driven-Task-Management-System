from pathlib import Path
from ruamel.yaml import YAML
from datetime import datetime, timedelta, time
from caldav import DAVClient
from icalendar import Event, Calendar
from operator import itemgetter

# 读取 iCloud 凭据
USERNAME = "1120252519@qq.com"
PASSWORD = "tpja-rfhr-jmos-ndeo"
ICLOUD_CALENDAR_URL = "https://caldav.icloud.com/"  # iCloud CalDAV 根地址

# 连接到 iCloud CalDAV
client = DAVClient(url=ICLOUD_CALENDAR_URL, username=USERNAME, password=PASSWORD)
principal = client.principal()
calendars = principal.calendars()

# 选择一个日历，如果你有多个日历可以用名字筛选
calendar = [c for c in calendars if "LifeOS" in c.name][0]

# 加载 YAML 任务
yaml = YAML()
tasks_dir = Path("/Users/gongbaoming/Library/CloudStorage/OneDrive-个人/life_os/tasks")

tasks = []
for p in tasks_dir.glob("t-*.yaml"):
    data = yaml.load(p.read_text(encoding="utf-8"))
    if not data.get("due"):
        continue
    tasks.append(data)

def importance_rank(x):
    return {"P1": 1, "P2": 2, "P3": 3}.get(x.get("importance","P3"), 3)
tasks.sort(key=lambda x: (importance_rank(x), x["due"]))

from datetime import date as _date

work_morning_start, work_morning_end = time(7, 0), time(10, 30)
work_afternoon_start, work_afternoon_end = time(11, 30), time(18, 0)

# 记录每天的“指针时间”，表示该天已安排到哪里
day_pointer = {}

# 取今天（按本地时区）
_today = datetime.now().date()

for data in tasks:
    due_date = datetime.fromisoformat(data["due"]).date()
    duration_left = int(data.get("duration_minutes", 60))  # 还需安排的工作分钟数（不含午休）

    # 从今天开始尝试排，直到截止日
    cur_day = max(_today, _today)
    if cur_day > due_date:
        print(f"[跳过] 任务 {data['id']} 的截止日已过：{due_date}")
        continue

    while duration_left > 0 and cur_day <= due_date:
        # 当天已安排到的时间点（墙钟）
        cur_pointer = day_pointer.get(cur_day)
        if cur_pointer is None:
            cur_pointer = datetime.combine(cur_day, work_morning_start)
        else:
            cur_pointer = cur_pointer

        # 若指针落在午休或下班后，移动到可工作的下一个时间点
        if work_morning_end <= cur_pointer.time() < work_afternoon_start:
            cur_pointer = datetime.combine(cur_day, work_afternoon_start)
        if cur_pointer.time() < work_morning_start:
            cur_pointer = datetime.combine(cur_day, work_morning_start)
        if cur_pointer.time() >= work_afternoon_end:
            # 今天排不下，换明天
            cur_day = cur_day + timedelta(days=1)
            continue

        # 计算当天从 cur_pointer 开始的“可工作分钟数”（不含午休）
        def available_work_minutes(start_dt: datetime) -> int:
            if start_dt.time() < work_morning_end:
                m1 = int((datetime.combine(cur_day, work_morning_end) - start_dt).total_seconds() // 60)
                m2 = int((datetime.combine(cur_day, work_afternoon_end) - datetime.combine(cur_day, work_afternoon_start)).total_seconds() // 60)
                return max(0, m1) + max(0, m2)
            elif start_dt.time() < work_afternoon_start:
                # 午休，应该被前面的对齐处理掉
                return int((datetime.combine(cur_day, work_afternoon_end) - datetime.combine(cur_day, work_afternoon_start)).total_seconds() // 60)
            else:
                return int((datetime.combine(cur_day, work_afternoon_end) - start_dt).total_seconds() // 60)

        can_work = available_work_minutes(cur_pointer)
        if can_work <= 0:
            cur_day = cur_day + timedelta(days=1)
            continue

        # 本天要安排的工作分钟数（不含午休）
        chunk_work = min(duration_left, can_work)

        # 计算事件的墙钟结束时间：如果跨越 10:30→11:30，需要 +60 分钟覆盖午休
        if cur_pointer.time() < work_morning_end and chunk_work > int((datetime.combine(cur_day, work_morning_end) - cur_pointer).total_seconds() // 60):
            # 会跨过午休
            morning_work = int((datetime.combine(cur_day, work_morning_end) - cur_pointer).total_seconds() // 60)
            afternoon_needed = chunk_work - morning_work
            event_end_dt = datetime.combine(cur_day, work_afternoon_start) + timedelta(minutes=afternoon_needed)
        else:
            # 不跨午休
            event_end_dt = cur_pointer + timedelta(minutes=chunk_work)

        # 若墙钟结束晚于 18:00，则截断到 18:00，并相应减少今日可安排的工作量
        if event_end_dt.time() > work_afternoon_end:
            over_minutes = int((event_end_dt - datetime.combine(cur_day, work_afternoon_end)).total_seconds() // 60)
            if over_minutes > 0:
                # 有溢出，则把今天的 chunk_work 回退相同工作分钟数
                chunk_work -= over_minutes
                event_end_dt = datetime.combine(cur_day, work_afternoon_end)

        # 写入单个事件（跨午休的情况已经通过墙钟时间延长 1h 覆盖，不再拆分）
        cal = Calendar()
        cal.add('prodid', '-//LifeOS//example.com//')
        cal.add('version', '2.0')
        event = Event()
        # 同一任务可能跨多天安排，确保 UID 唯一：加上日期后缀
        uid_suffix = cur_day.strftime('%Y%m%d')
        event.add('uid', f"{data['id']}-{uid_suffix}")
        event.add('summary', data["title"])
        event.add('description',
                  f"Area: {data.get('area','')} | Objective: {data.get('objective','')} | Project: {data.get('project','')} | Importance: {data.get('importance','')} | Difficulty: {data.get('difficulty','')}")
        event.add('dtstart', cur_pointer)
        event.add('dtend', event_end_dt)
        cal.add_component(event)
        calendar.add_event(cal.to_ical())
        print(f"任务 {data['id']} 已同步到 Apple 日历 ({cur_pointer}-{event_end_dt})")

        # 更新剩余工作量与当天指针
        duration_left -= chunk_work
        day_pointer[cur_day] = event_end_dt

        # 如果今天已到 18:00，则换到下一天继续
        if day_pointer[cur_day].time() >= work_afternoon_end:
            cur_day = cur_day + timedelta(days=1)

    if duration_left > 0:
        print(f"[警告] 任务 {data['id']} 未能在截止日前排完，剩余 {duration_left} 分钟未排。")
