"""Run the exact Physics helper against Horizon's actual material shader on APIs 0.1 and 0.2."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

import physics_title_compat as recipe


def cleanup_generated(work: Path) -> None:
    """Remove only this probe's reproducible inputs and compilation output."""
    for name in ("src", "target"):
        target = (work / name).resolve()
        if target.parent != work or target.is_symlink() or target.is_junction():
            raise ValueError(f"Unsafe probe cleanup path: {target}")
        if not target.exists():
            continue
        for directory, dirs, files in os.walk(target):
            for part in dirs + files:
                path = Path(directory) / part
                if part == ".git" or path.is_symlink() or path.is_junction():
                    raise ValueError(f"Unexpected checkout or link in probe output: {path}")
        shutil.rmtree("\\\\?\\" + str(target) if os.name == "nt" else target)
    for name in ("Cargo.toml", "Cargo.lock"):
        (work / name).unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--work", type=Path, required=True)
    args = p.parse_args()
    workspace = args.workspace.resolve()
    work = args.work.resolve()
    if "agent-runs" not in work.parts:
        raise ValueError("Probe belongs in the registered agent-runs task")
    work.mkdir(parents=True, exist_ok=True)
    created = datetime.now(timezone.utc)
    (work / "RUN.json").write_text(json.dumps({
        "owner": "codex/jei_fix", "purpose": "Exact Physics/Horizon material WGSL validation",
        "createdUtc": created.isoformat(), "expectedMiB": 250,
        "deleteBy": (created + timedelta(days=1)).date().isoformat(), "results": ["result.json"],
    }, indent=2) + "\n", encoding="utf-8")
    archive_sha = hashlib.sha256(args.archive.read_bytes()).hexdigest()
    if archive_sha == "213f6264a20bed7fef64f12f539d0b2364f710857a3acaa351b96e78b3239dc1":
        version, hashes = "0.18.0", recipe.SOURCE_HASHES
    elif archive_sha == "8eeba82ac0e7755298269fd328679473720a9894cfe10df5c8e3aaf88f59c34c":
        version, hashes = "0.20.7", recipe.PHYSICS_0_20_7_HASHES
    else:
        raise ValueError("Unexpected Physics archive")
    with zipfile.ZipFile(args.archive) as archive:
        sway = archive.read("mixins/horizon_physics/sway.rs")
        shader = archive.read("mixins/horizon_physics/shader.rs")
    for name, data in (("sway.rs", sway), ("shader.rs", shader)):
        if hashlib.sha256(data).hexdigest() != hashes[name]:
            raise ValueError(f"Unexpected Physics source: {name}")
    gfx = workspace / "crates/horizon-client-render/src/gfx"
    material_raw = (gfx / "native_material.rs").read_bytes()
    material_sha = hashlib.sha256(material_raw).hexdigest()
    latest = material_sha == recipe.LATEST_MATERIAL_SHA256
    if not latest and material_sha != recipe.MATERIAL_SHA256:
        raise ValueError("Unexpected Horizon material source")
    if version == "0.20.7":
        if not latest:
            raise ValueError("Physics 0.20.7 requires the pinned f001 Naga host")
        sway = recipe.adapt_0_20_7_sway(sway.decode("utf-8"))
        shader = recipe.adapt_0_20_7_shader(shader.decode("utf-8"))
    else:
        adapt_sway = recipe.adapt_latest_sway if latest else recipe.adapt_sway
        sway = adapt_sway(sway.decode("utf-8"))
        shader = recipe.adapt_shader(shader.decode("utf-8"), latest=latest)
    material = material_raw.decode("utf-8")
    constants = material[material.index('const PREFIX:'):material.index('/// The surface target')]
    constants = constants[:constants.index('/// What the material\'s displaced pipeline')] + constants[constants.index('/// terrain.wgsl\'s terrain section'):]
    if latest:
        constants += material[material.index('const LIT_MARKS:'):material.index("/// The terrain shader for the pack")]
    body = material[material.index('fn shader_source('):material.index('/// The material\'s naga module')]
    body = body.replace('fn shader_source(', 'pub fn shader_source(', 1)
    prefix = (gfx / "wgsl_prefix.rs").read_text(encoding="utf-8").split("#[cfg(test)]")[0]
    def absolute_include(match):
        path = (gfx / match[1]).resolve(strict=True)
        path.relative_to(workspace)
        return f'include_str!(r"{path}")'

    prefix = re.sub(r'include_str!\("(\.\./[^"]+)"\)', absolute_include, prefix)
    displacement = (gfx / "displace.rs").read_text(encoding="utf-8")
    displacement = displacement[displacement.index('pub const RELIEF:'):displacement.index('pub const RELIEF:') + len(displacement)]
    # These two const strings are before the renderer's following Rust types.
    end = displacement.index('\n";', displacement.index('pub const GRID:')) + len('\n";')
    displacement = displacement[:end]
    post = (gfx / "native_post.rs").read_text(encoding="utf-8")
    line_end = "/// The decoding copy:" if latest else "/// 0.2's decoding copy:"
    post = post[post.index('pub fn line_helpers('):post.index(line_end)]
    source = Path(__file__).with_name("PhysicsShaderCompatTests.rs").read_text(encoding="utf-8")
    gamma = '"return horizon_physics_surface(in.layer, in.position, in.light, in.lightmap, in.hp_open, vec4(horizon_line_out(out.rgb), out.a));"'
    if latest:
        looks = (gfx / "looks.rs").read_text(encoding="utf-8")
        fixture = {
            "API_IMPORT": "",
            "LOOKS": looks[looks.index("pub const LOOK_WGSL:"):looks.index("/// A mesh's quad", looks.index("pub const LOOK_WGSL:"))],
            "COLOR_CASES": '[(false, "gamma"), (true, "linear")]',
            "MANIFEST_VARIANT": 'if color { sway::BUILTIN_MANIFEST.to_owned() + "\\n[frame]\\nblend = \\"linear\\"\\n" } else { sway::BUILTIN_MANIFEST.to_owned() }',
            "FLAGS": "(false, false, stage.shaded_tint(), stage.light_source(), stage.lit_tint())",
            "MATERIAL_INPUT": "stage.source.clone() + LOOK_WGSL",
            "EXPECTED": ('if color { "return vec4(horizon_line_in(hp_gamma.rgb), hp_gamma.a);" } else { ' + gamma + " }"),
            "CHECK_GAMMA": "!color",
            "PATCH_COLOR": ", color",
        }
    else:
        fixture = {
            "API_IMPORT": "use horizon_shader_api::Api;",
            "LOOKS": "",
            "COLOR_CASES": '[(Api::V0_1, "0.1"), (Api::V0_2, "0.2")]',
            "MANIFEST_VARIANT": r'sway::BUILTIN_MANIFEST.replace("api = \"0.1\"", &format!("api = \"{name}\""))',
            "FLAGS": "(false, false)",
            "MATERIAL_INPUT": "stage.source.clone()",
            "EXPECTED": gamma,
            "CHECK_GAMMA": "color == Api::V0_2",
            "PATCH_COLOR": "",
        }
    if version == "0.20.7":
        fixture["HOOK"] = '''let std::ops::ControlFlow::Break(Ok(module)) =
            horizon_physics_shader((&material, &stage.file), &[], &[], flags, color) else {
            panic!("Physics hook did not parse the patched terrain");
        };
        assert!(module.functions.iter().any(|(_, function)| function.name.as_deref() == Some("horizon_physics_surface")),
            "{name}: the live module fell back to the unpatched shader");
        naga::valid::Validator::new(naga::valid::ValidationFlags::all(), naga::valid::Capabilities::all())
            .validate(&module).unwrap_or_else(|error| panic!("{name}/module: {error}"));
        assert!(tentative.contains("water_open"), "{name}: authored side-face water styling missing");
        let patched = tentative;'''
    else:
        fixture["HOOK"] = '''let std::ops::ControlFlow::Break(patched) = horizon_physics_shader(&material, &[], &[], flags, color) else {
            panic!("Physics hook did not return the patched terrain");
        };'''
    for key, value in {"SWAY": sway, "SHADER": shader, "PREFIX": prefix, "DISPLACE": displacement,
                       "LINE": post, "MATERIAL": constants + body, **fixture}.items():
        source = source.replace("/* " + key + " */", value)
    (work / "src").mkdir(exist_ok=True)
    (work / "src/main.rs").write_text(source, encoding="utf-8")
    api = workspace / "crates/horizon-shader-api"
    naga = workspace / "third_party/naga"
    (work / "Cargo.toml").write_text('''[package]
name = "physics-shader-compat-probe"
version = "0.0.0"
edition = "2024"
[dependencies]
naga = { version = "=30.0.1", features = ["wgsl-in"] }
horizon-shader-api = { path = ''' + json.dumps(str(api).replace("\\", "/")) + ''' }
[patch.crates-io]
naga = { path = ''' + json.dumps(str(naga).replace("\\", "/")) + ''' }
''', encoding="utf-8")
    env = os.environ.copy()
    env.pop("RUSTFLAGS", None)
    env["CARGO_TARGET_DIR"] = str(work / "target")
    try:
        cargo = ["cargo", "+1.99.1"] if latest else ["cargo"]
        result = subprocess.run([*cargo, "run", "--offline", "--manifest-path", str(work / "Cargo.toml")],
                                text=True, encoding="utf-8", capture_output=True, env=env)
        if result.returncode:
            print(result.stderr[-12000:])
            result.check_returncode()
        print(result.stdout)
        (work / "result.json").write_text(json.dumps({
            "status": "PASS", "summary": result.stdout.strip(), "actualGPU": "NOT RUN",
            "shaderSourceSha256": material_sha,
            "swaySourceSha256": hashes["sway.rs"], "physicsVersion": version, "archiveSha256": archive_sha,
            "recipeSha256": hashlib.sha256(Path(recipe.__file__).read_bytes()).hexdigest(),
            "cargoLockSha256": hashlib.sha256((work / "Cargo.lock").read_bytes()).hexdigest(),
            "naga": "30.0.1 (author's third_party/naga patch)", "api": ["gamma", "linear"] if latest else ["0.1", "0.2"],
        }, indent=2) + "\n", encoding="utf-8")
    finally:
        cleanup_generated(work)


if __name__ == "__main__":
    main()
