"""Explicit first-run actions for the Preview host; no legacy adoption."""
import argparse
import json

from prtfm.store import import_policy
from prtfm.store.first_run import first_run_plan


def execute(action, channel):
    plan = first_run_plan(channel)  # Recheck immediately before each action.
    if 'review_import' in plan['choices'] and action in ('plan', 'import'):
        try:
            assessment = import_policy.assess(plan['source'], plan['target'])
        except (OSError, RuntimeError, ValueError) as exc:
            assessment = import_policy.failure(str(exc))
        plan['import_assessment'] = assessment
        if assessment['status'] != 'ready':
            if action == 'import':
                return assessment
            plan['choices'].remove('review_import')
    if action == 'review-legacy' and plan.get('source_reason') == 'legacy' and 'start_new' in plan['choices']:
        from prtfm.store.legacy_review import review_legacy_source
        review = review_legacy_source(plan['source'])
        # Native host needs a bounded summary, not per-document diagnostics.
        plan['legacy_review'] = {key: review[key] for key in ('status', 'reason')}
        if review['status'] == 'recognized_candidate':
            assessment = import_policy.assess(plan['source'], plan['target'])
            plan['import_assessment'] = assessment
            if assessment['status'] == 'ready':
                plan['choices'].append('review_import')
        else:
            plan['import_assessment'] = import_policy.failure('legacy_review_required')
        return plan
    if action == 'import-legacy' and plan.get('source_reason') == 'legacy' and 'start_new' in plan['choices']:
        assessment = import_policy.assess(plan['source'], plan['target'])
        if assessment['status'] != 'ready':
            return assessment
        from prtfm.store.import_workflow import import_legacy_store
        return import_legacy_store(plan['source'], plan['target'], channel,
                                   max_bytes=import_policy.MAX_BYTES, max_seconds=import_policy.MAX_SECONDS,
                                   max_files=import_policy.MAX_FILES, allow_empty_target=True)
    if action == 'plan':
        return plan
    if action == 'start-new' and 'start_new' in plan['choices']:
        from prtfm.store.lifecycle import storage_session
        with storage_session(channel):
            pass
        return {'status': 'ready'}
    if action == 'import' and 'review_import' in plan['choices']:
        from prtfm.store.import_workflow import import_store
        return import_store(plan['source'], plan['target'], channel,
                            max_bytes=import_policy.MAX_BYTES, max_seconds=import_policy.MAX_SECONDS,
                            max_files=import_policy.MAX_FILES, allow_empty_target=True)
    return {'status': 'blocked', 'reason': 'first_run_action_unavailable'}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['plan', 'start-new', 'import', 'import-legacy', 'review-legacy'])
    parser.add_argument('--channel', required=True, choices=['preview'])
    args = parser.parse_args(argv)
    try:
        report = execute(args.action, args.channel)
    except (OSError, RuntimeError, ValueError) as exc:
        report = import_policy.failure(str(exc))
    print(json.dumps(report, ensure_ascii=False))
    return 2 if report.get('status') == 'blocked' else 0


if __name__ == '__main__':
    raise SystemExit(main())
