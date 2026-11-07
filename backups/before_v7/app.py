"""Start with: python -m streamlit run app.py"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from agent_engine import add_ui, add_user_request, advance, approve_or_reject, new_state
from local_tools import LocalTools, make_demo_files
from ollama_client import OllamaClient, OllamaError

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT / "workspace"
WORKSPACE.mkdir(parents=True, exist_ok=True)

st.set_page_config(page_title="Workspace Assistant", page_icon="", layout="centered")

st.markdown(
    """
    <style>
    html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {
        direction: rtl;
    }
    .stApp, [data-testid="stSidebar"] * {
        text-align: right;
    }
    [data-testid="stChatMessage"], [data-testid="stChatMessageContent"] {
        direction: rtl;
        text-align: right;
    }
    .stTextInput input, .stTextArea textarea, [data-testid="stChatInputTextArea"] textarea {
        direction: rtl !important;
        text-align: right !important;
    }
    code, pre, .stCodeBlock, [data-testid="stJson"] {
        direction: ltr !important;
        text-align: left !important;
    }
    [data-testid="stSidebar"] .stCodeBlock, [data-testid="stSidebar"] code {
        direction: ltr !important;
        text-align: left !important;
    }
    .block-container {
        padding-top: 1.2rem;
    }
    [data-testid="stToolbar"], #MainMenu, footer, [data-testid="stDecoration"] {
        display: none !important;
        visibility: hidden !important;
        height: 0 !important;
    }
    header[data-testid="stHeader"] {
        background: transparent;
    }
    h1, h2, h3 {
        letter-spacing: 0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

if "agent" not in st.session_state:
    st.session_state.agent = new_state()
state = st.session_state.agent

with st.sidebar:
    st.subheader("تنظیمات")
    model_name = st.text_input("مدل", value="qwen3:4b")
    client = OllamaClient(model_name)
    if st.button("بررسی اتصال", use_container_width=True):
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
    delete_choice = st.radio(
        "حالت حذف",
        ["Dry Run", "Real Mode (Trash)"],
        index=0,
        key="delete_choice",
        help="در Dry Run تغییری ایجاد نمی‌شود. در Real Mode مورد انتخاب‌شده به Trash منتقل می‌شود.",
    )
    st.text_input("workspace", value=str(WORKSPACE), disabled=True)

    if st.button("دموی حذف نمونه", use_container_width=True, disabled=state["pending"] is not None):
        files = make_demo_files(WORKSPACE)
        preview = LocalTools(WORKSPACE).preview_delete(files)
        state["pending"] = {"name": "delete_files", "args": {"paths": files}, "preview": preview, "demo": True}
        add_ui(state, "user", "از سه فایل نمونه، دو فایل اول رو برای حذف انتخاب کن.")
        add_ui(state, "assistant", "فایل‌های مورد نظر را انتخاب و تأیید کن.")
        st.rerun()

    if st.button("گفت‌وگوی جدید", use_container_width=True):
        st.session_state.agent = new_state()
        st.rerun()

plan = state.get("two_step_plan")
if plan and not plan["reported"]:
    if plan["cancelled"]:
        st.info("فرایند متوقف شد.")
    else:
        current = "انتقال فایل" if plan["created"] else "ساخت پوشه"
        st.info(f"فرایند دو مرحله‌ای فعال است. مرحله جاری: {current}")

for message in state["ui_messages"]:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

pending = state["pending"]
if pending:
    name, args = pending["name"], pending["args"]
    st.divider()
    st.warning("عملیات متوقف شده و منتظر تصمیم شماست.")
    st.subheader("تأیید عملیات")
    selected = []
    delete_mode = "dry_run"
    real_ack = True

    if name == "delete_files":
        delete_mode = "trash" if delete_choice == "Real Mode (Trash)" else "dry_run"
        if delete_mode == "trash":
            st.error("در حالت واقعی، مورد انتخاب‌شده به Trash منتقل می‌شود.")
        else:
            st.info("در حالت آزمایشی، هیچ تغییری روی فایل‌ها و پوشه‌ها انجام نمی‌شود.")

        preview = pending.get("preview") or {}
        item_map = {item.get("path"): item for item in preview.get("items", [])}
        paths = args.get("paths", [])
        if not isinstance(paths, list):
            paths = []

        for i, path in enumerate(paths[:30]):
            if not isinstance(path, str):
                continue
            item = item_map.get(path, {"status": "blocked", "reason": "پیش‌نمایش نامعتبر"})
            eligible = item.get("status") == "eligible"
            kind = item.get("kind")

            if eligible and kind == "folder":
                label = (
                    f"پوشه {path} | {item['files_count']} فایل، {item['folders_count']} زیرپوشه، "
                    f"{item['size_bytes']} بایت"
                )
            elif eligible:
                label = f"فایل {path} ({item.get('size_bytes', '?')} بایت)"
            else:
                label = f"{path} (غیرمجاز: {item.get('reason', 'مسیر نامعتبر')})"

            checkbox_id = f"approval_{len(state['events'])}_{delete_mode}_{i}"
            is_selected = st.checkbox(
                label,
                value=bool(pending.get("demo") and i < 2 and delete_mode == "dry_run" and eligible),
                disabled=not eligible,
                key=checkbox_id,
            )
            if is_selected and eligible:
                selected.append(path)

        st.caption(f"تعداد انتخاب‌شده: {len(selected)}")
        selected_folders = [p for p in selected if item_map[p].get("kind") == "folder"]
        if selected_folders and delete_mode == "trash":
            st.warning("با تأیید، پوشه‌های انتخاب‌شده همراه با تمام محتویاتشان به Trash منتقل می‌شوند.")

        if delete_mode == "trash":
            real_ack = st.checkbox(
                "تأیید می‌کنم موارد انتخاب‌شده به Trash منتقل شوند.",
                value=False,
                key=f"trash_ack_{len(state['events'])}",
            )

    elif name == "create_folder":
        st.write("ساخت پوشه:")
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
        approve = st.button(
            "تأیید انتخاب‌ها" if name == "delete_files" else "تأیید اجرا",
            use_container_width=True,
            type="primary",
            disabled=(name == "delete_files" and (not selected or not real_ack)) or name not in {"delete_files", "create_folder", "move_file"},
        )

    if approve or reject:
        demo = bool(pending.get("demo"))
        approve_or_reject(state, WORKSPACE, approved=approve, selected_paths=selected, delete_mode=delete_mode)
        if not demo and approve:
            try:
                with st.spinner("در حال ادامه فرایند..."):
                    advance(state, WORKSPACE, client)
            except OllamaError as exc:
                add_ui(state, "assistant", "اتصال مدل قطع شد. جزئیات: " + str(exc))
        st.rerun()

user_text = st.chat_input("پیام خود را بنویسید...", disabled=pending is not None)
if user_text:
    add_user_request(state, user_text)
    try:
        with st.spinner("در حال پردازش..."):
            advance(state, WORKSPACE, client)
    except OllamaError as exc:
        add_ui(state, "assistant", "خطا در ارتباط با Ollama: " + str(exc) + "\n\nاول Ollama و مدل را نصب و اجرا کن.")
    st.rerun()

with st.expander("گزارش عملیات"):
    if not state["events"]:
        st.caption("هنوز رویدادی ثبت نشده است.")
    for i, event in enumerate(reversed(state["events"]), start=1):
        st.caption(f"{len(state['events']) - i + 1}. {event['tool']}")
        st.json(event["result"])

with st.expander("فایل‌های workspace"):
    try:
        st.json(LocalTools(WORKSPACE).list_files("."))
    except Exception as exc:
        st.error(str(exc))
