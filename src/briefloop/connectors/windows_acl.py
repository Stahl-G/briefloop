"""Private connector files on Windows; chmod does not set a Windows DACL."""
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path


_kernel = c.WinDLL('kernel32', use_last_error=True)
_security = c.WinDLL('advapi32', use_last_error=True)


def _api(library, name, args, result=w.BOOL):
    function = getattr(library, name)
    function.argtypes = args
    function.restype = result
    return function


_close = _api(_kernel, 'CloseHandle', [w.HANDLE])
_free = _api(_kernel, 'LocalFree', [c.c_void_p], c.c_void_p)
_dacl_info = 4
_protected_dacl = 0x80000000


def _check(ok):
    if not ok:
        raise c.WinError(c.get_last_error())


def _sid_text(sid):
    text = w.LPWSTR()
    _check(_api(_security, 'ConvertSidToStringSidW', [c.c_void_p, c.POINTER(w.LPWSTR)])(sid, c.byref(text)))
    try:
        return text.value
    finally:
        _free(text)


def current_user_sid():
    token = w.HANDLE()
    process = _api(_kernel, 'GetCurrentProcess', [], w.HANDLE)()
    _check(_api(_security, 'OpenProcessToken', [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)])(process, 8, c.byref(token)))
    try:
        size = w.DWORD()
        get = _api(_security, 'GetTokenInformation', [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)])
        get(token, 1, None, 0, c.byref(size))  # TokenUser
        if not size.value:
            raise c.WinError(c.get_last_error())
        value = c.create_string_buffer(size.value)
        _check(get(token, 1, value, size, c.byref(size)))
        return _sid_text(c.cast(value, c.POINTER(c.c_void_p))[0])
    finally:
        _close(token)


def _open(path, write=False):
    # Do not follow reparse points, and prevent replacement while verifying/updating.
    handle = _api(_kernel, 'CreateFileW', [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE], w.HANDLE)(
        str(Path(path).absolute()), 0x20000 | (0x40000 if write else 0), 3, None, 3, 0x02200000, None)
    if handle == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())
    class AttributeTag(c.Structure):
        _fields_ = [('attributes', w.DWORD), ('tag', w.DWORD)]
    attributes = AttributeTag()
    try:
        _check(_api(_kernel, 'GetFileInformationByHandleEx', [w.HANDLE, c.c_int, c.c_void_p, w.DWORD])(
            handle, 9, c.byref(attributes), c.sizeof(attributes)))
        if attributes.attributes & 0x400:
            raise OSError('Connector paths cannot be Windows reparse points')
        return handle, bool(attributes.attributes & 0x10)
    except BaseException:
        _close(handle)
        raise


def _verify(handle, directory, sid):
    descriptor = c.c_void_p()
    dacl = c.c_void_p()
    error = _api(_security, 'GetSecurityInfo', [w.HANDLE, c.c_int, w.DWORD, c.c_void_p, c.c_void_p,
        c.POINTER(c.c_void_p), c.c_void_p, c.POINTER(c.c_void_p)], w.DWORD)(
            handle, 1, _dacl_info, None, None, c.byref(dacl), None, c.byref(descriptor))
    if error:
        raise c.WinError(error)
    try:
        control, revision = w.WORD(), w.DWORD()
        _check(_api(_security, 'GetSecurityDescriptorControl', [c.c_void_p, c.POINTER(w.WORD), c.POINTER(w.DWORD)])(
            descriptor, c.byref(control), c.byref(revision)))
        class AclSize(c.Structure):
            _fields_ = [('count', w.DWORD), ('used', w.DWORD), ('free', w.DWORD)]
        size = AclSize()
        if not dacl or not control.value & 0x1000:
            raise OSError('Connector DACL is absent or inherits permissions')
        _check(_api(_security, 'GetAclInformation', [c.c_void_p, c.c_void_p, w.DWORD, c.c_int])(
            dacl, c.byref(size), c.sizeof(size), 2))
        if size.count != 1:
            raise OSError('Connector DACL must grant access only to its user')
        ace = c.c_void_p()
        _check(_api(_security, 'GetAce', [c.c_void_p, w.DWORD, c.POINTER(c.c_void_p)])(dacl, 0, c.byref(ace)))
        class AllowAce(c.Structure):
            _fields_ = [('kind', w.BYTE), ('flags', w.BYTE), ('size', w.WORD), ('mask', w.DWORD)]
        header = c.cast(ace, c.POINTER(AllowAce)).contents
        if (header.kind != 0 or header.flags != (3 if directory else 0) or header.mask != 0x1f01ff
                or _sid_text(ace.value + 8) != sid):
            raise OSError('Connector DACL does not provide private user access')
    finally:
        _free(descriptor)


def verify_private(path):
    """Verify the real ACL, including inherited grants; never rely on st_mode."""
    handle, directory = _open(path)
    try:
        _verify(handle, directory, current_user_sid())
    finally:
        _close(handle)


def protect_private(path):
    handle, directory = _open(path, write=True)
    descriptor = c.c_void_p()
    try:
        sid = current_user_sid()
        sddl = 'D:P(A;' + ('OICI' if directory else '') + ';FA;;;' + sid + ')'
        _check(_api(_security, 'ConvertStringSecurityDescriptorToSecurityDescriptorW',
            [w.LPCWSTR, w.DWORD, c.POINTER(c.c_void_p), c.c_void_p])(sddl, 1, c.byref(descriptor), None))
        present, defaulted = w.BOOL(), w.BOOL()
        dacl = c.c_void_p()
        _check(_api(_security, 'GetSecurityDescriptorDacl',
            [c.c_void_p, c.POINTER(w.BOOL), c.POINTER(c.c_void_p), c.POINTER(w.BOOL)])(
                descriptor, c.byref(present), c.byref(dacl), c.byref(defaulted)))
        error = _api(_security, 'SetSecurityInfo',
            [w.HANDLE, c.c_int, w.DWORD, c.c_void_p, c.c_void_p, c.c_void_p, c.c_void_p], w.DWORD)(
                handle, 1, _dacl_info | _protected_dacl, None, None, dacl, None)
        if error:
            raise c.WinError(error)
        _verify(handle, directory, sid)
    finally:
        if descriptor:
            _free(descriptor)
        _close(handle)
