"""Compile verified Rust Mods sidecars against the unchanged shared Horizon client."""

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap

from inspect_mods import selected_ids
from mixins_tool import ARCHIVE_SHA256, COMPAT_PATCH, SOURCE_PATCH_SHA256
from modern_native_stage import toml_value
from native_build import DECISIONS, INPUT_LOCK, build_recipe_sha, load, sha, tree_hash
from shared_sdk_mod_inputs import MIXINS, inspect_mixins, local_file
from shared_sdk_pack import REVISION, ordinary, replace_once
from shared_sdk_render import adapt_render


TOOLS = Path(__file__).resolve().parent
TOOLCHAIN = "1.99.1-x86_64-pc-windows-gnu"
TARGET = "x86_64-pc-windows-gnu"
REGISTRAR_TEMPLATE = "crates/horizon-client/src/native_mod_entry.rs.template"
TEMPLATE_SHA256 = "1c77f20468fc65accc7d4334b36ff6ba71c964c8cbcc044636818240821d8349"
GENERATOR_SHA256 = "2963c82c33b5eb2211c119d8415f543e2e3e1902c83fab3abe765acb3fcdd4b2"
RUST_NAME = re.compile(r"[a-z][a-z0-9_]*")
HEX40 = re.compile(r"[a-f0-9]{40}")
LIBRARY_OWNERS = {"client": "Client", "server": "Server", "both": "Both"}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as output:
        output.write(text)


def projection(plan):
    for mod in plan["mods"]:
        if mod["id"] == "tectonic_horizon":
            rows = [row for row in mod["injections"] if row["id"] == "snow-height"]
            expected = "horizon_world::worldgen::biome_temperature::BiomeTemperatureSettings::height_adjusted"
            if len(rows) != 1 or rows[0]["target"] != expected:
                raise ValueError("Unexpected Tectonic biome shim target")
            rows[0]["target"] = "horizon_world::worldgen::biome_temperature::height_adjusted"
    return plan


def encode_rows(mod, copies, abi):
    document = ["[mod]\n" + "".join(f"{name} = {toml_value(value)}\n" for name, value in mod["header"].items()),
                f'\n[compatibility]\nhorizon_revision = "{abi}"\n']
    for row in mod["injections"]:
        values = dict(row, target=copies[row["target"]]["copy"])
        document.append("\n[[injection]]\n" + "".join(f"{key} = {toml_value(value)}\n" for key, value in values.items()))
    return "".join(document)


def verify_pack_inputs(inputs, selection):
    record = load(inputs / "mods.json")
    required = {"schemaVersion": 1, "horizon": REVISION, "mixins": MIXINS, "inputLockSha256": sha(INPUT_LOCK),
                "conflictDecisionsSha256": sha(DECISIONS), "buildRecipeSha256": build_recipe_sha()}
    if any(record.get(key) != value for key, value in required.items()):
        raise ValueError("Mod inputs differ from the approved pack recipe")
    if set(record["treeSha256"]) != {"mixins-input", "compiled-native", "content"}:
        raise ValueError("The mod input receipt has an unexpected inventory")
    for tree, expected in record["treeSha256"].items():
        if tree_hash(inputs / tree) != expected:
            raise ValueError(f"Verified input tree changed: {tree}")
    raw = INPUT_LOCK.read_bytes()
    names = selected_ids(selection, raw)
    rows = {row["id"]: row for row in json.loads(raw)["projects"]}
    if record["mods"] != inspect_mixins(inputs, [rows[name] for name in sorted(names)]):
        raise ValueError("The mod inventory differs from the selected package sources")
    return projection(record)


