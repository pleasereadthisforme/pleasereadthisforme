"""의미 후보 검색은 별도 프로세스에서 한 번 실행하고 종료한다."""
from __future__ import annotations

import json
import math
import subprocess
import sys
import threading
import time
from pathlib import Path

from prtfm.credentials.process_env import child_environment
from prtfm.store.vectors import DIMENSIONS, MODEL, Vectors, validate

SLOT = threading.BoundedSemaphore(1)
TIMEOUT = 30.0
EXIT_TIMEOUT = 5.0
FIELDS = ('generation', 'doc_id', 'revision', 'idx', 'text_hash', 'model', 'dimensions', '_distance')


class SearchWorkerError(Exception):
    pass


def validate_result(value):
    if not isinstance(value, list) or len(value) > 12:
        raise SearchWorkerError('invalid candidate list')
    for row in value:
        if not isinstance(row, dict) or set(row) != set(FIELDS):
            raise SearchWorkerError('invalid candidate fields')
        if any(type(row[k]) is not int or row[k] < 0 for k in ('doc_id', 'revision', 'idx', 'dimensions')):
            raise SearchWorkerError('invalid candidate numbers')
        if any(not isinstance(row[k], str) or len(row[k]) > 128 for k in ('generation', 'text_hash', 'model')):
            raise SearchWorkerError('invalid candidate strings')
        if row['model'] != MODEL or row['dimensions'] != DIMENSIONS:
            raise SearchWorkerError('invalid model')
        if not isinstance(row['_distance'], (float, int)) or not math.isfinite(row['_distance']):
            raise SearchWorkerError('invalid distance')
    return value


class SearchWorker:
    def __init__(self, path: Path, cancel=None):
        self.path = path.resolve()
        self.cancel = cancel or threading.Event()

    def search(self, vector, generations):
        validate(vector)
        if not generations:
            return []
        deadline = time.monotonic() + TIMEOUT

        def check():
            if self.cancel.is_set():
                raise SearchWorkerError('cancelled')
            if time.monotonic() >= deadline:
                raise SearchWorkerError('timeout')

        while True:
            check()
            if SLOT.acquire(timeout=min(.1, max(0, deadline - time.monotonic()))):
                break
        process = None
        try:
            check()
            # 환경의 API 키는 검색 작업자에 필요하지 않다.
            env = child_environment()
            process = subprocess.Popen([sys.executable, '-B', '-m', 'prtfm.retrieval.worker'],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       env=env)
            payload = json.dumps({'path': str(self.path), 'vector': vector, 'generations': generations}).encode()
            while True:
                check()
                try:
                    output, _ = process.communicate(input=payload, timeout=min(.1, deadline - time.monotonic()))
                    break
                except subprocess.TimeoutExpired:
                    payload = None
            check()
            if process.returncode or len(output) > 65536:
                raise SearchWorkerError('worker failed')
            try:
                return validate_result(json.loads(output))
            except (ValueError, TypeError) as exc:
                raise SearchWorkerError('invalid response') from exc
        finally:
            try:
                if process is not None:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=EXIT_TIMEOUT)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                    for stream in (process.stdin, process.stdout):
                        if stream is not None:
                            stream.close()
            finally:
                SLOT.release()


def main():
    try:
        request = json.loads(sys.stdin.buffer.read())
        hits = Vectors(Path(request['path'])).search(request['vector'], request['generations'])
        result = [{k: row[k] for k in FIELDS} for row in hits]
        validate_result(result)
        sys.stdout.write(json.dumps(result, allow_nan=False))
        return 0
    except Exception:
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
