# Windows desktop host

Windows x64용 Tauri 호스트입니다. Python 사이드카를 실행하고 인증된 로컬 작업 화면을 엽니다.
브라우저 개발 화면과 같은 UI 자산을 사용합니다.

## 구성

- `src-tauri/src`: 사이드카 수명 관리, 로컬 화면 연결, 네이티브 메뉴와 API 키 관리
- `ui`: 시작·오류 화면
- `installer`: NSIS 템플릿과 설치 정책
- `search-tools.json`, `packaging-tools.json`: 빌드 도구 출처·버전·해시
- `python-test-exclusions.json`: 배포 런타임에서 제외할 의존성 테스트 파일 목록

호스트는 인증 정보를 명령줄·URL에 넣지 않으며, 허용한 루프백 origin으로만 작업 화면을 연결합니다.
호스트가 종료되면 소유한 사이드카 프로세스를 종료합니다. 앱 데이터는 설치 디렉터리 밖에 보관합니다.

## 빌드

빌드 머신에는 Microsoft C++ Build Tools의 Desktop development with C++, MSVC 및 Windows SDK,
Rust와 잠금 파일에 맞는 의존성이 필요합니다. 최종 사용자는 이 개발 도구를 설치할 필요가 없습니다.

`../scripts/check-windows-desktop.ps1`은 `-RuntimeRoot` 아래의 격리된 Rust 도구로
잠금 파일을 사용하는 `cargo check`를 실행합니다. `-Action build`로 실행 파일을 빌드합니다.

배포 런타임은 `prepare-windows-trial.ps1`, `build-windows-trial-parser.ps1`,
`build-windows-bundle.py`를 통해 준비합니다. 각 도구의 인자와 역할은 [scripts README](../scripts/README.md)를 참고하세요.
검증한 번들을 `build-windows-installer.ps1 -BundlePath <bundle>`에 전달합니다.
`-PreviewVersion`으로 설치 버전을 지정하며, `-DirectoryOnly`는 디렉터리 빌드용입니다.
도구 버전·해시는 위 manifest를 따릅니다.

일반 개발 빌드는 installer bundling이 꺼져 있습니다. 개발 실행 시 `PRTFM_DESKTOP_RUNTIME`으로
번들을 지정할 수 있고, 패키지는 동봉 `runtime-bundle`을 사용합니다.
소스를 변경하면 사이드카 번들과 설치 파일도 다시 빌드해야 반영됩니다.

현재 Preview 설치파일에는 코드 서명이 없습니다.
