"""Bounded native secret store; no file, argv, or environment fallback.

The macOS implementation calls the SDK's SecItem APIs directly. Other systems
remain unavailable until their native credential path has its own evidence.
Only the service's explicitly selected Worker account may use this module.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes as C
import hashlib
import sys

from .errors import BoardError


def unavailable() -> BoardError:
    return BoardError('ACCOUNT_SECRET_STORE_UNAVAILABLE', 'The verified system credential store is unavailable')


def identity(state, adapter: str) -> tuple[str, str]:
    if adapter != 'claude':
        raise BoardError('ACCOUNT_KEY_UNSUPPORTED', 'This harness does not use the system credential store')
    from .private_dirs import _absolute, linked_component
    path = _absolute(state)
    if linked_component(path) is not None:
        raise BoardError('PRIVATE_PATH_UNSAFE', 'The account state root contains a link')
    digest = hashlib.sha256(str(path).encode()).hexdigest()
    return 'hey-my-buddy.worker-account.claude', digest


class MacKeychain:
    """One exact generic-password service/account; never enumerate the keychain."""

    def __init__(self):
        if sys.platform != 'darwin':
            raise unavailable()
        try:
            self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
            self.security = C.CDLL('/System/Library/Frameworks/Security.framework/Security')
            self._configure()
        except Exception:
            raise unavailable() from None

    def _configure(self):
        signatures = {
            'CFStringCreateWithCString': ([C.c_void_p, C.c_char_p, C.c_uint32], C.c_void_p),
            'CFDataCreate': ([C.c_void_p, C.c_void_p, C.c_long], C.c_void_p),
            'CFDataGetLength': ([C.c_void_p], C.c_long),
            'CFDataGetBytePtr': ([C.c_void_p], C.c_void_p),
            'CFGetTypeID': ([C.c_void_p], C.c_ulong),
            'CFDataGetTypeID': ([], C.c_ulong),
            'CFDictionaryCreateMutable': ([C.c_void_p, C.c_long, C.c_void_p, C.c_void_p], C.c_void_p),
            'CFDictionarySetValue': ([C.c_void_p, C.c_void_p, C.c_void_p], None),
            'CFRelease': ([C.c_void_p], None),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.cf, name)
            function.argtypes, function.restype = arguments, result
        for name, arguments in {
            'SecItemAdd': [C.c_void_p, C.POINTER(C.c_void_p)],
            'SecItemCopyMatching': [C.c_void_p, C.POINTER(C.c_void_p)],
            'SecItemUpdate': [C.c_void_p, C.c_void_p],
            'SecItemDelete': [C.c_void_p],
        }.items():
            function = getattr(self.security, name)
            function.argtypes, function.restype = arguments, C.c_int32

    def _constant(self, name):
        library = self.cf if name.startswith('kCF') else self.security
        return C.c_void_p.in_dll(library, name).value

    @contextmanager
    def _dictionary(self, values):
        dictionary = self.cf.CFDictionaryCreateMutable(None, 0, None, None)
        owned = []
        try:
            if not dictionary:
                raise unavailable()
            for key, value in values.items():
                if isinstance(value, bytes):
                    buffer = C.create_string_buffer(value)
                    pointer = self.cf.CFDataCreate(None, buffer, len(value))
                    owned.append(pointer)
                elif isinstance(value, str) and not value.startswith(('kSec', 'kCF')):
                    pointer = self.cf.CFStringCreateWithCString(None, value.encode(), 0x08000100)
                    owned.append(pointer)
                else:
                    pointer = self._constant(value)
                if not pointer:
                    raise unavailable()
                self.cf.CFDictionarySetValue(dictionary, self._constant(key), pointer)
            yield dictionary
        except BoardError:
            raise
        except Exception:
            raise unavailable() from None
        finally:
            if dictionary:
                self.cf.CFRelease(dictionary)
            for pointer in owned:
                if pointer:
                    self.cf.CFRelease(pointer)

    @staticmethod
    def _query(service, account):
        if (not isinstance(service, str) or not service.startswith('hey-my-buddy.worker-account')
                or len(service) > 160 or not isinstance(account, str) or not 1 <= len(account) <= 128
                or '\0' in service or '\0' in account):
            raise BoardError('ACCOUNT_SECRET_SCOPE', 'System credentials require a dedicated Worker account identity')
        return {'kSecClass': 'kSecClassGenericPassword', 'kSecAttrService': service, 'kSecAttrAccount': account,
                'kSecUseAuthenticationUI': 'kSecUseAuthenticationUIFail'}

    def put(self, service: str, account: str, secret: str) -> None:
        if not isinstance(secret, str) or not secret or len(secret.encode()) > 16384 or '\0' in secret:
            raise BoardError('ACCOUNT_KEY_INVALID', 'The key must be a nonempty bounded string')
        query = self._query(service, account)
        with self._dictionary({**query, 'kSecValueData': secret.encode()}) as attributes:
            status = self.security.SecItemAdd(attributes, None)
        if status == -25299:  # errSecDuplicateItem
            with self._dictionary(query) as selected, self._dictionary({'kSecValueData': secret.encode()}) as updated:
                status = self.security.SecItemUpdate(selected, updated)
        if status != 0:
            raise unavailable()

    def get(self, service: str, account: str) -> str | None:
        query = {**self._query(service, account), 'kSecReturnData': 'kCFBooleanTrue', 'kSecMatchLimit': 'kSecMatchLimitOne'}
        data = C.c_void_p()
        with self._dictionary(query) as selected:
            status = self.security.SecItemCopyMatching(selected, C.byref(data))
        if status == -25300:  # errSecItemNotFound
            return None
        if status != 0:
            raise unavailable()
        try:
            if not data.value or self.cf.CFGetTypeID(data) != self.cf.CFDataGetTypeID():
                raise unavailable()
            length = self.cf.CFDataGetLength(data)
            if not 1 <= length <= 16384:
                raise unavailable()
            return C.string_at(self.cf.CFDataGetBytePtr(data), length).decode('utf-8')
        except BoardError:
            raise
        except Exception:
            raise unavailable() from None
        finally:
            if data.value:
                self.cf.CFRelease(data)

    def delete(self, service: str, account: str) -> None:
        with self._dictionary(self._query(service, account)) as selected:
            status = self.security.SecItemDelete(selected)
        if status not in (0, -25300):
            raise unavailable()


def open_store():
    if sys.platform != 'darwin':
        raise unavailable()
    return MacKeychain()
