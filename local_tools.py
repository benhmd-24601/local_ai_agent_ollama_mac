"""Auditable workspace-only tools. Trash supports regular files and inspected folders."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import hashlib
import os
import tempfile


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

    # Text operations are scoped to .txt files and require human approval in the agent.
    MAX_TEXT_BYTES = 20_000

    def _text_target(self, path: str) -> Path:
        if not isinstance(path, str) or not path.strip():
            raise ToolError("نام فایل متنی معتبر نیست.")
        normalized = path.replace('\\', '/')
        name = PurePosixPath(normalized).name
        if not PurePosixPath(name).suffix:
            normalized += '.txt'
        elif PurePosixPath(name).suffix.lower() != '.txt':
            raise ToolError("در این نسخه فقط ایجاد یا ویرایش فایل .txt مجاز است.")
        if any(ord(c) < 32 for c in normalized):
            raise ToolError("نام فایل شامل کاراکتر نامعتبر است.")
        return self.safe_path(normalized)

    def _text_data(self, content: str) -> bytes:
        if not isinstance(content, str) or '\x00' in content:
            raise ToolError("محتوای فایل باید متن معتبر باشد.")
        encoded = content.encode('utf-8')
        if len(encoded) > self.MAX_TEXT_BYTES:
            raise ToolError("متن پیشنهادی بیش از ۲۰ کیلوبایت است.")
        return encoded

    def preview_create_text_file(self, path: str, content: str) -> dict:
        target = self._text_target(path)
        data = self._text_data(content)
        if not target.parent.is_dir():
            raise ToolError("پوشه مقصد وجود ندارد؛ ابتدا پوشه را بساز.")
        if target.exists():
            raise ToolError("فایل از قبل وجود دارد؛ ساخت فایل نباید اطلاعات قبلی را بازنویسی کند.")
        return {
            'path': target.relative_to(self.root).as_posix(),
            'operation': 'create', 'new_bytes': len(data),
            'new_preview': content[:6000],
        }

    def create_text_file(self, path: str, content: str) -> dict:
        preview = self.preview_create_text_file(path, content)
        target = self._text_target(path)
        data = self._text_data(content)
        # O_EXCL prevents overwriting an existing file between preview and approval.
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, 'O_NOFOLLOW'):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(target, flags, 0o600)
            with os.fdopen(fd, 'wb') as handle:
                handle.write(data)
        except FileExistsError as exc:
            raise ToolError("فایل پیش از اجرا ایجاد شده است؛ بازنویسی نشد.") from exc
        return {'status': 'created', 'kind': 'text_file',
                'path': preview['path'], 'size_bytes': len(data)}

    def _text_snapshot(self, target: Path) -> tuple[dict, bytes]:
        target = self.safe_path(target.relative_to(self.root).as_posix())
        if not target.is_file() or target.suffix.lower() != '.txt':
            raise ToolError("فایل متنی موجود پیدا نشد.")
        metadata = target.stat()
        if metadata.st_size > self.MAX_TEXT_BYTES:
            raise ToolError("فایل برای ویرایش آزمایشی بیش از حد بزرگ است.")
        data = target.read_bytes()
        try:
            data.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ToolError("فایل باید UTF-8 باشد.") from exc
        if len(data) > self.MAX_TEXT_BYTES:
            raise ToolError("حجم فایل بیش از حد مجاز است.")
        fingerprint = {
            'device': metadata.st_dev, 'inode': metadata.st_ino,
            'mtime_ns': metadata.st_mtime_ns, 'ctime_ns': metadata.st_ctime_ns,
            'mode': metadata.st_mode, 'size': len(data),
            'sha256': hashlib.sha256(data).hexdigest(),
        }
        return fingerprint, data

    def preview_edit_text_file(self, path: str, content: str, mode: str) -> dict:
        if mode not in {'append', 'replace'}:
            raise ToolError("حالت ویرایش باید append یا replace باشد.")
        target = self._text_target(path)
        addition = self._text_data(content)
        identity, current = self._text_snapshot(target)
        new_content = current + addition if mode == 'append' else addition
        if len(new_content) > self.MAX_TEXT_BYTES:
            raise ToolError("حجم نهایی فایل از ۲۰ کیلوبایت بیشتر می‌شود.")
        return {
            'path': target.relative_to(self.root).as_posix(),
            'operation': mode, 'before_bytes': len(current),
            'new_bytes': len(new_content), 'identity': identity,
            'before_preview': current.decode('utf-8')[:6000],
            'new_preview': new_content.decode('utf-8')[:6000],
        }

    def edit_text_file(self, path: str, content: str, mode: str, identity: dict | None) -> dict:
        if not isinstance(identity, dict):
            raise ToolError("پیش‌نمایش ویرایش موجود نیست؛ دوباره درخواست بده.")
        target = self._text_target(path)
        # Check the original snapshot to protect against edits made after preview.
        actual, old_bytes = self._text_snapshot(target)
        if actual != identity:
            raise ToolError("فایل پس از پیش‌نمایش تغییر کرده است؛ عملیات لغو شد.")
        if mode not in {'append', 'replace'}:
            raise ToolError("حالت ویرایش نامعتبر است.")
        addition = self._text_data(content)
        new_data = old_bytes + addition if mode == 'append' else addition
        if len(new_data) > self.MAX_TEXT_BYTES:
            raise ToolError("حجم نهایی فایل از حد مجاز بیشتر است.")
        # Atomic replacement keeps the original file intact if writing fails.
        # Like other local file tools, this is not a transactional defense
        # against a hostile process modifying paths at precisely the same time.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='wb', prefix='.agent-edit-', suffix='.tmp',
                                             dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(new_data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, stat.S_IMODE(actual['mode']))
            if self._text_snapshot(target)[0] != identity:
                raise ToolError("فایل همزمان تغییر کرده است؛ ویرایش لغو شد.")
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return {'status': 'updated', 'path': target.relative_to(self.root).as_posix(),
                'mode': mode, 'size_bytes': len(new_data)}

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
