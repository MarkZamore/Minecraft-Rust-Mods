"""Preserve PhysicsModHorizon startup clicks and adapt its native shader hook."""

from hashlib import sha256
from pathlib import Path


SOURCE_HASHES = {
    "horizon-mixin.toml": "c8513953d5346aa2750c5f44d67f895f32b418e870e1ce6323b7f17ab8bd284f",
    "setup_draw.rs": "bb77df382c5a33c86b28010a7381eb8d1097dc955a0cee73565d8d6f414f3f63",
    "setup_click.rs": "338424b10339c4788fff593a191d67472567f7c1168183c54e8dcd0f2d553528",
    "shader.rs": "39cc299b6ca721f81959e27fc20250bd7cd9546a2f47c4c9c97945332131d727",
    "sway.rs": "434d1577162330f64212e1298c4aba3ca5d5f9481331b666eb02eceddc3e7dfa",
}

MATERIAL_SOURCE = "crates/horizon-client-render/src/gfx/native_material.rs"
MATERIAL_SHA256 = "23dffc59507996590944df8ea51585fa290430f30a18d4580dea9add1c7a537d"


def adapt_sway(source: str) -> str:
    # Physics computes its built-in water's look in the game's gamma space.
    # Horizon now converts a material's output back from its selected API line
    # before returning it. Wrap that final result, keeping line_in/line_out and
    # the pack's material calculation unchanged (including experimental 0.2).
    start = source.index("pub fn patch(entries: &str, source: &str) -> Option<String> {")
    end = source.index("// ---------------------------------------------------------------------------------------------", start)
    patch = source[start:end]
    patch = replace_once(patch,
        '    const COLOR_END: &str = ")).color;";',
        '''    const COLOR_END: &str = ")).color;";
    const GAMMA_OUT: &str = "return vec4(horizon_line_out(out.rgb), out.a);";''')
    patch = replace_once(patch,
        '    let shade = builtin && entries.matches(COLOR).count() == 1 && entries.matches(COLOR_END).count() == 1;',
        '''    let legacy_shade = entries.matches(COLOR).count() == 1 && entries.matches(COLOR_END).count() == 1;
    let gamma_shade = entries.matches(GAMMA_OUT).count() == 1;
    let shade = builtin && (legacy_shade || gamma_shade);''')
    patch = replace_once(patch,
        '''    if shade {
        patched = patched''',
        '''    if shade && gamma_shade {
        patched = patched.replace(GAMMA_OUT,
            "return horizon_physics_surface(in.layer, in.position, in.light, in.lightmap, in.hp_open, vec4(horizon_line_out(out.rgb), out.a));");
    } else if shade && legacy_shade {
        patched = patched''')
    return source[:start] + patch + source[end:]


def replace_once(source: str, before: str, after: str) -> str:
    if source.count(before) != 1:
        raise ValueError(f"PhysicsModHorizon shader anchor changed: {before[:100]!r}")
    return source.replace(before, after)


def prepare_source(workspace: Path) -> None:
    path = workspace / MATERIAL_SOURCE
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != MATERIAL_SHA256:
        raise ValueError("Horizon material shader source changed before the Physics compatibility port")
    # Mixins 0.6.0 requires named function arguments. Keep the tuple API and
    # every caller unchanged; unpack it inside the original function instead.
    source = replace_once(raw.decode("utf-8"),
        "    (vertex, displaced): (bool, bool),\n    api: horizon_shader_api::Api,\n) -> String {",
        "    vertex_flags: (bool, bool),\n    api: horizon_shader_api::Api,\n) -> String {\n    let (vertex, displaced) = vertex_flags;")
    path.write_text(source, encoding="utf-8", newline="\n")


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

    shader_bindings = '''  { name = "vertex", ty = "bool", value = "vertex" },
  { name = "displaced", ty = "bool", value = "displaced" },'''
    # Restrict the binding update to this hook; other Physics hooks also have
    # vertex/displaced arguments belonging to different author APIs.
    start = manifest.index('id = "shader"')
    end = manifest.index('[[injection]]', start)
    shader_hook = replace_once(manifest[start:end], shader_bindings,
        '''  { name = "vertex_flags", ty = "(bool, bool)", value = "vertex_flags" },
  { name = "api", ty = "horizon_shader_api::Api", value = "api" },''')
    manifest = manifest[:start] + shader_hook + manifest[end:]
    shader = replace_once(originals["shader.rs"], "    displaced: bool,\n)",
        "    displaced: bool,\n    api: horizon_shader_api::Api,\n)")
    shader = replace_once(shader, "    vertex: bool,\n    displaced: bool,",
        "    vertex_flags: (bool, bool),")
    shader = replace_once(shader,
        "shader_source(source, tags, materials, vertex, displaced)",
        "shader_source(source, tags, materials, vertex_flags, api)")
    replacements = {"horizon-mixin.toml": manifest, "shader.rs": shader,
                    "sway.rs": adapt_sway(originals["sway.rs"])}
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
