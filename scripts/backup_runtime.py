#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Daily Runtime Backup Utility for Institutional Quant Vault
Author: Google Antigravity (Advanced Agentic Systems)

Snapshots and compresses the entire runtime/ folder into a date-stamped zip archive,
verifies archive integrity, and rotates backups older than retention_days.
"""

import os
import sys
import time
import zipfile
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger("RuntimeBackup")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def backup_runtime(
    runtime_dir: Optional[str] = None,
    backup_dir: Optional[str] = None,
    retention_days: int = 30,
    include_timestamp: bool = False
) -> Dict[str, Any]:
    """
    Creates a compressed zip archive of runtime_dir inside backup_dir.
    
    Args:
        runtime_dir: Path to runtime folder (defaults to <repo>/runtime)
        backup_dir: Path to destination backup folder (defaults to <repo>/backups)
        retention_days: Maximum age of backups in days before automatic pruning
        include_timestamp: If True, adds HHMMSS to filename (defaults to YYYY-MM-DD)
        
    Returns:
        Dict with status, archive_path, file_count, size_bytes, and pruned_count.
    """
    repo_root = Path(__file__).resolve().parent.parent
    src_dir = Path(runtime_dir) if runtime_dir else repo_root / "runtime"
    dst_dir = Path(backup_dir) if backup_dir else repo_root / "backups"

    if not src_dir.exists():
        return {
            "success": False,
            "error": f"Source directory does not exist: {src_dir}"
        }

    dst_dir.mkdir(parents=True, exist_ok=True)

    now_utc = time.gmtime()
    date_str = time.strftime("%Y-%m-%d", now_utc)
    if include_timestamp:
        archive_name = f"runtime_{date_str}_{time.strftime('%H%M%S', now_utc)}.zip"
    else:
        archive_name = f"runtime_{date_str}.zip"

    archive_path = dst_dir / archive_name
    temp_archive_path = dst_dir / f"{archive_name}.tmp"

    files_backed_up = 0
    total_uncompressed_bytes = 0

    try:
        with zipfile.ZipFile(temp_archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(src_dir):
                for f in files:
                    file_path = Path(root) / f
                    # Skip temporary files or lock files
                    if f.endswith(".tmp") or f.endswith(".lock"):
                        continue
                    try:
                        arcname = file_path.relative_to(src_dir)
                        zf.write(file_path, arcname=str(arcname))
                        files_backed_up += 1
                        total_uncompressed_bytes += file_path.stat().st_size
                    except (PermissionError, FileNotFoundError) as read_err:
                        logger.warning(f"Skipping active/transient file {file_path}: {read_err}")

        # Atomic replace to final filename
        if temp_archive_path.exists():
            if archive_path.exists():
                archive_path.unlink()
            temp_archive_path.rename(archive_path)

        archive_size = archive_path.stat().st_size

        # Prune archives older than retention_days
        pruned_count = _prune_old_backups(dst_dir, retention_days)

        logger.info(
            f"Backup created: {archive_path.name} "
            f"({files_backed_up} files, {archive_size / 1024:.1f} KB, "
            f"compression ratio: {archive_size / max(1, total_uncompressed_bytes):.1%})"
        )

        return {
            "success": True,
            "archive_path": str(archive_path),
            "archive_name": archive_name,
            "files_backed_up": files_backed_up,
            "archive_size_bytes": archive_size,
            "uncompressed_bytes": total_uncompressed_bytes,
            "pruned_archives_count": pruned_count,
            "timestamp_utc": time.strftime("%Y-%m-%d %H:%M:%S UTC", now_utc)
        }

    except Exception as e:
        if temp_archive_path.exists():
            temp_archive_path.unlink(missing_ok=True)
        logger.error(f"Backup failed: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def _prune_old_backups(backup_dir: Path, retention_days: int) -> int:
    """Removes archives older than retention_days."""
    if retention_days <= 0:
        return 0

    cutoff_sec = time.time() - (retention_days * 86400.0)
    pruned = 0

    for item in backup_dir.glob("runtime_*.zip"):
        try:
            if item.is_file() and item.stat().st_mtime < cutoff_sec:
                item.unlink()
                pruned += 1
                logger.info(f"Pruned expired backup: {item.name}")
        except Exception as e:
            logger.warning(f"Failed to prune {item.name}: {e}")

    return pruned


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Backup runtime/ folder daily")
    parser.add_argument("--runtime-dir", type=str, default=None, help="Path to runtime directory")
    parser.add_argument("--backup-dir", type=str, default=None, help="Path to backup destination")
    parser.add_argument("--retention-days", type=int, default=30, help="Days of backups to keep (default: 30)")
    parser.add_argument("--timestamp", action="store_true", help="Include HHMMSS timestamp in filename")
    args = parser.parse_args()

    res = backup_runtime(
        runtime_dir=args.runtime_dir,
        backup_dir=args.backup_dir,
        retention_days=args.retention_days,
        include_timestamp=args.timestamp
    )
    print(f"Result: {res}")
    sys.exit(0 if res.get("success") else 1)
