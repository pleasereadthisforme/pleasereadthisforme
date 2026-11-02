from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from prtfm.config import data_root

SKIP_DIRS = frozenset({"node_modules", "__pycache__", ".venv", "venv"})
# Windows 휴지통·시스템 복원 폴더(대소문자 무시)
SKIP_DIRS_CI = frozenset({"$recycle.bin", "system volume information"})
_HIDDEN_ATTRS = stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM


def _stat(p: str) -> os.stat_result:
    return Path(p).stat()


def _hidden(st) -> bool:
    # Windows의 숨김·시스템 속성. 다른 OS에는 이 필드가 없어 항상 False
    return bool(getattr(st, "st_file_attributes", 0) & _HIDDEN_ATTRS)


def _under(p: Path, base: Path) -> bool:
    return p == base or base in p.parents


def _is_dir_link(p: str) -> bool:
    return os.path.islink(p) or os.path.isjunction(p)


class ScopeError(Exception):
    pass


@dataclass(frozen=True)
class FileEntry:
    rel: str
    path: Path
    size: int
    mtime_ns: int


class Scope:
    """사용자가 고른 폴더. 모든 경로 접근은 실제 경로 기준으로 이 안인지 검사한다."""

    def __init__(self, root: Path):
        self.root = Path(os.path.realpath(root))
        if not self.root.is_dir():
            raise ScopeError(f"not a directory: {root}")

    def contains(self, p: Path) -> bool:
        return _under(Path(os.path.realpath(p)), self.root)

    def resolve(self, rel: str) -> Path:
        real = Path(os.path.realpath(self.root / rel))
        if not self.contains(real):
            raise ScopeError(f"outside scope: {rel}")
        return real

    def walk(self, exts: frozenset[str], *, errors=None) -> Iterator[FileEntry]:
        seen: set[Path] = set()
        def failed(path, exc):
            if errors is not None:
                try:
                    rel = Path(path).relative_to(self.root).as_posix()
                except (TypeError, ValueError):
                    rel = '.'
                errors.append((rel, type(exc).__name__))

        # 앱데이터 폴더가 범위 안에 있으면(기본 위치가 홈 아래) 자기 캐시를 다시 색인하게 되므로 제외한다
        app_data = Path(os.path.realpath(data_root()))

        def keep_dir(dirpath: str, d: str) -> bool:
            if d.startswith(".") or d in SKIP_DIRS or d.lower() in SKIP_DIRS_CI:
                return False
            p = os.path.join(dirpath, d)
            if _is_dir_link(p) or _under(Path(os.path.realpath(p)), app_data):
                return False
            try:
                return not _hidden(_stat(p))
            except OSError as exc:
                failed(p, exc)
                return False

        # 폴더 링크는 따라가지 않는다(범위 밖 순회·순환 방지).
        # Windows 정션은 islink가 False라 os.walk가 따라 들어가므로 직접 걸러낸다.
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False,
                                                       onerror=lambda exc: failed(exc.filename, exc)):
            dirnames[:] = sorted(d for d in dirnames if keep_dir(dirpath, d))
            for name in sorted(filenames):
                if name.startswith((".", "~$")):  # ~$ = Office 잠금 파일
                    continue
                p = Path(dirpath) / name
                if p.suffix.lower() not in exts:
                    continue
                # 목록 작성과 stat 사이에 사라지거나 읽을 수 없게 된 파일(잠금 파일 등)은 건너뛴다
                try:
                    real = Path(os.path.realpath(p))
                    if real in seen or not self.contains(real) or _under(real, app_data):
                        continue
                    st = _stat(str(real))
                except OSError as exc:
                    failed(p, exc)
                    continue
                if not stat.S_ISREG(st.st_mode) or _hidden(st):
                    continue
                seen.add(real)
                yield FileEntry(p.relative_to(self.root).as_posix(), real, st.st_size, st.st_mtime_ns)
