# NSIS file-copy and running-app handling

Based on Tauri CLI 2.12.4's [official template](https://github.com/tauri-apps/tauri/blob/tauri-cli-v2.12.4/crates/tauri-bundler/src/bundle/windows/nsis/installer.nsi).
Upstream SHA256: `d00089d3b1a2c4a30d7b6e098079100cb8f25e980f84d4515aca4597b95e967d`. Tauri is licensed under MIT OR Apache-2.0; this copy uses the accompanying upstream `LICENSE-MIT`.

Local changes: clear/check Errors immediately around the main executable, resource, external binary, and uninstaller writes. On failure abort with exit 2 before registering completion. No rollback is claimed: close programs holding files and rerun setup. Original documents and app data are outside this write set.

A post-install hook is too late: registration has already occurred. Tauri supports a [custom NSIS template](https://v2.tauri.app/distribute/windows-installer/). When upgrading the pinned CLI, review its template changes and rerun the locked-file recovery test.

## Running app policy (2026-10-29)

`PrtfmRequireAppClosed` uses Restart Manager only to query ownership of the target executable. It never calls RmShutdown. Installation, prior-version removal and uninstallation stop with exit code **3** when the app is running or detection fails. Interactive mode explains that the user must wait or close the app through its own confirmation, then rerun setup; silent/passive mode also stops without killing anything. Copy failure remains exit **2**.

This is a preflight check, not a system-wide lock preventing a launch after the check. A concurrent late launch can still cause a checked file-write failure. Older setup/uninstall executables retain their old behavior; use the new package for this policy. Existing recovery tests that expect automatic shutdown must be treated as historical behavior, not current acceptance criteria.

## Obsolete metadata migration — Preview 0.1.4

An explicit allowlist removes only `runtime-bundle/python/Lib/site-packages/prtfm-0.1.0.dist-info/uv_build.json`, previously shipped in 0.1.2 but omitted from newer wheels. Installation removes it after the running-app check and before copying resources; uninstallation also attempts it before deleting current resources. A deletion failure stops with exit 2. There is no wildcard or recursive site-packages cleanup. The `0.1.0` here is the Python package version, independent of the desktop Preview version. Future obsolete files require a separately reviewed migration entry.

The optional removal checkbox says “앱 화면 설정도 삭제” / “Also remove app display settings”. A separate notice says original documents, processed documents, embeddings and conversations are preserved. It defaults to unchecked. The existing bundle-ID host directories and installer registry cleanup are unchanged; this option does not delete the Python document storage root (`PleaseReadThisForMe` or `PRTFM_DATA_DIR`).

Installer language selection starts with English on a fresh installation, including Korean Windows. Korean remains selectable. NSIS retains a previously saved explicit installer language choice.
