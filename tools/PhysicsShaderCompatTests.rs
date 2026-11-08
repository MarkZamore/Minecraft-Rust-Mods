#![allow(dead_code)]
use horizon_shader_api::{Api, Pack, StageKind, MANIFEST};

mod mesher { pub struct Vertex { pub pos: [f32; 3] } }
mod native_horizon_physics_sway { /* SWAY */ }
mod gfx {
    mod wgsl_prefix { /* PREFIX */ }
    mod displace { /* DISPLACE */ }
    mod native_post { use horizon_shader_api::Api; /* LINE */ }
    pub mod native_material {
        use super::wgsl_prefix::{TERRAIN, prefixed, section};
        /* MATERIAL */
    }
}
use gfx::native_material::shader_source;
/* SHADER */

fn main() {
    use native_horizon_physics_sway as sway;
    sway::set(1, || vec![("minecraft:block/water_still", 1), ("minecraft:block/short_grass", 2)], true, true, 1.0);
    for (api, name) in [(Api::V0_1, "0.1"), (Api::V0_2, "0.2")] {
        let manifest = sway::BUILTIN_MANIFEST.replace("api = \"0.1\"", &format!("api = \"{name}\""));
        let files = [(MANIFEST, manifest.as_str()), ("material.wgsl", sway::BUILTIN_MATERIAL)]
            .map(|(n, s)| (n.to_owned(), s.as_bytes().to_vec())).into();
        let report = horizon_shader_api::check(&Pack::from_files(files).unwrap()).unwrap();
        let stage = report.stages.iter().find(|s| s.kind == StageKind::Material).unwrap();
        let original = shader_source(&stage.source, &[], &[], (false, false), api);
        let expected = "return horizon_physics_surface(in.layer, in.position, in.light, in.lightmap, in.hp_open, vec4(horizon_line_out(out.rgb), out.a));";
        let std::ops::ControlFlow::Break(patched) = horizon_physics_shader(&stage.source, &[], &[], (false, false), api) else {
            panic!("Physics hook did not return the patched terrain");
        };
        assert!(patched.contains("HORIZON_PHYSICS_SHADE: bool = true"), "{name}: water look disabled");
        assert_eq!(patched.matches(expected).count(), 1, "{name}: gamma output wrapped once");
        assert!(patched.contains("horizon_line_in(albedo.rgb)"), "{name}: author's input retained");
        assert!(patched.contains("horizon_physics_sway(position + origin"), "{name}: waves retained");
        if api == Api::V0_2 {
            assert!(patched.contains("vec3<f32>(2.2)"));
            assert!(patched.contains("vec3<f32>(1.0 / 2.2)"));
        }
        for (label, source) in [("author", &original), ("physics", &patched)] {
            let module = naga::front::wgsl::parse_str(source).unwrap_or_else(|e| panic!("{name}/{label}: {}", e.emit_to_string(source)));
            naga::valid::Validator::new(naga::valid::ValidationFlags::all(), naga::valid::Capabilities::all())
                .validate(&module).unwrap_or_else(|e| panic!("{name}/{label}: {}", e.emit_to_string(source)));
        }
        // A third-party pack gets waves with its own surface shading retained.
        let third_party = stage.source.replace(sway::BUILTIN_MARK, "THIRD_PARTY");
        let other_original = shader_source(&third_party, &[], &[], (false, false), api);
        let suffix = other_original.strip_prefix(&third_party).unwrap();
        let other = sway::patch(suffix, &third_party).unwrap();
        assert!(!other.contains(expected));
        assert!(other.contains("HORIZON_PHYSICS_SHADE: bool = false"));
        println!("API {name}: original+Physics WGSL parse/validation PASS; gamma boundary/waves/third-party gate PASS");
    }
}
