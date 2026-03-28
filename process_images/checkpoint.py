"""Checkpoint support for crash recovery.

Writes a JSON checkpoint file after each processed image so the pipeline
can resume from where it left off after a crash.  The checkpoint records
which images have been processed and their status, plus a hash of the
rules YAML to detect config changes between runs.

Thread safety: all writes go through the main thread (same pattern as
file I/O in parallel mode).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .models import Flag, ProcessingStatus

logger = logging.getLogger(__name__)


@dataclass
class CheckpointEntry:
    """One processed image in the checkpoint."""

    status: str
    outputs: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    timestamp: float = 0.0


@dataclass
class Checkpoint:
    """In-memory checkpoint state.

    Loaded from disk on --resume, written incrementally during processing.
    """

    path: Path
    config_hash: str = ""
    started_at: str = ""
    completed: dict[str, CheckpointEntry] = field(default_factory=dict)
    _dirty: bool = False

    def is_done(self, image_name: str) -> bool:
        """Check if an image has already been processed."""
        return image_name in self.completed

    def record(
        self,
        image_name: str,
        status: ProcessingStatus,
        outputs: list[str],
        flags: list[Flag],
    ) -> None:
        """Record a processed image."""
        self.completed[image_name] = CheckpointEntry(
            status=status.value,
            outputs=outputs,
            flags=[f.value for f in flags],
            timestamp=time.time(),
        )
        self._dirty = True

    def flush(self) -> None:
        """Write checkpoint to disk if dirty."""
        if not self._dirty:
            return
        data = {
            "started_at": self.started_at,
            "config_hash": self.config_hash,
            "total_completed": len(self.completed),
            "completed": {
                name: {
                    "status": entry.status,
                    "outputs": entry.outputs,
                    "flags": entry.flags,
                }
                for name, entry in self.completed.items()
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        tmp.replace(self.path)  # atomic rename
        self._dirty = False

    @property
    def skip_count(self) -> int:
        return len(self.completed)


def load_checkpoint(
    path: Path,
    current_config_hash: str,
    force: bool = False,
) -> Checkpoint:
    """Load an existing checkpoint for resume.

    Args:
        path: Checkpoint file path.
        current_config_hash: Hash of current rules YAML.
        force: If True, ignore config hash mismatch.

    Returns:
        Checkpoint with previously completed images.

    Raises:
        ValueError: If config hash doesn't match and force is False.
    """
    if not path.exists():
        logger.info("No checkpoint file found — starting fresh")
        return new_checkpoint(path, current_config_hash)

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    saved_hash = data.get("config_hash", "")
    if saved_hash and saved_hash != current_config_hash and not force:
        raise ValueError(
            f"Rules YAML changed since checkpoint was created "
            f"(saved={saved_hash[:12]}..., current={current_config_hash[:12]}...). "
            f"Use --resume --force to resume anyway, or delete the checkpoint file."
        )

    if saved_hash != current_config_hash:
        logger.warning(
            "Config hash mismatch (--force): checkpoint may contain "
            "results from different rules"
        )

    completed: dict[str, CheckpointEntry] = {}
    for name, entry_data in data.get("completed", {}).items():
        completed[name] = CheckpointEntry(
            status=entry_data.get("status", ""),
            outputs=entry_data.get("outputs", []),
            flags=entry_data.get("flags", []),
        )

    logger.info(
        "Loaded checkpoint: %d images already processed", len(completed)
    )

    cp = Checkpoint(
        path=path,
        config_hash=current_config_hash,
        started_at=data.get("started_at", ""),
        completed=completed,
    )
    return cp


def new_checkpoint(path: Path, config_hash: str) -> Checkpoint:
    """Create a fresh checkpoint."""
    from datetime import datetime, timezone

    return Checkpoint(
        path=path,
        config_hash=config_hash,
        started_at=datetime.now(timezone.utc).isoformat(),
    )


def hash_file(path: Path) -> str:
    """Compute SHA-256 hash of a file (used for rules YAML)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
