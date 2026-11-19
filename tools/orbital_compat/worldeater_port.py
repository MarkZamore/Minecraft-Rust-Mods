"""Port Orbital Laser 0.5.17's World Eater terrain paint to Horizon f00125f."""

from hashlib import sha256
from pathlib import Path


FULL_PASS = "engine/data/gen/pass_worldeater_false.wgsl"
FAST_PASS = "engine/data/gen/pass_worldeater_true.wgsl"
ENGINE = "engine/src/lib.rs"
SDK = "engine/src/horizon_sdk.rs"
SOURCE_HASHES = {
    FULL_PASS: "ebc9594a5efa97c1277cc13fbecbfb61f7b3d2f2dcc9500fc1823f40fa6573dd",
    FAST_PASS: "5de075afd54d9bceeb8c10ca2df44337c21fab2970cbbafc3bbcf13a5e4e0574",
    ENGINE: "b68c65313a227ac7f8ea8dfdd02d8777e5d2b954a0acfbbb40dd153700ab69c9",
    SDK: "755b5be329777b0198fc82de3663eeb0f69b3d14388f2efb7426d72d19bb89ec",
}

# Upstream's 0.5.17 world_warp returns the original vertex. Its world_fragment
# paints cracks on upward-facing terrain near the breach. The current render
# slots provide scene depth, so draw the author's crack pattern at the
# reconstructed surface without re-introducing geometry displacement.
WGSL = """fn wef_depth_neighbor(uv: vec2<f32>, step_uv: vec2<f32>, d: f32) -> vec3<f32> {
    let edge = frame.resolution.zw * 0.5;
    let a = clamp(uv - step_uv, edge, vec2<f32>(1.0) - edge);
    let b = clamp(uv + step_uv, edge, vec2<f32>(1.0) - edge);
    let da = depth_at(a);
    let db = depth_at(b);
    if (is_sky(da) && is_sky(db)) {
        return world_offset(uv, d);
    }
    let use_a = !is_sky(da) && (is_sky(db) || abs(d - da) < abs(d - db));
    return world_offset(select(b, a, use_a), select(db, da, use_a));
}

fn wef_native_cracks(col: vec3<f32>, uv: vec2<f32>, d: f32, rel: vec3<f32>, time: f32) -> vec3<f32> {
    let dome = frame.mod_params[2].z;
    if (is_sky(d) || dome <= 0.01 || time <= 0.0 ||
        abs(frame.mod_params[1].w - 19.0) >= 0.5 || frame.mod_params[2].y <= 0.5) {
        return col;
    }
    let at = rel - we_o;
    let x = at.xz - frame.mod_params[0].xz;
    let r2 = dot(x, x);
    let gr = (1.0 - smoothstep(2.0, 5.0, at.y)) * smoothstep(-8.0, -4.0, at.y);
    if (gr <= 0.0 || r2 >= WE_DOME_R * WE_DOME_R * 1.3) {
        return col;
    }
    let k = wef_crack(x, dome / WE_DOME_H);
    if (k.x + k.y <= 0.001) {
        return col;
    }
    let dx = wef_depth_neighbor(uv, vec2<f32>(frame.resolution.z, 0.0), d) - rel;
    let dy = wef_depth_neighbor(uv, vec2<f32>(0.0, frame.resolution.w), d) - rel;
    let normal = cross(dx, dy);
    let nn = dot(normal, normal);
    if (nn <= 0.0) {
        return col;
    }
    let up = abs(normal.y) * inverseSqrt(nn);
    let fade = gr * smoothstep(0.75, 0.95, up) * (1.0 - smoothstep(WE_DOME_R * 0.4, WE_DOME_R * 1.1, sqrt(r2)));
    if (fade <= 0.0) {
        return col;
    }
    let pulse = 0.8 + 0.2 * sin(time * 9.0 + wef_vh(floor(x * 0.3)) * 6.28);
    let core = saturate(k.x) * fade;
    let plain = linear_to_srgb(max(col, vec3<f32>(0.0)));
    return srgb_to_linear(mix(plain * (1.0 - 0.35 * saturate(k.y) * fade),
        vec3<f32>(0.05, 0.5, 0.46) * pulse, 0.7 * core));
}

"""