def verify_sdk(sdk, receipt):
    identity = sdk / "horizon-prebuilt.json"
    build = load(identity)
    abi = build["commit"]
    if (receipt["horizon"] != REVISION or not HEX40.fullmatch(abi) or abi != receipt["abiBuildId"]
            or build["rustc"] != receipt["rustc"] or build["target"] != TARGET or build["side"] != "client"
            or sha(identity) != receipt["sdkIdentitySha256"]
            or build["files"]["bin/horizon-client.exe"] != receipt["clientSha256"]):
        raise ValueError("The SDK differs from its verified shared client receipt")
    for name, expected in build["files"].items():
        if sha(local_file(sdk, name)) != expected:
            raise ValueError(f"Shared SDK artifact changed: {name}")
    rust = subprocess.check_output(["rustup", "run", TOOLCHAIN, "rustc", "-vV"], text=True).strip()
    if rust != build["rustc"]:
        raise ValueError("Rust compiler and target must match the SDK exactly")
    functions = local_file(sdk, "functions.json")
    if sha(functions) != receipt["patchSourceCatalogSha256"]:
        raise ValueError("The SDK function index differs from its verified patch source")
    return build


def verify_mixins_tool(path):
    source = load(local_file(path.parent, "source.json"))
    required = {"revision": MIXINS, "archiveSha256": ARCHIVE_SHA256, "horizonPatchSha256": SOURCE_PATCH_SHA256,
                "modulePathPatchSha256": sha(COMPAT_PATCH), "sha256": sha(path)}
    if source != required:
        raise ValueError("Use the horizon-mixins binary and receipt produced by the approved Mods recipe")


def sdk_library(sdk, crate):
    if not RUST_NAME.fullmatch(crate):
        raise ValueError(f"Invalid Rust dependency: {crate}")
    deps = sdk / "deps"
    for filename in (f"{crate}.dll", f"lib{crate}.dll"):
        path = deps / filename
        if path.is_file():
            ordinary(path)
            return path
    paths = {*deps.glob(f"lib{crate}-*.rmeta"), *deps.glob(f"lib{crate}.rmeta")}
    if len(paths) != 1:
        raise ValueError(f"SDK needs exactly one linkable library or metadata file for {crate}")
    path = next(iter(paths))
    ordinary(path)
    return path


def tool_environment(mingw_bin):
    ordinary(mingw_bin, directory=True)
    for tool in ("gcc.exe", "dlltool.exe", "objdump.exe"):
        ordinary(mingw_bin / tool)
    return {**os.environ, "PATH": str(mingw_bin) + os.pathsep + os.environ.get("PATH", "")}


def rustc(sdk, output, source, name, externs, *, environment, kind="rlib", edition="2024", features=()):
    if not RUST_NAME.fullmatch(name):
        raise ValueError(f"Invalid crate name: {name}")
    output.mkdir(parents=True, exist_ok=True)
    command = ["rustup", "run", TOOLCHAIN, "rustc", "--edition", edition, "--crate-type", kind,
               "--crate-name", name, "-C", "prefer-dynamic", "-C", "opt-level=3", "--cap-lints", "allow",
               "--out-dir", str(output)]
    for path in (sdk / "deps", sdk / "host", output):
        command.extend(("-L", f"dependency={path}"))
    command.extend(("-L", f"native={sdk / 'native'}"))
    for feature in features:
        command.extend(("--cfg", f'feature="{feature}"'))
    arguments = output / (name + ".externs")
    write(arguments, "".join(f"--extern\n{alias}={path}\n" for alias, path in sorted(externs.items())))
    subprocess.run([*command, "@" + str(arguments), str(source)], check=True, cwd=source.parent, env=environment)
    result = output / (name + ".dll" if kind == "dylib" else "lib" + name + ".rlib")
    ordinary(result)
    return result


def entry(template, abi, rust_release, id, version, *, side=None, installs=()):
    built = ("" if side is None else f'    mod_api::__private::native::BuiltMod {{ id: "{id}", '
                                     f'version: "{version}", side: mod_api::Side::{side} }},')
    hooks = "" if side is None else f'        "{id}" => crate::hooks,'
    identity = f"{abi} {id} {version} {rust_release}"
    for before, after in (("/* MOD_ENTRIES */", hooks), ("/* MOD_SLOTS */", ""), ("/* BUILT_MODS */", built),
                          ("/* NATIVE_BUILD */", identity), ("/* MOD_REDIRECTS */", "\n".join(f"{x}," for x in installs))):
        template = replace_once(template, before, after)
    return template, identity


