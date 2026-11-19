"""Disable author-promoted server seeding without reading or editing saved rows."""

from hashlib import sha256
from pathlib import Path
import re


SOURCE = "crates/horizon-client/src/multiplayer.rs"
OFFICIAL_SOURCE = "crates/horizon-official-servers/src/lib.rs"
OFFICIAL_SOURCE_SHA256 = "c700d2836a7cb5e9ff21166e23cbe44f1cde7c3d1b8c70a6c6ffc57b25edecff"
STARTUP_IMPORT = "use horizon_platform::hooks::{GetTheGame, Hooks, Started};"
STARTUP_HOOK = '''    /// Someone is playing (not a server, a benchmark or an automated run): their list, once.
    fn started(&self, game: &Started<'_>) {
        update(game.game_dir);
    }

'''
FUNCTION_SHA256 = {
    "add_official_server": "83f5eb68b637e0f6858f319b874f0cae0f264e148378f47fcf27312053eb89ec",
    "add_partner_servers": "5d62a13ea0624f509285f39798b96f4699211307a38d7281d4493b11bf071811",
}


def adapt(source: str) -> str:
    patches = []
    for name, expected in FUNCTION_SHA256.items():
        pattern = re.compile(r"(?ms)^pub fn " + re.escape(name) +
                             r"\(game_dir: &Path\) -> std::io::Result<\(\)> \{.*?^\}")
        matches = list(pattern.finditer(source))
        if (source.count(f"pub fn {name}(") != 1 or len(matches) != 1 or
                sha256(matches[0].group().encode("utf-8")).hexdigest() != expected):
            raise ValueError(f"Horizon server seeding function changed before mandatory policy preparation: {name}")
        match = matches[0]
        replacement = f"pub fn {name}(_game_dir: &Path) -> std::io::Result<()> {{\n    Ok(())\n}}"
        patches.append((match.start(), match.end(), replacement))
    # Validate both complete author functions before changing any byte. Other
    # multiplayer/Steam code is governed by the signed base overlay verifier.
    for start, end, replacement in sorted(patches, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source


def adapt_official(source: bytes) -> bytes:
    if sha256(source).hexdigest() != OFFICIAL_SOURCE_SHA256:
        raise ValueError("Horizon official server startup hook changed before mandatory policy preparation")
    text = source.decode("utf-8")
    if text.count(STARTUP_HOOK) != 1 or text.count(STARTUP_IMPORT) != 1:
        raise ValueError("Horizon official server startup policy anchors changed")
    # The official crate also supplies get_the_game. Only remove its automatic
    # servers.dat mutation, which now includes withdrawing old partner entries.
    return text.replace(STARTUP_HOOK, "").replace(
        STARTUP_IMPORT, "use horizon_platform::hooks::{GetTheGame, Hooks};").encode("utf-8")


def prepare_source(workspace: Path) -> None:
    official = workspace / OFFICIAL_SOURCE
    path = official if official.exists() else workspace / SOURCE
    if path.is_symlink() or path.is_junction():
        raise ValueError("The server seed policy requires an ordinary prepared source file")
    source = path.read_bytes()
    prepared = adapt_official(source) if path == official else adapt(source.decode("utf-8")).encode("utf-8")
    path.write_bytes(prepared)
