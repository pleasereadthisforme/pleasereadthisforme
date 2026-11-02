# Licensing scope

The project's own application source, build scripts and required application assets in this repository are licensed under the GNU Affero General Public License version 3 only (`AGPL-3.0-only`), unless a file or component has a separate license notice. The complete license is in [LICENSE](LICENSE). The software is provided without warranty, as described there.

This includes our motion implementation in `sidecar/src/prtfm/server/activity_ui/characters.js`, its associated first-party UI code, and these required mascot sprites in that UI directory:

- `character-user-typing-sheet-v7.png`
- `character-bot-typing-approved-v1.png`
- `character-bot-question-v4.png`
- `character-bot-papers-v5.png`
- `character-bot-idle-v8.png`

These images are not subject to a separate noncommercial or no-derivatives restriction. Copying, modification and commercial redistribution are permitted under the applicable AGPL terms. The grant concerns our concrete assets and code, not the general idea of a stick figure or a type of movement.

Third-party components retain their own licenses. In particular, the Tauri-derived NSIS template has separate MIT licensing and attribution in `desktop/installer/`. Dependencies, runtimes, fonts, models and external tools are not relicensed by this document. Their notices and applicable source obligations must be satisfied separately.

Private development material under `dev/`, third-party reference media and separately managed promotional materials are outside this repository's first-party application grant. Identical copies of the listed mascot sprites retain their AGPL permission when used on the promotional site; this does not license that entire separate repository.

See [BRAND_USAGE.md](BRAND_USAGE.md) for distinguishing official releases from independent projects. Brand guidance does not add a noncommercial restriction or remove the rights granted by AGPL.

This licensing decision does not certify that an existing installer contains every required third-party notice or corresponding-source link. Release-specific dependency review and source delivery remain separate release tasks.

The [Reviewed third-party notice supplement](runtime/reviewed-notices/README.md) records original notices, actual component versions and source locations. The bundle build verifies binary fingerprints before including it.
