"""Document content contract independent of evidence and command execution."""
import bisect
import hashlib
from collections import OrderedDict
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


def _stable_file_stats(before, opened, after, current, *, windows):
    def signature(st):
        return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns

    # Windows stat/fstat can report different ctime values for the same file.
    # Compare ctime within each API, never discard the before/after checks.
    cross_end = 4 if windows else 5
    return (signature(before) == signature(current)
            and signature(opened) == signature(after)
            and signature(after)[:cross_end] == signature(current)[:cross_end])


@dataclass
class Source:
    path: str
    doc_id: int
    revision: int
    content_hash: str
    text_hash: str
    text: str
    page_starts: tuple
    page_kind: str
    original_line_offset: int | None = None

    @property
    def lines(self):
        text = self.text
        starts, offset = [0], 0
        for line in text.split('\n')[:-1]:
            offset += len(line) + 1
            if offset < len(text):
                starts.append(offset)
        return tuple(starts)

    def location(self, start, end):
        last = max(start, end - 1)
        lines = self.lines
        result = {'start': start, 'end': end,
                'line_start': bisect.bisect_right(lines, start),
                'line_end': bisect.bisect_right(lines, last),
                'page_start': bisect.bisect_right(self.page_starts, start),
                'page_end': bisect.bisect_right(self.page_starts, last)}
        if self.original_line_offset is not None:
            result.update(original_line_start=result['line_start'] + self.original_line_offset,
                          original_line_end=result['line_end'] + self.original_line_offset)
        return result


class SourceBodies:
    """Question-local bounded RAM, never another persistent document copy."""

    def __init__(self, limit):
        self.limit = limit
        self.entries = OrderedDict()
        self.bytes = 0
        self.closed = False

    def remember(self, path, text):
        import sys
        size = sys.getsizeof(text)
        if path in self.entries:
            self.bytes -= self.entries.pop(path)[1]
        while self.entries and self.bytes + size > self.limit:
            self.bytes -= self.entries.popitem(last=False)[1][1]
        if size <= self.limit:
            self.entries[path] = (text, size)
            self.bytes += size

    def get(self, path, load):
        if self.closed:
            raise SourceUnavailable('source_context_closed')
        if path in self.entries:
            self.entries.move_to_end(path)
            return self.entries[path][0]
        text = load()
        self.remember(path, text)
        return text

    def close(self):
        self.entries.clear()
        self.bytes = 0
        self.closed = True


class DeferredSource(Source):
    def __init__(self, source, bodies, load, *, utf8_bytes=None):
        self.__dict__.update({key: value for key, value in source.__dict__.items() if key != 'text'})
        self.utf8_bytes = len(source.text.encode('utf-8')) if utf8_bytes is None else utf8_bytes
        self._bodies, self._load = bodies, load
        self._held_text = None

    @contextmanager
    def hold(self):
        """Reuse one validated body through command execution and authorization."""
        previous = self._held_text
        self._held_text = self.text
        try:
            yield
        finally:
            self._held_text = previous

    @property
    def text(self):
        if self._bodies.closed:
            raise SourceUnavailable('source_context_closed')
        if self._held_text is not None:
            return self._held_text
        return self._bodies.get(self.path, self._load)


@dataclass(frozen=True)
class SourceBatch:
    documents: tuple[Source, ...]
    errors: dict[str, str]


class SourceReader(Protocol):
    def open(self) -> AbstractContextManager[SourceBatch]:
        """Keep each returned document version stable until context exit."""
        ...


