"""Read persisted document metadata before scheduling automatic preparation."""
from prtfm.config import workspace_dir
from prtfm.indexing.extract import SUPPORTED, VLM_EXTS
from prtfm.indexing.manifest import diff
from prtfm.indexing.scope import Scope
from prtfm.store.workspace import open_workspace


def preparation_state(folder):
    scope = Scope(folder)
    excluded = [entry.rel for entry in scope.walk(VLM_EXTS)]
    first_visit = not (workspace_dir(scope.root) / 'index.db').exists()
    result = {'needed': True, 'first_visit': first_visit, 'excluded_files': excluded}
    if first_visit:
        return result
    ws = open_workspace(scope.root, readonly=True)
    try:
        from prtfm.indexing.vision import has_reusable, pending
        result['excluded_files'] = pending(scope, ws)
        docs = {path: doc for path, doc in ws.store.docs().items()
                if not any(path.lower().endswith(ext) for ext in VLM_EXTS)}
        changes = diff(list(scope.walk(SUPPORTED - VLM_EXTS)), docs)
        candidates = set(result['excluded_files'])
        result['needed'] = bool(changes.todo or changes.deleted) or any(
            has_reusable(scope, relative, known_paths=candidates) for relative in candidates)
        return result
    finally:
        ws.close()
