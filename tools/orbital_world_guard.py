"""Back up existing local worlds before loading the composition without two mod registries."""

import hashlib
from pathlib import Path


SOURCE = "crates/horizon-client/src/game/world_loading.rs"
SOURCE_SHA256 = "dd00d259b4b8115ffd7c07c7c2f0ac6923ca6a6cbc882fe72e53f8ae33c9fee9"
WORLD_SOURCE = "crates/horizon-client/src/game/world/world_loading.rs"
WORLD_SOURCE_SHA256 = "d691848d0fedf80125241125d6ffda7b108d7840000aaa086c3ba6bd978fc6ef"
BACKUP_SOURCE = "crates/horizon-client/src/title/world_management.rs"
BACKUP_SOURCE_SHA256 = "a75f390ceec6b3311b627a0680d3144a3a4c871eb4eda8c939e8f2e137760e56"
LOCK = "        let session_lock = horizon_client::save::SessionLock::acquire(&save_root)?;\n"
EARLY = "        let create = create.as_ref();\n"
REQUEST = "pub(super) struct WorldRequest {"
GUARD = '''const ORBITAL_WORLD_MARKER: &str = ".lanminecraft-rust-mods-orbital-v1";

fn prepare_orbital_world(save_root: &std::path::Path, folder: &str) -> Result<()> {
    use horizon_client::title::world_management;
    use std::fs::{self, Metadata, OpenOptions};
    use std::io::ErrorKind;
    use std::os::windows::fs::MetadataExt;

    fn reparse(metadata: &Metadata) -> bool {
        metadata.file_attributes() & 0x400 != 0
    }

    let saves = std::env::current_dir()?.join("saves");
    if save_root.parent() != Some(saves.as_path())
        || save_root.file_name() != Some(std::ffi::OsStr::new(folder))
        || reparse(&fs::symlink_metadata(save_root)?)
    {
        return Err("The world is not a regular folder in the current game's saves directory".into());
    }

    let marker = save_root.join(ORBITAL_WORLD_MARKER);
    match fs::symlink_metadata(&marker) {
        Ok(meta) if meta.is_file() && meta.len() == 0 && !reparse(&meta) => return Ok(()),
        Ok(_) => return Err("The Minecraft Rust Mods world marker is invalid".into()),
        Err(error) if error.kind() == ErrorKind::NotFound => {}
        Err(error) => return Err(error.into()),
    }

    // The caller holds this world's session lock. A world with no level.dat
    // has never stored the removed registry and needs no snapshot.
    match fs::symlink_metadata(save_root.join("level.dat")) {
        Ok(meta) if meta.is_file() && !reparse(&meta) => {
            let backups = world_management::backups_path(&saves);
            match fs::symlink_metadata(&backups) {
                Ok(meta) if meta.is_dir() && !reparse(&meta) => {}
                Ok(_) => return Err("The game's backups path is not a regular directory".into()),
                Err(error) if error.kind() == ErrorKind::NotFound => {}
                Err(error) => return Err(error.into()),
            }
            log::info!("Minecraft Rust Mods: backing up {folder} before its first Orbital-era load");
            let size = world_management::backup(&saves, folder)?;
            if size == 0 {
                return Err("Horizon returned an empty world backup".into());
            }
            log::info!("Minecraft Rust Mods: backed up {folder} ({size} bytes)");
        }
        Ok(_) => return Err("The world's level.dat is not a regular file".into()),
        Err(error) if error.kind() == ErrorKind::NotFound => {}
        Err(error) => return Err(error.into()),
    }
    OpenOptions::new().write(true).create_new(true).open(marker)?.sync_all()?;
    Ok(())
}

'''
CHECK = '''        // Integrated servers set options.dedicated for compatibility too.
        // Protect local worlds by their fixed execution role, before fallback,
        // data fixes or legacy migration can change the on-disk bytes. This
        // backup-only lock ends here: the author's file fixer may remove
        // session.lock, then the original load acquires its lifetime lock.
        if options.execution_role
            != execution_role::ExecutionRole::DedicatedServer
        {
            let _backup_lock = horizon_client::save::SessionLock::acquire(&save_root)?;
            prepare_orbital_world(&save_root, &folder).map_err(|error| {
                format!("Minecraft Rust Mods could not protect world \\"{folder}\\" before opening: {error}")
            })?;
        }
'''


def patch(raw: bytes, *, split_world: bool = False) -> bytes:
    expected = WORLD_SOURCE_SHA256 if split_world else SOURCE_SHA256
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Horizon world-loading source changed before the Orbital save guard")
    source = raw.decode("utf-8")
    if source.count(LOCK) != 1 or source.count(REQUEST) != 1 or source.count(EARLY) != 1:
        raise ValueError("Horizon world-loading anchors changed")
    guard = GUARD
    if split_world:
        guard = guard.replace("let size = world_management::backup", "let (_, size) = world_management::backup")
    source = source.replace(REQUEST, guard + REQUEST).replace(EARLY, EARLY + CHECK)
    return source.encode("utf-8")


def apply(game_source: Path) -> None:
    target = game_source / SOURCE
    split_world = not target.exists()
    if split_world:
        target = game_source / WORLD_SOURCE
        backup = game_source / BACKUP_SOURCE
        if hashlib.sha256(backup.read_bytes()).hexdigest() != BACKUP_SOURCE_SHA256:
            raise ValueError("Horizon world backup changed before the Orbital save guard")
    target.write_bytes(patch(target.read_bytes(), split_world=split_world))
