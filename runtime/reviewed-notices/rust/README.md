# Windows Rust dependency notices

Resolved from the 0.1.21 Windows source snapshot, custom-protocol feature, offline locked Cargo tree. `normal` excludes proc-macro edges; other entries are conservatively retained build/proc-macro dependencies. This is a dependency graph, not byte-level proof that every crate survives linker elimination.

Original license files are preserved. All source crate archives match Cargo.lock checksums; notice files are copied directly from these verified source archives. Source URLs and checksums are in manifest.json. This supplement does not relicense these crates.

| Crate | Version | License expression | Scope |
| --- | --- | --- | --- |
| adler2 | 2.0.1 | 0BSD OR MIT OR Apache-2.0 | build-or-proc-macro-only |
| aho-corasick | 1.1.5 | Unlicense OR MIT | normal |
| alloc-no-stdlib | 3.0.0 | BSD-3-Clause | normal |
| alloc-stdlib | 0.3.0 | BSD-3-Clause | normal |
| anyhow | 1.0.104 | MIT OR Apache-2.0 | normal |
| autocfg | 1.5.1 | Apache-2.0 OR MIT | build-or-proc-macro-only |
| base64 | 0.23.1 | MIT OR Apache-2.0 | normal |
| bit-set | 0.8.0 | Apache-2.0 OR MIT | build-or-proc-macro-only |
| bit-vec | 0.8.0 | Apache-2.0 OR MIT | build-or-proc-macro-only |
| bitflags | 2.13.2 | MIT OR Apache-2.0 | normal |
| block-buffer | 0.10.4 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| brotli | 9.1.1 | BSD-3-Clause AND MIT | normal |
| brotli-decompressor | 6.2.0 | BSD-3-Clause/MIT | normal |
| byteorder | 1.5.0 | Unlicense OR MIT | build-or-proc-macro-only |
| bytes | 1.12.1 | MIT | normal |
| camino | 1.2.6 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| cargo-platform | 0.1.9 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| cargo_metadata | 0.19.2 | MIT | build-or-proc-macro-only |
| cargo_toml | 1.0.1 | Apache-2.0 OR MIT | build-or-proc-macro-only |
| cc | 1.10.0 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| cfb | 0.14.0 | MIT | normal |
| cfg-if | 1.0.5 | MIT OR Apache-2.0 | normal |
| cookie | 0.18.2 | MIT OR Apache-2.0 | normal |
| cpufeatures | 0.2.17 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| crc32fast | 1.5.3 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| crossbeam-channel | 0.5.17 | MIT OR Apache-2.0 | normal |
| crossbeam-utils | 0.8.23 | MIT OR Apache-2.0 | normal |
| crypto-common | 0.1.7 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| cssparser | 0.37.0 | MPL-2.0 | build-or-proc-macro-only |
| cssparser-macros | 0.7.1 | MPL-2.0 | build-or-proc-macro-only |
| ctor | 1.0.13 | Apache-2.0 OR MIT | normal |
| darling | 0.24.1 | MIT | build-or-proc-macro-only |
| darling_core | 0.24.1 | MIT | build-or-proc-macro-only |
| darling_macro | 0.24.1 | MIT | build-or-proc-macro-only |
| deranged | 0.5.9 | MIT OR Apache-2.0 | normal |
| derive_more | 2.1.1 | MIT | build-or-proc-macro-only |
| derive_more-impl | 2.1.1 | MIT | build-or-proc-macro-only |
| digest | 0.10.7 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| dirs | 7.0.0 | MIT OR Apache-2.0 | normal |
| dirs-sys | 0.5.0 | MIT OR Apache-2.0 | normal |
| displaydoc | 0.2.7 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| dom_query | 0.28.0 | MIT | build-or-proc-macro-only |
| dpi | 0.1.2 | Apache-2.0 AND MIT | normal |
| dtoa | 1.0.11 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| dtoa-short | 0.3.5 | MPL-2.0 | build-or-proc-macro-only |
| dunce | 1.0.5 | CC0-1.0 OR MIT-0 OR Apache-2.0 | normal |
| dyn-clone | 1.0.20 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| embed-resource | 3.0.12 | MIT | build-or-proc-macro-only |
| equivalent | 1.0.3 | Apache-2.0 OR MIT | normal |
| erased-serde | 0.4.11 | MIT OR Apache-2.0 | normal |
| fastrand | 2.5.0 | Apache-2.0 OR MIT | build-or-proc-macro-only |
| fdeflate | 0.3.7 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| find-msvc-tools | 0.1.15 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| flate2 | 1.1.10 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| fnv | 1.0.7 | Apache-2.0 / MIT | normal |
| foldhash | 0.2.0 | Zlib | build-or-proc-macro-only |
| form_urlencoded | 1.2.2 | MIT OR Apache-2.0 | normal |
| generic-array | 0.14.7 | MIT | build-or-proc-macro-only |
| getrandom | 0.3.4 | MIT OR Apache-2.0 | normal |
| getrandom | 0.4.3 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| glob | 0.3.4 | MIT OR Apache-2.0 | normal |
| hashbrown | 0.12.3 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| hashbrown | 0.17.1 | MIT OR Apache-2.0 | normal |
| heck | 0.5.0 | MIT OR Apache-2.0 | normal |
| html5ever | 0.39.0 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| http | 1.5.0 | MIT OR Apache-2.0 | normal |
| ico | 0.6.0 | MIT | build-or-proc-macro-only |
| icu_collections | 2.4.0 | Unicode-3.0 | normal |
| icu_locale_core | 2.4.0 | Unicode-3.0 | normal |
| icu_normalizer | 2.4.0 | Unicode-3.0 | normal |
| icu_normalizer_data | 2.4.0 | Unicode-3.0 | normal |
| icu_properties | 2.4.0 | Unicode-3.0 | normal |
| icu_properties_data | 2.4.0 | Unicode-3.0 | normal |
| icu_provider | 2.4.0 | Unicode-3.0 | normal |
| ident_case | 1.0.1 | MIT/Apache-2.0 | build-or-proc-macro-only |
| idna | 1.1.0 | MIT OR Apache-2.0 | normal |
| idna_adapter | 1.2.2 | Apache-2.0 OR MIT | normal |
| indexmap | 1.9.3 | Apache-2.0 OR MIT | build-or-proc-macro-only |
| indexmap | 2.14.2 | Apache-2.0 OR MIT | normal |
| infer | 0.22.0 | MIT | normal |
| itoa | 1.0.18 | MIT OR Apache-2.0 | normal |
| json-patch | 4.2.0 | MIT/Apache-2.0 | normal |
| jsonptr | 0.7.1 | MIT OR Apache-2.0 | normal |
| keyboard-types | 0.8.3 | MIT OR Apache-2.0 | normal |
| libc | 0.2.191 | MIT OR Apache-2.0 | normal |
| litemap | 0.8.3 | Unicode-3.0 | normal |
| lock_api | 0.4.14 | MIT OR Apache-2.0 | normal |
| log | 0.4.34 | MIT OR Apache-2.0 | normal |
| markup5ever | 0.39.0 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| memchr | 2.8.3 | Unlicense OR MIT | normal |
| mime | 0.3.17 | MIT OR Apache-2.0 | normal |
| miniz_oxide | 0.8.9 | MIT OR Zlib OR Apache-2.0 | build-or-proc-macro-only |
| miniz_oxide | 0.9.1 | MIT OR Zlib OR Apache-2.0 | build-or-proc-macro-only |
| muda | 0.20.0 | Apache-2.0 OR MIT | normal |
| new_debug_unreachable | 1.0.6 | MIT | build-or-proc-macro-only |
| num-conv | 0.2.2 | MIT OR Apache-2.0 | normal |
| once_cell | 1.21.4 | MIT OR Apache-2.0 | normal |
| option-ext | 0.2.0 | MPL-2.0 | normal |
| parking_lot | 0.12.5 | MIT OR Apache-2.0 | normal |
| parking_lot_core | 0.9.12 | MIT OR Apache-2.0 | normal |
| percent-encoding | 2.3.2 | MIT OR Apache-2.0 | normal |
| phf | 0.13.1 | MIT | normal |
| phf_codegen | 0.13.1 | MIT | build-or-proc-macro-only |
| phf_generator | 0.13.1 | MIT | build-or-proc-macro-only |
| phf_macros | 0.13.1 | MIT | build-or-proc-macro-only |
| phf_shared | 0.13.1 | MIT | normal |
| pin-project-lite | 0.2.17 | Apache-2.0 OR MIT | normal |
| plist | 1.10.3 | MIT | normal |
| png | 0.18.1 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| potential_utf | 0.1.6 | Unicode-3.0 | normal |
| powerfmt | 0.2.1 | MIT OR Apache-2.0 | normal |
| precomputed-hash | 0.1.1 | MIT | build-or-proc-macro-only |
| proc-macro2 | 1.0.107 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| quick-xml | 0.42.0 | MIT | normal |
| quote | 1.0.47 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| raw-window-handle | 0.6.2 | MIT OR Apache-2.0 OR Zlib | normal |
| regex | 1.13.1 | MIT OR Apache-2.0 | normal |
| regex-automata | 0.4.18 | MIT OR Apache-2.0 | normal |
| regex-syntax | 0.8.11 | MIT OR Apache-2.0 | normal |
| rfd | 0.17.2 | MIT | normal |
| rustc-hash | 2.1.3 | Apache-2.0 OR MIT | build-or-proc-macro-only |
| rustc_version | 0.4.1 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| same-file | 1.0.6 | Unlicense/MIT | normal |
| schemars | 0.8.22 | MIT | build-or-proc-macro-only |
| schemars_derive | 0.8.22 | MIT | build-or-proc-macro-only |
| scopeguard | 1.2.0 | MIT OR Apache-2.0 | normal |
| selectors | 0.38.0 | MPL-2.0 | build-or-proc-macro-only |
| semver | 1.0.28 | MIT OR Apache-2.0 | normal |
| serde | 1.0.229 | MIT OR Apache-2.0 | normal |
| serde-untagged | 0.1.9 | MIT OR Apache-2.0 | normal |
| serde_core | 1.0.229 | MIT OR Apache-2.0 | normal |
| serde_derive | 1.0.229 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| serde_derive_internals | 0.29.1 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| serde_json | 1.0.154 | MIT OR Apache-2.0 | normal |
| serde_repr | 0.1.21 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| serde_spanned | 1.1.2 | MIT OR Apache-2.0 | normal |
| serde_with | 3.24.0 | MIT OR Apache-2.0 | normal |
| serde_with_macros | 3.24.0 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| serialize-to-javascript | 0.1.2 | MIT OR Apache-2.0 | normal |
| serialize-to-javascript-impl | 0.1.2 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| servo_arc | 0.4.3 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| sha2 | 0.10.9 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| shlex | 2.0.1 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| simd-adler32 | 0.3.10 | MIT | build-or-proc-macro-only |
| siphasher | 1.0.4 | MIT OR Apache-2.0 | normal |
| smallvec | 1.16.4 | MIT OR Apache-2.0 | normal |
| softbuffer | 0.4.8 | MIT OR Apache-2.0 | normal |
| stable_deref_trait | 1.2.1 | MIT OR Apache-2.0 | normal |
| static_vcruntime | 3.0.0 | MIT OR Apache-2.0 OR Zlib | build-or-proc-macro-only |
| string_cache | 0.9.0 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| string_cache_codegen | 0.6.1 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| strsim | 0.11.1 | MIT | build-or-proc-macro-only |
| syn | 2.0.119 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| syn | 3.0.9 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| synstructure | 0.14.0 | MIT | build-or-proc-macro-only |
| tao | 0.37.2 | Apache-2.0 | normal |
| tauri | 2.12.5 | Apache-2.0 OR MIT | normal |
| tauri-build | 2.7.4 | Apache-2.0 OR MIT | build-or-proc-macro-only |
| tauri-codegen | 2.7.4 | Apache-2.0 OR MIT | build-or-proc-macro-only |
| tauri-macros | 2.7.4 | Apache-2.0 OR MIT | build-or-proc-macro-only |
| tauri-runtime | 2.12.2 | Apache-2.0 OR MIT | normal |
| tauri-runtime-wry | 2.12.3 | Apache-2.0 OR MIT | normal |
| tauri-utils | 2.10.2 | Apache-2.0 OR MIT | normal |
| tauri-winres | 0.3.7 | MIT | build-or-proc-macro-only |
| tendril | 0.5.1 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| thiserror | 2.0.21 | MIT OR Apache-2.0 | normal |
| thiserror-impl | 2.0.21 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| time | 0.3.55 | MIT OR Apache-2.0 | normal |
| time-core | 0.1.9 | MIT OR Apache-2.0 | normal |
| time-macros | 0.2.32 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| tinystr | 0.8.5 | Unicode-3.0 | normal |
| tokio | 1.54.1 | MIT | normal |
| toml | 1.1.8+spec-1.1.0 | MIT OR Apache-2.0 | normal |
| toml_datetime | 1.1.2+spec-1.1.0 | MIT OR Apache-2.0 | normal |
| toml_parser | 1.1.5+spec-1.1.0 | MIT OR Apache-2.0 | normal |
| toml_writer | 1.1.3+spec-1.1.0 | MIT OR Apache-2.0 | normal |
| tracing | 0.1.44 | MIT | normal |
| tracing-core | 0.1.36 | MIT | normal |
| typeid | 1.0.3 | MIT OR Apache-2.0 | normal |
| typenum | 1.20.1 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| unicode-ident | 1.0.27 | (MIT OR Apache-2.0) AND Unicode-3.0 | build-or-proc-macro-only |
| unicode-segmentation | 1.13.3 | MIT OR Apache-2.0 | normal |
| url | 2.5.8 | MIT OR Apache-2.0 | normal |
| urlpattern | 0.6.0 | MIT | normal |
| utf8_iter | 1.0.4 | Apache-2.0 OR MIT | normal |
| uuid | 1.29.0 | Apache-2.0 OR MIT | normal |
| version_check | 0.9.5 | MIT/Apache-2.0 | build-or-proc-macro-only |
| vswhom | 0.1.0 | MIT | build-or-proc-macro-only |
| vswhom-sys | 0.1.3 | MIT | build-or-proc-macro-only |
| walkdir | 2.5.0 | Unlicense/MIT | normal |
| web-time | 1.1.0 | MIT OR Apache-2.0 | normal |
| web_atoms | 0.2.6 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| webview2-com | 0.39.1 | MIT | normal |
| webview2-com-macros | 0.8.1 | MIT | build-or-proc-macro-only |
| webview2-com-sys | 0.39.1 | MIT | normal |
| winapi-util | 0.1.11 | Unlicense OR MIT | normal |
| window-vibrancy | 0.8.1 | Apache-2.0 OR MIT | normal |
| windows | 0.62.2 | MIT OR Apache-2.0 | normal |
| windows-collections | 0.3.2 | MIT OR Apache-2.0 | normal |
| windows-core | 0.62.2 | MIT OR Apache-2.0 | normal |
| windows-future | 0.3.2 | MIT OR Apache-2.0 | normal |
| windows-implement | 0.60.2 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| windows-interface | 0.59.3 | MIT OR Apache-2.0 | build-or-proc-macro-only |
| windows-link | 0.2.1 | MIT OR Apache-2.0 | normal |
| windows-numerics | 0.3.1 | MIT OR Apache-2.0 | normal |
| windows-result | 0.4.1 | MIT OR Apache-2.0 | normal |
| windows-strings | 0.5.1 | MIT OR Apache-2.0 | normal |
| windows-sys | 0.61.2 | MIT OR Apache-2.0 | normal |
| windows-threading | 0.2.1 | MIT OR Apache-2.0 | normal |
| windows-version | 0.1.7 | MIT OR Apache-2.0 | normal |
| winnow | 1.0.4 | MIT | normal |
| winreg | 0.56.0 | MIT | build-or-proc-macro-only |
| writeable | 0.6.4 | Unicode-3.0 | normal |
| wry | 0.57.1 | Apache-2.0 OR MIT | normal |
| yoke | 0.8.4 | Unicode-3.0 | normal |
| yoke-derive | 0.8.4 | Unicode-3.0 | build-or-proc-macro-only |
| zerofrom | 0.1.8 | Unicode-3.0 | normal |
| zerofrom-derive | 0.1.8 | Unicode-3.0 | build-or-proc-macro-only |
| zerotrie | 0.2.5 | Unicode-3.0 | normal |
| zerovec | 0.11.9 | Unicode-3.0 | normal |
| zerovec-derive | 0.11.7 | Unicode-3.0 | build-or-proc-macro-only |
| zmij | 1.0.23 | MIT | normal |
