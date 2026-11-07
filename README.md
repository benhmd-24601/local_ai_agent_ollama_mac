# Local AI Agent with Ollama and Human-in-the-Loop

A local, Persian-speaking AI assistant that turns natural-language requests into **controlled file operations**. Built with **Python**, **Streamlit**, and **Ollama**, it uses a local LLM to decide which tools to call, while keeping the user in control of changes to the filesystem.

The key idea is simple: **the model can propose an action, but it cannot approve its own changes.** File-writing and deletion actions pause for explicit human review before execution.

> **Scope:** This is a workspace-based file assistant, not an unrestricted desktop automation tool. It only operates through its defined tools inside the project's `workspace/` directory.

## Features

- **Natural-language interaction:** Understands conversational Persian, including requests that involve several steps.
- **Local inference:** Uses Ollama with `qwen3:4b` as the development model; no hosted LLM API is required for inference.
- **File discovery:** Lists directories, finds files, and reads supported text files within the workspace.
- **File and folder management:** Creates folders, moves files, and creates or edits text files.
- **Human-in-the-Loop (HITL):** Requires explicit approval for operations that change files or folders. Rejected actions are not executed.
- **Extra overwrite confirmation:** Editing that would overwrite existing content requires an additional warning/confirmation step.
- **Controlled deletion:** Supports deletion previews and a dry-run demonstration. In Real Mode, approved items are moved to the macOS Trash rather than permanently erased.
- **Folder deletion safeguards:** Shows a preview of a folder's contents before approval and checks for changes between preview and execution.
- **Multi-step execution:** Can carry out supported sequences such as creating a folder and then moving a file, with a separate approval for each write step.
- **Execution history:** The Streamlit interface shows tool results and approval decisions for inspection.

## How it works

```text
Natural-language request
          |
          v
   Streamlit chat UI
          |
          v
   Ollama / Qwen3 model
          |
          v
   Tool-call proposal
          |
          +---- Read-only action ------> Execute within workspace
          |
          +---- Change/delete action --> Human approval UI
                                             |
                                   +---------+---------+
                                   |                   |
                                Approve              Reject
                                   |                   |
                                   v                   v
                            Validate and run      Do not execute
                                   |
                                   v
                           Result + event log
```

The language model interprets the request and chooses from a limited set of tools. **Python code performs the actual operations**, validates paths, and enforces the approval workflow. The model does not receive general-purpose shell access.

## Tech stack

| Component | Purpose |
| --- | --- |
| Python | Agent logic, tool execution, and safety checks |
| Ollama | Local LLM runtime |
| Qwen3 (`qwen3:4b`) | Natural-language understanding and tool selection |
| Streamlit | Chat interface, approvals, and execution logs |
| `unittest` | Automated tests |
| Send2Trash | Moves approved deletions to the macOS Trash in Real Mode |

## Getting started

### Prerequisites

- macOS (the Trash-based deletion workflow is designed for macOS)
- Python (developed with Python 3.13)
- [Ollama](https://ollama.com/download) installed and running

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
cd YOUR_REPOSITORY
```

Replace `YOUR_USERNAME` and `YOUR_REPOSITORY` with your GitHub details.

### 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Python dependencies

```bash
python -m pip install -r requirements.txt
```

### 4. Download and start the local model

```bash
ollama pull qwen3:4b
```

Make sure the Ollama application/service is running. If needed, start it with:

```bash
ollama serve
```

If Ollama is already running, do not start a second server.

### 5. Launch the application

```bash
python -m streamlit run app.py
```

Open the local URL shown in your terminal (normally `http://localhost:8501`). Select or verify the Ollama model in the sidebar, then send a request in the chat.

## Example requests

The interface is designed primarily for conversational Persian. Examples:

```text
فایل‌های txt داخل workspace رو پیدا کن و لیستشون رو بده.
```

```text
یک پوشه به اسم Archive بساز و فایل temporary_log.txt رو ببر داخلش.
```

```text
یک فایل به اسم notes.txt بساز و داخلش بنویس Hello World.
```

```text
محتوای فایل notes.txt رو ویرایش کن.
```

```text
فایل‌های اضافی رو پیدا کن تا خودم انتخاب کنم کدوم‌ها حذف بشن.
```

For write operations, review the proposed paths and contents in the approval interface. Multi-step tasks may require more than one approval.

## Project structure

```text
.
├── app.py                # Streamlit interface and approval controls
├── agent_engine.py       # Model/tool loop and approval workflow
├── local_tools.py        # Workspace-restricted filesystem operations
├── ollama_client.py      # Local Ollama integration
├── requirements.txt      # Python dependencies
├── tests/                # Automated tests
└── workspace/            # Allowed file-operation area
```

`workspace/` is created by the application if it does not already exist. Its contents are local working data, not necessarily part of the source code you should publish.

## Safety model

This project deliberately separates **interpreting a request** from **executing an action**:

1. **Restricted workspace:** Tools accept paths only within the configured `workspace/` area, rather than arbitrary locations such as Desktop or Downloads.
2. **Defined tool set:** The model can invoke only exposed file-management tools. It cannot run terminal commands or control the mouse, keyboard, or browser.
3. **Explicit approval:** File- or folder-changing actions require human approval before execution.
4. **Deletion safeguards:** Dry Run makes no deletion. Real Mode moves approved items to Trash; folder operations include preview checks.
5. **No silent overwrites:** Existing text content receives additional confirmation before replacement.
6. **Visible results:** Tool outcomes and decisions are recorded in the UI rather than being treated as successful merely because the model says so.

**Important:** This is a learning/demo project, not a security boundary for untrusted users. Review proposals carefully, use disposable files when trying deletion features, and avoid putting secrets or important personal files into `workspace/`.

## Running tests

```bash
python -m unittest discover -s tests -q
```

The test suite covers the supported file operations and approval workflow. Run it locally after any changes, particularly to path validation, editing, and deletion logic.

## Current limitations

- Operations are limited to the app's `workspace/` directory.
- The project does not offer arbitrary shell commands or full desktop control.
- It is designed around a local Ollama model; results can depend on the model's tool-calling behaviour.
- Filesystem changes are subject to application checks and human approval; this is not an unattended autonomous system.
- The Real Mode deletion flow uses macOS Trash and is not intended to be a cross-platform permanent deletion utility.

## Why this project?

The goal is to explore a practical pattern for **local AI agents with tool use and meaningful human oversight**. It demonstrates how to combine natural-language planning, deterministic Python tools, per-action approval, and observable execution without giving an LLM unrestricted access to the host computer.
