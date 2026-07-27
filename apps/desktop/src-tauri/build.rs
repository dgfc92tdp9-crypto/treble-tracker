fn main() {
    // Bake the repo path so the bundled app can find the virtual
    // environment it launches. Derived from the manifest location rather
    // than configured by hand, so it cannot be set to a stale path.
    let manifest = std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR");
    let repo = std::path::Path::new(&manifest)
        .ancestors()
        .nth(3)
        .expect("repo root above apps/desktop/src-tauri");
    println!("cargo:rustc-env=TREBLE_REPO={}", repo.display());
    tauri_build::build()
}