class StoredSourceReader:
    """Read active processed document generations under their existing leases."""

    def __init__(self, store, cache, *, resident_bytes=8 * 1024 * 1024):
        self.store, self.cache = store, cache
        self.resident_bytes = resident_bytes

    def read(self, path, doc, revision):
        result = self.cache.read_revision(doc.id, revision)
        if result is None:
            if self.cache.direct_revision(doc.id, revision):
                raise SourceUnavailable('source_scope_required')
            raise ValueError('snapshot generation missing')
        return result

    def original_line_offset(self, path):
        return None

    def metadata(self, path, doc):
        describe = getattr(self.cache, 'metadata_revision', None)
        return describe(doc.id, doc.revision) if describe else None

    @contextmanager
    def open(self):
        self.cache.bind(self.store)
        manifest = self.store.docs()
        pin_options = {'metadata_only': True} if hasattr(self.cache, 'metadata_revision') else {}
        with self.cache.pin(**pin_options) as refs:
            if refs != {d.id: d.revision for d in manifest.values() if d.status == 'ok'}:
                raise ValueError('manifest changed; retry query')
            sources = []
            errors = {p: d.status for p, d in manifest.items() if d.status != 'ok'}
            bodies = SourceBodies(self.resident_bytes)
            try:
                for path, doc in manifest.items():
                    if doc.id not in refs:
                        continue
                    if doc.error is not None:
                        errors[path] = 'processing_failed'
                        continue
                    try:
                        metadata = self.metadata(path, doc)
                        if metadata is None:
                            text, starts = self.read(path, doc, refs[doc.id])
                            digest = hashlib.sha256(text.encode()).hexdigest()
                        else:
                            text, starts, digest = '', metadata['page_starts'], metadata['sha256']
                    except SourceUnavailable as exc:
                        errors[path] = str(exc)
                        continue
                    suffix = Path(path).suffix.lower()
                    kind = 'physical' if suffix == '.pdf' else 'slide' if suffix == '.pptx' else 'logical'
                    source = Source(path, doc.id, doc.revision, doc.content_hash,
                                    digest, text, tuple(starts), kind, self.original_line_offset(path))

                    def reload(path=path, doc=doc, digest=digest, starts=tuple(starts)):
                        try:
                            body, pages = self.read(path, doc, doc.revision)
                            if hashlib.sha256(body.encode()).hexdigest() != digest or tuple(pages) != starts:
                                raise SourceUnavailable('source_changed')
                            return body
                        except SourceUnavailable as exc:
                            errors[path] = str(exc)
                            raise

                    if metadata is None:
                        bodies.remember(path, text)
                    sources.append(DeferredSource(source, bodies, reload,
                                                  utf8_bytes=metadata['utf8_bytes'] if metadata else None))
                    # Do not keep the last eager Source/body alive across yield.
                    del source, text
                yield SourceBatch(tuple(sources), errors)
            finally:
                bodies.close()



class SourceUnavailable(ValueError):
    """A source cannot safely be used as evidence for its registered version."""


class FolderSourceReader(StoredSourceReader):
    """Read text originals; keep transformed documents in the processed store."""

    def __init__(self, store, cache, scope, *, resident_bytes=8 * 1024 * 1024):
        super().__init__(store, cache, resident_bytes=resident_bytes)
        self.scope = scope
        self.original_offsets = {}

    def metadata(self, path, doc):
        from prtfm.indexing.extract import CODE_EXTS, DOC_EXTS
        direct = (DOC_EXTS | CODE_EXTS) - {'.pdf', '.docx', '.pptx'}
        # Original text still validates content at question start, including legacy copies.
        return None if Path(path).suffix.lower() in direct else super().metadata(path, doc)

    def original_line_offset(self, path):
        return self.original_offsets.get(path)

    def read(self, path, doc, revision):
        import os
        import stat

        from prtfm.config import Limits
        from prtfm.indexing.extract import CODE_EXTS, DOC_EXTS, ExtractError, _decode
        from prtfm.indexing.scope import ScopeError

        direct = (DOC_EXTS | CODE_EXTS) - {'.pdf', '.docx', '.pptx'}
        if Path(path).suffix.lower() not in direct:
            return super().read(path, doc, revision)
        try:
            source = self.scope.resolve(path)
            before = source.stat()
            if not stat.S_ISREG(before.st_mode) or before.st_size > Limits().skip_file_bytes:
                raise SourceUnavailable('source_unreadable')
            with source.open('rb') as handle:
                opened = os.fstat(handle.fileno())
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise SourceUnavailable('source_changed')
                raw = handle.read(min(opened.st_size, Limits().skip_file_bytes) + 1)
                after = os.fstat(handle.fileno())
            current = self.scope.resolve(path).stat()
            if (not _stable_file_stats(before, opened, after, current, windows=os.name == 'nt')
                    or len(raw) > Limits().skip_file_bytes
                    or hashlib.sha256(raw).hexdigest() != doc.content_hash):
                raise SourceUnavailable('source_changed')
            from prtfm.indexing.textnorm import normalize
            decoded = _decode(raw)
            normalized = normalize(decoded)
            # Normalization preserves physical line breaks; strip removes leading blank lines.
            physical = decoded.replace('\r\n', '\n').replace('\r', '\n')
            if normalized.count('\n') == physical.count('\n'):
                leading = normalized[:len(normalized) - len(normalized.lstrip())]
                self.original_offsets[path] = leading.count('\n')
            else:
                self.original_offsets.pop(path, None)
            return normalized.strip(), [0]
        except FileNotFoundError as exc:
            raise SourceUnavailable('source_missing') from exc
        except ScopeError as exc:
            raise SourceUnavailable('source_outside_scope') from exc
        except (OSError, ExtractError) as exc:
            raise SourceUnavailable('source_unreadable') from exc
