# 동봉 PDF 런타임

Python 제품 의존성과 JDK 21을 준비한 뒤 저장소 루트에서 `bash runtime/build_runtime.sh`를 실행합니다.
산출물은 `runtime/build/`이며 Git에서 제외된다.
다른 위치의 런타임은 `PRTFM_RUNTIME_DIR`로 지정한다.

현재 OS의 JDK 21에서 jlink + 기본 CDS + 앱 전용 CDS를 생성한다.
Linux 산출물을 Windows에 재사용하지 않는다. 각 플랫폼에서 빌드·검증해야 한다.
JRE legal 디렉터리와 파서 패키지 고지를 보존하며 manifest에 빌드 지문과 소스 출처를 기록한다.
릴리스에는 동봉 JDK에 대응하는 소스 아카이브를 함께 제공한다. 대상 플랫폼에서 생성한 런타임으로 설치·실행을 확인한다.
