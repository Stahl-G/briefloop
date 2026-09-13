"""POSIX launcher for an owner-bound host; invoked as an isolated script.

The launcher execs the host, preserving Popen's PID and exit status. Its tiny
watcher stays in that same process group, keeping the group identity reserved
even if the host exits first. EOF means the Python owner closed or died. No
PID file, executable-name lookup, polling HTTP, or model calls are involved.
Windows uses the existing kill-on-close Job Object instead.
"""
import os
import select
import signal
import sys
import time


def watch(read_fd, leader):
    def stop(*_):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        os.killpg(leader, signal.SIGTERM)
        time.sleep(1)
        # Includes this watcher; no stale group lookup after it exits.
        os.killpg(leader, signal.SIGKILL)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # Do not keep a host's protocol pipes or log handles open.
    null = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(null, fd)
    if null > 2:
        os.close(null)
    while True:
        ready, _, _ = select.select([read_fd], [], [], 1)
        if (ready and not os.read(read_fd, 1)) or os.getppid() != leader:
            stop()


def main():
    read_fd = int(sys.argv[1])
    command = sys.argv[2:]
    leader = os.getpid()
    if os.getpgrp() != leader:
        raise RuntimeError('Owner watcher requires its own process group')
    if os.fork() == 0:
        try:
            watch(read_fd, leader)
        finally:
            os._exit(1)
    os.close(read_fd)
    os.execvpe(command[0], command, os.environ)


if __name__ == '__main__':
    main()
