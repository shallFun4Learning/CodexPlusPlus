from codex_session_delete.launcher import ApiFirstDeleteService, handle_bridge_request
from codex_session_delete.models import DeleteResult, DeleteStatus, ExportResult, ExportStatus, SessionRef


class FakeApiAdapter:
    def delete(self, session: SessionRef):
        return DeleteResult(DeleteStatus.SERVER_DELETED, session.session_id, "Deleted")


class FakeExportService:
    def export(self, session: SessionRef):
        return ExportResult(ExportStatus.EXPORTED, session.session_id, "Exported", filename="thread.md", markdown="# T\n")


def test_handle_bridge_request_dispatches_export_markdown(tmp_path):
    service = ApiFirstDeleteService(FakeApiAdapter(), None, tmp_path / "backups")
    export_service = FakeExportService()

    result = handle_bridge_request(service, export_service, "/export-markdown", {"session_id": "s1", "title": "First"})

    assert result == {
        "status": "exported",
        "session_id": "s1",
        "message": "Exported",
        "filename": "thread.md",
        "markdown": "# T\n",
    }


def test_handle_bridge_request_reports_export_unavailable(tmp_path):
    service = ApiFirstDeleteService(FakeApiAdapter(), None, tmp_path / "backups")

    result = handle_bridge_request(service, None, "/export-markdown", {"session_id": "s1", "title": "First"})

    assert result["status"] == "failed"
    assert "unavailable" in str(result["message"]).lower()
