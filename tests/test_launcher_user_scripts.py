from codex_session_delete.launcher import handle_bridge_request
from codex_session_delete.models import ExportResult, ExportStatus
from codex_session_delete.settings_store import SettingsStore
from codex_session_delete.user_scripts import UserScriptManager


class FakeDeleteService:
    def delete(self, session):
        raise AssertionError("delete should not be called")

    def undo(self, undo_token):
        raise AssertionError("undo should not be called")

    def find_archived_thread_by_title(self, title):
        return None

    def move_thread_workspace(self, session, target_cwd):
        raise AssertionError("move_thread_workspace should not be called")

    def thread_sort_key(self, session):
        raise AssertionError("thread_sort_key should not be called")

    def thread_sort_keys(self, sessions):
        raise AssertionError("thread_sort_keys should not be called")

class FakeExportService:
    def export(self, session):
        return ExportResult(ExportStatus.EXPORTED, session.session_id, "Exported", filename="thread.md", markdown="# Thread\n")

    def choose_output_directory(self, initial_dir=None):
        return {"status": "selected", "directory": "/Exports", "message": "Directory selected"}

    def export_project(self, target_cwd, project_label=None, download_dir=None):
        return {
            "status": "exported",
            "target_cwd": target_cwd,
            "project_label": project_label or "",
            "output_dir": f"{download_dir or '/Downloads'}/{project_label or 'project'}",
            "exported_count": 1,
            "failed_count": 0,
            "message": "Project exported",
            "files": [{"filename": "thread.md", "path": f"{download_dir or '/Downloads'}/{project_label or 'project'}/thread.md"}],
            "failures": [],
        }


class FakeRuntime:
    def __init__(self, manager):
        self.user_scripts = manager
        self.injected = []
        self.devtools_opened = False
        self.repaired = False

    def reload_user_scripts(self):
        bundle = self.user_scripts.build_enabled_bundle()
        self.injected.append(bundle)
        return self.user_scripts.inventory()

    def open_devtools(self):
        self.devtools_opened = True
        return {"status": "ok"}

    def backend_status(self):
        return {"status": "ok", "message": "后端已连接"}

    def repair_backend(self):
        self.repaired = True
        return {"status": "ok", "message": "后端已修复"}


def test_handle_bridge_request_lists_user_scripts(tmp_path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    (builtin / "demo.js").write_text("window.demo = true;", encoding="utf-8")
    manager = UserScriptManager(builtin, user, tmp_path / "config.json")
    runtime = FakeRuntime(manager)

    result = handle_bridge_request(FakeDeleteService(), FakeExportService(), "/user-scripts/list", {}, runtime)

    assert result["enabled"] is True
    assert result["scripts"][0]["key"] == "builtin:demo.js"


def test_handle_bridge_request_updates_user_script_toggles(tmp_path):
    manager = UserScriptManager(tmp_path / "builtin", tmp_path / "user", tmp_path / "config.json")
    runtime = FakeRuntime(manager)

    global_result = handle_bridge_request(FakeDeleteService(), FakeExportService(), "/user-scripts/set-enabled", {"enabled": False}, runtime)
    script_result = handle_bridge_request(FakeDeleteService(), FakeExportService(), "/user-scripts/set-script-enabled", {"key": "user:a.js", "enabled": False}, runtime)

    assert global_result["enabled"] is False
    assert script_result["scripts"] == []
    assert manager.load_config().scripts["user:a.js"] is False


def test_handle_bridge_request_reports_and_repairs_backend_status(tmp_path):
    manager = UserScriptManager(tmp_path / "builtin", tmp_path / "user", tmp_path / "config.json")
    runtime = FakeRuntime(manager)

    status = handle_bridge_request(FakeDeleteService(), FakeExportService(), "/backend/status", {}, runtime)
    repaired = handle_bridge_request(FakeDeleteService(), FakeExportService(), "/backend/repair", {}, runtime)

    assert status == {"status": "ok", "message": "后端已连接"}
    assert runtime.repaired is True
    assert repaired == {"status": "ok", "message": "后端已修复"}


def test_handle_bridge_request_gets_backend_settings(monkeypatch, tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.update({"providerSyncEnabled": True})
    monkeypatch.setattr("codex_session_delete.launcher.SettingsStore", lambda: store)
    manager = UserScriptManager(tmp_path / "builtin", tmp_path / "user", tmp_path / "config.json")
    runtime = FakeRuntime(manager)

    result = handle_bridge_request(FakeDeleteService(), FakeExportService(), "/settings/get", {}, runtime)

    assert result == {"providerSyncEnabled": True}


def test_handle_bridge_request_sets_backend_settings(monkeypatch, tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    monkeypatch.setattr("codex_session_delete.launcher.SettingsStore", lambda: store)
    manager = UserScriptManager(tmp_path / "builtin", tmp_path / "user", tmp_path / "config.json")
    runtime = FakeRuntime(manager)

    result = handle_bridge_request(FakeDeleteService(), FakeExportService(), "/settings/set", {"providerSyncEnabled": True}, runtime)

    assert result == {"providerSyncEnabled": True}
    assert store.load().provider_sync_enabled is True


def test_handle_bridge_request_exports_markdown(tmp_path):
    manager = UserScriptManager(tmp_path / "builtin", tmp_path / "user", tmp_path / "config.json")
    runtime = FakeRuntime(manager)

    exported = handle_bridge_request(FakeDeleteService(), FakeExportService(), "/export-markdown", {"session_id": "s1", "title": "First"}, runtime)

    assert exported["status"] == "exported"
    assert exported["filename"] == "thread.md"


def test_handle_bridge_request_exports_project_markdown(tmp_path):
    manager = UserScriptManager(tmp_path / "builtin", tmp_path / "user", tmp_path / "config.json")
    runtime = FakeRuntime(manager)

    exported = handle_bridge_request(
        FakeDeleteService(),
        FakeExportService(),
        "/export-project-markdown",
        {"target_cwd": "/project/a", "project_label": "a", "download_dir": "/Exports"},
        runtime,
    )

    assert exported["status"] == "exported"
    assert exported["output_dir"] == "/Exports/a"


def test_handle_bridge_request_chooses_export_directory(tmp_path):
    manager = UserScriptManager(tmp_path / "builtin", tmp_path / "user", tmp_path / "config.json")
    runtime = FakeRuntime(manager)

    selected = handle_bridge_request(
        FakeDeleteService(),
        FakeExportService(),
        "/choose-export-directory",
        {"initial_dir": "/project/a"},
        runtime,
    )

    assert selected == {"status": "selected", "directory": "/Exports", "message": "Directory selected"}