def stamp(library, id, identity, kind):
    row = {"file": library.name, "sha256": sha(library), "kind": kind, "mod": id, "identity": identity}
    write(library.with_suffix(".identity.json"), json.dumps(row, indent=2) + "\n")


def verify_exports(library, mingw_bin, identity):
    table = subprocess.check_output([str(mingw_bin / "objdump.exe"), "-p", str(library)],
                                    encoding="utf-8", errors="replace")
    count = re.search(r"(?m)^\s+Export Address Table\s+([\da-fA-F]{8})\s*$", table)
    exports = table.partition("[Ordinal/Name Pointer] Table -- Ordinal Base ")[2]
    required = {"horizon_native_build", "horizon_native_mod_v1"}
    symbols = set(re.findall(r"(?m)^\s*\[\s*\d+\]\s+\+base\[\s*\d+\]\s+[\da-fA-F]+\s+(\w+)\s*$", exports))
    if ("file format pei-x86-64" not in table or not count or int(count[1], 16) > 65535
            or not required <= symbols or identity.encode("ascii") + b"\0" not in library.read_bytes()):
        raise ValueError(f"{library.name}: invalid Windows AMD64 SDK exports or build identity")


def generate_copies(args, plan, abi, *, release=True):
    projected = args.out / "projection.json"
    write(projected, json.dumps(plan, indent=2) + "\n")
    generated = args.out / "generated-copies"
    generator = args.sdk_stage / "crates/horizon-mod-api-gen"
    upstream_patch = local_file(generator, "src/patch.rs")
    original = upstream_patch.read_bytes()
    if sha(upstream_patch) != GENERATOR_SHA256:
        raise ValueError("Horizon changed the SDK function-copy generator")
    # Upstream adds every support module to the client mirror. Legacy mod
    # support modules also target Render, ClientLib and World modules, where
    # their helpers and patched functions must share the same Rust crate.
    fixed = replace_once(original.decode("utf-8"),
        'let mut node = trees.entry("horizon_client".to_owned()).or_default();\n'
        '                for segment in path.split("::").skip(1) {\n'
        '                    node = node.children.entry(segment.to_owned()).or_default();\n'
        '                }\n                node.own.insert(module.name.clone());',
        'let mut scope = path.split("::");\n'
        '                let mut node = trees.entry(scope.next().expect("a crate").to_owned()).or_default();\n'
        '                for segment in scope {\n'
        '                    node = node.children.entry(segment.to_owned()).or_default();\n'
        '                }\n                node.own.insert(module.name.clone());')
    # A module that only gains support code was not widened by horizon-stage.
    # Its mirror cannot re-export the SDK's private items.
    fixed = replace_once(fixed, "    copies: Vec<proc_macro2::TokenStream>,",
                         "    copies: Vec<proc_macro2::TokenStream>,\n    has_replacements: bool,")
    fixed = replace_once(fixed, "        node.copies.push(copy);",
                         "        node.has_replacements = true;\n        node.copies.push(copy);")
    fixed = replace_once(fixed, 'Some("horizon_client") && tree.copies.is_empty()',
                         'Some("horizon_client") && !tree.has_replacements')
    # The build's staging source is idle once the SDK is produced. Restore the
    # compiler bytes and remove the temporary Cargo example on every exit.
    tool = generator / "examples/lanminecraft_shared_sdk_patch.rs"
    extension = tool.parent / "lanminecraft_shared_sdk_extensions/mod.rs"
    environment = {**tool_environment(args.mingw_bin), "RUSTUP_TOOLCHAIN": TOOLCHAIN,
                   "CARGO_TARGET_DIR": str(args.host_target), "CARGO_NET_OFFLINE": "true"}
    environment.pop("RUSTFLAGS", None)
    environment.pop("CARGO_ENCODED_RUSTFLAGS", None)
    write(tool, (TOOLS / "shared_sdk_patch.rs").read_text(encoding="utf-8"))
    extension_created = False
    try:
        write(extension, (TOOLS / "shared_sdk_extensions.rs").read_text(encoding="utf-8"))
        extension_created = True
        upstream_patch.write_bytes(fixed.encode("utf-8"))
        command = ["cargo", "run", "--locked", "--offline", *(["--release"] if release else []),
                   "-p", "horizon-mod-api-gen", "--example",
                   "lanminecraft_shared_sdk_patch", "--", str(projected), str(args.inputs), str(args.patch_source),
                   str(args.sdk / "functions.json"), abi, str(generated)]
        subprocess.run(command, check=True, cwd=args.sdk_stage, env=environment)
    finally:
        upstream_patch.write_bytes(original)
        tool.unlink()
        if extension_created:
            extension.unlink()
            extension.parent.rmdir()
    tool_binary = args.host_target / ("release" if release else "debug") / "examples" / (tool.stem + ".exe")
    ordinary(tool_binary)
    return generated, load(generated / "patch.json"), tool_binary


