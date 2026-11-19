"""Preserve PhysicsModHorizon startup clicks and adapt its native shader hook."""

from hashlib import sha256
from pathlib import Path


SOURCE_HASHES = {
    "cape.rs": "8bb4ebc4b5ca63573100a12ee8a897732ad96f917718e2cf81dc16818c9727c0",
    "horizon-mixin.toml": "c8513953d5346aa2750c5f44d67f895f32b418e870e1ce6323b7f17ab8bd284f",
    "items.rs": "8186298fdae384ddeed855b8e513e2b6d1efbb0d3f4589b39a97bf90c1440215",
    "runtime.rs": "06504f99dd7b146c36ee17155d70db910bfbea4abd54d028acf0305e98ce5a0c",
    "setup_draw.rs": "bb77df382c5a33c86b28010a7381eb8d1097dc955a0cee73565d8d6f414f3f63",
    "setup_click.rs": "338424b10339c4788fff593a191d67472567f7c1168183c54e8dcd0f2d553528",
    "shader.rs": "39cc299b6ca721f81959e27fc20250bd7cd9546a2f47c4c9c97945332131d727",
    "sway.rs": "434d1577162330f64212e1298c4aba3ca5d5f9481331b666eb02eceddc3e7dfa",
    "world.rs": "4483c442c28fc20f492b8a92c692771ed93b1a07ec72ca4639e334c90a505a39",
}

MATERIAL_SOURCE = "crates/horizon-client-render/src/gfx/native_material.rs"
MATERIAL_SHA256 = "23dffc59507996590944df8ea51585fa290430f30a18d4580dea9add1c7a537d"
LATEST_REVISION = "f00125f61f794aaa3d4e950d301f353b35b6ee3d"
LATEST_MATERIAL_SHA256 = "cbc7ba9bcb4ae8c31c37b75f8b4fc48fd24d3fd9a8b69079b7cef8b15e658d2b"
LATEST_PARTICLES_SOURCE = "crates/horizon-client-render/src/particles.rs"
LATEST_PARTICLES_SHA256 = "9ca7a12ca160f475d49210f6f5bbf3c9a5bf7b88062cdaf59135957d25530ff4"


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


def adapt_shader(source: str, *, latest: bool) -> str:
    flags = "(bool, bool, bool, bool, bool)" if latest else "(bool, bool)"
    color = "linear: bool" if latest else "api: horizon_shader_api::Api"
    source = replace_once(source, "    vertex: bool,\n    displaced: bool,",
                          f"    vertex_flags: {flags},\n    {color},")
    source = replace_once(source, "shader_source(source, tags, materials, vertex, displaced)",
                          f"shader_source(source, tags, materials, vertex_flags, {color.split(':')[0]})")
    if latest:
        source = replace_once(source, "sway::patch(rest, source)", "sway::patch(rest, source, linear)")
    return source


