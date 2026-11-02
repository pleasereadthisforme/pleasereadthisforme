"""Process-local provider credential. Initialize before starting any workers."""
import os
import threading

_key = None
_initialized = False
_lock = threading.RLock()


def configure(key=None):
    global _key, _initialized
    with _lock:
        # Startup-only, permanent removal; never restore around concurrent calls.
        os.environ.pop('OPENAI_API_KEY', None)
        os.environ.pop('OPENAI_CUSTOM_HEADERS', None)
        _key, _initialized = key, True


def configure_development():
    with _lock:
        if not _initialized:
            configure(os.environ.get('OPENAI_API_KEY'))


def get_key():
    # Library callers also initialize before they start model or parser work.
    with _lock:
        configure_development()
        return _key
