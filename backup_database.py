"""Consistent SQLite backup with integrity verification and retention."""
import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DEFAULT_DB = Path(os.getenv("FORENSICS_DB_PATH", APP_DIR / "forensics.db")).resolve()
DEFAULT_BACKUP_DIR = Path(os.getenv("BACKUP_DIR", APP_DIR / "backups")).resolve()

def integrity(path: Path) -> str:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def create_backup(source: Path, destination_dir: Path, retention_days: int = 14) -> Path:
    if not source.exists():
        raise FileNotFoundError(source)
    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = destination_dir / f"forensics-{timestamp}.db"
    temporary = target.with_suffix(".db.tmp")
    src = sqlite3.connect(str(source), timeout=30)
    dst = sqlite3.connect(str(temporary))
    try:
        src.execute("PRAGMA busy_timeout=30000")
        src.backup(dst, pages=256, sleep=0.05)
    finally:
        dst.close()
        src.close()
    result = integrity(temporary)
    if result != "ok":
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"backup integrity check failed: {result}")
    os.replace(temporary, target)
    checksum = sha256(target)
    target.with_suffix(".db.sha256").write_text(f"{checksum}  {target.name}\n", encoding="ascii")
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))
    for old in destination_dir.glob("forensics-*.db"):
        if datetime.fromtimestamp(old.stat().st_mtime, timezone.utc) < cutoff:
            old.unlink(missing_ok=True)
            old.with_suffix(".db.sha256").unlink(missing_ok=True)
    print(json.dumps({"backup": str(target), "sha256": checksum, "integrity": result}, indent=2))
    return target

def restore_backup(backup: Path, target: Path):
    if integrity(backup) != "ok":
        raise RuntimeError("refusing to restore a corrupt backup")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".restore.tmp")
    src = sqlite3.connect(f"file:{backup.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(str(temporary))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    if integrity(temporary) != "ok":
        temporary.unlink(missing_ok=True)
        raise RuntimeError("restored temporary database failed integrity check")
    if target.exists():
        create_backup(target, DEFAULT_BACKUP_DIR / "pre-restore", retention_days=30)
    os.replace(temporary, target)
    print(json.dumps({"restored": str(backup), "target": str(target)}, indent=2))

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--retention-days", type=int, default=14)
    parser.add_argument("--restore", type=Path)
    parser.add_argument("--confirm-restore", action="store_true")
    args = parser.parse_args()
    if args.restore:
        if not args.confirm_restore:
            parser.error("--restore requires --confirm-restore")
        restore_backup(args.restore.resolve(), args.db.resolve())
    else:
        create_backup(args.db.resolve(), args.backup_dir.resolve(), args.retention_days)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
