# Python native-extension dependency notices

Reviewed binaries: LanceDB 0.40.0 cp310-abi3-win_amd64 and cryptography 50.0.2 cp311-abi3-win_amd64. Both installed PYD files match the official wheel bytes. Wheel downloads match the application uv.lock hashes. Fingerprints are in binaries.json and checked during bundling.

## Evidence and scope

The original wheel SBOMs are included: LanceDB lists 800 components; cryptography lists 39 Rust components and OpenSSL 4.0.3. There are 822 distinct registry crates across the two Rust lists. Their source archives were downloaded and all SHA256 values matched the wheel SBOM. This is not a claim that every SBOM component survives target selection or linker elimination: the SBOM also includes non-Windows dependencies.

manifest.json records original license expressions, source URLs/checksums and preserved notices. Preserved notices include files from the corresponding upstream source commits. OpenSSL's official release source archive matches its wheel SBOM SHA256; original license/attribution files are in openssl-4.0.3/. Workspace-component notices are in lancedb/ and cryptography/.

## Review findings

- Preserve compound license conditions, notably the AWS-LC, ring, compression and Unicode notices. The application's AGPL does not replace these terms.
- colored 3.1.1 and option-ext 0.2.0 declare MPL-2.0. Their source URLs/checksums and original notices are supplied; release source availability must be maintained.
- self_cell offers Apache-2.0 OR GPL-2.0-only; the Apache option is available. BSL-1.0 in this inventory means Boost Software License, not Business Source License.
- r-efi 5.3.0/6.0.0 are reached only through getrandom's UEFI-specific dependency conditions. rustls-platform-verifier-android is gated by target_os=android. They are retained as SBOM evidence, not asserted as Windows-linked components.
- **kanaria 0.2.0:** source and LICENSE.txt are available at samunohito/kanaria (MIT, copyright 2019 samu no hito). The original notice and matching source commit are included.

Original sources: https://github.com/lancedb/lancedb/tree/v0.40.0 and https://github.com/pyca/cryptography/tree/50.0.2 . OpenSSL source location and hash are recorded in sbom.json.
