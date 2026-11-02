"""Windows Credential Manager adapter. No file or environment fallback."""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


class CredentialError(RuntimeError):
    pass


def target_name(channel: str) -> str:
    if channel not in {'preview', 'stable'}:
        raise ValueError('unsupported credential channel')
    return f'PleaseReadThisForMe/{channel}/openai'


def validate_key(value: str) -> bytes:
    # Do not enforce a provider prefix; formats can change. No network validation.
    if not value or value != value.strip() or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ValueError('invalid API key')
    encoded = value.encode('utf-8')
    if len(encoded) > 2560:
        raise ValueError('API key too long')
    return encoded


class _Credential(ctypes.Structure):
    _fields_ = [('Flags', wintypes.DWORD), ('Type', wintypes.DWORD),
                ('TargetName', wintypes.LPWSTR), ('Comment', wintypes.LPWSTR),
                ('LastWritten', wintypes.FILETIME), ('CredentialBlobSize', wintypes.DWORD),
                ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)), ('Persist', wintypes.DWORD),
                ('AttributeCount', wintypes.DWORD), ('Attributes', ctypes.c_void_p),
                ('TargetAlias', wintypes.LPWSTR), ('UserName', wintypes.LPWSTR)]


class WindowsCredentials:
    """Current Windows user's generic credential; locally persisted, not roaming."""

    def __init__(self):
        if sys.platform != 'win32':
            raise CredentialError('Windows credential storage unavailable')
        dll = ctypes.WinDLL('advapi32', use_last_error=True)
        self._read = dll.CredReadW
        self._read.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               ctypes.POINTER(ctypes.POINTER(_Credential))]
        self._read.restype = wintypes.BOOL
        self._write = dll.CredWriteW
        self._write.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
        self._write.restype = wintypes.BOOL
        self._delete = dll.CredDeleteW
        self._delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._delete.restype = wintypes.BOOL
        self._free = dll.CredFree
        self._free.argtypes = [ctypes.c_void_p]
        self._free.restype = None

    def _load(self, channel: str, *, status_only: bool = False):
        pointer = ctypes.POINTER(_Credential)()
        if not self._read(target_name(channel), 1, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == 1168:  # ERROR_NOT_FOUND only
                return False if status_only else None
            raise CredentialError('Cannot read API key from Windows')
        try:
            if status_only:
                return True
            item = pointer.contents
            if not 0 < item.CredentialBlobSize <= 2560:
                raise CredentialError('Invalid stored API key')
            try:
                value = ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize).decode('utf-8')
                validate_key(value)
            except (UnicodeError, ValueError):
                raise CredentialError('Invalid stored API key') from None
            return value
        finally:
            if pointer.contents.CredentialBlob and pointer.contents.CredentialBlobSize:
                ctypes.memset(pointer.contents.CredentialBlob, 0, pointer.contents.CredentialBlobSize)
            self._free(pointer)

    def configured(self, channel: str) -> bool:
        return self._load(channel, status_only=True)

    def read_for_provider(self, channel: str) -> str | None:
        # Internal only. Never expose through UI/HTTP/stdout or exception text.
        return self._load(channel)

    def save(self, channel: str, value: str) -> None:
        target = target_name(channel)
        data = validate_key(value)
        blob = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        item = _Credential(Type=1, TargetName=target, CredentialBlobSize=len(data),
                           CredentialBlob=blob, Persist=2, UserName='OpenAI API key')
        try:
            if not self._write(ctypes.byref(item), 0):
                raise CredentialError('Cannot save API key to Windows')
        finally:
            ctypes.memset(blob, 0, len(data))

    def delete(self, channel: str) -> None:
        if not self._delete(target_name(channel), 1, 0) and ctypes.get_last_error() != 1168:
            raise CredentialError('Cannot delete API key from Windows')