def replace_once(source: str, before: str, after: str) -> str:
    if source.count(before) != 1:
        raise ValueError(f"Orbital Laser 0.5.17 World Eater anchor changed: {before[:100]!r}")
    return source.replace(before, after)


def shader(source: str, *, fast: bool) -> str:
    # These are the author's compiled shader variants, actually included by
    # the native engine. Keep the preview-only heightfield and its flag intact.
    branch = """    if (WE_STANDIN_MOUND && !sky && wef_m.dome > 0.01) {
        let xk = lr.xz - wef_m.dome_at.xz;
        let gr = smoothstep(5.0, 2.0, lr.y - wef_m.dome_at.y) * smoothstep(-8.0, -4.0, lr.y - wef_m.dome_at.y);
        if (gr > 0.0 && dot(xk, xk) < WE_DOME_R * WE_DOME_R * 1.3) {
            let k = wef_crack(xk, wef_m.dome / WE_DOME_H);
            let fd = gr * smoothstep(WE_DOME_R * 1.1, WE_DOME_R * 0.4, length(xk));
            let core = saturate(k.x) * fd;
            col = col * (1.0 - 0.35 * saturate(k.y) * fd);
            col = mix(col, vec3<f32>(0.05, 0.5, 0.46) * (0.8 + 0.2 * sin(time * 9.0)), 0.7 * core);
        }
    }
"""
    source = replace_once(source, branch, "    col = wef_native_cracks(col, uv, d, rel, time);\n")
    pass_start = "fn worldeater(in: StageIn, stage: u32, up: vec4<f32>, up_t: f32) -> WeOut {"
    source = replace_once(source, pass_start, WGSL + pass_start)
    if fast:
        join = "    var col = srgb_to_linear(textureSampleLevel(scene_color, linear_sampler, in.uv, 0.0).rgb) * u.e.z;\n"
        source = replace_once(source, join, """    let scene = srgb_to_linear(textureSampleLevel(scene_color, linear_sampler, in.uv, 0.0).rgb);
    let rel = world_offset(in.uv, select(d, 0.5, sky));
    var col = wef_native_cracks(scene, in.uv, d, rel, t) * u.e.z;
""")
    return source


def transform_engine(source: str) -> str:
    source = replace_once(source, """                // (and the World Eater's wormsign's: shaders/worldeater_warp.wgsl)
                if LASERS[self.laser].key == "worldeater" {
                    render::set_warp(PASS_WE_WARP);
                    render::warp_on(true, 0.0, 0.0, 0.0, 0, 0.0);
                }
""", "")
    source = replace_once(source, """        if laser.key == "worldeater" {
            // (the wormsign bends the world's own land: shaders/worldeater_warp.wgsl, on only while it does: `frame`)
            render::set_warp(PASS_WE_WARP);
        }
""", "")
    source = replace_once(source, """            render::set_params(&s.params);
            // (the warp on for its span only: before and after it the frame keeps its culling)""",
        """            let mut visible_params = s.params;
            // (the warp on for its span only: before and after it the frame keeps its culling)""")
    source = replace_once(source, "                render::warp_on(on && !nowarp, 0.0, 0.0, 0.0, 0, 0.0);",
        """                if !on || nowarp {
                    visible_params[10] = 0.0;
                }""")
    return replace_once(source, """            if s.key == "earthquake" {
                // (the plates moving: the warp on from just before the first heave to rest;""",
        """            render::set_params(&visible_params);
            if s.key == "earthquake" {
                // (the plates moving: the warp on from just before the first heave to rest;""")