def adapt_latest_sway(source: str) -> str:
    source = adapt_sway(source)
    source = replace_once(source, 'api = \\"0.1\\"', 'api = \\"0.2\\"')
    start = source.index("pub fn patch(")
    end = source.index("// ---------------------------------------------------------------------------------------------", start)
    patch = source[start:end]
    replacements = (
        ('patch(entries: &str, source: &str)', 'patch(entries: &str, source: &str, linear: bool)'),
        ('horizon_material_displace(position + origin, uv, layer, color, light)',
         'horizon_material_displace(position + origin.xyz, uv, layer, color, light, look, ao)'),
        ('horizon_physics_sway(position + origin, position, uv, layer)',
         'horizon_physics_sway(position + origin.xyz, position, uv, layer)'),
        (r'@location(14) @interpolate(flat) normal: vec3<f32>,\n}',
         '@location(14) @interpolate(flat) normal: vec3<f32>,'),
        (r'@location(15) hp_open: f32,\n}', '@location(15) hp_open: f32,'),
        ('return horizon_material_out(pos, uv, layer, color, vec2<f32>(light));',
         'var out = horizon_material_out(pos, uv, layer, color, vec2<f32>(light), look, ao);'),
        ('return horizon_material_out(pos, uv, layer, color, vec2<f32>(light), horizon_physics_open(position, layer));',
         'var out = horizon_material_out(pos, uv, layer, color, vec2<f32>(light), look, ao, horizon_physics_open(position, layer));'),
        ('light: vec2<f32>) -> HorizonMaterialOut', 'light: vec2<f32>, look: vec2<u32>, ao: f32) -> HorizonMaterialOut'),
        ('light: vec2<f32>, hp_open: f32)', 'light: vec2<f32>, look: vec2<u32>, ao: f32, hp_open: f32)'),
        ('p.color, p.light);', 'p.color, p.light, p.look, p.ao);'),
        ('p.color, p.light, 0.0);', 'p.color, p.light, p.look, p.ao, 0.0);'),
    )
    for old, new in replacements:
        # The displacement call appears in both its anchor and replacement.
        if old.startswith("horizon_material_displace"):
            if patch.count(old) != 2:
                raise ValueError("Physics displacement call changed")
            patch = patch.replace(old, new)
        else:
            patch = replace_once(patch, old, new)
    # On a linear frame, shade in the same gamma space as the default frame
    # and convert the styled result back before handing it to the renderer.
    patch = replace_once(patch, "    if shade && gamma_shade {\n",
        '''    if shade && gamma_shade && linear {
        patched = patched.replace(GAMMA_OUT,
            "let hp_gamma = horizon_physics_surface(in.layer, in.position, in.light, in.lightmap, in.hp_open, vec4(horizon_encode(out.rgb), out.a));\\n    return vec4(horizon_line_in(hp_gamma.rgb), hp_gamma.a);");
    } else if shade && gamma_shade {
''')
    return port_snow_dimension(source[:start] + patch + source[end:])


def prepare_source(workspace: Path) -> None:
    path = workspace / MATERIAL_SOURCE
    raw = path.read_bytes()
    digest = sha256(raw).hexdigest()
    # Mixins 0.6.0 requires named function arguments. Keep the tuple API and
    # every caller unchanged; unpack it inside the original function instead.
    source = raw.decode("utf-8")
    if digest == MATERIAL_SHA256:
        before = "    (vertex, displaced): (bool, bool),\n    api: horizon_shader_api::Api,\n) -> String {"
        after = ("    vertex_flags: (bool, bool),\n    api: horizon_shader_api::Api,\n) -> String {\n"
                 "    let (vertex, displaced) = vertex_flags;")
    elif digest == LATEST_MATERIAL_SHA256:
        before = "    (vertex, displaced, shaded, light, lit): Inputs,\n    linear: bool,\n) -> String {"
        after = ("    vertex_flags: Inputs,\n    linear: bool,\n) -> String {\n"
                 "    let (vertex, displaced, shaded, light, lit) = vertex_flags;")
    else:
        raise ValueError("Horizon material shader source changed before the Physics compatibility port")
    source = replace_once(source, before, after)
    if digest == LATEST_MATERIAL_SHA256:
        prepare_latest_particles(workspace)
    path.write_text(source, encoding="utf-8", newline="\n")


def prepare_latest_particles(workspace: Path) -> None:
    path = workspace / LATEST_PARTICLES_SOURCE
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != LATEST_PARTICLES_SHA256:
        raise ValueError("Horizon particle source changed before the Physics compatibility port")
    header = '''    pub fn add_with(
        &mut self,
        options: &ParticleOptions,
        p: [f64; 3],
        v: [f64; 3],
        override_limiter: bool,
        always_show: bool,
    ) {\n'''
    # Keep the upstream replacement hook on add_with. Mixins can target its
    # normal body at the point where the original particle was intercepted.
    delegated = (
        header + "        self.add_with_default(options, p, v, override_limiter, always_show);\n"
        "    }\n\n" + header.replace("pub fn add_with(", "fn add_with_default(")
    )
    source = replace_once(raw.decode("utf-8"), header + "        let c = self.camera;",
                          delegated + "        let c = self.camera;")
    path.write_text(source, encoding="utf-8", newline="\n")


