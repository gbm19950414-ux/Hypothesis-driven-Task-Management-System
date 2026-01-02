import streamlit as st
from streamlit_calendar import calendar

st.set_page_config(page_title="Year View Test", layout="wide")

st.markdown("## 🧪 streamlit-calendar 年视图自测")

events = []  # 暂时不放事件，只测视图本身是否可用

calendar_options = {
    "initialView": "multiMonthYear",
    "headerToolbar": {
        "left": "today prev,next",
        "center": "title",
        "right": "dayGridMonth,timeGridWeek,timeGridDay,multiMonthYear",
    },
    # 为 year view 定义一个自定义视图：12 个月
    "views": {
        "multiMonthYear": {
            "type": "multiMonth",
            "duration": {"months": 12},
        }
    },
    "locale": "zh-cn",
    "height": "auto",
}

cal_state = calendar(
    events=events,
    options=calendar_options,
    key="year_view_test",
    # 如果你在主项目有用 callbacks，可以先不传，避免干扰
)

st.write("状态回调（调试用）：", cal_state)
