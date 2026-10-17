from __future__ import annotations

import os
import signal
import subprocess

import psutil


class PosixGroup:
    def __init__(self, child: subprocess.Popen) -> None:
        self.child = child
        self.group = child.pid
        owner = psutil.Process(child.pid)
        self.created = owner.create_time()
        self.uid = owner.uids().real
        if self.uid != os.getuid():
            raise OSError("child user does not match guard")
        if os.getsid(child.pid) != self.group or os.getpgid(child.pid) != self.group:
            raise OSError('child does not own a dedicated session and group')
        self.gone = False
        self.verified = True

    def active_process_count(self) -> int:
        self.verified = False
        if self.gone:
            return 0
        self.child.poll()
        try:
            os.killpg(self.group, 0)
        except ProcessLookupError:
            self.gone = True
            return 0
        if self.child.returncode is None:
            try:
                leader = psutil.Process(self.group)
                if (leader.create_time() != self.created or leader.uids().real != self.uid
                        or os.getsid(self.group) != self.group
                        or os.getpgid(self.group) != self.group):
                    raise OSError('owned group identity changed')
            except (psutil.NoSuchProcess, ProcessLookupError):
                self.child.poll()
                if self.child.returncode is None:
                    return 1
                return self.active_process_count()
            self.verified = True
        return 1

    def terminate(self) -> bool:
        if not self.active_process_count():
            return False
        if self.child.returncode is not None:
            raise OSError('group leader ended; descendant ownership requires review')
        if not self.verified:
            raise OSError('owned leader is unverified')
        if os.getpgid(self.child.pid) != self.group:
            raise OSError('owned group identity changed')
        os.killpg(self.group, signal.SIGTERM)
        return True

    def close(self) -> None:
        pass
