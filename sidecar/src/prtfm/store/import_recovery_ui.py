"""Small pre-server recovery command; never initializes the app store."""
import argparse
import json
import os
from pathlib import Path

from prtfm.config import data_root
from prtfm.store.ownership import CHANNELS, IMPORT_PENDING, MARKER, _unsafe, inspect_ownership


def recovery_status(target):
    target = Path(target).absolute()
    report = {'status': 'not_pending', 'path': str(target), 'actions': [], 'read_only': True}
    if inspect_ownership(target).state != 'import_pending':
        return report
    report['status'] = 'needs_review'
    try:
        from prtfm.store.empty_target import journal_only_state, journal_path, read_record
        from prtfm.store.import_target import inspect_target
        from prtfm.store.import_workflow import _pending

        journal = journal_path(target)
        if journal.exists() and not os.path.lexists(target / IMPORT_PENDING):
            if _unsafe(journal.lstat()) or journal.stat().st_size > 4096:
                return report
            data = json.loads(journal.read_bytes())
            channel = data.get('channel') if isinstance(data, dict) else None
            if channel not in CHANNELS:
                return report
            backup = read_record(target, channel)
            if os.path.lexists(target):
                state = journal_only_state(target, channel)
                actions = {'original_in_place': ['rollback'], 'final_verification_required': ['resume']}
                if state not in actions:
                    return report
                report.update(status='pending', channel=channel, phase=state,
                              actions=actions[state] if os.name == 'nt' else [])
                return report
            if inspect_target(backup, channel)['state'] not in ('initialized_empty', 'empty_directory'):
                return report
            report.update(status='pending', channel=channel, phase='restore_initialization',
                          actions=['rollback'] if os.name == 'nt' else [])
            return report
        data, _ = _pending(target)
        marker = target / MARKER
        if _unsafe(marker.lstat()) or marker.stat().st_size > 4096:
            return report
        owner = json.loads(marker.read_bytes())
        if (not isinstance(owner, dict) or set(owner) != {'application', 'format_version', 'channel'}
                or owner['application'] != 'prtfm' or type(owner['format_version']) is not int
                or owner['format_version'] != 1 or owner['channel'] not in CHANNELS):
            return report
        report.update(status='pending', channel=owner['channel'], phase=data['phase'])
        if os.name == 'nt':
            if data['phase'] == 'source_verified':
                report['actions'].append('resume')
            if not os.path.lexists(data['stage']):
                report['actions'].append('rollback')
    except (OSError, ValueError, RuntimeError, TypeError, RecursionError):
        pass
    return report


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['status', 'resume', 'rollback'])
    parser.add_argument('--target', type=Path)
    parser.add_argument('--channel', choices=['preview'])
    args = parser.parse_args(argv)
    target = args.target if args.target is not None else data_root()
    if args.target is None and args.channel:
        from prtfm.config import resolve_storage_location
        target = resolve_storage_location(args.channel).path
    report = recovery_status(target)
    if args.action != 'status':
        if args.action not in report['actions']:
            report = {'status': 'blocked', 'reason': 'action_unavailable'}
        else:
            try:
                from prtfm.store.import_workflow import recover_import

                report = recover_import(target, report['channel'], action=args.action)
            except (OSError, ValueError, RuntimeError, TypeError, RecursionError):
                report = {'status': 'blocked', 'reason': 'recovery_failed_recheck_required'}
    print(json.dumps(report, ensure_ascii=False))
    return 2 if report['status'] == 'blocked' else 0


if __name__ == '__main__':
    raise SystemExit(main())
