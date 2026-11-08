"""Run the exact Physics helper against Horizon's actual material shader on APIs 0.1 and 0.2."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
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
    if hashlib.sha256(args.archive.read_bytes()).hexdigest() != "213f6264a20bed7fef64f12f539d0b2364f710857a3acaa351b96e78b3239dc1":
        raise ValueError("Unexpected Physics archive")
    with zipfile.ZipFile(args.archive) as archive:
        sway = archive.read("mixins/horizon_physics/sway.rs")
        shader = archive.read("mixins/horizon_physics/shader.rs")
    for name, data in (("sway.rs", sway), ("shader.rs", shader)):
        if hashlib.sha256(data).hexdigest() != recipe.SOURCE_HASHES[name]:
            raise ValueError(f"Unexpected Physics source: {name}")
    sway = recipe.adapt_sway(sway.decode("utf-8"))
    shader = shader.decode("utf-8").replace("    displaced: bool,\n)", "    displaced: bool,\n    api: horizon_shader_api::Api,\n)")
    shader = shader.replace("    vertex: bool,\n    displaced: bool,", "    vertex_flags: (bool, bool),")
    shader = shader.replace("shader_source(source, tags, materials, vertex, displaced)", "shader_source(source, tags, materials, vertex_flags, api)")
    gfx = workspace / "crates/horizon-client-render/src/gfx"
    material_raw = (gfx / "native_material.rs").read_bytes()
    if hashlib.sha256(material_raw).hexdigest() != recipe.MATERIAL_SHA256:
        raise ValueError("Unexpected Horizon material source")
    material = material_raw.decode("utf-8")
    constants = material[material.index('const PREFIX:'):material.index('/// The surface target')]
    constants = constants[:constants.index('/// What the material\'s displaced pipeline')] + constants[constants.index('/// terrain.wgsl\'s terrain section'):]
    body = material[material.index('fn shader_source('):material.index('/// The material\'s naga module')]
    body = body.replace('fn shader_source(', 'pub fn shader_source(', 1)
    prefix = (gfx / "wgsl_prefix.rs").read_text(encoding="utf-8").split("#[cfg(test)]")[0]
    prefix = prefix.replace('include_str!("../terrain.wgsl")', 'include_str!(r"' + str(gfx.parent / "terrain.wgsl") + '")')
    displacement = (gfx / "displace.rs").read_text(encoding="utf-8")
    displacement = displacement[displacement.index('pub const RELIEF:'):displacement.index('pub const RELIEF:') + len(displacement)]
    # These two const strings are before the renderer's following Rust types.
    end = displacement.index('\n";', displacement.index('pub const GRID:')) + len('\n";')
    displacement = displacement[:end]
    post = (gfx / "native_post.rs").read_text(encoding="utf-8")
    post = post[post.index('pub fn line_helpers('):post.index('/// 0.2\'s decoding copy:')]
    source = Path(__file__).with_name("PhysicsShaderCompatTests.rs").read_text(encoding="utf-8")
    for key, value in {"SWAY": sway, "SHADER": shader, "PREFIX": prefix, "DISPLACE": displacement,
                       "LINE": post, "MATERIAL": constants + body}.items():
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
        result = subprocess.run(["cargo", "run", "--offline", "--manifest-path", str(work / "Cargo.toml")],
                                text=True, encoding="utf-8", capture_output=True, env=env)
        if result.returncode:
            print(result.stderr[-12000:])
            result.check_returncode()
        print(result.stdout)
        (work / "result.json").write_text(json.dumps({
            "status": "PASS", "summary": result.stdout.strip(), "actualGPU": "NOT RUN",
            "shaderSourceSha256": recipe.MATERIAL_SHA256,
            "swaySourceSha256": recipe.SOURCE_HASHES["sway.rs"],
            "recipeSha256": hashlib.sha256(Path(recipe.__file__).read_bytes()).hexdigest(),
            "cargoLockSha256": hashlib.sha256((work / "Cargo.lock").read_bytes()).hexdigest(),
            "naga": "30.0.1 (author's third_party/naga patch)", "api": ["0.1", "0.2"],
        }, indent=2) + "\n", encoding="utf-8")
    finally:
        cleanup_generated(work)


if __name__ == "__main__":
    main()
