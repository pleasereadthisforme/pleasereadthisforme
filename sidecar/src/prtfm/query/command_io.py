"""Bounded subprocess pipe transport, including Windows anonymous pipes.

Only real command binaries (no shell or descendant processes) are supported.
The caller owns Popen and keeps the input source pinned until this returns.
"""
from __future__ import annotations

import os
import queue
import threading
import time

BLOCK_BYTES = 8192
QUEUE_BLOCKS = 8


def read_pipe(pipe):
    return os.read(pipe.fileno(), BLOCK_BYTES)


def collect_output(proc, chunks, *, deadline, output_limit, cancel=None):
    """Drain both pipes while feeding bounded chunks; always reap/join on exit."""
    events = queue.Queue(maxsize=QUEUE_BLOCKS)
    stop = threading.Event()
    workers = []
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    used, status = 0, 'complete'

    def publish(channel, data):
        while not stop.is_set():
            try:
                events.put((channel, data), timeout=0.05)
                return
            except queue.Full:
                pass

    def read(channel, pipe):
        try:
            while not stop.is_set():
                data = read_pipe(pipe)
                publish(channel, data)
                if not data:
                    break
        except BaseException as exc:
            publish('error', exc)

    def write():
        try:
            for chunk in chunks:
                pending = memoryview(chunk)
                while pending and not stop.is_set():
                    written = os.write(proc.stdin.fileno(), pending)
                    if written == 0:
                        raise OSError('zero-byte command stdin write')
                    pending = pending[written:]
                if stop.is_set():
                    break
        except BrokenPipeError:
            pass  # A command may finish before consuming its entire input.
        except BaseException as exc:
            publish('error', exc)
        finally:
            try:
                proc.stdin.close()
            except BrokenPipeError:
                pass
            except BaseException as exc:
                publish('error', exc)
            publish('stdin', b'')

    active = {'stdout', 'stderr'}
    try:
        for channel in active:
            worker = threading.Thread(target=read, args=(channel, getattr(proc, channel)),
                                      name=f'prtfm-command-{channel}')
            worker.start()
            workers.append(worker)
        if proc.stdin is not None:
            active.add('stdin')
            worker = threading.Thread(target=write, name='prtfm-command-stdin')
            worker.start()
            workers.append(worker)
        # Closed pipes alone do not mean the process exited. Keep enforcing the
        # deadline/cancellation even when a command closes output then stalls.
        while active or proc.poll() is None:
            if cancel is not None and cancel.is_set():
                status = 'cancelled'
                break
            if time.monotonic() >= deadline:
                status = 'timeout'
                break
            try:
                channel, data = events.get(timeout=0.05)
            except queue.Empty:
                continue
            if channel == 'error':
                raise data
            if not data:
                active.discard(channel)
                continue
            remaining = max(0, output_limit - used)
            buffers[channel].extend(data[:remaining])
            used += min(len(data), remaining)
            if len(data) > remaining:
                status = 'truncated'
                break
    finally:
        stop.set()
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        # Killing/reaping the direct child releases blocking pipe I/O. Join
        # before Popen closes handles or the caller releases the source hold.
        for worker in workers:
            worker.join()
    return status, proc.returncode, buffers, used
