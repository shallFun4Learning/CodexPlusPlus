#[cfg(target_os = "macos")]
use std::fs;
#[cfg(target_os = "macos")]
use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
#[cfg(target_os = "macos")]
use std::path::Path;

use super::{
    InstallOptions, MANAGER_BINARY, MANAGER_NAME, MacosAppBundle, SILENT_BINARY, SILENT_NAME,
    install_root_or_default, macos_app_bundle_from_exe, macos_bundled_silent_app_from_bundle,
    option_or_current_exe,
};

const ICON_NAME: &str = "codex-plus-plus.icns";

pub fn build_app_bundle(options: &InstallOptions, manager: bool) -> MacosAppBundle {
    let install_root = install_root_or_default(options);
    let display_name = if manager { MANAGER_NAME } else { SILENT_NAME };
    let executable_name = if manager {
        "CodexPlusPlusManager"
    } else {
        "CodexPlusPlus"
    };
    let binary = if manager {
        MANAGER_BINARY
    } else {
        SILENT_BINARY
    };
    let target = option_or_current_exe(
        if manager {
            &options.manager_path
        } else {
            &options.launcher_path
        },
        binary,
    );
    let identifier_suffix = if manager { ".manager" } else { "" };
    let app_path = install_root.join(format!("{display_name}.app"));
    MacosAppBundle {
        app_path: app_path.clone(),
        info_plist: info_plist(display_name, executable_name, identifier_suffix),
        launch_target: target.clone(),
        launch_script: format!("#!/bin/sh\nexec \"{}\"\n", target.to_string_lossy()),
        source_app: source_bundle_for_manager_runtime(manager),
    }
}

#[cfg(target_os = "macos")]
pub fn install_app_bundles(options: &InstallOptions) -> anyhow::Result<()> {
    write_bundle(&build_app_bundle(options, false))?;
    write_bundle(&build_app_bundle(options, true))?;
    Ok(())
}

#[cfg(target_os = "macos")]
pub fn uninstall_app_bundles(options: &InstallOptions) -> anyhow::Result<()> {
    let install_root = install_root_or_default(options);
    for name in [SILENT_NAME, MANAGER_NAME] {
        let app = install_root.join(format!("{name}.app"));
        if app.exists() {
            fs::remove_dir_all(app)?;
        }
    }
    Ok(())
}

#[cfg(not(target_os = "macos"))]
pub fn install_app_bundles(_options: &InstallOptions) -> anyhow::Result<()> {
    anyhow::bail!("macOS app bundles are only supported on macOS")
}

#[cfg(not(target_os = "macos"))]
pub fn uninstall_app_bundles(_options: &InstallOptions) -> anyhow::Result<()> {
    anyhow::bail!("macOS app bundles are only supported on macOS")
}

#[cfg(target_os = "macos")]
fn write_bundle(bundle: &MacosAppBundle) -> anyhow::Result<()> {
    if let Some(source_app) = bundle.source_app.as_ref().filter(|source| source.exists()) {
        if source_app != &bundle.app_path {
            if bundle.app_path.exists() {
                fs::remove_dir_all(&bundle.app_path)?;
            }
            copy_app_bundle(source_app, &bundle.app_path)?;
            return Ok(());
        }
        return Ok(());
    }
    let self_target = bundle
        .app_path
        .join("Contents")
        .join("MacOS")
        .join(executable_name_from_plist(&bundle.info_plist));
    if bundle.launch_target == self_target {
        anyhow::bail!(
            "refusing to install self-referential app bundle at {}",
            bundle.app_path.display()
        );
    }
    let contents = bundle.app_path.join("Contents");
    let macos = contents.join("MacOS");
    let resources = contents.join("Resources");
    fs::create_dir_all(&macos)?;
    fs::create_dir_all(&resources)?;
    fs::write(contents.join("Info.plist"), &bundle.info_plist)?;
    let executable = macos.join(executable_name_from_plist(&bundle.info_plist));
    fs::write(&executable, &bundle.launch_script)?;
    let mut permissions = fs::metadata(&executable)?.permissions();
    permissions.set_mode(0o755);
    fs::set_permissions(executable, permissions)?;
    copy_icon(&resources)?;
    Ok(())
}

