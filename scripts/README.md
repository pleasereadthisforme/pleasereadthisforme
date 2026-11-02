# 제품 빌드 도구

이 디렉터리는 개인 개발 도구 없이 동작하는 제품 빌드 입력만 포함합니다.
Windows 빌드 환경의 Python 3.12, JDK 21, Rust/MSVC, uv 및 Tauri CLI는 별도로 준비합니다.
도구 출처·버전·해시는 `desktop`의 manifest를 따릅니다.

1. `prepare-windows-trial.ps1 -RuntimeRoot <workspace>`: workspace/staging에 잠금 파일 기준 제품 Python 환경 생성.
2. `build-windows-trial-parser.ps1 -RuntimeRoot <workspace> -JdkArchive <zip>`: workspace/staging/runtime/build에 파서 런타임 생성.
3. `build-command-manifest.py`: 준비한 검색 도구의 manifest 생성 (`--help` 참조).
4. `build-windows-bundle.py --runtime-root <workspace> --python-root <python> --site-packages <packages> --parser-runtime <runtime> --output <new-bundle>`: 표준 라이브러리가 풀려 있는 전체 Windows Python 배포본과 명시한 제품 입력으로 번들 생성(임베디드 ZIP 배포본 제외). workspace에는 git/, rg/, commands.json이 필요합니다.
5. `check-windows-desktop.ps1 -RuntimeRoot <workspace> -Action build`: 잠금 파일 기준 네이티브 빌드.
6. `build-windows-installer.ps1 -RuntimeRoot <workspace> -BundlePath <bundle> -PreviewVersion <version>`: 설치 파일 생성.

`search-tools-plan.py`, `python-test-plan.py`는 검색 도구와 Python 배포 파일의 포함 범위를 계산합니다.
`trial` 이름의 두 준비 스크립트는 기존 호출 이름을 유지하지만 출력은 제품 빌드 작업공간의 staging입니다.
번들 생성기는 개인 시험 실행기·fixture·검증 보고서를 포함하지 않습니다.
원본 문서·앱 저장소·비밀 키를 빌드 작업공간으로 사용하지 마세요.

## Source release preparation

`python scripts/package-release-sources.py --source-map <local-path-and-hash-map.json> --output <new-directory>` prepares an application snapshot and reviewed upstream-source archive. The local map supplies `{sha256, local}` for every hash in `runtime/reviewed-notices/source-catalog.json`; archive bytes must match. It does not download, stage, commit or publish. The application archive uses Git's nonignored file list, rejects private dev/env paths and symlinks, and includes build scripts, lockfiles, notices and required assets.

Build from that snapshot, record the new installer SHA256, and publish the source archives/checksums with the same release. An old installer must not be paired with a newer source snapshot. GPL/LGPL/MPL source availability is an actual distribution obligation, not satisfied by a private local archive. Preserve original component notices and build recipes inside the upstream sources; modified third-party code would require the corresponding changes too.

## Release build inputs and source pairing

Keep the following alongside each release's source archive and installer (no secrets):

- Explicit `PreviewVersion` and the installer SHA256; do not infer the release version from a moving branch.
- `desktop/src-tauri/{Cargo.lock,tauri.conf.json,tauri.installer.conf.json}`, `sidecar/uv.lock`, `desktop/{packaging-tools,search-tools}.json` and `desktop/installer/obsolete-runtime-files.json`.
- The builder's Python, uv, Rust/MSVC, Tauri CLI and JDK versions; runtime `manifest.json`, `commands.json` and `bundle.json`.
- Generated `installer-src/runtime-resources.json` and expanded installer template. The script generates the former from the allowed runtime tree, excluding caches/tests and the duplicate parser, and the latter from the obsolete-file manifest. These are generated build evidence, not hand-maintained application source.

`runtime-resources.json` contains build-machine absolute paths. For publication provide a copy with the build root replaced by a documented placeholder; retain the relative payload paths and regenerate it on the recipient's machine. Never publish credentials, signing keys or unrelated local paths.

The source catalog is public: each record contains a component, official source URL and expected SHA256. A recipient can obtain those archives, verify each hash and create the local `--source-map` as a JSON array of `{ "sha256": "...", "local": "path/to/archive" }`. This removes any need for the maintainer's private dev directory. Reuse the supplied third-party archive to avoid repeated downloads. Building also requires the binary tools specified in the packaging/search manifests and valid toolchain usage rights.
