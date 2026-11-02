"""Read-only location/ownership preflight, not schema or import approval."""
from prtfm.localization import text as _ui_text

from dataclasses import asdict

from prtfm.config import resolve_storage_location, storage_paths_overlap
from prtfm.store.ownership import inspect_ownership, write_precondition

MESSAGES = {
    'ready': _ui_text('이 채널의 저장소입니다. 실행 전에 사용 중인지 다시 확인합니다.'),
    'initialization_required': _ui_text('새 저장소를 초기화해야 합니다. 기존 데이터는 이동하거나 삭제하지 않습니다.'),
    'import_required': _ui_text('기존 앱 데이터가 있는 위치입니다. Preview에서 바로 사용하지 않습니다. 기존 앱으로 열어 주세요. 가져오기 기능은 아직 준비 중입니다.'),
    'adoption_required': _ui_text('기존 저장소의 호환성 확인이 필요합니다. 자동 변경하지 않았습니다. 현재 데이터를 사용하던 앱으로 열어 주세요.'),
    'channel_mismatch': _ui_text('다른 채널의 저장소입니다. 해당 데이터를 만든 앱으로 열거나 별도 저장 위치를 사용해 주세요.'),
    'storage_busy': _ui_text('다른 앱이나 명령이 이 저장소를 사용 중입니다. 해당 작업을 종료한 뒤 다시 실행해 주세요.'),
    'blocked': _ui_text('저장소 정보를 안전하게 확인하지 못했습니다. 경로와 접근 권한을 확인해 주세요. 소유 표식을 삭제하거나 수정하지 마세요.'),
}


def blocked_notice(reason):
    # Do not expose exception details, credentials, or arbitrary paths.
    return MESSAGES.get(reason, MESSAGES['blocked'])


def storage_preflight(channel):
    location = resolve_storage_location(channel)
    owner = inspect_ownership(location.path)
    action = write_precondition(owner, channel)
    overlap = storage_paths_overlap(location.path, location.legacy_path)
    legacy = inspect_ownership(location.legacy_path)
    discovered = not overlap and legacy.state not in ('missing', 'empty')
    return {
        'schema_version': 1, 'read_only': True, 'channel': channel,
        'path': str(location.path), 'custom_location': location.custom,
        'ownership': asdict(owner), 'required_action': action,
        'message': blocked_notice(action),
        'previous_location': {'path': str(location.legacy_path), 'ownership': asdict(legacy),
                              'overlaps_target': overlap, 'needs_review': discovered},
        'notice': (_ui_text('기존 앱 데이터 위치가 발견되었습니다. 아직 가져오지 않았습니다. 기존 앱에서 계속 사용할 수 있습니다.')) if discovered else '',
        'import_available': False, 'schema_checked': False, 'references_checked': False,
        'consistency': 'observational_not_atomic',
    }
