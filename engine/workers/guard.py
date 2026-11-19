"""Parent-death guard for engine children (ffmpeg, face helper): runs the command and kills it if the engine dies.

Usage: guard.py <cmd> [args…]. The engine starts this in its own process group; when the engine disappears we are
re-parented (getppid changes) and the child is killed within ~0.5 s instead of rendering on as an orphan."""
import os
import signal
import subprocess
import sys


def main():
    ppid = os.getppid()
    p = subprocess.Popen(sys.argv[1:])

    def stop(signum, frame):
        try:
            p.kill()
        finally:
            os._exit(128 + signum)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while True:
        try:
            sys.exit(p.wait(timeout=0.5))
        except subprocess.TimeoutExpired:
            pass
        if os.getppid() != ppid:
            p.kill()
            os._exit(137)


if __name__ == "__main__":
    main()