def apply_mixins(args, plan, generated, patch):
    copies = {function["target"]: function for function in patch["functions"]}
    targets = defaultdict(set)
    workspace = args.out / "patch-workspace"
    bundles = args.out / "patch-mods"
    names = [name for name in patch["crates"] if RUST_NAME.fullmatch(name)]
    if names != patch["crates"] or not names:
        raise ValueError("Unexpected replacement crate inventory")
    write(workspace / ".horizon-revision", patch["abiBuildId"] + "\n")
    write(workspace / "Cargo.toml", "[workspace]\nresolver = \"2\"\nmembers = " + json.dumps(names) + "\n")
    lock = "version = 4\n" + "".join(f'\n[[package]]\nname = "{name}"\nversion = "0.0.0"\n' for name in names)
    write(workspace / "Cargo.lock", lock)
    for name in names:
        manifest = f'[package]\nname = "{name}"\nversion = "0.0.0"\nedition = "2024"\n'
        write(workspace / name / "Cargo.toml", manifest)
        write(workspace / name / "src/lib.rs", (generated / f"{name}.rs").read_text(encoding="utf-8"))
    for mod in plan["mods"]:
        injections = mod.get("injections", [])
        if not injections:
            continue
        output = bundles / mod["id"]
        source = args.inputs / mod["source"]
        write(output / "horizon-mixin.toml", encode_rows(mod, copies, patch["abiBuildId"]))
        for row in injections:
            targets[row["target"]].add(mod["id"])
            destination = output / row["helper"]
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(local_file(source, row["helper"]), destination)
    if set(targets) != set(copies) or any(not value["install"] for value in copies.values()):
        raise ValueError("Some selected source hooks have no SDK replacement")
    stage = args.out / "patch-applied"
    subprocess.run([str(args.mixins), "apply", "--workspace", str(workspace), "--mods", str(bundles), "--out", str(stage)],
                   check=True)
    installs = [f'    ({json.dumps(name)}, &{json.dumps(sorted(targets[name]))}, {value["install"]})'
                for name, value in sorted(copies.items())]
    return stage, names, installs


def dependencies(sdk, manifest):
    aliases = {"horizon_mod_api": "mod_api", "horizon_mod_vocab": "mod_vocab", "horizon_mod_points": "mod_points"}
    return {alias: sdk_library(sdk, aliases.get(crate, crate)) for alias, crate in manifest["dependencies"].items()}


