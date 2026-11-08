"""Bridge the pack's native mod preset entries and Horizon's personal mod settings."""

import hashlib


SETTINGS = "crates/horizon-client-render/src/settings.rs"
SETTINGS_SHA256 = "02c85ee6ec89e807e6f7fb6c9d8e5d611e7deafcff7bc70358d69710bced7419"


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

// options.txt mapping, native mod ID, action ID, author's fallback.
// Waypoints' alternatives are applied by the explicit pack preset. When old
// options lack a row, the existing personal Mods UI mapping remains authoritative.
const PACK_MOD_BINDINGS: [(&str, &str, &str, &str); 3] = [
    ("key_orbital.wheel:", "orbital", "wheel", "key.keyboard.insert"),
    ("key_waypoints.create:", "waypoints", "create", "key.keyboard.n"),
    ("key_waypoints.list:", "waypoints", "list", "key.keyboard.m"),
];

fn read_pack_mod_bindings(mods: &mut crate::mod_list::ModList, text: &str) {
    for (prefix, id, action, _) in PACK_MOD_BINDINGS {
        if let Some(key) = text.lines()
            .filter_map(|line| line.strip_prefix(prefix))
            .last()
            .and_then(|name| crate::options_file::canonical_key(name).ok())
        {
            mods.keys.entry(id.to_owned()).or_default().insert(action.to_owned(), key);
        }
    }
}

fn write_pack_mod_bindings(mods: &crate::mod_list::ModList, text: &mut String) {
    for (prefix, id, action, fallback) in PACK_MOD_BINDINGS {
        let key = mods.keys.get(id).and_then(|keys| keys.get(action));
        text.push_str(prefix);
        text.push_str(key.map_or(fallback, String::as_str));
        text.push('\\n');
    }
}''',
        ),
        (
            'let mods = path\n            .parent()',
            'let mut mods = path\n            .parent()',
        ),
        (
            '                extras.read_old_options(&extras_text, &text);',
            '''                extras.read_old_options(&extras_text, &text);
                // Only explicitly stored preset rows override personal mod keys.
                read_pack_mod_bindings(&mut mods, &text);''',
        ),
        (
            '        self.to_file().serialize()\n    }\n    pub fn save',
            '''        let mut text = self.to_file().serialize();
        write_pack_mod_bindings(&self.mods, &mut text);
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
