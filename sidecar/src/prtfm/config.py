from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

APP_DIR_NAME = "PleaseReadThisForMe"


@dataclass(frozen=True)
class SemanticLimits:
    optimize_chunks: int = 1_000
    chunks: int = 10_000
    batch_chunks: int = 32
    batch_tokens: int = 8_000
    timeout_seconds: int = 30
    retries_429: int = 2


SEMANTIC_LIMITS = SemanticLimits()


@dataclass(frozen=True)
class Limits:
    warn_files: int = 20_000
    warn_total_bytes: int = 20 * 1024**3
    skip_file_bytes: int = 500 * 1024**2
    skip_file_pages: int = 3_000


@dataclass(frozen=True)
class StorageLocation:
    """Path decision only: resolving a location never opens or creates storage."""

    channel: str
    path: Path
    legacy_path: Path
    custom: bool


def resolve_storage_location(channel: str = "development") -> StorageLocation:
    # The native host will supply a trusted build channel after legacy protection
    # and first-run guidance are integrated. Never infer it from version or URL.
    names = {"development": APP_DIR_NAME, "stable": APP_DIR_NAME,
             "preview": "PleaseReadThisForMePreview"}
    if channel not in names:
        raise ValueError("unsupported storage channel")
    base = Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share"))
    override = os.environ.get("PRTFM_DATA_DIR")
    return StorageLocation(channel=channel,
                           path=Path(override) if override else base / names[channel],
                           legacy_path=base / APP_DIR_NAME, custom=bool(override))


def storage_paths_overlap(first: Path, second: Path) -> bool:
    """Same/ancestor paths, including existing aliases. Not a write authorization.

    A later filesystem mutation can change aliases; import must recheck under its
    own locking/handle policy before writing. This helper performs no creation.
    """
    left = os.path.normcase(os.path.realpath(first))
    right = os.path.normcase(os.path.realpath(second))
    try:
        return os.path.commonpath((left, right)) in (left, right)
    except ValueError:  # Different Windows drives.
        return False


def data_root() -> Path:
    # Preserve the existing application path until the channel transition is
    # shipped together with ownership checks and existing-data guidance.
    return resolve_storage_location().path


def workspace_dir(scope_root: Path) -> Path:
    key = hashlib.sha256(os.path.realpath(scope_root).encode("utf-8")).hexdigest()[:16]
    return data_root() / "folders" / key
