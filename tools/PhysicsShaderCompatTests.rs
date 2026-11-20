#![allow(dead_code)]
use horizon_shader_api::{Pack, StageKind, MANIFEST};
/* API_IMPORT */

mod mesher { pub struct Vertex { pub pos: [f32; 3] } }
mod native_horizon_physics_sway { /* SWAY */ }
/* LOOKS */
mod gfx {
    mod wgsl_prefix { /* PREFIX */ }
    mod displace { /* DISPLACE */ }
    mod native_post { /* API_IMPORT */ /* LINE */ }
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
    for (color, name) in /* COLOR_CASES */ {
        let manifest = /* MANIFEST_VARIANT */;
        let files = [(MANIFEST, manifest.as_str()), ("material.wgsl", sway::BUILTIN_MATERIAL)]
            .map(|(n, s)| (n.to_owned(), s.as_bytes().to_vec())).into();
        let report = horizon_shader_api::check(&Pack::from_files(files).unwrap()).unwrap();
        let stage = report.stages.iter().find(|s| s.kind == StageKind::Material).unwrap();
        let flags = /* FLAGS */;
        let material = /* MATERIAL_INPUT */;
        let original = shader_source(&material, &[], &[], flags, color);
        let expected = /* EXPECTED */;
        let suffix = original.strip_prefix(&material).expect("author material prefix");
        let tentative = sway::patch(suffix, &material /* PATCH_COLOR */).expect("Physics terrain anchors");
        let tentative = format!("{material}{tentative}");
        naga::front::wgsl::parse_str(&tentative)
            .unwrap_or_else(|e| panic!("{name}/patch: {}", e.emit_to_string(&tentative)));
        /* HOOK */
        assert!(patched.contains("HORIZON_PHYSICS_SHADE: bool = true"), "{name}: water look disabled");
        assert_eq!(patched.matches(expected).count(), 1, "{name}: gamma output wrapped once");
        assert!(patched.contains("horizon_line_in(albedo.rgb)"), "{name}: author's input retained");
        assert!(patched.contains("horizon_physics_sway(position + origin"), "{name}: waves retained");
        /* SWAY_PHASE */
        if /* CHECK_GAMMA */ {
            assert!(patched.contains("vec3<f32>(2.2)"));
            assert!(patched.contains("vec3<f32>(1.0 / 2.2)"));
        }
        for (label, source) in [("author", &original), ("physics", &patched)] {
            let module = naga::front::wgsl::parse_str(source).unwrap_or_else(|e| panic!("{name}/{label}: {}", e.emit_to_string(source)));
            naga::valid::Validator::new(naga::valid::ValidationFlags::all(), naga::valid::Capabilities::all())
                .validate(&module).unwrap_or_else(|e| panic!("{name}/{label}: {}", e.emit_to_string(source)));
        }
        // A third-party pack gets waves with its own surface shading retained.
        let third_party = material.replace(sway::BUILTIN_MARK, "THIRD_PARTY");
        let other_original = shader_source(&third_party, &[], &[], flags, color);
        let suffix = other_original.strip_prefix(&third_party).unwrap();
        let other = sway::patch(suffix, &third_party /* PATCH_COLOR */).unwrap();
        assert!(!other.contains(expected));
        assert!(other.contains("HORIZON_PHYSICS_SHADE: bool = false"));
        println!("API {name}: original+Physics WGSL parse/validation PASS; gamma boundary/waves/third-party gate PASS");
    }
}