def transform_sdk(source: str) -> str:
    if 'pub const HELD_BACK: &[&str] = &["fold", "earthquake"];' not in source:
        raise ValueError("Orbital Laser native availability changed")
    source = replace_once(source, """        // (the Fold's world warp and held world are drawn since upstream's #11955 and #11956)
""", """        // f00125f no longer provides the terrain hold and vertex warp.
        "render.set-warp",
        "render.warp-on",
        "render.hold-world",
        "render.hold-world-within",
""")
    source = replace_once(source, """        // the World Eater: its wormsign bends the land through the world warp
        ("worldeater", &["render.set-warp", "render.warp-on"]),
""", "")
    return replace_once(source,
        '            assert!(supports("render.set-warp") && supports("render.hold-world-within") && supports("render.set-post"));',
        '            assert!(!supports("render.set-warp") && !supports("render.hold-world-within") && supports("render.set-post"));')


def transform_manifest(source: str) -> str:
    # This carries only two unavailable callbacks. Other audited manifest rows
    # may be retargeted by the separate version-specific network/renderer port.
    before = """# The Fold's world warp (render.set-warp, warp-on): handed to the game's world warp each frame (post.rs).
[[injection]]
id = "world_warp"
hook = "world_warp"
hook_version = 1
kind = "HEAD"
helper = "world_warp.rs"
bindings = [{ name = "warp", ty = "&mut crate::gfx::native_render::WorldWarp", value = "&mut *warp" }]

# The Fold's held world (render.hold-world, hold-world-within): asked by the game each frame (glue.rs `world_hold`).
[[injection]]
id = "world_hold"
hook = "world_hold"
hook_version = 1
kind = "HEAD"
helper = "world_hold.rs"
bindings = [
  { name = "game", ty = "&crate::game::Game<'_>", value = "self" },
  { name = "hold", ty = "&mut horizon_client::mod_api::WorldHold", value = "&mut *hold" },
]
"""
    return replace_once(source, before, "")


def transform_glue(source: str) -> str:
    source = replace_once(source, """    /// The mod's world warp as it gave it, and on this game's contract (`orbital_post::native_warp_source`).
    warp: Option<(std::sync::Arc<str>, std::sync::Arc<str>)>,
""", "")
    start = "    // (the Fold's world warp: its source put on this game's contract once a source, `post.rs`)\n"
    source = replace_once(source, _through(source, start, "\n    horizon_client_render::orbital_post::set_warp(warp.map(|w| (w, warp_on)));\n"), "")
    start = "/// `native_world_hold`: the mod's `render.hold-world` and `hold-world-within`."
    return replace_once(source, _through(source, start, "\n}\n"), "")


def _through(source: str, start: str, end: str) -> str:
    if source.count(start) != 1:
        raise ValueError("Expected one Orbital Laser 0.5.17 warp section")
    first = source.index(start)
    last = source.index(end, first) + len(end)
    return source[first:last]


def transform_post(source: str) -> str:
    start = "// ---- The Fold's world warp on the game's `native_world_warp`"
    marker = "    if let Some(params) = PASS.lock().ok().and_then(|p| p.as_ref().map(|p| p.params)) {\n        warp.params = params;\n    }\n}\n"
    return replace_once(source, _through(source, start, marker), "")


def changes(bundle: Path) -> dict[str, str]:
    originals = {}
    for name, expected in SOURCE_HASHES.items():
        raw = (bundle / name).read_bytes()
        if sha256(raw).hexdigest() != expected:
            raise ValueError(f"Unexpected Orbital Laser 0.5.17 World Eater source: {name}")
        originals[name] = raw.decode("utf-8")
    return {
        FULL_PASS: shader(originals[FULL_PASS], fast=False),
        FAST_PASS: shader(originals[FAST_PASS], fast=True),
        ENGINE: transform_engine(originals[ENGINE]),
        SDK: transform_sdk(originals[SDK]),
    }
