from pathlib import Path


def test_setup_bat_offers_install_and_uninstall_choices():
    text = Path("setup.bat").read_text(encoding="utf-8")

    assert "Codex++" in text
    assert "[1]" in text and "install" in text.lower()
    assert "[2]" in text and "uninstall" in text.lower()
    assert "[3]" in text and "update" in text.lower()
    assert 'set "VENV_DIR=%~dp0.venv"' in text
    assert 'set "VENV_PY=%VENV_DIR%\\Scripts\\python.exe"' in text
    assert 'python -m venv "%VENV_DIR%"' in text
    assert '"%VENV_PY%" -m pip install -e .' in text
    assert '"%VENV_PY%" -m codex_session_delete setup' in text
    assert '"%RUNPY%" -m codex_session_delete remove' in text
    assert '"%VENV_PY%" -m codex_session_delete update' in text
    assert "pause" in text.lower()
