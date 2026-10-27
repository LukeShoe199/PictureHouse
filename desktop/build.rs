fn main() {
    // App commands the console page may call (see capabilities/console.json).
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(&["pick_folder"])),
    )
    .expect("failed to run tauri build script");
}
