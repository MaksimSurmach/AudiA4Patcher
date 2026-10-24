"""Private, durable local evidence. Hashes detect corruption, not malicious editing."""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import hmac
import json
import os
import tempfile
import uuid
import zipfile
from pathlib import Path

from .model import SafetyError


def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds")


def digest(document: dict | list) -> str:
    data = {k: v for k, v in document.items() if k != "sha256"} if isinstance(document, dict) else document
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


class Store:
    KINDS = ("snapshots", "plans", "transactions", "traces", "exports")

    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for kind in self.KINDS:
            (self.root / kind).mkdir(exist_ok=True, mode=0o700)
        self.trace_path: Path | None = None

    @contextlib.contextmanager
    def lock(self):
        path = self.root / ".session.lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SafetyError("Another session is already using this data directory") from exc
            yield
        finally:
            os.close(fd)

    def _atomic(self, path: Path, data: bytes):
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def save(self, kind: str, document: dict) -> Path:
        if kind not in self.KINDS or kind in ("exports", "traces"):
            raise ValueError("Invalid artifact category")
        path = self.root / kind / f"{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:12]}.json"
        self.update(path, document)
        return path

    def update(self, path: Path | str, document: dict):
        path = Path(path).resolve()
        if path.parent not in {self.root / kind for kind in self.KINDS}:
            raise SafetyError("Artifact writes must stay in the selected data directory")
        value = dict(document)
        value["sha256"] = digest(value)
        self._atomic(path, (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode())

    @staticmethod
    def read(path: Path | str) -> dict:
        path = Path(path).expanduser()
        if path.stat().st_size > 16 * 1024 * 1024:
            raise SafetyError("Artifact is too large")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or not isinstance(value.get("sha256"), str):
                raise TypeError("Missing hash")
            if not hmac.compare_digest(value["sha256"], digest(value)):
                raise ValueError("Hash mismatch")
        except (ValueError, TypeError) as exc:
            raise SafetyError(f"Artifact integrity check failed: {path.name}") from exc
        return value

    def start_trace(self):
        self.trace_path = self.root / "traces" / f"{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:12]}.jsonl"
        fd = os.open(self.trace_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        self.trace({"event": "session_start"})

    def trace(self, event: dict):
        if self.trace_path is None:
            return
        line = json.dumps({"time": now(), **event}, ensure_ascii=False, allow_nan=False) + "\n"
        with self.trace_path.open("a", encoding="utf-8") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())

    def files(self, kind: str) -> list[Path]:
        """Return oldest writes first; random filename suffixes do not encode order."""
        if kind not in self.KINDS:
            raise ValueError("Unknown artifact category")
        return sorted((self.root / kind).glob("*.json"),
                      key=lambda path: (path.stat().st_mtime_ns, path.name))

    def export(self, dictionary: dict | list | None = None) -> Path:
        """Explicit local export; never uploads anything. Includes unredacted evidence."""
        with self.lock():
            path = self.root / "exports" / f"audi-support-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}.zip"
            files = []
            for kind in ("snapshots", "plans", "transactions", "traces"):
                files.extend(sorted((self.root / kind).iterdir()))
            files = [p for p in files if p.is_file() and not p.is_symlink() and not p.name.startswith(".")]
            if len(files) > 5000 or sum(p.stat().st_size for p in files) > 256 * 1024 * 1024:
                raise SafetyError("Export exceeds 256 MiB / 5000 files; use a separate run directory")
            fd, temporary = tempfile.mkstemp(prefix=".export-", dir=path.parent)
            os.close(fd)
            try:
                with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                    manifest = {"schema": "audi-workbench.export/v1", "created_at": now(), "redacted": False,
                                "purpose": "Read-only evidence for ECU-specific dictionary research", "files": {}}
                    for item in files:
                        data = item.read_bytes()
                        name = item.relative_to(self.root).as_posix()
                        archive.writestr(name, data)
                        manifest["files"][name] = hashlib.sha256(data).hexdigest()
                    if dictionary is not None:
                        archive.writestr("dictionary.json", json.dumps(dictionary, indent=2, ensure_ascii=False))
                    archive.writestr("manifest.json", json.dumps(manifest, indent=2))
                    archive.writestr("README.txt", "Local evidence export. May include VIN, serial numbers and raw BLE/UDS logs.\n"
                                     "Successful reads do not prove field semantics or writability.\n"
                                     "Snapshot coverage is explicit and partial; this is not a flash image.\n")
                with open(temporary, "rb") as stream:
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return path