def module_routes(plan, index):
    destinations = defaultdict(set)
    for crate in index["crates"].values():
        for module, files in crate["modules"].items():
            for file in files:
                destinations[file["path"]].add(module)
    routes = {}
    for mod in plan["mods"]:
        for item in mod.get("modules", []):
            possible = destinations[item["file"]]
            if len(possible) != 1:
                raise ValueError(f"{mod['id']}: cannot identify the SDK module at {item['file']}")
            original = next(iter(possible)) + "::" + item["name"]
            engine, _, path = original.partition("::")
            patch = "horizon_patched" if engine == "horizon_client" else f"horizon_patched_{engine.removeprefix('horizon_')}"
            aliases = [original]
            if engine == "horizon_client_lib":
                aliases.append("horizon_client::" + path)
            if original == "horizon_client_render::chat::native_chatheads_layout":
                aliases.append("horizon_client::" + path)  # Chat is re-exported by ClientLib.
            for alias in aliases:
                if routes.setdefault(alias, (patch, path)) != (patch, path):
                    raise ValueError(f"Ambiguous mod module {alias}")
    return routes


def adapt_tectonic_world(text, source):
    generator = local_file(source, "crates/horizon-world/src/worldgen/generator.rs")
    # Preserve the copied caller's pure error constructor and imports of
    # public SDK items from modules without replacements in this mirror.
    body = re.findall(r"(?m)^fn resource\(message: impl Into<String>\) -> GeneratorError \{\n"
                      r"    GeneratorError::Resource\(message.into\(\)\)\n\}",
                      generator.read_text(encoding="utf-8"))
    if len(body) != 1 or "pub use ::horizon_world::worldgen::generator::resource;" in text:
        raise ValueError("Tectonic's private generator error helper changed")
    world = "    pub use ::horizon_world::worldgen::decode_noise;\n"
    module = "    pub mod generator {\n"
    structure = "    pub mod structure {\n"
    for before, after in (
        (world, world + "    pub use ::horizon_world::worldgen::generator_structures;\n"),
        (module, module + textwrap.indent(body[0], "        ") + "\n"
                       + "        pub use ::horizon_world::worldgen::generator::{queries, WorkerState};\n"),
        (structure, structure + "        pub use ::horizon_world::worldgen::structure::{BlockInfo, StructureTemplate};\n"),
        ("        use super::{DecodeError, Object, number_text, optional, required};\n", ""),
        ("            biome_chunks::BiomeChunks,\n", ""),
        ("decode_noise, density::DensityRegistry, generator_structures::Starts,", "decode_noise, density::DensityRegistry,"),
    ):
        text = replace_once(text, before, after)
    return text


def patch_sources(args, plan, stage, names, index, *, game_adapter=None):
    # Old helpers used added modules through their original game crate names.
    # Point only those added modules at the replacement crate that owns their
    # state. All engine types and stock modules continue using the shared SDK.
    routes = module_routes(plan, index)
    alternatives = "|".join(re.escape(path) for path in sorted(routes, key=len, reverse=True))
    qualified = re.compile(r"(?<![\w:])(?:::)?(" + alternatives + r")(?!\w)") if routes else None
    pending = {}
    ordered = []
    for name in names:
        required = set()

        def bind(match):
            patch, path = routes[match[1]]
            if patch == name:
                return f"crate::{path}"
            required.add(patch)
            return f"::{patch}::{path}"

        text = (stage / name / "src/lib.rs").read_text(encoding="utf-8")
        if name == "horizon_patched_client_render":
            text = adapt_render(text)
        if name == "horizon_patched_world" and any(mod["id"] == "tectonic_horizon" for mod in plan["mods"]):
            text = adapt_tectonic_world(text, args.patch_source)
        source = args.out / "link-sources" / f"{name}.rs"
        write(source, qualified.sub(bind, text) if qualified else text)
        if name == "horizon_patched" and game_adapter is not None:
            adapted = source.with_suffix(".adapted.rs")
            subprocess.run([str(game_adapter), "--adapt-game-source", str(source), str(adapted)], check=True)
            adapted.replace(source)
        pending[name] = (source, required)
    complete = set()
    while pending:
        ready = [name for name, (_, required) in pending.items() if required <= complete]
        if not ready:
            raise ValueError(f"Cyclic or missing patch crate dependency: {pending}")
        for name in ready:
            source, required = pending.pop(name)
            ordered.append((name, source, required))
        complete.update(ready)
    return ordered


