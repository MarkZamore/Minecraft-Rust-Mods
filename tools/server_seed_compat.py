"""Disable author-promoted server seeding without reading or editing saved rows."""

from hashlib import sha256
from pathlib import Path
import re


SOURCE = "crates/horizon-client/src/multiplayer.rs"
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


def prepare_source(workspace: Path) -> None:
    path = workspace / SOURCE
    if path.is_symlink() or path.is_junction():
        raise ValueError("The server seed policy requires an ordinary prepared source file")
    source = path.read_bytes().decode("utf-8")
    path.write_bytes(adapt(source).encode("utf-8"))
