"""Auditable workspace-only tools. Trash supports regular files and inspected folders."""
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

    # Limits protect the app from unexpectedly large or deep directory trees.
    MAX_PROPOSALS = 30
    MAX_TREE_ENTRIES = 5000
    MAX_TREE_DEPTH = 32

    @staticmethod
    def _validate_delete_paths(proposed_paths: list[str], selected_paths: list[str] | None = None) -> None:
        if not isinstance(proposed_paths, list) or not (1 <= len(proposed_paths) <= 30):
            raise ToolError("لیست پیشنهادی باید شامل ۱ تا ۳۰ مسیر باشد.")
        if any(not isinstance(p, str) or not p.strip() for p in proposed_paths):
            raise ToolError("مسیرهای پیشنهادی نامعتبرند.")
        normalized = [PurePosixPath(p.replace('\\', '/')).as_posix() for p in proposed_paths]
        if len(set(normalized)) != len(normalized):
            raise ToolError("مسیرهای تکراری مجاز نیستند.")
        # Never put a folder and something INSIDE it in a single trash proposal.
        for i, first in enumerate(normalized):
            for second in normalized[i+1:]:
                if first.startswith(second.rstrip('/') + '/') or second.startswith(first.rstrip('/') + '/'):
                    raise ToolError("پوشه و یکی از محتویاتش نباید همزمان برای حذف پیشنهاد شوند.")
        if selected_paths is not None:
            if not isinstance(selected_paths, list) or any(not isinstance(p, str) for p in selected_paths):
                raise ToolError("فهرست تأییدشده نامعتبر است.")
            if len(set(selected_paths)) != len(selected_paths) or any(p not in proposed_paths for p in selected_paths):
                raise ToolError("فقط مسیرهای پیشنهادیِ غیرتکراری قابل تأییدند.")

    def _snapshot_trash_target(self, path: str) -> dict:
        """Snapshot target AND whole subtree; fail closed on links/special nodes/mounts.

        The metadata snapshot detects normal edits between UI preview and approval.
        This is not a transactional filesystem lock; production code must also
        address concurrent filesystem mutation at the OS level.
        """
        target = self.safe_path(path)  # blocks root, absolute, '..' and symlink ancestors
        root_device = self.root.lstat().st_dev
        stack = [(target, 0)]
        signatures = {}
        files = folders = total_bytes = 0
        root_kind = None
        while stack:
            node, depth = stack.pop()
            if depth > self.MAX_TREE_DEPTH:
                raise ToolError(f"پوشه بیش از حد تو در تو است (حداکثر {self.MAX_TREE_DEPTH} سطح): {path}")
            if len(signatures) >= self.MAX_TREE_ENTRIES:
                raise ToolError(f"پوشه بیش از حد بزرگ است (حداکثر {self.MAX_TREE_ENTRIES} مورد): {path}")
            try:
                info = node.lstat()  # NOFOLLOW, even for nested children
            except (OSError, ValueError) as exc:
                raise ToolError(f"امکان بررسی مسیر وجود ندارد: {node.name}: {exc}") from exc
            if info.st_dev != root_device:
                raise ToolError("پوشه شامل مسیر روی دستگاه/درایو دیگری است؛ عملیات مسدود شد.")
            if stat.S_ISLNK(info.st_mode):
                raise ToolError(f"وجود symbolic link داخل پوشه مجاز نیست: {node.relative_to(self.root)}")
            is_file = stat.S_ISREG(info.st_mode)
            is_dir = stat.S_ISDIR(info.st_mode)
            if node == target:
                root_kind = 'file' if is_file else 'folder'
            if not (is_file or is_dir):
                raise ToolError(f"فایل ویژه/غیرعادی داخل پوشه قابل حذف نیست: {node.relative_to(self.root)}")
            rel = node.relative_to(target).as_posix()
            signatures[rel] = [info.st_dev, info.st_ino, info.st_mode, info.st_size,
                               info.st_mtime_ns, info.st_ctime_ns]
            if is_file:
                files += 1
                total_bytes += info.st_size
            else:
                if node != target:
                    folders += 1
                try:
                    # Sort for reproducible previews. Never traverse through links.
                    children = sorted(node.iterdir(), key=lambda x: x.name, reverse=True)
                except (OSError, ValueError) as exc:
                    raise ToolError(f"نمی‌توان محتویات پوشه را بررسی کرد: {node.name}: {exc}") from exc
                for child in children:
                    stack.append((child, depth + 1))
        kind = root_kind
        return {
            "kind": kind,
            "files_count": files,
            "folders_count": folders,
            "size_bytes": total_bytes,
            "entries": signatures,
        }

    def preview_delete(self, proposed_paths: list[str]) -> dict:
        """Inspect all candidate files/folders and capture a pre-approval snapshot."""
        self._validate_delete_paths(proposed_paths)
        items, identities = [], {}
        for path in proposed_paths:
            try:
                identity = self._snapshot_trash_target(path)
                identities[path] = identity
                items.append({
                    "path": path, "status": "eligible", "kind": identity['kind'],
                    "size_bytes": identity['size_bytes'],
                    "files_count": identity['files_count'],
                    "folders_count": identity['folders_count'],
                })
            except (ToolError, OSError) as exc:
                items.append({"path": path, "status": "blocked", "reason": str(exc)})
        return {"items": items, "identities": identities}

    def simulate_delete(self, proposed_paths: list[str], approved_paths: list[str]) -> dict:
        """Dry Run: inspect selected items but never remove/move anything."""
        self._validate_delete_paths(proposed_paths, approved_paths)
        preview = self.preview_delete(proposed_paths)
        by_path = {i['path']: i for i in preview['items']}
        details = []
        for path in proposed_paths:
            item = by_path[path]
            if item['status'] == 'blocked':
                details.append({"path": path, "status": "blocked", "reason": item['reason']})
            elif path in approved_paths:
                details.append({"path": path,
                                "status": "simulated_delete" if item['kind'] == 'file' else "simulated_trash_folder",
                                "kind": item['kind'], "files_count": item['files_count'],
                                "folders_count": item['folders_count']})
            else:
                details.append({"path": path, "status": "kept", "kind": item['kind']})
        return {"status": "dry_run_only", "real_deletions": 0, "items": details}

    def trash_selected(self, proposed_paths: list[str], approved_paths: list[str],
                       identities: dict | None, *, trash_fn=None) -> dict:
        """Transfer only selected, unchanged workspace items to OS Trash.

        Folder transfers include ALL descendants; the user sees the count first.
        Nothing is permanently deleted. Stops at first send2trash failure.
        """
        self._validate_delete_paths(proposed_paths, approved_paths)
        if not approved_paths:
            raise ToolError("برای انتقال به Trash حداقل یک فایل یا پوشه انتخاب کن.")
        if not isinstance(identities, dict):
            raise ToolError("پیش‌نمایش معتبر وجود ندارد؛ دوباره درخواست بده.")
        # Validate everything BEFORE first side effect, including every subtree.
        for path in approved_paths:
            actual = self._snapshot_trash_target(path)
            if identities.get(path) != actual:
                raise ToolError(f"فایل یا پوشه {path} بعد از پیش‌نمایش تغییر کرده است؛ دوباره درخواست بده.")
        if trash_fn is None:
            try:
                from send2trash import send2trash
            except ImportError as exc:
                raise ToolError("کتابخانه Send2Trash نصب نیست. requirements.txt را نصب کن.") from exc
            trash_fn = send2trash

        items = []
        trashed_files = trashed_folders = 0
        selected = set(approved_paths)
        for path in proposed_paths:
            if path not in selected:
                items.append({"path": path, "status": "kept"})
                continue
            try:
                target = self.safe_path(path)
                # Another pre-send check narrows, but cannot eliminate, a TOCTOU race.
                actual = self._snapshot_trash_target(path)
                if actual != identities[path]:
                    raise ToolError("مسیر پس از تأیید تغییر کرده است؛ درخواست جدید ثبت کن.")
                trash_fn(str(target))
                kind = actual['kind']
                if kind == 'folder':
                    trashed_folders += 1
                else:
                    trashed_files += 1
                items.append({"path": path, "status": "moved_to_trash", "kind": kind,
                              "files_count": actual['files_count'],
                              "folders_count": actual['folders_count']})
            except (OSError, ToolError) as exc:
                items.append({"path": path, "status": "error", "reason": str(exc)})
                seen = {x['path'] for x in items}
                items.extend({"path": other,
                              "status": "not_attempted" if other in selected else "kept"}
                             for other in proposed_paths if other not in seen)
                return {"status": "partial_failure", "mode": "trash",
                        "trashed_count": trashed_files + trashed_folders,
                        "trashed_files": trashed_files, "trashed_folders": trashed_folders,
                        "permanently_deleted": 0, "items": items}
        return {"status": "moved_to_trash", "mode": "trash",
                "trashed_count": trashed_files + trashed_folders,
                "trashed_files": trashed_files, "trashed_folders": trashed_folders,
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
