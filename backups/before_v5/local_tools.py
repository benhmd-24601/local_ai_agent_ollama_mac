"""Auditable workspace-only tools. Deletion means moving regular files to OS Trash."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import re
import shutil
import stat


class ToolError(ValueError):
    pass


class LocalTools:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def safe_path(self, relative_path: str, *, allow_root: bool = False) -> Path:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ToolError("مسیر فایل یا پوشه معتبر نیست.")
        normal = relative_path.replace('\\', '/')
        # Reject absolute paths, Windows drive paths, parent traversal and hidden files.
        if normal.startswith('/') or re.match(r'^[A-Za-z]:', normal):
            raise ToolError("مسیر مطلق مجاز نیست. از مسیر نسبی workspace استفاده کن.")
        relative = PurePosixPath(normal)
        parts = relative.parts
        if any(part == '..' or part.startswith('.') and part != '.' for part in parts):
            raise ToolError("مسیرهای بالادستی یا فایل‌های مخفی مجاز نیستند.")
        if any(part in {'', '..'} for part in parts):
            raise ToolError("مسیر نامعتبر است.")
        target = self.root.joinpath(*parts)
        # Prevent symlink traversal, including when a parent points outside workspace.
        current = self.root
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise ToolError("مسیر شامل symbolic link است و دسترسی به آن مجاز نیست.")
        resolved = target.resolve(strict=False)
        if not resolved.is_relative_to(self.root):
            raise ToolError("دسترسی خارج از workspace مجاز نیست.")
        if not allow_root and resolved == self.root:
            raise ToolError("روی ریشه workspace نمی‌توان این عملیات را انجام داد.")
        return resolved

    def list_files(self, folder: str = '.') -> dict:
        p = self.safe_path(folder, allow_root=True)
        if not p.is_dir():
            raise ToolError("پوشه پیدا نشد.")
        entries = []
        for x in sorted(p.iterdir(), key=lambda e: e.name.casefold()):
            try:
                self.safe_path(str(x.relative_to(self.root)))
            except ToolError:
                continue
            entries.append({
                "path": x.relative_to(self.root).as_posix(),
                "type": "folder" if x.is_dir() else "file",
                "size_bytes": x.stat().st_size if x.is_file() else None,
            })
            if len(entries) >= 80:
                break
        return {"status": "ok", "folder": folder, "entries": entries, "limit": 80}

    def find_files(self, folder: str, keyword: str, extension: str) -> dict:
        root = self.safe_path(folder, allow_root=True)
        if not root.is_dir():
            raise ToolError("پوشه پیدا نشد.")
        if not isinstance(keyword, str) or not isinstance(extension, str):
            raise ToolError("پارامتر جست‌وجو معتبر نیست.")
        ext = extension.lower().strip().lstrip('.')
        if ext and (not ext.isalnum() or len(ext) > 12):
            raise ToolError("پسوند معتبر نیست.")
        found = []
        for x in sorted(root.rglob('*')):
            if not x.is_file():
                continue
            try:
                self.safe_path(str(x.relative_to(self.root)))
            except ToolError:
                continue
            if keyword.casefold() not in x.name.casefold():
                continue
            if ext and x.suffix.lower() != '.' + ext:
                continue
            found.append(x.relative_to(self.root).as_posix())
            if len(found) >= 80:
                break
        return {"status": "ok", "matches": found, "limit": 80}

    def read_text_file(self, path: str) -> dict:
        p = self.safe_path(path)
        if not p.is_file() or p.suffix.lower() not in {'.txt', '.md', '.csv', '.json', '.log'}:
            raise ToolError("فقط فایل‌های متنی پشتیبانی می‌شوند.")
        if p.stat().st_size > 200_000:
            raise ToolError("فایل برای نسخه آزمایشی بیش از حد بزرگ است.")
        return {"status": "ok", "path": path, "content": p.read_text(encoding='utf-8')[:12000]}

    def create_folder(self, path: str) -> dict:
        p = self.safe_path(path)
        if p.exists():
            raise ToolError("پوشه یا فایل از قبل وجود دارد.")
        p.mkdir(parents=True)
        return {"status": "created", "path": p.relative_to(self.root).as_posix()}

    def move_file(self, source: str, destination: str) -> dict:
        src = self.safe_path(source)
        dst = self.safe_path(destination)
        if not src.is_file():
            raise ToolError("فایل مبدا پیدا نشد.")
        if dst.exists():
            raise ToolError("فایل مقصد از قبل وجود دارد. بازنویسی مجاز نیست.")
        if not dst.parent.is_dir():
            raise ToolError("پوشه مقصد وجود ندارد. ابتدا آن را بساز.")
        shutil.move(str(src), str(dst))
        return {"status": "moved", "source": source, "destination": destination}

    def simulate_delete(self, proposed_paths: list[str], approved_paths: list[str]) -> dict:
        # Deliberately no os.remove(), Path.unlink(), shutil.rmtree() or similar.
        if not isinstance(proposed_paths, list) or not isinstance(approved_paths, list):
            raise ToolError("لیست فایل‌های پیشنهادی یا تأییدشده نامعتبر است.")
        if len(proposed_paths) > 30 or len(set(proposed_paths)) != len(proposed_paths):
            raise ToolError("حداکثر ۳۰ مسیر غیرتکراری در هر درخواست مجاز است.")
        if any(p not in proposed_paths for p in approved_paths):
            raise ToolError("فایلی خارج از لیست پیشنهادی تأیید شده است.")
        details = []
        for path in proposed_paths:
            try:
                p = self.safe_path(path)
                if not p.is_file():
                    details.append({"path": path, "status": "not_a_file"})
                elif path in approved_paths:
                    details.append({"path": path, "status": "simulated_delete"})
                else:
                    details.append({"path": path, "status": "kept"})
            except ToolError as exc:
                details.append({"path": path, "status": "blocked", "reason": str(exc)})
        return {"status": "dry_run_only", "real_deletions": 0, "items": details}


    @staticmethod
    def _validate_delete_paths(proposed_paths: list[str], selected_paths: list[str] | None = None) -> None:
        if not isinstance(proposed_paths, list) or not proposed_paths or len(proposed_paths) > 30:
            raise ToolError("لیست پیشنهادی باید شامل ۱ تا ۳۰ فایل باشد.")
        if any(not isinstance(p, str) for p in proposed_paths) or len(set(proposed_paths)) != len(proposed_paths):
            raise ToolError("مسیرها باید رشته‌های غیرتکراری باشند.")
        if selected_paths is not None:
            if not isinstance(selected_paths, list) or any(not isinstance(p, str) for p in selected_paths):
                raise ToolError("انتخاب فایل‌ها نامعتبر است.")
            if len(set(selected_paths)) != len(selected_paths):
                raise ToolError("انتخاب تکراری مجاز نیست.")
            if any(p not in proposed_paths for p in selected_paths):
                raise ToolError("فایلی خارج از لیست پیشنهادی تأیید شده است.")

    def _file_identity(self, path: str) -> dict:
        p = self.safe_path(path)
        try:
            info = p.lstat()
        except FileNotFoundError:
            raise ToolError(f"فایل پیدا نشد: {path}") from None
        if not stat.S_ISREG(info.st_mode):
            raise ToolError(f"فقط فایل معمولی قابل انتقال به Trash است: {path}")
        return {
            "device": info.st_dev, "inode": info.st_ino,
            "size": info.st_size, "modified_ns": info.st_mtime_ns,
        }

    def preview_delete(self, proposed_paths: list[str]) -> dict:
        """Run BEFORE showing approval. Capture identity to detect edits after preview."""
        self._validate_delete_paths(proposed_paths)
        items = []
        identities = {}
        for path in proposed_paths:
            try:
                signature = self._file_identity(path)
                identities[path] = signature
                items.append({"path": path, "status": "eligible", "size_bytes": signature["size"]})
            except (ToolError, OSError) as exc:
                items.append({"path": path, "status": "blocked", "reason": str(exc)})
        return {"items": items, "identities": identities}

    def trash_selected(self, proposed_paths: list[str], approved_paths: list[str],
                       identities: dict | None, *, trash_fn=None) -> dict:
        """Real Mode: send only approved workspace files to OS Trash (never unlink).

        Fail closed if preview identity changed, and stop on the first failure.
        `trash_fn` is injectable for tests; default uses native send2trash.
        """
        self._validate_delete_paths(proposed_paths, approved_paths)
        if not approved_paths:
            raise ToolError("برای انتقال به Trash حداقل یک فایل انتخاب کن.")
        if not isinstance(identities, dict):
            raise ToolError("پیش‌نمایش معتبر وجود ندارد؛ دوباره درخواست بده.")
        # Validate ALL approved paths BEFORE moving anything, to prevent surprise partial results.
        for path in approved_paths:
            actual = self._file_identity(path)
            if identities.get(path) != actual:
                raise ToolError(f"فایل {path} بعد از نمایش پیش‌نمایش تغییر کرده است؛ دوباره درخواست بده.")
        if trash_fn is None:
            try:
                from send2trash import send2trash
            except ImportError as exc:
                raise ToolError("پکیج Send2Trash نصب نیست: python -m pip install send2trash") from exc
            trash_fn = send2trash
        items = []
        done = 0
        for path in proposed_paths:
            if path not in approved_paths:
                items.append({"path": path, "status": "kept"})
                continue
            try:
                # Recheck path/identity directly before sending, even after earlier preflight.
                p = self.safe_path(path)
                if self._file_identity(path) != identities[path]:
                    raise ToolError("فایل بین تأیید و اجرا تغییر کرده است.")
                trash_fn(str(p))
                done += 1
                items.append({"path": path, "status": "moved_to_trash"})
            except (OSError, ToolError) as exc:
                items.append({"path": path, "status": "error", "reason": str(exc)})
                # Remaining items were never processed after the first failure.
                already_seen = {item["path"] for item in items}
                items += [{"path": other,
                           "status": "not_attempted" if other in approved_paths else "kept"}
                          for other in proposed_paths if other not in already_seen]
                return {"status": "partial_failure", "mode": "trash", "trashed_count": done,
                        "permanently_deleted": 0, "items": items}
        return {"status": "moved_to_trash", "mode": "trash", "trashed_count": done,
                "permanently_deleted": 0, "items": items}


def make_demo_files(root: Path) -> list[str]:
    tools = LocalTools(root)
    paths = ['old_setup.exe', 'temporary_log.txt', 'project_backup.zip']
    demo_content = {
        paths[0]: b'EXAMPLE INSTALLER PLACEHOLDER - NOT A REAL EXECUTABLE\n',
        paths[1]: 'This is a harmless sample log.\n'.encode('utf-8'),
        paths[2]: b'EXAMPLE ZIP PLACEHOLDER - NOT A REAL ARCHIVE\n',
    }
    for path, content in demo_content.items():
        file = tools.safe_path(path)
        if not file.exists():
            file.write_bytes(content)
    return paths
