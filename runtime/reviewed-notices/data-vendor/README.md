# Data, model and vendored-code notices

Scope: the reviewed Windows 0.1.21 staging components. Preserve these notices in addition to package-local license files. Original assets are unmodified by this notice supplement.

| Component | Terms and action |
| --- | --- |
| SudachiDict core 20261015 | Apache-2.0 plus the UniDic BSD notice and NEologd attribution in LEGAL. The installed wheel retained only Apache text; `SudachiDict-20261015-LEGAL` restores the complete notice from the exact official sdist, also matching release tag v20261015. Do not label all source dictionary data solely Apache. |
| Kiwi model 0.24.0 | The original NOTICE explicitly releases trained model files under Apache-2.0 and preserves NIKL corpus attribution. Source training corpora are not included or licensed by this app. |
| Kiwi native vendors | Original source notices under `kiwi-source/` cover Eigen MPL-2.0, cpp-btree/streamvbyte Apache-2.0, cpuinfo BSD-2-Clause, mimalloc/JSON MIT. CLI/test notices are also preserved without claiming they are linked. The exact Kiwi source archive contains Eigen source and is listed in the source catalog. |
| OpenAI SDK httpx_aiohttp vendor | BSD-3-Clause, copyright Karen Petrosyan. Its own LICENSE is preserved under installed/openai/_vendor; the SDK's top-level Apache license does not replace it. |
| tiktoken 0.14.0 and bundled cl100k_base data | MIT. The upstream maintainer explicitly confirmed encoding files use the repository license: https://github.com/openai/tiktoken/issues/92#issuecomment-1497875652 . The installed encoding bytes match the official constructor's expected hash; see manifest.json. |
| certifi 2026.10.19 | MPL-2.0 certificate bundle; original LICENSE and exact official source archive are provided in the release-source catalog. No independent trust-store relicensing. |
| tqdm 4.70.1 | Preserve its mixed MPL-2.0/MIT LICENCE, not a simplified MIT label. Exact official sdist is in the source catalog. |

No noncommercial-use restriction was found in these reviewed originals. Commercial distribution still requires their attribution, notice and applicable source obligations; this is not a waiver or an all-components legal certification. App AGPL rights/obligations are separate.

`manifest.json` records evidence and hashes. `binaries.json` pins dictionary/model/tokenizer/vendor inputs so a changed component requires review during bundling. Source catalog and `scripts/package-release-sources.py` prepare archives for publication alongside each binary release. Private local preservation is not public provision.
