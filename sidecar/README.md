# Python sidecar

Python 3.12 기반 문서 처리·검색·저장소·로컬 API입니다.
`pyproject.toml`과 `uv.lock`으로 의존성을 관리합니다.

이 디렉터리에서 `uv sync --locked --no-dev`로 제품 의존성을 설치하고
`uv run --locked --no-dev prtfm --help`로 CLI를 확인합니다.
PDF 파서 및 파일 검색에는 동봉 런타임·검색 실행 파일 설정이 필요합니다.
설치형 호스트는 패키지의 로컬 서버를 시작합니다.

`src/prtfm`은 배포 패키지이며 `server/activity_ui`는 동봉 작업 화면입니다.
사용자 데이터·API 키·개인 검증 자산은 패키지에 포함하지 않습니다.

### CLI 검색 종료 상태

`prtfm search`는 정상 검색(일치 결과 없음 포함)에 종료 코드 0을 반환합니다. 원본 변경·삭제 등으로 일부 또는 모든 문서를 제외하면 결과는 stdout에, 제외 경로와 사유는 stderr에 출력하며 종료 코드 1을 반환합니다. 잘못된 검색 입력은 종료 코드 2입니다.
