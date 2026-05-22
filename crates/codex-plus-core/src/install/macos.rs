#[cfg(target_os = "macos")]
use std::fs;
#[cfg(target_os = "macos")]
use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
#[cfg(target_os = "macos")]
use std::path::Path;

use super::{
    InstallOptions, MANAGER_BINARY, MANAGER_NAME, MacosAppBundle, SILENT_BINARY, SILENT_NAME,
    install_root_or_default, macos_app_bundle_from_exe, option_or_current_exe,
};

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
    MacosAppBundle {
        app_path: install_root.join(format!("{display_name}.app")),
        info_plist: info_plist(display_name, executable_name, identifier_suffix),
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
        .and_then(|path| path.parent().map(Path::to_path_buf))
        .map(|path| path.join("codex-plus-plus.png"));
    if let Some(source) = source.filter(|path| path.exists()) {
        fs::copy(source, resources.join("codex-plus-plus.png"))?;
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn source_bundle_for_manager_runtime(manager: bool) -> Option<PathBuf> {
    let current_exe = std::env::current_exe().ok()?;
    let current_bundle = macos_app_bundle_from_exe(&current_exe)?;
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
  <string>codex-plus-plus.png</string>
  <key>LSUIElement</key>
  <true/>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
</dict>
</plist>"#
    )
}
