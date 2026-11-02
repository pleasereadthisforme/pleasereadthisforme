"""Environment for non-model child processes, excluding ambient secrets."""
import os

_ALLOWED = frozenset({
    'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'TMPDIR',
    'HOME', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'LANG', 'LC_ALL', 'LC_CTYPE',
    'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'PRTFM_RUNTIME_DIR',
    'PRTFM_DATA_DIR', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME',
    'DISPLAY', 'WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS',
})


def child_environment():
    return {k: v for k, v in os.environ.items() if k.upper() in _ALLOWED}
