"""비활성 세대도 저장할 수 있지만 조회 활성 여부는 SQLite가 결정한다."""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

MODEL = 'text-embedding-3-small'
DIMENSIONS = 1536
SEARCH_WORKERS = 4
SEARCH_FIELDS = ('generation', 'doc_id', 'revision', 'idx', 'text_hash', 'model', 'dimensions')


def validate(vector: list[float]) -> None:
    if len(vector) != DIMENSIONS or not all(math.isfinite(x) for x in vector) or not any(vector):
        raise ValueError('invalid embedding')


class RawVectors:
    def __init__(self, path: Path):
        self.path = path

    def _table(self, create=False):
        import lancedb
        import pyarrow as pa

        db = lancedb.connect(str(self.path))
        if create:
            schema = pa.schema([
                ('generation', pa.string()), ('doc_id', pa.int64()), ('revision', pa.int64()),
                ('idx', pa.int64()), ('text_hash', pa.string()), ('model', pa.string()),
                ('dimensions', pa.int64()), ('vector', pa.list_(pa.float32(), DIMENSIONS)),
            ])
            return db.create_table('chunks', schema=schema, exist_ok=True)
        return db.open_table('chunks')

    def add(self, rows: list[dict]) -> None:
        for row in rows:
            validate(row['vector'])
        if rows:
            self._table(True).add(rows)

    def rows(self, generation: str) -> list[dict]:
        if not (self.path / 'chunks.lance').exists():
            return []
        value = generation.replace("'", "''")
        return self._table().search().where(f"generation='{value}'").limit(10_000).to_list()

    def search(self, vector: list[float], generations: list[str]) -> list[dict]:
        validate(vector)
        if not generations:
            return []
        # 세대는 내부 UUID지만 SQL 리터럴도 별도로 이스케이프한다.
        values = ','.join("'" + g.replace("'", "''") + "'" for g in generations)
        for attempt in range(2):
            try:
                return self._table().search(vector).distance_type('cosine').where(
                    f'generation IN ({values})', prefilter=True).select([*SEARCH_FIELDS, '_distance']).limit(12).to_list()
            except Exception as exc:
                if attempt or 'not found' not in str(exc).lower():
                    raise
        return []

    def delete_document(self, doc_id: int) -> None:
        if type(doc_id) is not int or doc_id <= 0:
            raise ValueError('invalid document id')
        if (self.path / 'chunks.lance').exists():
            self._table().delete(f'doc_id = {doc_id}')
            self.optimize()

    def recover(self, active: list[str]) -> None:
        if not (self.path / 'chunks.lance').exists():
            return
        table = self._table()
        values = ','.join("'" + g.replace("'", "''") + "'" for g in active)
        table.delete(f'generation NOT IN ({values})' if values else 'true')
        self.optimize()

    def optimize(self) -> None:
        self._table().optimize(cleanup_older_than=timedelta(minutes=10))


class Vectors(RawVectors):
    """Legacy local generations plus folder-local views of shared generations."""

    def rows(self, generation):
        from prtfm.store import shared_vectors as shared
        with shared.catalogue() as con:
            ref = shared.refs(con, self.path).get(generation)
            if ref:
                return [shared.translated(r, ref) for r in shared.raw(ref['key']).rows(ref['key'])]
            return super().rows(generation)

    def add(self, rows):
        from prtfm.store import shared_vectors as shared
        if not (shared.root() / 'catalogue.db').exists():
            return super().add(rows)
        with shared.catalogue() as con:
            refs = shared.refs(con, self.path)
            local, groups = [], {}
            for row in rows:
                ref = refs.get(row['generation'])
                if ref is None:
                    local.append(row)
                else:
                    groups.setdefault(ref['key'], []).append({**row, 'generation': ref['key'], 'doc_id': 0, 'revision': 0})
            for key, batch in groups.items():
                shared.raw(key).add(batch)
            if local:
                super().add(local)

    def search(self, vector, generations):
        from prtfm.store import shared_vectors as shared
        with shared.catalogue() as con:
            refs = shared.refs(con, self.path)
            hits, local, selected = [], [], []
            for generation in generations:
                ref = refs.get(generation)
                if ref:
                    selected.append(ref)
                else:
                    local.append(generation)

            def search_ref(ref):
                return [shared.translated(row, ref)
                        for row in shared.raw(ref['key']).search(vector, [ref['key']])]

            def merge(rows):
                nonlocal hits
                hits = sorted(hits + rows, key=lambda row: row['_distance'])[:12]

            if len(selected) <= SEARCH_WORKERS:
                for ref in selected:
                    merge(search_ref(ref))
            else:
                # Bound both worker count and queued candidate lists. Consume in input
                # order to preserve stable ties; hold catalogue until all workers exit.
                with ThreadPoolExecutor(max_workers=SEARCH_WORKERS) as pool:
                    for offset in range(0, len(selected), SEARCH_WORKERS):
                        for rows in pool.map(search_ref, selected[offset:offset + SEARCH_WORKERS]):
                            merge(rows)
            if local:
                hits.extend(super().search(vector, local))
            return sorted(hits, key=lambda r: r['_distance'])[:12]

    def delete_document(self, doc_id):
        from prtfm.store import shared_vectors as shared
        if type(doc_id) is not int or doc_id <= 0:
            raise ValueError('invalid document id')
        shared.release(self.path, doc_id=doc_id)
        super().delete_document(doc_id)

    def recover(self, active):
        from prtfm.store import shared_vectors as shared
        shared.release(self.path, keep=set(active))
        super().recover(active)

    def optimize(self):
        # Shared payload is optimized at publication under its document lock.
        if (self.path / 'chunks.lance').exists():
            super().optimize()
