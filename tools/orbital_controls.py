"""Persist the pack's Orbital Laser preset entry through Horizon's mod settings."""

import hashlib


SETTINGS = "crates/horizon-client-render/src/settings.rs"
SETTINGS_SHA256 = "7868b708ddf7e0243458b33e904484a3b5a336c698e5620533df9172599a120d"


def apply(stage):
    path = stage / SETTINGS
    original = path.read_bytes()
    if hashlib.sha256(original).hexdigest() != SETTINGS_SHA256:
        raise ValueError(f"Orbital controls overlay source has changed: {SETTINGS}")
    source = original.decode("utf-8")
    changes = (
        (
            'pub use horizon_client_core::sound_source::SOUND_SOURCES;',
            '''pub use horizon_client_core::sound_source::SOUND_SOURCES;

const ORBITAL_WHEEL_OPTION: &str = "key_orbital.wheel:";
const ORBITAL_WHEEL_DEFAULT: &str = "key.keyboard.insert";''',
        ),
        (
            'let mods = path\n            .parent()',
            'let mut mods = path\n            .parent()',
        ),
        (
            '                extras.read_old_options(&extras_text, &text);',
            '''                extras.read_old_options(&extras_text, &text);
                // The LANMinecraft preset writes this mapping into options.txt.
                // Old saves keep the existing Mods UI binding until a preset is applied.
                if let Some(key) = text
                    .lines()
                    .filter_map(|line| line.strip_prefix(ORBITAL_WHEEL_OPTION))
                    .last()
                    .and_then(|name| crate::options_file::canonical_key(name).ok())
                {
                    mods.keys
                        .entry("orbital".to_owned())
                        .or_default()
                        .insert("wheel".to_owned(), key);
                }''',
        ),
        (
            '        self.to_file().serialize()\n    }\n    pub fn save',
            '''        let mut text = self.to_file().serialize();
        let key = self.mods.keys.get("orbital").and_then(|keys| keys.get("wheel"));
        text.push_str(ORBITAL_WHEEL_OPTION);
        text.push_str(key.map_or(ORBITAL_WHEEL_DEFAULT, String::as_str));
        text.push('\\n');
        text
    }
    pub fn save''',
        ),
    )
    for before, after in changes:
        if source.count(before) != 1:
            raise ValueError(f"Expected one Orbital controls anchor: {before!r}")
        source = source.replace(before, after)
    path.write_bytes(source.encode("utf-8"))
