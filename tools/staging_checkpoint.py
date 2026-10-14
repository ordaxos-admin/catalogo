#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import BinaryIO

BASE_STATE_NAME = "staging-offload-state.json"
JOURNAL_NAME = "staging-offload-journal.jsonl"
LOCK_NAME = "staging-offload.lock"
STATE_VERSION = 1
EVENT_VERSION = 1

def _empty_state() -> dict:
    return {"version": STATE_VERSION, "bucket": "tonecos-catalogo-staging", "objects": {}}

def load_state(root: Path) -> dict:
    root = Path(root)
    baseline = root / BASE_STATE_NAME
    state = _empty_state()
    if baseline.exists():
        state = json.loads(baseline.read_text(encoding="utf-8"))
    if state.get("version") != STATE_VERSION or not isinstance(state.get("objects"), dict):
        raise RuntimeError(f"checkpoint staging inválido: {baseline}")
    journal = root / JOURNAL_NAME
    if not journal.exists():
        return state
    raw_lines = journal.read_bytes().splitlines(keepends=True)
    for index, raw in enumerate(raw_lines):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if index == len(raw_lines) - 1 and not raw.endswith(b"\n"):
                break
            raise RuntimeError(f"journal staging corrompido na linha {index + 1}") from exc
        if event.get("version") != EVENT_VERSION or event.get("op") != "upsert":
            raise RuntimeError(f"evento staging inválido na linha {index + 1}")
        target = str(event.get("targetPath") or "").strip()
        row = event.get("object")
        if not target or not isinstance(row, dict) or row.get("remoteVerified") is not True:
            raise RuntimeError(f"evento staging incompleto na linha {index + 1}")
        state["objects"][target] = row
    return state

def append_verified_object(root: Path, target_path: str, row: dict) -> None:
    root = Path(root)
    target = str(target_path or "").strip()
    if not target or not isinstance(row, dict) or row.get("remoteVerified") is not True:
        raise ValueError("checkpoint exige targetPath e objeto remoteVerified")
    event = {"version": EVENT_VERSION, "op": "upsert", "targetPath": target, "object": row}
    payload = (json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    journal = root / JOURNAL_NAME
    root.mkdir(parents=True, exist_ok=True)
    with journal.open("ab", buffering=0) as handle:
        written = handle.write(payload)
        if written != len(payload):
            raise RuntimeError("escrita parcial no journal staging")
        os.fsync(handle.fileno())

class CheckpointLease:
    def __init__(self, handle: BinaryIO, platform: str):
        self.handle = handle
        self.platform = platform
    def close(self) -> None:
        if self.handle.closed:
            return
        try:
            self.handle.seek(0)
            if self.platform == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
    def __enter__(self) -> "CheckpointLease":
        return self
    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

def acquire_checkpoint_lease(root: Path) -> CheckpointLease:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / LOCK_NAME
    handle = path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError(f"outro fluxo já possui o lease do checkpoint: {path}") from exc
    return CheckpointLease(handle, os.name)

def verified_target_paths(root: Path) -> set[str]:
    return {
        str(target)
        for target, row in load_state(root)["objects"].items()
        if isinstance(row, dict) and row.get("remoteVerified") is True
    }