def build_libraries(args, plan, sdk, stage, names, installs, game_adapter):
    abi = sdk["commit"]
    rust_release = re.search(r"(?m)^release: (\S+)$", sdk["rustc"]).group(1)
    entry_template = local_file(args.patch_source, REGISTRAR_TEMPLATE)
    if sha(entry_template) != TEMPLATE_SHA256:
        raise ValueError("The SDK native entry template differs from the reviewed version")
    template = entry_template.read_text(encoding="utf-8")
    environment = tool_environment(args.mingw_bin)
    output = args.out / "libraries"
    payload = args.out / "pack/native"
    packages = payload / "horizon-native-mods"
    packages.mkdir(parents=True)
    common = {name: sdk_library(args.sdk, name) for name in ("mod_api", "mod_vocab", "mod_points")}
    libraries = []
    externs = {name: sdk_library(args.sdk, name) for name in ["horizon_client", *sdk["facade_crates"]]}
    for mod in plan["mods"]:
        if "content" in mod:
            content = local_file(args.inputs, mod["content"])
            destination = payload / "horizon-mods" / content.name
            destination.parent.mkdir(exist_ok=True)
            shutil.copyfile(content, destination)
        for library in mod.get("supportCrates", []):
            source = args.inputs / library["source"]
            name = library["crate"]
            externs[name] = rustc(args.sdk, output, source / library["entry"], name,
                                  dependencies(args.sdk, library), edition=library["edition"], environment=environment)
        registrar = mod.get("registrar")
        if not registrar:
            continue
        id, version = mod["id"], mod["version"]
        package = packages / id
        shutil.copytree(args.inputs / registrar["source"], package)
        manifest = package / "horizon-mod.toml"
        metadata = manifest.read_text(encoding="utf-8")
        manifest.write_text(replace_once(metadata, f'horizon = "{REVISION}"', f'horizon = "{abi}"'),
                            encoding="utf-8", newline="\n")
        scratch = args.out / "registrar-sources" / id
        source = scratch / registrar["entry"]
        shutil.copytree(package / "src", scratch / "src")
        original = source.read_text(encoding="utf-8")
        if re.search(r"#!\s*\[\s*forbid\([^\]]*\bunsafe_code\b", original):
            raise ValueError(f"{id}: its crate forbids the SDK's generated native export module")
        source.write_text(original + '\n#[allow(unsafe_code)]\n#[path = "__horizon_entry.rs"] mod __horizon_entry;\n',
                          encoding="utf-8", newline="\n")
        entry_source, identity = entry(template, abi, rust_release, id, version, side=LIBRARY_OWNERS[registrar["side"]])
        write(source.with_name("__horizon_entry.rs"), entry_source)
        link = {**common, **dependencies(args.sdk, registrar)}
        name = registrar["crate"]
        library = rustc(args.sdk, output, source, name, link, kind="dylib", edition=registrar["edition"],
                        environment=environment)
        verify_exports(library, args.mingw_bin, identity)
        stamp(library, id, identity, "mod")
        libraries.append(library)
        externs[name] = library
    patches = {}
    for name, source, required in patch_sources(args, plan, stage, names, load(args.sdk / "functions.json"),
                                                game_adapter=game_adapter):
        patch_deps = {owner: patches[owner] for owner in required}
        patches[name] = rustc(args.sdk, output, source, name, {**externs, **patch_deps},
                              features=sdk.get("game_features", []), environment=environment)
    source = args.out / "horizon_patch.rs"
    patched, identity = entry(template, abi, rust_release, "horizon_patch", "0", installs=installs)
    write(source, patched + "\nextern crate horizon_mod_host as _;\n")
    bridge = {name: sdk_library(args.sdk, name) for name in ("horizon_client", "horizon_mod_package", "horizon_mod_host")}
    links = {**common, **patches, **bridge}
    library = rustc(args.sdk, output, source, "horizon_patch", links, kind="dylib", environment=environment)
    verify_exports(library, args.mingw_bin, identity)
    stamp(library, "horizon_patch", identity, "patch")
    return payload, [*libraries, library]