def apply(bundle: Path, *, revision: str | None = None) -> None:
    latest = revision == LATEST_REVISION
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
    draw_module = "title::draw" if latest else "title"
    manifest = manifest.replace(old_target, f'target = "horizon_client_lib::{draw_module}::Title::draw_with_items"')
    for anchor in (binding, click_binding):
        manifest = manifest.replace(anchor, anchor.replace('"_title"', '"title"'))

    shader_bindings = '''  { name = "vertex", ty = "bool", value = "vertex" },
  { name = "displaced", ty = "bool", value = "displaced" },'''
    # Restrict the binding update to this hook; other Physics hooks also have
    # vertex/displaced arguments belonging to different author APIs.
    start = manifest.index('id = "shader"')
    end = manifest.index('[[injection]]', start)
    bindings = ('''  { name = "vertex_flags", ty = "(bool, bool, bool, bool, bool)", value = "vertex_flags" },
  { name = "linear", ty = "bool", value = "linear" },''' if latest else
        '''  { name = "vertex_flags", ty = "(bool, bool)", value = "vertex_flags" },
  { name = "api", ty = "horizon_shader_api::Api", value = "api" },''')
    shader_hook = replace_once(manifest[start:end], shader_bindings, bindings)
    manifest = manifest[:start] + shader_hook + manifest[end:]
    if latest:
        manifest = replace_once(manifest,
            'target = "horizon_client_render::particles::ParticleEngine::add_with"',
            'target = "horizon_client_render::particles::ParticleEngine::add_with_default"')
        manifest = replace_once(manifest, '  { name = "_hide_splash", ty = "bool", value = "hide_splash" },',
            '''  { name = "_hide_splash", ty = "bool", value = "hide_splash" },
  { name = "phases", ty = "&mut dyn FnMut(crate::screen_draw::RenderPhase, &mut HudBatch)", value = "&mut *phases" },''')
    shader = adapt_shader(originals["shader.rs"], latest=latest)
    sway = adapt_latest_sway(originals["sway.rs"]) if latest else adapt_sway(originals["sway.rs"])
    replacements = {"horizon-mixin.toml": manifest, "shader.rs": shader,
                    "sway.rs": sway}
    if latest:
        replacements["cape.rs"] = port_cape_slot(originals["cape.rs"])
        replacements["items.rs"] = port_item_outline(originals["items.rs"])
        replacements["runtime.rs"] = port_frame_dimension(originals["runtime.rs"])
        replacements["world.rs"] = port_world_dimension_calls(originals["world.rs"])
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
    if latest:
        draw = replace_once(replacements["setup_draw.rs"], "    _hide_splash: bool,\n",
            "    _hide_splash: bool,\n    phases: &mut dyn FnMut(crate::screen_draw::RenderPhase, &mut HudBatch),\n")
        replacements["setup_draw.rs"] = replace_once(draw,
            "    crate::native_horizon_physics_setup::draw(atlas, batch, cursor);",
            "    phases(crate::screen_draw::RenderPhase::Background, batch);\n"
            "    crate::native_horizon_physics_setup::draw(atlas, batch, cursor);\n"
            "    phases(crate::screen_draw::RenderPhase::Foreground, batch);")

    for name, source in replacements.items():
        (bundle / name).write_text(source, encoding="utf-8", newline="\n")


def port_cape_slot(source: str) -> str:
    return replace_once(source, "horizon_world::player_inventory::EquipmentSlot::Chest",
                        "horizon_core::equipment_slot::EquipmentSlot::Chest")


def port_item_outline(source: str) -> str:
    return replace_once(source, '''        if game.entity_glowing(item.id) {
            let rgb = super::glowing::team_color(game.mob_team(item.id));''',
                        '''        if let Some(rgb) = game.thing_outline(item.id, "minecraft:item") {''')


def port_snow_dimension(source: str) -> str:
    source = replace_once(source, "    world: u64,\n", "    world: &'static str,\n")
    return replace_once(source, "pub fn snow_world(key: u64,", "pub fn snow_world(key: &'static str,")


def port_frame_dimension(source: str) -> str:
    # Dimension ids are interned, so the transient state can compare full ids
    # without a per-frame allocation or a numeric alias for custom dimensions.
    source = replace_once(source, "    dim: u8,\n", "    dim: &'static str,\n")
    source = replace_once(source, "        dim: 255,\n", '        dim: "",\n')
    return replace_once(source, "pub fn frame(dim: u8,", "pub fn frame(dim: &'static str,")


def port_world_dimension_calls(source: str) -> str:
    source = replace_once(source, "sway::snow_world(game.dim as u64,", "sway::snow_world(game.dim.id(),")
    return replace_once(source, "hp::frame(game.dim as u8,", "hp::frame(game.dim.id(),")
