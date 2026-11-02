"""단일 writer 작업 안에서 신규 저장량에 따라 벡터 병합을 예약한다."""
from prtfm.config import SEMANTIC_LIMITS


class VectorMaintenance:
    def __init__(self, vectors, warnings, threshold=SEMANTIC_LIMITS.optimize_chunks):
        if threshold <= 0:
            raise ValueError('maintenance threshold must be positive')
        self.vectors, self.warnings, self.threshold = vectors, warnings, threshold
        self.pending = 0
        self.dirty = False

    def add(self, rows):
        if not rows:
            return
        # 저장 호출이 커밋 뒤 실패할 수도 있어 finally 정리가 필요하다.
        self.dirty = True
        self.vectors.add(rows)
        self.pending += len(rows)
        if self.pending >= self.threshold:
            self.finish()

    def finish(self):
        if not self.dirty:
            return
        try:
            self.vectors.optimize()
        except Exception as exc:
            self.warnings.append(type(exc).__name__)
        else:
            self.dirty = False
        finally:
            # 실패해도 다음 작은 배치마다 재시도하지 않는다. 종료 시에는 재시도한다.
            self.pending = 0
