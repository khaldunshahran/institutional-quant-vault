import os
import time
import zipfile
import pytest
from pathlib import Path
from scripts.backup_runtime import backup_runtime, _prune_old_backups


def test_backup_runtime_creates_valid_zip(tmp_path):
    # Setup mock runtime directory with test files
    mock_runtime = tmp_path / "runtime"
    mock_runtime.mkdir()
    (mock_runtime / "trade_history.json").write_text('{"trades": []}', encoding="utf-8")
    (mock_runtime / "state.json").write_text('{"enabled": true}', encoding="utf-8")
    (mock_runtime / "decision.log").write_text("decision line 1\ndecision line 2\n", encoding="utf-8")

    mock_backups = tmp_path / "backups"

    res = backup_runtime(
        runtime_dir=str(mock_runtime),
        backup_dir=str(mock_backups),
        retention_days=30,
        include_timestamp=False
    )

    assert res["success"] is True
    assert res["files_backed_up"] == 3
    assert res["archive_size_bytes"] > 0
    assert Path(res["archive_path"]).exists()

    # Test zip integrity and contents
    with zipfile.ZipFile(res["archive_path"], "r") as zf:
        assert zf.testzip() is None
        names = zf.namelist()
        assert "trade_history.json" in names
        assert "state.json" in names
        assert "decision.log" in names


def test_backup_runtime_prunes_expired(tmp_path):
    mock_runtime = tmp_path / "runtime"
    mock_runtime.mkdir()
    (mock_runtime / "state.json").write_text('{}', encoding="utf-8")

    mock_backups = tmp_path / "backups"
    mock_backups.mkdir()

    # Create a simulated old backup (45 days old)
    old_archive = mock_backups / "runtime_2026-08-01.zip"
    with zipfile.ZipFile(old_archive, "w") as zf:
        zf.writestr("test.txt", "data")
    
    # Set mtime to 45 days ago
    old_time = time.time() - (45 * 86400.0)
    os.utime(old_archive, (old_time, old_time))

    assert old_archive.exists()

    res = backup_runtime(
        runtime_dir=str(mock_runtime),
        backup_dir=str(mock_backups),
        retention_days=30
    )

    assert res["success"] is True
    assert res["pruned_archives_count"] == 1
    assert not old_archive.exists(), "Old archive should be pruned"
    assert Path(res["archive_path"]).exists(), "Fresh archive must exist"


def test_backup_runtime_nonexistent_src(tmp_path):
    res = backup_runtime(
        runtime_dir=str(tmp_path / "nonexistent"),
        backup_dir=str(tmp_path / "backups")
    )
    assert res["success"] is False
    assert "does not exist" in res["error"]
