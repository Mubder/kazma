"""Standard-library child lifetime guard; safe to load in an isolated entry."""
import os
import sys


def arm_deadline(parent_pid, lifetime):
    if os.name != 'posix':
        return  # The child is already assigned to a kill-on-close Windows job.
    import signal

    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.setitimer(signal.ITIMER_REAL, lifetime)
    if sys.platform.startswith('linux'):
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        actual = ctypes.c_int()
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or libc.prctl(2, ctypes.byref(actual), 0, 0, 0) != 0:
            raise OSError('Parent-death protection unavailable')
        if actual.value != signal.SIGKILL:
            raise OSError('Parent-death protection did not hold')
        if os.getppid() != parent_pid:
            os.kill(os.getpid(), signal.SIGKILL)
