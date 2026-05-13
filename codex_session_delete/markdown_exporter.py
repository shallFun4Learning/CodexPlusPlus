from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from codex_session_delete.models import ExportResult, ExportStatus, SessionRef


_WINDOWS_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE_RE = re.compile(r"\s+")


class _DirectoryChooserUnavailable(RuntimeError):
    pass


class _DirectoryChooserFailed(RuntimeError):
    pass


class MarkdownExportService:
    def __init__(self, db_path: Path | None):
        self.db_path = db_path

    def export(self, session: SessionRef) -> ExportResult:
        if self.db_path is None:
            return self._failed(session.session_id, "未配置本地 Codex 数据库")
        if not self.db_path.exists():
            return self._failed(session.session_id, f"数据库不存在：{self.db_path}")

        thread_id = self._normalize_session_id(session.session_id)
        try:
            with sqlite3.connect(self.db_path) as db:
                db.row_factory = sqlite3.Row
                if not self._supports_codex_threads(db):
                    return self._failed(thread_id, "不支持当前本地存储结构")
                row = db.execute("SELECT id, title, rollout_path FROM threads WHERE id = ?", (thread_id,)).fetchone()
        except sqlite3.Error as exc:
            return self._failed(thread_id, f"读取本地数据库失败：{exc}")

        if row is None:
            return self._failed(thread_id, "未找到对应会话")

        return self._export_from_row(row, session.title)

    def choose_output_directory(self, initial_dir: str | Path | None = None) -> dict[str, object]:
        try:
            selected = self._choose_output_directory(self._existing_directory(self._resolve_download_dir(initial_dir)))
        except Exception as exc:
            return {
                "status": "failed",
                "directory": "",
                "message": f"选择导出位置失败：{exc}",
            }
        if selected is None:
            return {
                "status": "cancelled",
                "directory": "",
                "message": "已取消选择导出位置",
            }
        return {
            "status": "selected",
            "directory": str(selected),
            "message": f"已选择导出位置：{selected}",
        }

    def export_project(self, target_cwd: str, project_label: str | None = None, download_dir: Path | str | None = None) -> dict[str, object]:
        if self.db_path is None:
            return self._project_failed(target_cwd, "未配置本地 Codex 数据库")
        if not self.db_path.exists():
            return self._project_failed(target_cwd, f"数据库不存在：{self.db_path}")

        normalized_cwd = self._normalize_workspace_path(target_cwd)
        if not normalized_cwd:
            return self._project_failed(target_cwd, "目标项目路径为空")

        try:
            with sqlite3.connect(self.db_path) as db:
                db.row_factory = sqlite3.Row
                if not self._supports_codex_threads(db):
                    return self._project_failed(target_cwd, "不支持当前本地存储结构")
                columns = {row[1] for row in db.execute('PRAGMA table_info("threads")')}
                if "cwd" not in columns:
                    return self._project_failed(target_cwd, "当前本地存储缺少项目路径信息")
                select_columns = ["id", "title", "rollout_path", "cwd", *[column for column in ("updated_at", "updated_at_ms", "created_at_ms") if column in columns]]
                where = " WHERE COALESCE(archived, 0) = 0" if "archived" in columns else ""
                rows = db.execute(f"SELECT {', '.join(select_columns)} FROM threads{where}").fetchall()
        except sqlite3.Error as exc:
            return self._project_failed(target_cwd, f"读取本地数据库失败：{exc}")

        matched_rows = [row for row in rows if self._normalize_workspace_path(str(row["cwd"] or "")) == normalized_cwd]
        if not matched_rows:
            return self._project_failed(target_cwd, "该项目下没有可导出的会话")

        matched_rows.sort(key=self._sort_key_for_row, reverse=True)
        folder_name = self._sanitize_path_segment(project_label or self._project_name_from_path(target_cwd))
        try:
            base_dir = self._resolve_download_dir(download_dir)
            if base_dir.exists() and not base_dir.is_dir():
                return self._project_failed(target_cwd, f"导出位置不是文件夹：{base_dir}")
            base_dir.mkdir(parents=True, exist_ok=True)
            output_dir = base_dir / folder_name
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return self._project_failed(target_cwd, f"创建导出目录失败：{exc}")

        exported: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []
        for row in matched_rows:
            result = self._export_from_row(row, str(row["title"] or ""))
            if result.status != ExportStatus.EXPORTED or not result.filename or result.markdown is None:
                failed.append({
                    "session_id": result.session_id,
                    "title": str(row["title"] or ""),
                    "message": result.message,
                })
                continue
            path = output_dir / result.filename
            try:
                path.write_text(result.markdown, encoding="utf-8")
            except OSError as exc:
                failed.append({
                    "session_id": result.session_id,
                    "title": str(row["title"] or ""),
                    "message": str(exc),
                })
                continue
            exported.append({
                "session_id": result.session_id,
                "title": str(row["title"] or ""),
                "filename": result.filename,
                "path": str(path),
            })

        if not exported:
            first_failure = failed[0]["message"] if failed else "项目导出失败"
            return {
                "status": "failed",
                "target_cwd": target_cwd,
                "project_label": project_label or self._project_name_from_path(target_cwd),
                "output_dir": str(output_dir),
                "exported_count": 0,
                "failed_count": len(failed),
                "message": f"项目导出失败：{first_failure}",
                "files": [],
                "failures": failed,
            }

        status = "exported" if not failed else "partial"
        message = (
            f"已导出 {len(exported)} 个会话到：{output_dir}"
            if not failed
            else f"已导出 {len(exported)} 个会话，{len(failed)} 个失败：{output_dir}"
        )
        return {
            "status": status,
            "target_cwd": target_cwd,
            "project_label": project_label or self._project_name_from_path(target_cwd),
            "output_dir": str(output_dir),
            "exported_count": len(exported),
            "failed_count": len(failed),
            "message": message,
            "files": exported,
            "failures": failed,
        }

    def _export_from_row(self, row: sqlite3.Row, fallback_title: str) -> ExportResult:
        thread_id = self._normalize_session_id(str(row["id"]))
        title = self._display_title(str(row["title"] or fallback_title or ""))
        rollout_path_value = str(row["rollout_path"] or "")
        if not rollout_path_value:
            return self._failed(thread_id, "会话缺少 rollout 文件路径")
        rollout_path = Path(rollout_path_value)
        if not rollout_path.is_file():
            return self._failed(thread_id, f"rollout 文件不存在：{rollout_path}")

        try:
            messages = self._load_messages(rollout_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return self._failed(thread_id, f"读取 rollout 失败：{exc}")

        if not messages:
            return self._failed(thread_id, "未找到可导出的用户或助手消息")

        filename = self._build_filename(title, thread_id)
        markdown = self._render_markdown(title, messages)
        return ExportResult(
            status=ExportStatus.EXPORTED,
            session_id=thread_id,
            message=f"已导出为 Markdown：{filename}",
            filename=filename,
            markdown=markdown,
        )

    def _sort_key_for_row(self, row: sqlite3.Row) -> tuple[int, str]:
        row_map = {key: row[key] for key in row.keys()}
        timestamp = self._timestamp_to_ms(row_map.get("updated_at_ms")) or self._timestamp_to_ms(row_map.get("updated_at")) or self._timestamp_to_ms(row_map.get("created_at_ms"))
        return (timestamp, str(row["id"]))

    def _supports_codex_threads(self, db: sqlite3.Connection) -> bool:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "threads" not in tables:
            return False
        columns = {row[1] for row in db.execute('PRAGMA table_info("threads")')}
        return {"id", "title", "rollout_path"}.issubset(columns)

    def _load_messages(self, rollout_path: Path) -> list[tuple[str, str | None, str]]:
        messages: list[tuple[str, str | None, str]] = []
        with rollout_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                if not raw_line.strip():
                    continue
                event = json.loads(raw_line)
                if event.get("type") != "response_item":
                    continue
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    continue
                if payload.get("type") != "message":
                    continue
                role = payload.get("role")
                if role not in {"user", "assistant"}:
                    continue
                body = self._serialize_message_content(payload.get("content"))
                if not body:
                    continue
                speaker = "User" if role == "user" else "Assistant"
                messages.append((speaker, self._format_timestamp(event.get("timestamp")), body))
        return messages

    def _serialize_message_content(self, content: object) -> str:
        if not isinstance(content, list):
            return ""
        blocks: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type in {"input_text", "output_text"}:
                text = self._normalize_newlines(str(block.get("text") or "")).strip("\n")
                if text.strip():
                    blocks.append(text)
                continue
            if block_type == "input_image":
                image_url = str(block.get("image_url") or "").strip()
                if image_url and not image_url.startswith("data:"):
                    blocks.append(f"> Image attachment\n[Image link](<{image_url}>)")
                else:
                    blocks.append("> Image attachment")
        return "\n\n".join(block for block in blocks if block.strip()).strip()

    def _format_timestamp(self, value: object) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S")

    def _display_title(self, value: str) -> str:
        normalized = _WHITESPACE_RE.sub(" ", self._normalize_newlines(value)).strip()
        return normalized or "Untitled session"

    def _build_filename(self, title: str, thread_id: str) -> str:
        safe_title = self._sanitize_path_segment(title)
        safe_thread_id = _WINDOWS_FILENAME_CHARS_RE.sub("-", thread_id).strip() or "thread"
        return f"{safe_title}-{safe_thread_id}.md"

    def _render_markdown(self, title: str, messages: list[tuple[str, str | None, str]]) -> str:
        lines = [f"# {title}", ""]
        for speaker, timestamp, body in messages:
            lines.append(f"### {speaker}")
            if timestamp:
                lines.append(f"_{timestamp}_")
            lines.append("")
            lines.append(body.rstrip())
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _normalize_session_id(self, session_id: str) -> str:
        return session_id.removeprefix("local:")

    def _normalize_workspace_path(self, value: str | None) -> str:
        if not isinstance(value, str):
            return ""
        stripped = value.strip()
        if not stripped:
            return ""
        if stripped.lower().startswith("\\\\?\\unc\\"):
            stripped = "\\\\" + stripped[8:]
        elif stripped.startswith("\\\\?\\"):
            stripped = stripped[4:]
        return stripped.replace("\\", "/").rstrip("/")

    def _normalize_newlines(self, value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n")

    def _sanitize_path_segment(self, value: str) -> str:
        cleaned = _WINDOWS_FILENAME_CHARS_RE.sub(" ", self._display_title(value))
        cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip(" .")
        return (cleaned or "Untitled session")[:80].rstrip(" .") or "Untitled session"

    def _timestamp_to_ms(self, value: Any) -> int:
        try:
            timestamp = int(value)
        except (TypeError, ValueError):
            return 0
        if timestamp <= 0:
            return 0
        return timestamp * 1000 if timestamp < 1000000000000 else timestamp

    def _project_name_from_path(self, value: str) -> str:
        normalized = self._normalize_workspace_path(value)
        if not normalized:
            return "Untitled project"
        return normalized.split("/")[-1] or "Untitled project"

    def _resolve_download_dir(self, value: str | Path | None) -> Path:
        if isinstance(value, Path):
            return value.expanduser()
        if isinstance(value, str) and value.strip():
            return Path(value.strip()).expanduser()
        return self._default_download_dir()

    def _existing_directory(self, value: Path) -> Path:
        candidates = [value, value.parent, self._default_download_dir(), Path.home()]
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
        return Path.home()

    def _choose_output_directory(self, initial_dir: Path) -> Path | None:
        native_error: Exception | None = None
        try:
            if sys.platform == "darwin":
                return self._choose_output_directory_macos(initial_dir)
            if sys.platform == "win32":
                return self._choose_output_directory_windows(initial_dir)
        except (_DirectoryChooserUnavailable, _DirectoryChooserFailed) as exc:
            native_error = exc
        try:
            return self._choose_output_directory_tk(initial_dir)
        except (_DirectoryChooserUnavailable, _DirectoryChooserFailed) as exc:
            if native_error is not None:
                raise native_error
            raise exc

    def _choose_output_directory_macos(self, initial_dir: Path) -> Path | None:
        escaped_dir = self._applescript_quote(str(initial_dir))
        script = (
            'set chosenFolder to choose folder with prompt "选择批量导出保存位置" '
            f'default location (POSIX file "{escaped_dir}")\n'
            "POSIX path of chosenFolder"
        )
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as exc:
            raise _DirectoryChooserUnavailable("osascript 不可用") from exc
        if result.returncode == 0:
            selected = result.stdout.strip()
            if not selected:
                raise _DirectoryChooserFailed("目录选择器未返回路径")
            return Path(selected).expanduser()
        stderr = (result.stderr or "").strip()
        if "-128" in stderr or "User canceled" in stderr:
            return None
        raise _DirectoryChooserFailed(stderr or f"osascript exited with code {result.returncode}")

    def _choose_output_directory_windows(self, initial_dir: Path) -> Path | None:
        selected_path = self._powershell_quote(str(initial_dir))
        script = f"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '选择批量导出保存位置'
$dialog.ShowNewFolderButton = $true
if (Test-Path -LiteralPath {selected_path}) {{
  $dialog.SelectedPath = {selected_path}
}}
$result = $dialog.ShowDialog()
if ($result -eq [System.Windows.Forms.DialogResult]::OK -and $dialog.SelectedPath) {{
  Write-Output $dialog.SelectedPath
  exit 0
}}
exit 2
""".strip()
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as exc:
            raise _DirectoryChooserUnavailable("powershell.exe 不可用") from exc
        if result.returncode == 0:
            selected = result.stdout.strip()
            if not selected:
                raise _DirectoryChooserFailed("目录选择器未返回路径")
            return Path(selected).expanduser()
        if result.returncode == 2:
            return None
        raise _DirectoryChooserFailed((result.stderr or result.stdout or "").strip() or f"PowerShell exited with code {result.returncode}")

    def _choose_output_directory_tk(self, initial_dir: Path) -> Path | None:
        try:
            import tkinter as tk
            from tkinter import filedialog
        except ImportError as exc:
            raise _DirectoryChooserUnavailable("tkinter 不可用") from exc

        root = None
        try:
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            root.update_idletasks()
            selected = filedialog.askdirectory(
                parent=root,
                initialdir=str(initial_dir),
                title="选择批量导出保存位置",
                mustexist=True,
            )
        except tk.TclError as exc:
            raise _DirectoryChooserUnavailable(str(exc)) from exc
        finally:
            if root is not None:
                root.destroy()
        if not selected:
            return None
        return Path(selected).expanduser()

    def _applescript_quote(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def _powershell_quote(self, value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def _default_download_dir(self) -> Path:
        return Path.home() / "Downloads"

    def _failed(self, session_id: str, message: str) -> ExportResult:
        return ExportResult(
            status=ExportStatus.FAILED,
            session_id=session_id,
            message=message,
            filename=None,
            markdown=None,
        )

    def _project_failed(self, target_cwd: str, message: str) -> dict[str, object]:
        return {
            "status": "failed",
            "target_cwd": target_cwd,
            "project_label": self._project_name_from_path(target_cwd),
            "output_dir": "",
            "exported_count": 0,
            "failed_count": 0,
            "message": message,
            "files": [],
            "failures": [],
        }
