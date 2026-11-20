"""Relocate Orbital 0.5.17 atlas tables into each receiver's resources."""

import hashlib
from pathlib import Path

from . import RECIPE, replace


AUTHOR_SOURCES = {
    "strike.rs": "1016db35b3b33e0ce4afcda62781b17c438220b6f15124565ee224fb1eccfa64",
    "strike/convergence.rs": "0b753e6c81405487c358ddb55b5e3611f5ccc5433e0324715e6df13bc289c07d",
    "strike/hu_blocks.rs": "2b325edaf43b4e02da18ccdf84f33598d8b30a6fda8d78aecdeb12ba189751b6",
    "strike/sb_lift.rs": "56816d82db57356ca1582ab116ceb82694a7a902075d2f7c1bea74a05d89403d",
    "strike/tsunami.rs": "fc099655f80188623c4cfe7888608774945b3861f08bb5289f791f7cd1f9817c",
    "strike/we_body.rs": "c1108dac425891ad886cd015efe29e2f4f28047c48f1585175161c7372261d87",
    "strike/we_fx.rs": "1762c1c629b242991f26c3f236179a13eb1d43655b5882105730dad6bc610ae8",
    "strike/we_path.rs": "a41270d5a117fcf7a047632273782fb7d6377ce469178e8d18850868ac4aeeaa",
}


def write(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8", newline="\n")


def apply(bundle: Path) -> None:
    src = bundle / "engine/src"
    checked = {}
    for name, digest in AUTHOR_SOURCES.items():
        raw = (src / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError(f"Orbital Laser 0.5.17 receiver atlas table changed: {name}")
        checked[name] = raw.decode("utf-8")

    for name, anchor in (("strike/convergence.rs", "fn cv_sprites()"),
                         ("strike/sb_lift.rs", "fn cross_of("),
                         ("strike/we_fx.rs", "const WEF_BK0:")):
        write(src / name, replace(checked[name], anchor, "pub(super) " + anchor))

    write(src / "strike.rs", checked["strike.rs"] +
          "\nmod atlas_latest;\npub(crate) use atlas_latest::{repack as repack_network_atlas, uses_atlas};\n")
    write(src / "strike/atlas_latest.rs", (RECIPE / "atlas_latest.rs").read_text(encoding="utf-8"))
    engine = src / "lib.rs"
    source = replace(engine.read_text(encoding="utf-8"),
                     "pub fn network_uses_atlas(shader: u8) -> bool { shader == 9 }",
                     "pub fn network_uses_atlas(shader: u8) -> bool { strike::uses_atlas(shader) }")
    write(engine, replace(source, "    if shader == 9 { strike::hu_repack(words); }",
                          "    strike::repack_network_atlas(shader, words);"))
