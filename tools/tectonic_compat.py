"""Keep Tectonic's world activation before Horizon's generator cache lookup."""

from hashlib import sha256
from pathlib import Path


SOURCE = "crates/horizon-client/src/vanillagen.rs"
SOURCE_SHA256 = "a5d971e17e3762b651b24a68203352a8afc780a539bba2c6cce88c6245e93d78"
OLD_TARGET = "horizon_client_lib::vanillagen::shared_data_with"
ACTIVATION_TARGET = "horizon_client_lib::vanillagen::select_worldgen_overrides"


def prepare_source(workspace: Path) -> None:
    path = workspace / SOURCE
    original = path.read_bytes()
    if sha256(original).hexdigest() != SOURCE_SHA256:
        raise ValueError("Horizon world generation source changed before the Tectonic port")
    source = original.decode("utf-8")
    before = """    registry: &'static BlockRegistry,
) -> crate::Result<Arc<GeneratorData>> {
    let mut cache = SHARED_DATA.lock().unwrap_or_else(|e| e.into_inner());"""
    insertion = source.index("impl ChunkGenerator for VanillaTerrain {")
    world_data = source[:insertion]
    if world_data.count(before) != 1:
        raise ValueError("Horizon world generation cache lookup changed")
    # Keep the public replaceable function and its cache behavior. The mixin
    # adds world activation to this ordinary call before anything is cached.
    after = before.replace("    let mut cache", "    let overrides = select_worldgen_overrides(overrides);\n    let mut cache")
    selection = """fn select_worldgen_overrides(overrides: &WorldgenOverrides) -> &WorldgenOverrides {
    overrides
}

"""
    patched = world_data.replace(before, after) + selection + source[insertion:]
    path.write_text(patched, encoding="utf-8", newline="\n")


def route_manifest(text: str) -> str:
    before = f'target = "{OLD_TARGET}"'
    if text.count(before) != 1:
        raise ValueError("Tectonic activate-world hook changed")
    return text.replace(before, f'target = "{ACTIVATION_TARGET}"')
