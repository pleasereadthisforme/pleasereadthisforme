# Reviewed third-party notice supplement

This directory is bundled as `runtime/legal/prtfm-reviewed/` by `scripts/build-windows-bundle.py`.

- [MSYS-NOTICE.md](MSYS-NOTICE.md): exact Git for Windows runtime source and build recipe.
- [PARSER-NOTICE.md](PARSER-NOTICE.md): actual PDF parser component versions and original license supplements.
- [manifest.json](manifest.json): reviewed binary fingerprints and corresponding-source URLs/checksums.
- `component-notices/`: preserved upstream license/notice files and relevant veraPDF POM attribution.

The bundle build rejects changed parser/MSYS fingerprints until this review is refreshed. Upstream notices are preserved rather than edited in place. Where their aggregate table lists older parser dependency versions, PARSER-NOTICE.md supplies the version correction for this distribution.

This is a supplement for these reviewed components, not a complete license inventory for every bundled dependency. Public availability of the corresponding source must be maintained with each binary release; the app's private audit directory is not a public source-distribution channel.

- [Windows Rust notices](rust/README.md): resolved Windows dependency graph, source checksums and preserved licenses (including build/proc-macro entries). Cargo.lock changes require refreshing this review.

- [Python static-extension review](python-static/README.md): exact wheel SBOMs, source hashes, notices and the recovered exact-commit kanaria notice.

- [Data and vendor review](data-vendor/README.md): dictionary attributions, Kiwi model and native notices, SDK vendor, tokenizer data, certifi and tqdm.
- [Source catalog](source-catalog.json): hash-pinned reviewed archives for source release packaging.
