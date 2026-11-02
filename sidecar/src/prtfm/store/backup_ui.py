"""Bounded native-host protocol; invoked outside the running server."""
from prtfm.localization import text as _ui_text

import argparse
import json
from pathlib import Path

from prtfm.config import resolve_storage_location
from prtfm.store.backup import create_backup
from prtfm.store.backup_plan import plan_backup
from prtfm.store.backup_restore import backup_status, restore_backup
from prtfm.store.ownership import inspect_ownership

MESSAGES = {
    'storage_busy': _ui_text('저장소를 사용하는 작업이 있습니다. 작업 완료 후 다시 시도하세요.'),
    'restore_target_exists': _ui_text('복원 대상에 기존 데이터가 있습니다. 덮어쓰지 않았습니다.'),
    'backup_path_too_long': _ui_text('백업 경로가 너무 깁니다. 드라이브에 더 가까운 짧은 경로를 선택하세요.'),
    'restore_path_too_long': _ui_text('복원 경로가 너무 깁니다. 더 짧은 경로를 선택하세요.'),
    'insufficient_disk_space': _ui_text('저장 공간이 부족합니다. 다른 위치를 선택하세요.'),
    'incomplete_backup': _ui_text('완료되지 않은 백업입니다. 다른 백업을 선택하세요.'),
    'backup_hash_mismatch': _ui_text('백업 내용이 기록과 다릅니다. 원본을 보존하고 다른 백업을 선택하세요.'),
    'unclassified_entry': _ui_text('분류되지 않은 파일이 있어 백업을 중단했습니다. 기존 데이터는 보존했습니다.'),
    'source_changed': _ui_text('작업 중 원본이 변경됐습니다. 기존 데이터를 보존했습니다.'),
}


def failure(reason):
    return {'status': 'blocked', 'reason': reason if reason in MESSAGES else 'backup_operation_failed',
            'message': MESSAGES.get(reason, _ui_text('작업을 완료하지 못했습니다. 기존 데이터는 보존했습니다. 저장 위치와 앱 종료 상태를 확인하세요.'))}


def execute(action, channel, target):
    location = resolve_storage_location(channel)
    if action == 'inspect':
        return backup_status(Path(target))
    if action == 'restore':
        # First-run only: the default/current store must be absent. Do not switch
        # PRTFM_DATA_DIR or replace an initialized store as a UI side effect.
        return restore_backup(Path(target), location.path, channel)
    owner = inspect_ownership(location.path)
    if owner.state != 'owned' or owner.channel != channel:
        return failure('storage_owner_mismatch')
    if action == 'plan':
        plan = plan_backup(location.path, Path(target))
        if plan['status'] != 'planned':
            return failure(plan['reasons'][0])
        # File lists may be large and contain private paths. Native stdout is 64KiB.
        return {key: plan[key] for key in ('status', 'backup_available', 'writer_state',
                'included_files', 'included_bytes', 'excluded_bytes', 'protected_files',
                'protected_bytes', 'required_free_bytes', 'free_bytes')}
    if action == 'create':
        return create_backup(location.path, Path(target))
    return failure('unknown_action')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['plan', 'create', 'inspect', 'restore'])
    parser.add_argument('--channel', choices=['preview', 'stable'], default='stable')
    parser.add_argument('--target', required=True)
    args = parser.parse_args(argv)
    try:
        report = execute(args.action, args.channel, args.target)
    except (OSError, RuntimeError, ValueError, RecursionError) as exc:
        report = failure(str(exc))
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report.get('status') in ('planned', 'created', 'verified', 'restored') else 2


if __name__ == '__main__':
    raise SystemExit(main())
