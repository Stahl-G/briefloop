"""Windows handles for owned child trees; no file/network isolation claim."""
import ctypes as c
from ctypes import wintypes as w

k = c.WinDLL('kernel32', use_last_error=True)


def api(name, args, result=w.BOOL):
    fn = getattr(k, name)
    fn.argtypes = args
    fn.restype = result
    return fn


close_handle = api('CloseHandle', [w.HANDLE])
open_process = api('OpenProcess', [w.DWORD, w.BOOL, w.DWORD], w.HANDLE)
wait_handle = api('WaitForSingleObject', [w.HANDLE, w.DWORD], w.DWORD)


def alive(pid):
    handle = open_process(0x00100000, False, pid)  # SYNCHRONIZE, never terminate
    if not handle:
        return c.get_last_error() == 5  # Access denied is not evidence of exit.
    try:
        return wait_handle(handle, 0) == 258
    finally:
        close_handle(handle)


class BasicLimit(c.Structure):
    _fields_ = [('PerProcessUserTimeLimit', c.c_int64), ('PerJobUserTimeLimit', c.c_int64),
                ('LimitFlags', w.DWORD), ('MinimumWorkingSetSize', c.c_size_t),
                ('MaximumWorkingSetSize', c.c_size_t), ('ActiveProcessLimit', w.DWORD),
                ('Affinity', c.c_size_t), ('PriorityClass', w.DWORD), ('SchedulingClass', w.DWORD)]


class IoCounters(c.Structure):
    _fields_ = [(name, c.c_uint64) for name in ('ReadOperationCount', 'WriteOperationCount',
                'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class ExtendedLimit(c.Structure):
    _fields_ = [('BasicLimitInformation', BasicLimit), ('IoInfo', IoCounters),
                ('ProcessMemoryLimit', c.c_size_t), ('JobMemoryLimit', c.c_size_t),
                ('PeakProcessMemoryUsed', c.c_size_t), ('PeakJobMemoryUsed', c.c_size_t)]


class ThreadEntry(c.Structure):
    _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD), ('th32ThreadID', w.DWORD),
                ('th32OwnerProcessID', w.DWORD), ('tpBasePri', w.LONG),
                ('tpDeltaPri', w.LONG), ('dwFlags', w.DWORD)]


class Job:
    def __init__(self):
        self.handle = api('CreateJobObjectW', [c.c_void_p, w.LPCWSTR], w.HANDLE)(None, None)
        if not self.handle:
            raise c.WinError(c.get_last_error())
        limits = ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not api('SetInformationJobObject', [w.HANDLE, c.c_int, c.c_void_p, w.DWORD])(
                self.handle, 9, c.byref(limits), c.sizeof(limits)):
            error = c.WinError(c.get_last_error())
            self.close()
            raise error

    def assign_and_resume(self, process):
        if not api('AssignProcessToJobObject', [w.HANDLE, w.HANDLE])(self.handle, int(process._handle)):
            raise c.WinError(c.get_last_error())
        # Popen closes the primary thread handle. Enumerate this still-suspended
        # process's initial thread; no child code runs before job assignment.
        snapshot = api('CreateToolhelp32Snapshot', [w.DWORD, w.DWORD], w.HANDLE)(4, 0)
        if snapshot == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        try:
            entry = ThreadEntry(dwSize=c.sizeof(ThreadEntry))
            first = api('Thread32First', [w.HANDLE, c.POINTER(ThreadEntry)])
            next_ = api('Thread32Next', [w.HANDLE, c.POINTER(ThreadEntry)])
            ok = first(snapshot, c.byref(entry))
            while ok:
                if entry.th32OwnerProcessID == process.pid:
                    thread = api('OpenThread', [w.DWORD, w.BOOL, w.DWORD], w.HANDLE)(2, False, entry.th32ThreadID)
                    if not thread:
                        raise c.WinError(c.get_last_error())
                    try:
                        if api('ResumeThread', [w.HANDLE], w.DWORD)(thread) == 0xffffffff:
                            raise c.WinError(c.get_last_error())
                    finally:
                        close_handle(thread)
                    return
                ok = next_(snapshot, c.byref(entry))
            raise RuntimeError('未找到本应用新建进程的初始线程')
        finally:
            close_handle(snapshot)

    def close(self):
        if self.handle:
            close_handle(self.handle)
            self.handle = None
