"""Explain observations without turning an audit into deletion authorization."""
from collections import Counter
from copy import deepcopy

_RESTORE = frozenset({'ready_payload_missing', 'generation_body_missing', 'generation_metadata_missing',
                      'workspace_vector_reference_missing'})
_INSPECT = frozenset({'local_vector_generation_unverified', 'legacy_generation_unverified'})
_REVIEW = frozenset({'reference_not_in_workspace', 'artifact_without_reference'})


def preview(report):
    """Pure report conversion. No locks, storage mutations or provider calls.

    This plan is deliberately not executable: cross-store observations can race
    with generation activation, promotion and lease acquisition.
    """
    items = []
    for finding in report['findings']:
        kind = finding['kind']
        if kind in _RESTORE:
            action = 'investigate_recovery'
            reason = '필요한 저장 항목이 보이지 않습니다. 같은 버전의 기존 결과로 복구 가능한지 확인하세요.'
            checks = ['소유 DB와 원본 버전 재확인', '재사용 결과의 checksum·세대 확인',
                      '유료 재처리는 별도 승인 필요']
        elif kind in _INSPECT:
            action = 'verify_legacy'
            reason = '기존 저장 형식일 수 있습니다. 존재 여부만으로 정상 또는 유실로 판단할 수 없습니다.'
            checks = ['실제 세대·청크·모델·hash 정밀 확인', '검증된 기존 이행 경로 적용 여부 확인']
        elif kind in _REVIEW:
            action = 'review_reference'
            reason = '참조 정리 검토 대상입니다. 진행 중 활성화·이행으로 생긴 일시 상태일 수 있습니다.'
            checks = ['소유 workspace와 active/staging 재확인', 'writer·공유 키·catalogue 잠금 순서 확정',
                      '잠금 아래 최신 참조 재조회', '다른 폴더·진행 중 작업·중단 복구 보호']
        else:
            action = 'preserve_and_investigate'
            reason = '소유권이나 상태를 확정할 수 없습니다. 데이터를 보존하고 원인을 확인하세요.'
            checks = ['DB·catalogue 복원 가능성 확인', '연결된 모든 범위와 처리 작업 확인']
        items.append({'observation': deepcopy(finding), 'action': action, 'reason': reason,
                      'required_checks': checks, 'deletion_allowed': False})
    for generation in report.get('generations', []):
        if generation['kind'] != 'nonactive_or_unknown':
            continue
        items.append({'observation': deepcopy(generation), 'action': 'preserve_generation',
                      'reason': '과거 세대 또는 소유 상태 미확인 세대입니다. lease 기록 유무만으로 미사용을 확정할 수 없습니다.',
                      'required_checks': ['workspace DB와 활성 revision 확인', 'generation guard 아래 OS lease 상태 확인',
                                          '손상된 lease와 진행 중 세대 교체가 있으면 정리 보류'],
                      'deletion_allowed': False})
    return {'schema_version': 1, 'read_only': True, 'executable': False,
            'consistency': report['consistency'], 'items': items,
            'summary': dict(sorted(Counter(item['action'] for item in items).items())),
            'protected_data': ['원본', '유료 처리 결과·벡터', '진행 중·사용 중 세대', '과거 인용·비용 기록'],
            'notice': '진단 시점의 제안이며 실행 승인이 아닙니다. 적용 전 최신 상태와 잠금을 다시 확인해야 합니다.'}
