"""Persist public execution facts, omitting opaque reasoning and credentials."""


def public(value):
    if isinstance(value, list):
        return [public(item) for item in value]
    if isinstance(value, dict):
        if value.get('type') == 'reasoning':
            return {'type': 'reasoning', 'omitted': True}
        return {key: ('[omitted]' if key.lower() in
                      {'authorization', 'api_key', 'encrypted_content', 'headers'} else public(item))
                for key, item in value.items()}
    return value


class Activity:
    def __init__(self, sessions, session_id):
        self.sessions = sessions
        self.run_id = sessions.new_run(session_id)
        self._request_snapshot = None

    def emit(self, kind, snapshot=None, **fields):
        request_id = fields.get('request_id')
        reuse = None
        if (kind in ('token_count_sent', 'model_request_sent') and self._request_snapshot
                and self._request_snapshot[0] == request_id):
            reuse = self._request_snapshot[1]
        event = self.sessions.record_event(self.run_id, kind, public(fields), public(snapshot),
                                          reuse_snapshot=reuse)
        if kind == 'model_request_prepared':
            self._request_snapshot = ((request_id, event['snapshot_ref'])
                                      if request_id is not None and event and 'snapshot_ref' in event else None)
        return event
