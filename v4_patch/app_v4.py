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
st.caption("Ollama + Qwen3 + Python | اجرای کنترل‌شده روی workspace | Dry Run یا انتقال به Trash")

if "agent" not in st.session_state:
    st.session_state.agent = new_state()
state = st.session_state.agent

with st.sidebar:
    st.subheader("تنظیمات مدل")
    model_name = st.text_input("مدل محلی", value="qwen3:4b")
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
    st.info("فقط داخل workspace مجاز است. حذف در حالت Real Mode فقط به Trash مک انتقال می‌دهد و نیازمند تأیید صریح است.")
    delete_choice = st.radio(
        "حالت حذف فایل‌ها",
        ["Dry Run", "Real Mode (Trash)"],
        index=0,
        key="delete_choice",
        help="Dry Run هیچ تغییری ایجاد نمی‌کند. Real Mode فقط فایل‌های انتخاب‌شده و تأییدشده را به Trash می‌فرستد.",
    )
    st.code(str(WORKSPACE), language="text")
    if st.button("دموی انتخاب ۲ فایل از ۳ فایل", use_container_width=True, disabled=state["pending"] is not None):
        files = make_demo_files(WORKSPACE)
        preview = LocalTools(WORKSPACE).preview_delete(files)
        state["pending"] = {"name": "delete_files", "args": {"paths": files}, "preview": preview, "demo": True}
        add_ui(state, "user", "از سه فایل نمونه، دو فایل اول رو برای حذف انتخاب کن.")
        add_ui(state, "assistant", "سه فایل نمونه پیدا شد. فایل‌های مورد نظر و حالت اجرا را بررسی و تأیید کن.")
        st.rerun()
    if st.button("شروع گفت‌وگوی جدید", use_container_width=True):
        st.session_state.agent = new_state()
        st.rerun()

plan = state.get('two_step_plan')
if plan and not plan['reported']:
    if plan['cancelled']:
        st.caption("فرایند دو مرحله‌ای متوقف شد.")
    else:
        current = 'انتقال فایل' if plan['created'] else 'ساخت پوشه'
        st.info(f"برنامه دو مرحله‌ای: ساخت `{plan['folder']}` سپس انتقال "
                f"`{plan['source']}`. مرحله جاری: {current}. هر مرحله تأیید جداگانه دارد.")

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
    delete_mode = "dry_run"
    real_ack = True
    if name == "delete_files":
        delete_mode = "trash" if delete_choice == "Real Mode (Trash)" else "dry_run"
        if delete_mode == "trash":
            st.error("REAL MODE: فایل‌های انتخاب‌شده پس از تأیید، واقعاً از workspace خارج و به Trash سیستم منتقل می‌شوند.")
        else:
            st.info("DRY RUN: فقط شبیه‌سازی است؛ هیچ فایلی جابه‌جا نمی‌شود.")
        preview = pending.get("preview") or {}
        item_map = {item.get("path"): item for item in preview.get("items", [])}
        paths = args.get("paths", [])
        if not isinstance(paths, list):
            paths = []
        for i, path in enumerate(paths[:30]):
            if isinstance(path, str):
                checkbox_id = f"approval_{len(state['events'])}_{i}"
                item = item_map.get(path, {"status": "blocked", "reason": "پیش‌نمایش نامعتبر"})
                eligible = item.get("status") == "eligible"
                label = f"{path} ({item.get('size_bytes', '?')} بایت)" if eligible else f"{path} (غیرمجاز: {item.get('reason', 'فایل نامعتبر')})"
                is_selected = st.checkbox(label, value=(i < 2 if pending.get("demo") else True) and eligible,
                                          disabled=not eligible, key=checkbox_id)
                if is_selected and eligible:
                    selected.append(path)
        st.caption(f"تعداد فایل‌های انتخاب‌شده: {len(selected)}")
        real_ack = True
        if delete_mode == "trash":
            real_ack = st.checkbox(
                "تأیید می‌کنم فایل‌های انتخاب‌شده از workspace به Trash منتقل شوند (حذف دائمی نیست).",
                value=False,
                key=f"trash_ack_{len(state['events'])}",
            )
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
                            disabled=(name == "delete_files" and (not selected or not real_ack))
                                     or name not in {"delete_files", "create_folder", "move_file"})
    if approve or reject:
        demo = bool(pending.get("demo"))
        approve_or_reject(state, WORKSPACE, approved=approve,
                          selected_paths=selected, delete_mode=delete_mode)
        if not demo and approve:
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
