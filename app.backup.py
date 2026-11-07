"""Start with: python -m streamlit run app.py"""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from agent_engine import add_ui, add_user_request, advance, approve_or_reject, new_state
from local_tools import LocalTools, make_demo_files
from ollama_client import OllamaClient, OllamaError

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT / "workspace"
WORKSPACE.mkdir(parents=True, exist_ok=True)

st.set_page_config(page_title="ایجنت محلی | Human-in-the-Loop", page_icon="🤖", layout="centered")
st.title("ایجنت محلی فارسی")
st.caption("Ollama + Qwen3 + Python | اجرای کنترل‌شده روی پوشه workspace")

if "agent" not in st.session_state:
    st.session_state.agent = new_state()
state = st.session_state.agent

with st.sidebar:
    st.subheader("تنظیمات مدل")
    model_name = st.text_input("مدل محلی", value="qwen3:8b")
    client = OllamaClient(model_name)
    if st.button("بررسی اتصال Ollama", use_container_width=True):
        try:
            models = client.installed_models()
            if model_name in models:
                st.success(f"Ollama متصل است و مدل {model_name} نصب شده است.")
            else:
                st.warning("Ollama متصل است، اما مدل انتخابی نصب نیست. در ترمینال اجرا کن: ollama pull " + model_name)
                if models:
                    st.caption("مدل‌های موجود: " + ", ".join(models))
        except OllamaError as exc:
            st.error(str(exc) + "\n\nOllama را باز کن یا در ترمینال `ollama serve` بزن.")
    st.divider()
    st.info("دسترسی فقط داخل workspace است. ایجاد پوشه و جابه‌جایی تأیید می‌خواهد. حذف فایل همیشه شبیه‌سازی است.")
    st.code(str(WORKSPACE), language="text")
    if st.button("دموی انتخاب ۲ فایل از ۳ فایل", use_container_width=True, disabled=state["pending"] is not None):
        files = make_demo_files(WORKSPACE)
        state["pending"] = {"name": "delete_files", "args": {"paths": files}, "demo": True}
        add_ui(state, "user", "از سه فایل نمونه، دو فایل اول رو برای حذف انتخاب کن.")
        add_ui(state, "assistant", "سه فایل پیدا کردم. دو مورد را تأیید کن. هیچ فایلی واقعاً حذف نمی‌شود.")
        st.rerun()
    if st.button("شروع گفت‌وگوی جدید", use_container_width=True):
        st.session_state.agent = new_state()
        st.rerun()

for message in state["ui_messages"]:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

pending = state["pending"]
if pending:
    name, args = pending["name"], pending["args"]
    st.divider()
    st.warning("عملیات متوقف شده و منتظر تصمیم شماست.")
    st.subheader("Human-in-the-Loop: تأیید عملیات")
    selected = []
    if name == "delete_files":
        st.write("فایل‌های پیشنهادی برای حذف شبیه‌سازی‌شده:")
        paths = args.get("paths", [])
        if not isinstance(paths, list):
            paths = []
        for i, path in enumerate(paths[:30]):
            if isinstance(path, str):
                checkbox_id = f"approval_{len(state['events'])}_{i}"
                is_selected = st.checkbox(path, value=i < 2 if pending.get("demo") else True, key=checkbox_id)
                if is_selected:
                    selected.append(path)
        st.caption("DRY RUN: هیچ فایل واقعی حذف نمی‌شود، حتی پس از تأیید.")
    elif name == "create_folder":
        st.write("ساخت این پوشه:")
        st.code(str(args.get("path", "?")))
    elif name == "move_file":
        st.write("جابه‌جایی فایل:")
        st.code(f"{args.get('source', '?')}  →  {args.get('destination', '?')}")
    else:
        st.error("ابزار نامشخص است و اجازه اجرا نخواهد داشت.")

    left, right = st.columns(2)
    with left:
        reject = st.button("رد درخواست", use_container_width=True)
    with right:
        approve = st.button("تأیید انتخاب‌ها" if name == "delete_files" else "تأیید اجرا",
                            use_container_width=True, type="primary",
                            disabled=(name == "delete_files" and not selected)
                                     or name not in {"delete_files", "create_folder", "move_file"})
    if approve or reject:
        demo = bool(pending.get("demo"))
        approve_or_reject(state, WORKSPACE, approved=approve, selected_paths=selected)
        if not demo:
            try:
                with st.spinner("مدل در حال ادامه کار است..."):
                    advance(state, WORKSPACE, client)
            except OllamaError as exc:
                add_ui(state, "assistant", "اتصال مدل قطع شد. جزئیات: " + str(exc))
        st.rerun()

user_text = st.chat_input("مثلاً: فایل‌های txt رو پیدا کن و لیستشون رو بده...", disabled=pending is not None)
if user_text:
    add_user_request(state, user_text)
    try:
        with st.spinner("مدل محلی در حال پردازش درخواست است..."):
            advance(state, WORKSPACE, client)
    except OllamaError as exc:
        add_ui(state, "assistant", "خطا در ارتباط با Ollama: " + str(exc) + "\n\nاول Ollama و مدل را نصب و اجرا کن.")
    st.rerun()

with st.expander("Execution Log | گزارش عملیات"):
    if not state["events"]:
        st.caption("هنوز رویدادی ثبت نشده است.")
    for i, event in enumerate(reversed(state["events"]), start=1):
        st.caption(f"{len(state['events']) - i + 1}. {event['tool']}")
        st.json(event["result"])

with st.expander("فهرست فایل‌های workspace"):
    try:
        st.json(LocalTools(WORKSPACE).list_files("."))
    except Exception as exc:
        st.error(str(exc))

st.caption("نسخه آزمایشی: بدون دسترسی به Desktop، Downloads، Shell یا کنترل ماوس و کیبورد.")
