from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from codex_session_delete.models import ExportResult, ExportStatus, SessionRef


@dataclass(frozen=True)
class _ThreadRecord:
    thread_id: str
    title: str
    rollout_path: Path


@dataclass(frozen=True)
class _MessageBlock:
    role: str
    timestamp: str | None
    body: str


class MarkdownExportService:
    def __init__(self, db_path: Path | None):
        self.db_path = db_path

    def export(self, session: SessionRef) -> ExportResult:
        if self.db_path is None:
            return ExportResult(ExportStatus.FAILED, session.session_id, "No local database configured")
        if not self.db_path.exists():
            return ExportResult(ExportStatus.FAILED, session.session_id, f"Database not found: {self.db_path}")

        thread_id = self._normalize_thread_id(session.session_id)
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            thread = self._load_thread(db, thread_id)
        if thread is None:
            return ExportResult(ExportStatus.FAILED, session.session_id, "Thread not found in local storage")
        if not thread.rollout_path.is_file():
            return ExportResult(ExportStatus.FAILED, thread.thread_id, f"Rollout file not found: {thread.rollout_path}")

        blocks = self._load_message_blocks(thread.rollout_path)
        if not blocks:
            return ExportResult(ExportStatus.FAILED, thread.thread_id, "No exportable user or assistant messages found")

        markdown = self._build_markdown(thread.title, blocks)
        filename = self._build_filename(thread.title, thread.thread_id)
        return ExportResult(
            ExportStatus.EXPORTED,
            thread.thread_id,
            "Markdown 导出成功",
            filename=filename,
            markdown=markdown,
        )

    def _normalize_thread_id(self, session_id: str) -> str:
        return session_id.removeprefix("local:")

    def _load_thread(self, db: sqlite3.Connection, thread_id: str) -> _ThreadRecord | None:
        row = db.execute(
            "SELECT id, title, rollout_path FROM threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
        if row is None:
            return None
        rollout_path = row["rollout_path"]
        if not rollout_path:
            return None
        return _ThreadRecord(
            thread_id=str(row["id"]),
            title=str(row["title"] or "Untitled session"),
            rollout_path=Path(str(rollout_path)),
        )

    def _load_message_blocks(self, rollout_path: Path) -> list[_MessageBlock]:
        blocks: list[_MessageBlock] = []
        for raw_line in rollout_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict) or item.get("type") != "response_item":
                continue
            payload = item.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "message":
                continue
            role = str(payload.get("role", ""))
            if role not in {"user", "assistant"}:
                continue
            body = self._message_body(payload.get("content"))
            if not body:
                continue
            blocks.append(
                _MessageBlock(
                    role=role,
                    timestamp=self._format_timestamp(item.get("timestamp")),
                    body=body,
                )
            )
        return blocks

    def _message_body(self, content: object) -> str:
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for entry in content:
            if not isinstance(entry, dict):
                continue
            entry_type = entry.get("type")
            if entry_type in {"input_text", "output_text"}:
                text = str(entry.get("text", "")).strip()
                if text:
                    parts.append(text)
                continue
            if entry_type == "input_image":
                image_parts = ["> Image attachment"]
                image_url = str(entry.get("image_url", "") or "").strip()
                if image_url and not image_url.startswith("data:"):
                    image_parts.append(f"> Source: {image_url}")
                parts.append("\n".join(image_parts))
        return "\n\n".join(part for part in parts if part).strip()

    def _format_timestamp(self, value: object) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")

    def _build_markdown(self, title: str, blocks: list[_MessageBlock]) -> str:
        normalized_title = title.strip() or "Untitled session"
        lines = [f"# {normalized_title}"]
        for block in blocks:
            lines.extend(["", f"### {self._role_label(block.role)}"])
            if block.timestamp:
                lines.append(f"_{block.timestamp}_")
            lines.extend(["", block.body])
        return "\n".join(lines).rstrip() + "\n"

    def _role_label(self, role: str) -> str:
        return "User" if role == "user" else "Assistant"

    def _build_filename(self, title: str, thread_id: str) -> str:
        normalized_title = re.sub(r"\s+", " ", (title.strip() or "Untitled session"))
        sanitized = re.sub(r'[<>:"/\\\\|?*\x00-\x1F]', "_", normalized_title).strip(" .")
        if not sanitized:
            sanitized = "Untitled session"
        if len(sanitized) > 80:
            sanitized = sanitized[:80].rstrip()
        return f"{sanitized}-{thread_id}.md"
