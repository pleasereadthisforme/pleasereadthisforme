"""Read-only file tools executing real binaries against a private revision snapshot."""
from __future__ import annotations

import os
import shlex
import subprocess
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path, PurePosixPath

from prtfm.query.command_io import collect_output
from prtfm.query.command_paths import CommandUnavailable as CommandUnavailable
from prtfm.query.command_paths import resolve_commands
from prtfm.query.search_temp import search_temp
from prtfm.query.source_snapshot import open_source_snapshot

BATCH_BYTES = 8 * 1024 * 1024
BATCH_FILES = 32


class FileCommands:
    def __init__(self, evidence, root, *, timeout=5, output_bytes=24000, lease_fds=()):
        self.evidence, self.root = evidence, Path(root)
        self.id = evidence.id
        self.timeout, self.output_bytes = timeout, output_bytes
        self.semantic = None
        self.lease_fds = lease_fds
        self.paths = {}
        self.aliases = {}
        directories = {str(parent).casefold() for path in evidence.docs
                       for parent in PurePosixPath(path).parents}
        occupied = set(directories)
        self.executables = resolve_commands()
        for path, doc in evidence.docs.items():
            relative = PurePosixPath(path)
            if relative.is_absolute() or '..' in relative.parts or '\\' in path:
                raise ValueError('invalid document path')
            alias = path + '.md'
            number = 0
            while alias.casefold() in occupied:
                number += 1
                alias = f'{path}.prtfm-{number}.md'
            occupied.add(alias.casefold())
            self.aliases[path] = alias
            target = self.root / alias
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
            target.chmod(0o400)
            self.paths[alias] = doc

    def _path(self, path, *, file=False):
        if not isinstance(path, str) or '\x00' in path or '\\' in path:
            raise ValueError('invalid path')
        rel = PurePosixPath(path)
        if rel.is_absolute() or '..' in rel.parts:
            raise ValueError('path outside selected documents')
        key = str(rel)
        target = self.root / key
        if not target.exists() or target.is_symlink() or not target.resolve().is_relative_to(self.root.resolve()):
            raise ValueError('unknown snapshot path')
        if file and key not in self.paths:
            raise ValueError('expected a document file')
        return './' + key

    def prepare(self, name, args):
        args = dict(args)
        if name == 'semantic_search':
            if self.semantic is None or set(args) != {'query'}:
                raise ValueError('semantic search unavailable or invalid arguments')
            self._pattern(args['query'])
            return []  # Provider operation, never presented as a shell command.
        if name == 'find':
            pattern, path = args.pop('pattern', '*'), args.pop('path', '.')
            argv = ['find', self._path(path), '-type', 'f', '-name', self._pattern(pattern), '-print']
        elif name == 'glob':
            argv = ['rg', '--files', '--hidden', '--no-ignore', '--glob', self._pattern(args.pop('pattern')), '.']
        elif name in ('rg', 'grep'):
            query = self._pattern(args.pop('query'))
            path = self._path(args.pop('path', '.'), file=name == 'grep')
            regex = args.pop('regex', False)
            if not isinstance(regex, bool):
                raise ValueError('regex must be boolean')
            if name == 'rg':
                context = args.pop('context', 2)
                if type(context) is not int or not 0 <= context <= 20:
                    raise ValueError('context must be 0..20')
                argv = ['rg', '--sort', 'path', '--hidden', '--no-ignore', '--color', 'never', '--text', '-n', '-H', '-C', str(context)]
                if 'pattern' in args:
                    argv += ['--glob', self._pattern(args.pop('pattern'))]
                argv += ([] if regex else ['-F']) + ['--', query, path]
            else:
                argv = ['grep', '-a', '-n', '-H', '--label', path, '-E' if regex else '-F', '--', query, '-']
        elif name in ('sed', 'read_file'):
            path = self._path(args.pop('path'), file=True)
            if name == 'sed':
                first, last = args.pop('start_line'), args.pop('end_line')
                if type(first) is not int or type(last) is not int or not 1 <= first <= last:
                    raise ValueError('invalid line range')
                argv = ['sed', '-n', f'{first},{last}p', '--', '-']
            else:
                argv = ['cat', '--', '-']
        else:
            raise ValueError('unknown file command')
        if args:
            raise ValueError('unsupported command options')
        argv[0] = self.executables[argv[0]]
        return argv

    @staticmethod
    def _pattern(value):
        if not isinstance(value, str) or not value or len(value) > 1000 or '\x00' in value:
            raise ValueError('invalid pattern')
        return value

    def _content_paths(self, name, args):
        if name in ('find', 'glob', 'semantic_search'):
            return []
        path = str(PurePosixPath(args.get('path', '.')))
        if name != 'rg' or path in self.paths:
            return [path]
        prefix = '' if path == '.' else path + '/'
        return [alias for alias in self.paths if alias.startswith(prefix)]

    def execute(self, name, args, argv, cancel=None):
        if argv != self.prepare(name, args):
            raise ValueError('command changed after preparation')
        if name != 'rg' or str(PurePosixPath(args.get('path', '.'))) in self.paths:
            return self._one_command(name, args, argv, cancel)
        return self._batched_search(args, argv, cancel)

    def _batched_search(self, args, argv, cancel):
        deadline = time.monotonic() + self.timeout
        aliases = sorted(self._content_paths('rg', args))
        batches, batch, size = [], [], 0
        for alias in aliases:
            doc = self.paths[alias]
            length = getattr(doc, 'utf8_bytes', None)
            if length is None:
                length = len(doc.text.encode('utf-8'))
            if batch and (len(batch) >= BATCH_FILES or size + length > BATCH_BYTES):
                batches.append(batch)
                batch, size = [], 0
            batch.append(alias)
            size += length
        if batch or not batches:
            batches.append(batch)
        original_paths = self.paths
        aggregate = None
        executions = []
        used = 0
        matched = False
        removed = []
        try:
            # Empty placeholders must not generate false empty-file regex hits.
            for alias in original_paths:
                self._path(alias, file=True)
                target = self.root / alias
                if os.name == 'nt':
                    target.chmod(0o600)  # Windows cannot unlink a read-only placeholder.
                target.unlink()
                removed.append(target)
            for batch in batches:
                self.paths = {alias: original_paths[alias] for alias in batch}
                for alias in batch:
                    (self.root / alias).touch()
                try:
                    part = self._one_command('rg', args, argv, cancel, deadline=deadline,
                                             output_limit=max(0, self.output_bytes - used))
                finally:
                    for alias in batch:
                        target = self.root / alias
                        if os.name == 'nt' and target.exists():
                            target.chmod(0o600)
                        target.unlink(missing_ok=True)
                executions.append({'argv': part['argv'], 'exit_code': part['exit_code'], 'status': part['status']})
                used += part['output_bytes']
                matched = matched or part['status'] == 'complete'
                if aggregate is None:
                    aggregate = dict(part)
                else:
                    aggregate['stdout'] += part['stdout']
                    aggregate['stderr'] += part['stderr']
                    aggregate['status'] = part['status']
                    aggregate['exit_code'] = part['exit_code']
                if part['status'] not in ('complete', 'no_match'):
                    if 'hint' in part:
                        aggregate['hint'] = part['hint']
                    break
            if aggregate['status'] in ('complete', 'no_match'):
                aggregate['status'] = 'complete' if matched else 'no_match'
                aggregate['exit_code'] = 0 if matched else 1
            aggregate.update(executions=executions, batch_count=len(executions),
                             output_bytes=used, planned_batches=len(batches))
            return aggregate
        finally:
            self.paths = original_paths
            for target in removed:
                target.touch(exist_ok=True)
                target.chmod(0o400)

    def _one_command(self, name, args, argv, cancel=None, *, deadline=None, output_limit=None):
        deadline = deadline if deadline is not None else time.monotonic() + self.timeout
        aliases = self._content_paths(name, args)
        doc = self.paths[aliases[0]] if len(aliases) == 1 else None
        stopped = (cancel is not None and cancel.is_set()) or self.timeout <= 0
        expired = deadline is not None and time.monotonic() >= deadline
        hold = doc.hold() if doc is not None and hasattr(doc, 'hold') and not stopped and not expired else nullcontext()
        with hold:
            return self._run_one_command(name, args, argv, cancel, deadline=deadline, output_limit=output_limit)

    def _run_one_command(self, name, args, argv, cancel=None, *, deadline=None, output_limit=None):
        if argv != self.prepare(name, args):
            raise ValueError('command changed after preparation')
        if name in ('sed', 'read_file', 'grep'):
            # The held source is streamed to the real binary, never materialized
            # as a second body file. Path validation still runs in prepare().
            return self._execute(name, args, argv, cancel, deadline=deadline, output_limit=output_limit)
        touched = []
        deadline = deadline if deadline is not None else time.monotonic() + self.timeout
        try:
            for alias in self._content_paths(name, args):
                if (cancel is not None and cancel.is_set()) or time.monotonic() >= deadline:
                    break
                # Never follow a replaced placeholder outside the private tree.
                self._path(alias, file=True)
                target = self.root / alias
                touched.append(target)
                target.chmod(0o600)
                with target.open('w', encoding='utf-8', newline='') as handle:
                    text = self.paths[alias].text
                    for offset in range(0, len(text), 65536):
                        if (cancel is not None and cancel.is_set()) or time.monotonic() >= deadline:
                            break
                        handle.write(text[offset:offset + 65536])
                target.chmod(0o400)
            return self._execute(name, args, argv, cancel, deadline=deadline, output_limit=output_limit)
        finally:
            for target in touched:
                target.chmod(0o600)
                with target.open('w'):
                    pass
                target.chmod(0o400)

    def _execute(self, name, args, argv, cancel=None, *, deadline=None, output_limit=None):
        # Verify the exact prepared command; UI events and execution share this argv.
        if argv != self.prepare(name, args):
            raise ValueError('command changed after preparation')
        if name == 'semantic_search':
            if cancel is not None and cancel.is_set():
                raise RuntimeError('query cancelled')
            try:
                result = self.semantic.document_ranges(args['query'], self.evidence.docs)
                hits = []
                for hit in result['hits']:
                    alias = self.aliases.get(hit['path'])
                    if alias not in self.paths:
                        continue
                    hits.append({k: hit[k] for k in
                                 ('revision', 'line_start', 'line_end', 'snippet')})
                    hits[-1]['path'] = alias
                return {'status': result['status'], 'hits': hits,
                        'warnings': result['warnings'], 'evidence_id': None}
            except Exception as exc:
                return {'status': 'unavailable', 'hits': [],
                        'warnings': [type(exc).__name__], 'evidence_id': None}
        env = {'PATH': os.environ.get('PATH', ''), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
        if os.name == 'nt':
            for key in ('SystemRoot', 'WINDIR', 'TEMP', 'TMP'):
                if key in os.environ:
                    env[key] = os.environ[key]
        result = {'argv': argv, 'command': shlex.join(argv), 'cwd': 'document snapshot',
                  'status': 'complete', 'stdout': '', 'stderr': '', 'evidence_id': None,
                  'unavailable_sources': dict(self.evidence.state['errors'])}
        output_limit = self.output_bytes if output_limit is None else output_limit
        deadline = deadline if deadline is not None else time.monotonic() + self.timeout
        stream_body = name in ('sed', 'read_file', 'grep')
        if stream_body:
            result['stdin_source'] = str(PurePosixPath(args['path']))
        # Generate bounded UTF-8 buffers lazily, including when input is much
        # larger than the output cap. Never encode/copy the whole source here.
        chunks = ()
        if stream_body and time.monotonic() < deadline and not (cancel and cancel.is_set()):
            text = self.paths[str(PurePosixPath(args['path']))].text
            chunks = (text[offset:offset + 16384].encode('utf-8')
                      for offset in range(0, len(text), 16384))

        with subprocess.Popen(argv, cwd=self.root, env=env, shell=False,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              stdin=subprocess.PIPE if stream_body else subprocess.DEVNULL,
                              **({'pass_fds': self.lease_fds} if self.lease_fds else {})) as proc:
            status, code, buffers, used = collect_output(
                proc, chunks, deadline=deadline, output_limit=output_limit, cancel=cancel)
            result.update(status=status, exit_code=code)
        result['output_bytes'] = used
        for channel, data in buffers.items():
            result[channel] = bytes(data).decode('utf-8', errors='replace')
        if result['status'] == 'complete':
            if result['exit_code'] == 1 and name in ('rg', 'grep', 'glob'):
                result['status'] = 'no_match'
            elif result['exit_code'] != 0:
                result['status'] = 'error'
            elif name in ('sed', 'read_file'):
                self._authorize(name, args, result)
        if result['status'] == 'truncated':
            result['hint'] = 'Output incomplete. Narrow the search or read a smaller sed line range.'
            if name in ('sed', 'read_file') and not buffers['stderr']:
                self._continuation(name, args, result, bytes(buffers['stdout']))
        return result

    def _continuation(self, name, args, result, raw):
        """Map actual process output to lines; never authorize a partial read."""
        alias = str(PurePosixPath(args['path']))
        doc = self.paths[alias]
        first = args['start_line'] if name == 'sed' else 1
        last = min(args['end_line'], len(doc.lines)) if name == 'sed' else len(doc.lines)
        starts = doc.lines
        start = starts[first - 1] if first <= len(starts) else len(doc.text)
        end = starts[last] if last < len(starts) else len(doc.text)
        if not doc.text[start:end].encode('utf-8').startswith(raw):
            raise ValueError('command output differs from pinned source')
        # Drop only an incomplete UTF-8 character at the output boundary.
        text = raw.decode('utf-8', errors='ignore')
        result['stdout'] = text
        complete_lines = text.count('\n')
        next_line = first + complete_lines
        result['read_progress'] = {
            'source': doc.path, 'revision': doc.revision,
            'requested_lines': [first, last],
            'returned_range': doc.location(start, start + len(text)) if text else None,
            'complete_lines': [first, next_line - 1] if complete_lines else None,
            'partial_line': next_line if text and not text.endswith('\n') else None,
        }
        def read_range(a, b):
            return {'tool': 'sed', 'arguments': {'path': alias, 'start_line': a, 'end_line': b}}
        result['reread_for_citation'] = read_range(first, next_line - 1) if complete_lines else None
        # A single line larger than the cap cannot be completed by narrowing sed.
        line_end = starts[next_line] if next_line < len(starts) else len(doc.text)
        too_long = len(doc.text[starts[next_line - 1]:line_end].encode('utf-8')) > self.output_bytes
        result['continuation'] = None if too_long else read_range(next_line, next_line)
        result['continuation_status'] = 'line_exceeds_output_limit' if too_long else 'available'
        result['hint'] = (
            'Partial output is not citation evidence. Use reread_for_citation for complete lines. '
            + ('The next line exceeds the output limit; do not repeat the same read or skip it silently.'
               if too_long else 'Use continuation to reread the unfinished line or read the next line; expand as needed.')
        )

    def _authorize(self, name, args, result):
        alias = str(PurePosixPath(args['path']))
        doc = self.paths[alias]
        # Reuse coordinates only within this read's held, verified body.
        lines = doc.lines
        first = args['start_line'] if name == 'sed' else 1
        last = args['end_line'] if name == 'sed' else len(lines)
        start = lines[first - 1] if first <= len(lines) else len(doc.text)
        end = lines[last] if last < len(lines) else len(doc.text)
        text = doc.text[start:end]
        if result['stdout'] != text:
            raise ValueError('command output differs from pinned source')
        if not text:
            return
        eid = f'{self.id}:{len(self.evidence.state["evidence"]) + 1}'
        evidence = {'path': doc.path, 'revision': doc.revision, 'content_hash': doc.content_hash,
                    'text_hash': doc.text_hash, 'page_kind': doc.page_kind, **doc.location(start, end),
                    'text': text, 'evidence_id': eid}
        self.evidence.state['evidence'][eid] = evidence
        result.update(evidence_id=eid, source=doc.path, line_start=evidence['line_start'],
                      line_end=evidence['line_end'])

    def reuse_evidence(self, citations, max_chars=16000):
        """Reuse unchanged, in-scope saved ranges; never authorize an ID alone."""
        reused, size = [], 0
        for saved in citations:
            eid = saved.get('evidence_id')
            if (not isinstance(eid, str) or saved.get('schema_version') != 2
                    or saved.get('kind') != 'document_range'
                    or eid in self.evidence.state['evidence']):
                continue
            text = saved.get('text')
            if not isinstance(text, str) or not text or size + len(text) > max_chars:
                continue
            self.evidence.state['evidence'][eid] = dict(saved)
            try:
                verified = self.resolve_citation(eid)
            except (KeyError, ValueError, TypeError, OSError):
                del self.evidence.state['evidence'][eid]
                continue
            reused.append(verified)
            size += len(text)
        return reused

    def resolve_citation(self, evidence_id):
        return self.evidence.resolve_citation(evidence_id)


@contextmanager
def open_file_commands(store=None, cache=None, *, reader=None):
    with open_source_snapshot(store, cache, reader=reader) as evidence:
        with search_temp() as (directory, lease_fds):
            tools = FileCommands(evidence, directory, lease_fds=lease_fds)
            yield tools
