"""동일 폴더의 CLI와 서버 writer가 캐시를 동시에 정리하지 않게 한다."""
import os


class WriterBusy(RuntimeError):
    pass


def acquire(path):
    handle = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            if handle.seek(0, 2) == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError as exc:
        handle.close()
        raise WriterBusy("workspace writer already running") from exc
