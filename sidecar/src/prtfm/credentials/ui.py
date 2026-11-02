"""Native masked credential prompt. Output contains status only."""
from __future__ import annotations
from prtfm.localization import text as _ui_text

import argparse
import ctypes
import json
import struct
from ctypes import wintypes

from prtfm.credentials.windows import CredentialError, WindowsCredentials


def _dialog_template() -> bytes:
    """Small native dialog: a masked key field, Save and Cancel; no web transport."""
    def text(value):
        return value.encode('utf-16-le') + b'\0\0'

    # DS_SETFONT | DS_CENTER | WS_CAPTION | WS_SYSMENU | WS_POPUP
    data = bytearray(struct.pack('<LLHhhhh', 0x80C00840, 0, 5, 0, 0, 300, 122))
    data += b'\0\0\0\0' + text(_ui_text('OpenAI API 키')) + struct.pack('<H', 10) + text('Segoe UI')

    def control(style, x, y, width, height, ident, kind, label):
        data.extend(b'\0' * (-len(data) % 4))
        data.extend(struct.pack('<LLhhhhH', style, 0, x, y, width, height, ident))
        data.extend(struct.pack('<HH', 0xFFFF, kind) + text(label) + b'\0\0')

    control(0x50000000, 14, 12, 272, 20, 101, 0x82, _ui_text('API 키를 입력하세요.'))
    # WS_TABSTOP | WS_BORDER | ES_PASSWORD | ES_AUTOHSCROLL
    control(0x508100A0, 14, 36, 272, 18, 100, 0x81, '')
    control(0x50000000, 14, 60, 272, 22, 102, 0x82,
            _ui_text('Windows 보안 저장소에 저장합니다. 등록 시 API 요청은 보내지 않습니다.'))
    control(0x50010001, 174, 94, 54, 18, 1, 0x80, _ui_text('저장'))
    control(0x50010000, 234, 94, 52, 18, 2, 0x80, _ui_text('취소'))
    return bytes(data)


def prompt_key(channel: str, parent: int = 0) -> str | None:
    del channel  # Storage target is selected only by WindowsCredentials.save.
    user = ctypes.WinDLL('user32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                                       wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
    user.GetDlgItem.restype = wintypes.HWND
    user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.SendMessageW.restype = ctypes.c_ssize_t
    user.GetDlgItemTextW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LPWSTR, ctypes.c_int]
    user.SetDlgItemTextW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LPCWSTR]
    user.EndDialog.argtypes = [wintypes.HWND, ctypes.c_ssize_t]
    user.SetFocus.argtypes = [wintypes.HWND]
    user.DialogBoxIndirectParamW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p,
                                           wintypes.HWND, callback_type, wintypes.LPARAM]
    user.DialogBoxIndirectParamW.restype = ctypes.c_ssize_t
    password = ctypes.create_unicode_buffer(2561)

    @callback_type
    def procedure(window, message, wparam, lparam):
        if message == 0x110:  # WM_INITDIALOG
            field = user.GetDlgItem(window, 100)
            user.SendMessageW(field, 0xC5, 2560, 0)  # EM_SETLIMITTEXT
            user.SetFocus(field)
            return 0
        if message == 0x111 and (wparam & 0xFFFF) in (1, 2):
            result = wparam & 0xFFFF
            if result == 1:
                user.GetDlgItemTextW(window, 100, password, len(password))
                if not password.value.strip():
                    user.SetFocus(user.GetDlgItem(window, 100))
                    return 1
            user.SetDlgItemTextW(window, 100, '')
            user.EndDialog(window, result)
            return 1
        if message == 0x10:  # WM_CLOSE
            user.SetDlgItemTextW(window, 100, '')
            user.EndDialog(window, 2)
            return 1
        return 0

    template = ctypes.create_string_buffer(_dialog_template())
    try:
        result = user.DialogBoxIndirectParamW(None, template, parent or None, procedure, 0)
        if result == -1:
            raise CredentialError('Cannot open API key dialog')
        return password.value if result == 1 else None
    finally:
        ctypes.memset(password, 0, ctypes.sizeof(password))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['save', 'delete', 'status'])
    parser.add_argument('--channel', choices=['preview', 'stable'], default='stable')
    parser.add_argument('--target', type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        store = WindowsCredentials()
        if args.action == 'status':
            report = {'status': 'ready', 'configured': store.configured(args.channel)}
        elif args.action == 'delete':
            store.delete(args.channel)
            report = {'status': 'deleted'}
        else:
            key = prompt_key(args.channel, args.target)
            if key is None:
                report = {'status': 'cancelled'}
            else:
                store.save(args.channel, key)
                report = {'status': 'saved'}
    except (CredentialError, ValueError, OSError):
        report = {'status': 'error', 'message': _ui_text('키 설정을 완료하지 못했습니다. Windows 보안 저장소와 입력을 확인하세요.')}
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