def compiled_inventory(plan, native, record):
    libraries = {row["mod"]: row for row in record["libraries"]}
    hashes = {}
    mods = []
    for mod in plan["mods"]:
        id = mod["id"]
        library = libraries.get(id)
        if library is not None:
            path = library["file"]
        elif mod.get("injections"):
            path = libraries["horizon_patch"]["file"]
        else:
            path = "horizon-mods/" + Path(mod["content"]).name
        if path not in hashes:
            hashes[path] = sha(local_file(native, path))
        mods.append({"id": id, "version": mod["version"], "source": "native/" + path,
                     "sha256": hashes[path]})
    return mods


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("edition", "inputs", "sdk", "receipt", "sdk-stage", "patch-source", "host-target", "mixins",
                 "mingw-bin", "out"):
        parser.add_argument(f"--{name}", required=True, type=lambda value: Path(value).absolute())
    parser.add_argument("--selection", type=Path, default=TOOLS / "mod-selection.json")
    args = parser.parse_args()
    root = args.out.parent
    for folder in (root, args.edition, args.inputs, args.sdk, args.sdk_stage, args.patch_source):
        ordinary(folder, directory=True)
    for file in (args.mixins, args.receipt):
        ordinary(file)
    if (args.out.exists() or args.out.is_symlink() or root.parent.name != "agent-runs"
            or root.parent.parent.name != "artifacts" or load(root / "RUN.json")["status"] != "active"
            or not args.sdk_stage.is_relative_to(root) or not args.host_target.is_relative_to(root)):
        raise ValueError("Use a new direct output directory and SDK build inputs in the same active registered run")
    receipt = load(args.receipt)
    plan = verify_pack_inputs(args.inputs, args.selection)
    sdk = verify_sdk(args.sdk, receipt)
    verify_mixins_tool(args.mixins)
    generated, patch, game_adapter = generate_copies(args, plan, sdk["commit"])
    stage, names, installs = apply_mixins(args, plan, generated, patch)
    payload, libraries = build_libraries(args, plan, sdk, stage, names, installs, game_adapter)
    profile = args.edition / "integration/shared-native/profile.py"
    command = [sys.executable, "-B", str(profile), "--sdk", str(args.sdk), "--sdk-receipt", str(args.receipt),
               "--pack-root", str(payload.parent)]
    for library in libraries:
        command.extend(("--library", str(library)))
    subprocess.run(command, check=True)
    marker = payload / "native-mods.json"
    record = load(marker)
    record["legacyModIds"] = sorted(mod["id"] for mod in plan["mods"] if mod.get("legacy"))
    marker.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    proof = {"schemaVersion": 1, "abiBuildId": sdk["commit"], "sdkIdentitySha256": receipt["sdkIdentitySha256"],
             "clientSha256": receipt["clientSha256"], "nativeProfileSha256": sha(marker),
             "nativeTreeSha256": tree_hash(payload), "packInputsSha256": sha(args.inputs / "mods.json"),
             "injections": sum(len(mod.get("injections", ())) for mod in plan["mods"]),
             "functionReplacements": len(installs), "compiledMods": compiled_inventory(plan, payload, record)}
    write(args.out / "mod-sidecar-receipt.json", json.dumps(proof, indent=2) + "\n")
    print(marker)


if __name__ == "__main__":
    main()
