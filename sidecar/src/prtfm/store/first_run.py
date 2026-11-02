"""Read-only first-run decisions, distinct from authorization to execute import."""
from prtfm.config import resolve_storage_location, storage_paths_overlap
from prtfm.store.import_target import inspect_target
from prtfm.store.ownership import inspect_ownership


def first_run_plan(channel):
    location = resolve_storage_location(channel)
    target = inspect_target(location.path, channel)
    source = location.legacy_path
    separate = not storage_paths_overlap(location.path, source)
    owner = inspect_ownership(source) if separate else None
    result = {'read_only': True, 'target': str(location.path), 'target_state': target,
              'source': str(source) if separate else None, 'choices': [],
              'import_available': False, 'source_review_required': False}
    if target['state'] in ('missing', 'empty_directory', 'initialized_empty'):
        result['choices'].append('start_new')
        if owner and owner.state == 'owned':
            result['choices'].append('review_import')
            result['source_review_required'] = True
        elif owner and owner.state not in ('missing', 'empty'):
            result['source_review_required'] = True
            result['source_reason'] = owner.state
    elif target['state'] == 'existing_data':
        result['choices'].append('continue_existing')
    # No choice implies review/block; never reinterpret old data as a new store.
    return result
