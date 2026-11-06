"""Keep PhysicsModHorizon's first-run screen and its mouse handler in sync."""

from hashlib import sha256
from pathlib import Path


SOURCE_HASHES = {
    "horizon-mixin.toml": "c8513953d5346aa2750c5f44d67f895f32b418e870e1ce6323b7f17ab8bd284f",
    "setup_draw.rs": "bb77df382c5a33c86b28010a7381eb8d1097dc955a0cee73565d8d6f414f3f63",
    "setup_click.rs": "338424b10339c4788fff593a191d67472567f7c1168183c54e8dcd0f2d553528",
}


def apply(bundle: Path) -> None:
    originals = {}
    for name, expected in SOURCE_HASHES.items():
        source = (bundle / name).read_bytes()
        if sha256(source).hexdigest() != expected:
            raise ValueError(f"Unexpected PhysicsModHorizon 0.18.0 source: {name}")
        originals[name] = source.decode("utf-8")

    manifest = originals["horizon-mixin.toml"]
    old_target = 'target = "horizon_client_lib::title::Title::draw"'
    binding = '{ name = "_title", ty = "&Title", value = "self" }'
    click_binding = '{ name = "_title", ty = "&mut Title", value = "&mut *self" }'
    if any(manifest.count(anchor) != 1 for anchor in (old_target, binding, click_binding)):
        raise ValueError("PhysicsModHorizon title hooks changed")
    # Both Horizon title paths call draw_with_items directly.
    manifest = manifest.replace(old_target, 'target = "horizon_client_lib::title::Title::draw_with_items"')
    for anchor in (binding, click_binding):
        manifest = manifest.replace(anchor, anchor.replace('"_title"', '"title"'))

    replacements = {"horizon-mixin.toml": manifest}
    for file_name, check in (
        ("setup_draw.rs", "crate::native_horizon_physics::setup_visible()"),
        ("setup_click.rs", "hp::setup_visible()"),
    ):
        source = originals[file_name]
        old_gate = f"if !{check} {{"
        if source.count(old_gate) != 1 or source.count("_title:") != 1:
            raise ValueError(f"PhysicsModHorizon setup handler changed: {file_name}")
        # Let Horizon handle its onboarding and other screens before showing
        # the mod's setup on the main title screen.
        replacements[file_name] = source.replace("_title:", "title:").replace(
            old_gate, f"if !matches!(&title.mode, Mode::Main) || !{check} {{"
        )

    for name, source in replacements.items():
        (bundle / name).write_text(source, encoding="utf-8", newline="\n")
