from pathlib import Path

from codex_session_delete.markdown_exporter import MarkdownExportService
from codex_session_delete.models import ExportStatus, SessionRef
from tests.test_storage_adapter import create_codex_thread_db


def write_rollout(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_markdown_exporter_exports_user_and_assistant_with_timestamps(tmp_path):
    db_path = tmp_path / "state_5.sqlite"
    rollout_path = tmp_path / "rollout.jsonl"
    write_rollout(
        rollout_path,
        "\n".join(
            [
                '{"timestamp":"2026-05-10T13:11:48.038Z","type":"response_item","payload":{"type":"message","role":"developer","content":[{"type":"input_text","text":"ignore"}]}}',
                '{"timestamp":"2026-05-10T13:11:48.038Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"Hello"}]}}',
                '{"timestamp":"2026-05-10T13:12:06.594Z","type":"response_item","payload":{"type":"reasoning","summary":[],"content":[],"encrypted_content":"x"}}',
                '{"timestamp":"2026-05-10T13:12:06.594Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"World"}]}}',
            ]
        )
        + "\n",
    )
    create_codex_thread_db(db_path, rollout_path)
    service = MarkdownExportService(db_path)

    result = service.export(SessionRef(session_id="t1", title="Codex Thread"))

    assert result.status == ExportStatus.EXPORTED
    assert result.filename == "Codex Thread-t1.md"
    assert result.markdown == (
        "# Codex Thread\n\n"
        "### User\n"
        "_2026-05-10 21:11:48_\n\n"
        "Hello\n\n"
        "### Assistant\n"
        "_2026-05-10 21:12:06_\n\n"
        "World\n"
    )


def test_markdown_exporter_outputs_image_placeholder_without_data_url(tmp_path):
    db_path = tmp_path / "state_5.sqlite"
    rollout_path = tmp_path / "rollout.jsonl"
    write_rollout(
        rollout_path,
        "\n".join(
            [
                '{"timestamp":"2026-05-10T13:11:48.038Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_image","image_url":"data:image/png;base64,AAAA","detail":"high"},{"type":"input_image","image_url":"https://example.com/image.png","detail":"high"}]}}',
            ]
        )
        + "\n",
    )
    create_codex_thread_db(db_path, rollout_path)
    service = MarkdownExportService(db_path)

    result = service.export(SessionRef(session_id="t1", title="Codex Thread"))

    assert result.status == ExportStatus.EXPORTED
    assert "> Image attachment" in (result.markdown or "")
    assert "> Source: https://example.com/image.png" in (result.markdown or "")
    assert "data:image/png" not in (result.markdown or "")


def test_markdown_exporter_accepts_local_prefixed_thread_id(tmp_path):
    db_path = tmp_path / "state_5.sqlite"
    rollout_path = tmp_path / "rollout.jsonl"
    write_rollout(
        rollout_path,
        '{"timestamp":"2026-05-10T13:11:48.038Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"Hello"}]}}\n',
    )
    create_codex_thread_db(db_path, rollout_path)
    service = MarkdownExportService(db_path)

    result = service.export(SessionRef(session_id="local:t1", title="Codex Thread"))

    assert result.status == ExportStatus.EXPORTED
    assert result.session_id == "t1"


def test_markdown_exporter_sanitizes_filename_and_appends_thread_id(tmp_path):
    db_path = tmp_path / "state_5.sqlite"
    rollout_path = tmp_path / "rollout.jsonl"
    write_rollout(
        rollout_path,
        '{"timestamp":"2026-05-10T13:11:48.038Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"Hello"}]}}\n',
    )
    create_codex_thread_db(db_path, rollout_path)
    service = MarkdownExportService(db_path)

    result = service.export(SessionRef(session_id="t1", title='A:/Very*Long?Title"' + "X" * 120))

    assert result.status == ExportStatus.EXPORTED
    assert result.filename is not None
    assert result.filename.endswith("-t1.md")
    assert ":" not in result.filename
    assert "*" not in result.filename
    assert "?" not in result.filename
    assert '"' not in result.filename


def test_markdown_exporter_skips_invalid_timestamp_but_keeps_body(tmp_path):
    db_path = tmp_path / "state_5.sqlite"
    rollout_path = tmp_path / "rollout.jsonl"
    write_rollout(
        rollout_path,
        '{"timestamp":"invalid","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Hello"}]}}\n',
    )
    create_codex_thread_db(db_path, rollout_path)
    service = MarkdownExportService(db_path)

    result = service.export(SessionRef(session_id="t1", title="Codex Thread"))

    assert result.status == ExportStatus.EXPORTED
    assert "### Assistant\n\nHello\n" in (result.markdown or "")
    assert "_invalid_" not in (result.markdown or "")


def test_markdown_exporter_fails_for_missing_thread(tmp_path):
    db_path = tmp_path / "state_5.sqlite"
    rollout_path = tmp_path / "rollout.jsonl"
    write_rollout(rollout_path, "")
    create_codex_thread_db(db_path, rollout_path)
    service = MarkdownExportService(db_path)

    result = service.export(SessionRef(session_id="missing", title="Nope"))

    assert result.status == ExportStatus.FAILED
    assert "Thread not found" in result.message


def test_markdown_exporter_fails_for_missing_rollout_file(tmp_path):
    db_path = tmp_path / "state_5.sqlite"
    rollout_path = tmp_path / "rollout.jsonl"
    create_codex_thread_db(db_path, rollout_path)
    service = MarkdownExportService(db_path)

    result = service.export(SessionRef(session_id="t1", title="Codex Thread"))

    assert result.status == ExportStatus.FAILED
    assert "Rollout file not found" in result.message


def test_markdown_exporter_fails_when_no_exportable_messages_exist(tmp_path):
    db_path = tmp_path / "state_5.sqlite"
    rollout_path = tmp_path / "rollout.jsonl"
    write_rollout(
        rollout_path,
        "\n".join(
            [
                '{"timestamp":"2026-05-10T13:12:06.594Z","type":"response_item","payload":{"type":"function_call","name":"test","arguments":"{}","call_id":"1"}}',
                '{"timestamp":"2026-05-10T13:12:07.594Z","type":"response_item","payload":{"type":"reasoning","summary":[],"content":[],"encrypted_content":"x"}}',
            ]
        )
        + "\n",
    )
    create_codex_thread_db(db_path, rollout_path)
    service = MarkdownExportService(db_path)

    result = service.export(SessionRef(session_id="t1", title="Codex Thread"))

    assert result.status == ExportStatus.FAILED
    assert "No exportable" in result.message