#[cfg(target_os = "macos")]
fn copy_icon(resources: &Path) -> anyhow::Result<()> {
    let source = std::env::current_exe()
        .ok()
        .and_then(|path| macos_app_bundle_from_exe(&path))
        .map(|bundle| bundle.join("Contents").join("Resources").join(ICON_NAME));
    if let Some(source) = source.filter(|path| path.exists()) {
        fs::copy(source, resources.join(ICON_NAME))?;
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn source_bundle_for_manager_runtime(manager: bool) -> Option<PathBuf> {
    let current_exe = std::env::current_exe().ok()?;
    source_bundle_for_runtime_from_exe(&current_exe, manager)
}

#[cfg(target_os = "macos")]
fn source_bundle_for_runtime_from_exe(current_exe: &Path, manager: bool) -> Option<PathBuf> {
    let current_bundle = macos_app_bundle_from_exe(current_exe)?;
    let applications_dir = current_bundle.parent()?.to_path_buf();
    let current_name = current_bundle.file_name()?.to_str()?;
    let expected_current = if manager {
        format!("{MANAGER_NAME}.app")
    } else {
        format!("{SILENT_NAME}.app")
    };

    if current_name == expected_current {
        return Some(current_bundle);
    }

    if current_name == format!("{MANAGER_NAME}.app") {
        let sibling = applications_dir.join(format!("{SILENT_NAME}.app"));
        if sibling.exists() {
            return Some(sibling);
        }
        let bundled = macos_bundled_silent_app_from_bundle(&current_bundle);
        if bundled.exists() {
            return Some(bundled);
        }
    }

    None
}

#[cfg(not(target_os = "macos"))]
fn source_bundle_for_manager_runtime(_manager: bool) -> Option<PathBuf> {
    None
}

#[cfg(target_os = "macos")]
fn copy_app_bundle(source: &Path, destination: &Path) -> anyhow::Result<()> {
    let status = std::process::Command::new("cp")
        .arg("-R")
        .arg(source)
        .arg(destination)
        .status()?;
    if status.success() {
        Ok(())
    } else {
        anyhow::bail!(
            "failed to copy app bundle from {} to {}",
            source.display(),
            destination.display()
        )
    }
}

#[cfg(target_os = "macos")]
fn executable_name_from_plist(plist: &str) -> String {
    plist
        .split("<key>CFBundleExecutable</key>")
        .nth(1)
        .and_then(|tail| tail.split("<string>").nth(1))
        .and_then(|tail| tail.split("</string>").next())
        .unwrap_or("CodexPlusPlus")
        .to_string()
}

#[cfg(all(test, target_os = "macos"))]
mod tests {
    use super::source_bundle_for_runtime_from_exe;
    use tempfile::tempdir;

    #[test]
    fn manager_runtime_uses_bundled_silent_app_when_sibling_is_missing() {
        let temp = tempdir().expect("create tempdir");
        let manager_exe = temp
            .path()
            .join("Applications")
            .join("Codex++ 管理工具.app")
            .join("Contents")
            .join("MacOS")
            .join("CodexPlusPlusManager");
        let bundled_silent = temp
            .path()
            .join("Applications")
            .join("Codex++ 管理工具.app")
            .join("Contents")
            .join("Resources")
            .join("Codex++.app");

        std::fs::create_dir_all(manager_exe.parent().expect("manager parent"))
            .expect("create manager bundle");
        std::fs::write(&manager_exe, "").expect("write manager executable");
        std::fs::create_dir_all(&bundled_silent).expect("create bundled silent app");

        assert_eq!(
            source_bundle_for_runtime_from_exe(&manager_exe, false),
            Some(bundled_silent)
        );
    }

    #[test]
    fn manager_runtime_prefers_installed_silent_app_over_bundled_template() {
        let temp = tempdir().expect("create tempdir");
        let manager_exe = temp
            .path()
            .join("Applications")
            .join("Codex++ 管理工具.app")
            .join("Contents")
            .join("MacOS")
            .join("CodexPlusPlusManager");
        let installed_silent = temp
            .path()
            .join("Applications")
            .join("Codex++.app");
        let bundled_silent = temp
            .path()
            .join("Applications")
            .join("Codex++ 管理工具.app")
            .join("Contents")
            .join("Resources")
            .join("Codex++.app");

        std::fs::create_dir_all(manager_exe.parent().expect("manager parent"))
            .expect("create manager bundle");
        std::fs::write(&manager_exe, "").expect("write manager executable");
        std::fs::create_dir_all(&installed_silent).expect("create installed silent app");
        std::fs::create_dir_all(&bundled_silent).expect("create bundled silent app");

        assert_eq!(
            source_bundle_for_runtime_from_exe(&manager_exe, false),
            Some(installed_silent)
        );
    }
}

fn info_plist(display_name: &str, executable_name: &str, identifier_suffix: &str) -> String {
    let version = crate::version::VERSION;
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>{display_name}</string>
  <key>CFBundleDisplayName</key>
  <string>{display_name}</string>
  <key>CFBundleIdentifier</key>
  <string>com.bigpizzav3.codexplusplus{identifier_suffix}</string>
  <key>CFBundleVersion</key>
  <string>{version}</string>
  <key>CFBundleShortVersionString</key>
  <string>{version}</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleExecutable</key>
  <string>{executable_name}</string>
  <key>CFBundleIconFile</key>
  <string>{ICON_NAME}</string>
  <key>LSUIElement</key>
  <true/>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
</dict>
</plist>"#
    )
}
