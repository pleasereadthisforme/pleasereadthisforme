"""Bounded, read-only sizing; execution still validates the protected source."""
from prtfm.localization import text as _ui_text

import shutil
import stat
import time
from pathlib import Path

from prtfm.store.body_audit import BODY_LIMIT, METADATA_LIMIT
from prtfm.store.lifecycle import StorageBlocked
from prtfm.store.locked_inspection import _inventory
from prtfm.store.offline_snapshot import PREPARED_DATABASE_LIMIT

MAX_BYTES = 1024**3
MAX_FILES = 2000  # Capture holds one protected handle per file.
MAX_SECONDS = 300

MESSAGES = {
    'legacy_review_required': _ui_text('구버전 데이터의 형식·본문·벡터를 확인하지 못했습니다. 원본을 보존했습니다. 기존 앱을 종료하고 호환성 검토 결과를 확인해 주세요.'),
    'snapshot_size_limit': _ui_text('개별 데이터베이스가 현재 검증 한도(256 MiB)를 초과합니다. 기존 앱에서 계속 사용할 수 있습니다.'),
    'body_size_limit': _ui_text('개별 처리 본문이 현재 검증 한도(64 MiB)를 초과합니다.'),
    'metadata_size_limit': _ui_text('개별 문서 메타데이터가 현재 검증 한도(8 MiB)를 초과합니다.'),
    'capture_size_limit': _ui_text('현재 가져오기 한도(1 GiB)를 초과합니다. 기존 앱에서 계속 사용할 수 있습니다.'),
    'capture_file_limit': _ui_text('가져올 파일 수가 현재 한도(2,000개)를 초과합니다.'),
    'capture_time_limit': _ui_text('가져오기 제한 시간(5분)을 초과했습니다. 다른 작업을 종료한 뒤 다시 시도해 주세요.'),
    'inventory_limit': _ui_text('저장소 구조를 제한 시간 안에 확인하지 못했습니다.'),
    'storage_busy': _ui_text('기존 앱이 저장소를 사용하고 있습니다. 기존 앱을 종료한 뒤 다시 시도해 주세요.'),
    'source_changed': _ui_text('확인 중 원본이 변경됐습니다. 기존 앱을 종료한 뒤 다시 시도해 주세요.'),
    'insufficient_disk_space': _ui_text('대상 드라이브의 여유 공간이 부족합니다.'),
    'capture_io_error': _ui_text('파일을 읽거나 복사하지 못했습니다. 기존 앱을 종료하고 접근 권한과 디스크 상태를 확인해 주세요.'),
}


def failure(reason):
    if reason in ('staging_time_limit', 'activation_time_limit'):
        reason = 'capture_time_limit'
    known = reason if reason in MESSAGES else 'first_run_failed_review_required'
    return {'status': 'blocked', 'reason': known, 'message': MESSAGES.get(
        known, _ui_text('가져오기를 완료하지 못했습니다. 원본은 보존했습니다. 다시 시작해 저장소 상태를 확인해 주세요.'))}


def assess(source, target):
    root = Path(source)
    deadline = time.monotonic() + 5
    entries = _inventory(root, deadline)
    total = count = 0
    entry_reason = None
    for name in entries:
        if time.monotonic() >= deadline:
            raise StorageBlocked('inventory_limit')
        info = (root / name).lstat()
        if stat.S_ISREG(info.st_mode):
            total += info.st_size
            count += 1
            path = Path(name)
            if path.suffix == '.db' and info.st_size > PREPARED_DATABASE_LIMIT:
                entry_reason = entry_reason or 'snapshot_size_limit'
            if 'cache' in path.parts:
                if path.name == 'document.md' and info.st_size > BODY_LIMIT:
                    entry_reason = entry_reason or 'body_size_limit'
                if path.name == 'metadata.json' and info.st_size > METADATA_LIMIT:
                    entry_reason = entry_reason or 'metadata_size_limit'
    if entries != _inventory(root, deadline):
        raise StorageBlocked('source_changed')
    parent = Path(target).parent
    while not parent.exists():
        parent = parent.parent
    # Logical size includes hardlinks conservatively; allow room for DB recovery.
    required = total * 2 + 64 * 1024**2
    free = shutil.disk_usage(parent).free
    reason = ('capture_size_limit' if total > MAX_BYTES else
              'capture_file_limit' if count > MAX_FILES else
              'insufficient_disk_space' if free < required else entry_reason)
    report = {'bytes': total, 'files': count, 'required_free_bytes': required,
              'free_bytes': free, 'max_bytes': MAX_BYTES, 'max_files': MAX_FILES,
              'max_seconds': MAX_SECONDS, 'read_only': True}
    report.update(failure(reason) if reason else {'status': 'ready'})
    return report
