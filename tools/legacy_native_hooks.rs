//! Launch the preserved source mixins at the same frame, HUD and input boundaries.
//!
//! The build-time mixin planner supplies code at the legacy hook points.
//! Native and Wasm extensions using the upstream typed API keep their own dispatch.

use super::*;

const LEGACY_SETTINGS_OWNERS: &[&str] = &[];

fn legacy_setting_key(key: &str) -> bool {
    if key.is_empty() || key.ends_with('.')
        || !key.bytes().all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte)) {
        return false;
    }
    let stem = key.split('.').next().unwrap_or_default().to_ascii_lowercase();
    !matches!(stem.as_str(), "con" | "prn" | "aux" | "nul")
        && !(stem.len() == 4 && matches!(&stem[..3], "com" | "lpt") && stem.as_bytes()[3].is_ascii_digit())
}

pub(in crate::game) struct NativeMod {
    pub id: String,
    pub title: String,
    pub description: String,
    pub keys: Vec<[String; 3]>,
}

impl Game<'_> {
    pub(in crate::game) fn native_frame(&mut self, elapsed: Duration) {
        let _ = elapsed;
    }

    pub(in crate::game) fn native_tick(&mut self) {}

    pub(in crate::game) fn legacy_server_tick(&mut self) {}

    pub(in crate::game) fn native_net_from_client(
        &mut self,
        player: u128,
        channel: &str,
        data: &[u8],
        epoch: &std::sync::Arc<horizon_client::raw_payloads::connection::Epoch>,
    ) {
        let _ = (player, channel, data, epoch);
    }

    pub(in crate::game) fn native_net_from_server(
        &mut self,
        channel: &str,
        data: &[u8],
        epoch: &std::sync::Arc<horizon_client::raw_payloads::connection::Epoch>,
    ) {
        let _ = (channel, data, epoch);
    }

    pub(in crate::game) fn native_hud(&mut self, batch: &mut HudBatch, corners: &mut Corners) {
        let _ = (batch, corners);
    }

    pub(in crate::game) fn native_key(&mut self, key: &str, pressed: bool) {
        let _ = (key, pressed);
    }

    pub(in crate::game) fn native_interact_entity(
        &mut self,
        entity: i32,
        kind: &str,
        claimed: &mut bool,
    ) {
        let _ = (entity, kind, claimed);
    }

    pub(in crate::game) fn native_wheel(&mut self, steps: f32, claimed: &mut bool) {
        let _ = (steps, claimed);
    }

    pub(in crate::game) fn legacy_wheel_claimed(&mut self, steps: f32) -> bool {
        let mut claimed = false;
        self.native_wheel(steps, &mut claimed);
        claimed
    }

    pub(in crate::game) fn native_mods(&mut self, mods: &mut Vec<NativeMod>) {
        let _ = mods;
    }

    pub(in crate::game) fn native_settings(&mut self, mod_id: &str, buttons: &mut Vec<String>) {
        let _ = (mod_id, buttons);
    }

    pub(in crate::game) fn native_settings_click(&mut self, mod_id: &str, button: usize) {
        let _ = (mod_id, button);
    }

    pub(in crate::game) fn legacy_register_commands(
        &mut self,
        commands: &mut Vec<mod_api::Command>,
    ) {
        let _ = commands;
    }

    pub(in crate::game) fn legacy_run_mod_command(
        &mut self,
        source: &mod_api::CommandSource,
        command: &mod_api::CommandInvocation,
        ran: &mut bool,
    ) {
        let _ = (source, command, ran);
    }

    pub(in crate::game) fn legacy_command(&mut self, command: &str, ran: &mut bool) {
        let _ = (command, ran);
    }

    pub(in crate::game) fn native_commands(&mut self, commands: &mut Vec<&'static str>) {
        let _ = commands;
    }

    pub(in crate::game) fn legacy_panel_event(&mut self, mod_id: &str, event: &PanelEvent) {
        let _ = (mod_id, event);
    }

    pub(in crate::game) fn native_on(&self, mod_id: &str) -> bool {
        !self.settings.mods.disabled.contains(mod_id)
    }

    pub(in crate::game) fn native_playing(&self) -> bool {
        self.screen == Screen::Playing
            && self.title.is_none()
            && self.sign_edit.is_none()
            && self.book.is_none()
    }

    fn legacy_setting_dir(&self, mod_id: &str, key: &str) -> Option<std::path::PathBuf> {
        if LEGACY_SETTINGS_OWNERS.binary_search(&mod_id).is_err() || !legacy_setting_key(key)
            || mod_api::content::crash::current_native_mod().is_some_and(|owner| owner != mod_id) {
            return None;
        }
        Some(self.native_data_dir(mod_id))
    }

    // The shipped source overlays used per-key files before typed settings
    // existed. Preserve their files without changing the typed host's registry.
    pub(in crate::game) fn native_setting(&self, mod_id: &str, key: &str) -> Option<String> {
        let directory = self.legacy_setting_dir(mod_id, key)?;
        horizon_world::native_load_context::read_setting(&directory, key)
    }

    pub(in crate::game) fn native_set_setting(&self, mod_id: &str, key: &str, value: &str) {
        let Some(directory) = self.legacy_setting_dir(mod_id, key) else { return; };
        if let Err(error) = std::fs::create_dir_all(&directory)
            .and_then(|()| std::fs::write(directory.join(key), value)) {
            log::warn!("Could not save {mod_id} setting {key}: {error}");
        }
    }

    pub(in crate::game) fn legacy_text_width(&self, text: &str) -> f32 {
        HudBatch::text_width(&self.gui.atlas, text)
    }

    pub(in crate::game) fn native_mod_key(&self, mod_id: &str, key: &str, default: &str) -> String {
        self.settings
            .mods
            .keys
            .get(mod_id)
            .and_then(|keys| keys.get(key))
            .map_or(default, String::as_str)
            .to_owned()
    }

    pub(super) fn legacy_native_mod_rows(&mut self) -> Vec<Installed> {
        let mut mods = Vec::new();
        self.native_mods(&mut mods);
        mods.into_iter()
            .map(|m| {
                log::info!("LANMinecraft native mod registered: {}", m.id);
                let settings = self.legacy_native_settings(&m.id);
                let mappings = m
                    .keys
                    .into_iter()
                    .map(|[id, label, key]| KeyMapping {
                        id,
                        label,
                        key,
                        ..KeyMapping::default()
                    })
                    .collect();
                Installed {
                    id: m.id,
                    title: m.title,
                    description: m.description,
                    problem: None,
                    dev: false,
                    native: Some(NativePage { mappings, settings }),
                }
            })
            .collect()
    }

    pub(super) fn has_legacy_native_mod(&mut self, id: &str) -> bool {
        let mut mods = Vec::new();
        self.native_mods(&mut mods);
        mods.iter().any(|m| m.id == id)
    }

    pub(super) fn legacy_native_settings(&mut self, id: &str) -> Vec<NativeSetting> {
        let mut buttons = Vec::new();
        self.native_settings(id, &mut buttons);
        buttons
            .into_iter()
            .enumerate()
            .map(|(button, label)| NativeSetting {
                key: button.to_string(),
                label,
                tooltip: None,
                value: NativeSettingValue::LegacyAction,
            })
            .collect()
    }
}
