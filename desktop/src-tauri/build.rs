fn main() {
    let manifest = std::path::PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap());
    let source = manifest.join("../../sidecar/src/prtfm/localization/native.json");
    let catalog = if source.exists() { source } else { manifest.join("native-messages.json") };
    println!("cargo:rerun-if-changed={}", catalog.display());
    std::fs::copy(&catalog, std::path::PathBuf::from(std::env::var("OUT_DIR").unwrap()).join("native-messages.json"))
        .expect("Native UI translation catalog is required");
    tauri_build::build()
}
