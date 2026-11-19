"""Run RightClickHarvest 0.2.1 through Horizon's authoritative use-hand path."""

from hashlib import sha256
from pathlib import Path


REVISION = "f00125f61f794aaa3d4e950d301f353b35b6ee3d"
MANIFEST_SHA = "e12e0c293fffa0633fc884484d5e40190644e68dd8d1ea2c3340fe6e2fd27669"
HARVEST_SHA = "26bb58f0ea19b123bb7fd2cfc495588e243436e616f5fd242a773780486b1744"


def apply(bundle: Path, *, revision: str, version: str) -> None:
    if revision != REVISION or version != "0.2.1":
        return
    manifest = bundle / "horizon-mixin.toml"
    harvest = bundle / "harvest.rs"
    if (sha256(manifest.read_bytes()).hexdigest(), sha256(harvest.read_bytes()).hexdigest()) != (
        MANIFEST_SHA, HARVEST_SHA,
    ):
        raise ValueError("RightClickHarvest 0.2.1 input changed")
    materialized = manifest.read_text(encoding="utf-8")
    beginning, marker, _ = materialized.partition("# The block's own right-click begin")
    if not marker:
        raise ValueError("RightClickHarvest right-click hook is absent")
    manifest.write_text(beginning + """# Current Horizon's authority handles each hand through use_hand.
[[injection]]
id = "use-hand"
kind = "HEAD"
cancellable = true
target = "horizon_client::game::Game<'_>::use_hand"
helper = "harvest.rs"
bindings = [
  { name = "game", ty = "&mut crate::game::Game<'_>", value = "&mut *self" },
  { name = "block_hit", ty = "Option<&Hit>", value = "block_hit" },
]
""", encoding="utf-8", newline="\n")
    code = harvest.read_text(encoding="utf-8")
    _, marker, body = code.partition("    use crate::game::native_rightharvest as m;\n    let _ = face;\n")
    if not marker or body.count("        return;\n") != 3 or not body.endswith("    *canceled = true;\n}\n"):
        raise ValueError("RightClickHarvest crop behavior changed")
    body = body.replace("        return;\n", "        return ControlFlow::Continue(());\n")
    body = body.removesuffix("    *canceled = true;\n}\n") + "    ControlFlow::Break(Ok(true))\n}\n"
    harvest.write_text("""\
fn rightharvest_use_hand(
    game: &mut crate::game::Game<'_>,
    block_hit: Option<&Hit>,
) -> ::core::ops::ControlFlow<Result<bool>> {
    use crate::game::native_rightharvest as m;
    use ::core::ops::ControlFlow;

    let Some(hit) = block_hit else { return ControlFlow::Continue(()); };
    let pos = hit.pos;
""" + body, encoding="utf-8", newline="\n")
