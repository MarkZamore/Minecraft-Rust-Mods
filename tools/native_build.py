"""Plan and compile one reviewed combination of Horizon mods with the Steam overlay."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
import sys
import textwrap
import tomllib
import zipfile

from inspect_mods import MOD_KINDS, read_archive, selected_ids, validate_members


TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
RECIPE = Path(__file__).resolve()
INPUT_LOCK = TOOLS / "mod-input-lock.json"
DECISIONS = TOOLS / "conflict-decisions.json"
HORIZON_VERSION = "nightly-2026-11-07-2ebf00a"
RUST = "1.98.1"
MIXINS_REPO = "https://github.com/horizon-rust/horizon-mixins"
MIXINS_REVISION = "e8cd0aa38cffef49cb2e088ef716baa107148c02"
WINDOWS_TARGET = "x86_64-pc-windows-msvc"
EXE = ".exe" if os.name == "nt" else ""
ATLAS_RUNTIME_SHA256 = "58d3a1bdd2d6ce7bb840d96cb727dc261a0d8fdb8e11e912e16c29c7f4171dac"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load(path):
    return json.loads(path.read_bytes())


def write(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.write("\n")


def run(args, env, *, capture=False):
    return subprocess.run(args, check=True, env=env, text=True, encoding="utf-8", capture_output=capture)


def build_recipe_sha():
    sources = [RECIPE, TOOLS / "orbital_controls.py", TOOLS / "orbital_world_guard.py", TOOLS / "physics_title_compat.py", TOOLS / "jei_compat.py", TOOLS / "spider_compat.py", TOOLS / "server_seed_compat.py",
               TOOLS / "multiplayer_warning.py", TOOLS / "legacy_native_hooks.py", TOOLS / "legacy_native_hooks.rs",
               TOOLS / "materialize_game_modules.rs", TOOLS / "mixins_tool.py",
               TOOLS / "mixins-contained-rust-paths.patch",
               TOOLS / "direct_hook_relocations.py",
               TOOLS / "tectonic_compat.py",
               TOOLS / "camera_compat.py", TOOLS / "inventory_compat.py", TOOLS / "placement_input_compat.py",
               TOOLS / "elytra_compat.py", TOOLS / "negotiated_native_mods.py", TOOLS / "legacy_payload_commit.rs",
               *(TOOLS / "orbital_compat").rglob("*")]
    files = {}
    for path in sources:
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"Linked file in native build recipe: {path.name}")
        if path.is_file() and "__pycache__" not in path.parts:
            files[path.relative_to(ROOT).as_posix()] = sha(path)
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode("ascii")).hexdigest()


def write_content_companion(archive, output):
    with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED) as companion:
        for member in sorted(archive.infolist(), key=lambda entry: entry.filename):
            name = member.filename
            if member.is_dir() or (name != "horizon-mod.toml" and not name.startswith(("assets/", "data/"))):
                continue
            entry = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            with archive.open(member) as source, companion.open(entry, "w") as destination:
                shutil.copyfileobj(source, destination)


def patch_server_atlas(bundle):
    path = bundle / "runtime.rs"
    if sha(path) != ATLAS_RUNTIME_SHA256:
        raise ValueError("Server Atlas must match the reviewed 2.2.1 source for the Steam row overlay")
    source = path.read_text(encoding="utf-8")
    before = """                    let motd = status
                        .map(|s| {
                            crate::component::to_text(&s.description, &mut |s| Text::literal(s))
                                .resolve_legacy()
                        })
                        .unwrap_or_else(|| Text::literal(health(status)));"""
    host_row = """                    let steam_host = if self.state.view.tab == Tab::Lan {
                        r.lan_games.iter().find(|game| game.address == s.address)
                            .and_then(|game| game.steam_host.as_deref())
                    } else {
                        None
                    };
                    let is_online = steam_host.is_some() || status.is_some_and(|s| s.health == Health::Online);"""
    after = """                    let motd = if let Some(host) = steam_host {
                        Text::literal(host)
                    } else {
                        status.map(|s| {
                            crate::component::to_text(&s.description, &mut |s| Text::literal(s))
                                .resolve_legacy()
                        }).unwrap_or_else(|| Text::literal(health(status)))
                    };"""
    extra_address = "                    if has_line2 && tag_x < left + width - 50.0 {"
    online = "                    let is_online = status.is_some_and(|s| s.health == Health::Online);"
    viewport = "        .map(|&index| r.source[index].address.clone())\n        .collect()"
    recent = "        self.record_recent(server);\n        Outcome::Connect"
    last_server = "                title.last_server = address.clone();\n                Action::Connect"
    changes = ((online, host_row), (before, after),
               (extra_address, extra_address.replace("if has_line2", "if steam_host.is_none() && has_line2")),
               (viewport, "        .map(|&index| r.source[index].address.as_str())\n"
                "        .filter(|address| !address.starts_with(\"lanmc-steam:\"))\n"
                "        .map(str::to_owned)\n        .collect()"),
               (recent, "        if !address.starts_with(\"lanmc-steam:\") {\n"
                "            self.record_recent(server);\n        }\n        Outcome::Connect"),
               (last_server, "                if !address.starts_with(\"lanmc-steam:\") {\n"
                "                    title.last_server = address.clone();\n                }\n                Action::Connect"))
    for old, new in changes:
        if source.count(old) != 1:
            raise ValueError("A reviewed Server Atlas Steam row anchor changed")
        source = source.replace(old, new)
    third_line = "                    // Row Line 3 (y + 31.0): MOTD Line 2 or Server Address (Host:Port)"
    following = "                    let ms = status.and_then(|s| s.ms);"
    start = source.index(third_line)
    end = source.index(following, start)
    normal_row = textwrap.indent(source[start:end].rstrip(), "    ")
    source = (source[:start] + "                    if steam_host.is_none() {\n" + normal_row +
              "\n                    }\n\n" + source[end:])
    path.write_text(source, encoding="utf-8", newline="\n")


def unpack_inputs(rows, archives, work, revision):
    from legacy_native_hooks import REVISION, route_bundle_source, route_manifest

    native_bundles = work / "mixins-input"
    native_bundles.mkdir()
    content = work / "content"
    content.mkdir()
    for row in rows:
        file = row["file"]
        path = archives / (file["sha256"] + MOD_KINDS[row["kind"]])
        if path.is_symlink() or path.is_junction() or path.stat().st_size != file["size"] or sha(path) != file["sha256"]:
            raise ValueError(f"Archive changed after provenance verification: {row['id']}")
        read_archive(row, path)
        if row["kind"] == "mod":
            with zipfile.ZipFile(path) as archive:
                if "mod.wasm" in archive.namelist():
                    raise ValueError(f"{row['id']} needs the WebAssembly runtime removed by this Horizon revision")
            shutil.copyfile(path, content / (row["id"] + ".hmod"))
            continue

        prefix = f"mixins/{row['id']}/"
        bundle = native_bundles / row["id"]
        bundle.mkdir()
        with zipfile.ZipFile(path) as archive:
            validate_members(archive)
            if companion := row.get("contentCompanion"):
                package = content / (row["id"] + ".hmod")
                with package.open("xb") as output:
                    write_content_companion(archive, output)
                expected = companion["file"]
                if package.stat().st_size != expected["size"] or sha(package) != expected["sha256"]:
                    raise ValueError(f"Derived content companion differs from its lock: {row['id']}")
            for member in archive.infolist():
                if member.is_dir() or not member.filename.startswith(prefix):
                    continue
                relative = member.filename[len(prefix):]
                parts = relative.split("/")
                if any(PureWindowsPath(part).is_reserved() or part.endswith((".", " ")) for part in parts):
                    raise ValueError(f"Reserved Windows bundle path: {relative}")
                target = bundle.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("xb") as dest:
                    shutil.copyfileobj(source, dest)
        if row["id"] == "server_browser":
            patch_server_atlas(bundle)
        if row["id"] == "horizon_physics":
            from physics_title_compat import apply as apply_physics_title
            apply_physics_title(bundle, revision=revision)
        if row["id"] == "orbital":
            from orbital_compat import apply
            apply(bundle, revision=revision)
        if row["id"] == "jei":
            from jei_compat import apply as apply_jei
            apply_jei(bundle, revision=revision)
        if row["id"] == "elytratrails":
            from elytra_compat import apply as apply_elytra
            apply_elytra(bundle, revision=revision)
        manifest = bundle / "horizon-mixin.toml"
        text = manifest.read_text(encoding="utf-8")
        parsed = tomllib.loads(text)
        if set(parsed).intersection({"api", "dependencies", "post_build"}):
            raise ValueError(f"{row['id']} requires a different upstream native staging pipeline")
        # The archive pin identifies its tested revision. The compatibility probe tests a separate copy on ours.
        revision_line = re.compile(r"^(\s*horizon_revision\s*=\s*)['\"][0-9a-f]{40}['\"]", re.MULTILINE)
        text, count = revision_line.subn(lambda match: f'{match[1]}"{revision}"', text)
        if count != 1:
            raise ValueError(f"Cannot bind {row['id']} to the selected probe revision")
        if revision == REVISION:
            try:
                text = route_manifest(text)
                if row["id"] == "tectonic_horizon":
                    from tectonic_compat import route_manifest as route_tectonic
                    text = route_tectonic(text)
                for source in bundle.rglob("*.rs"):
                    original = source.read_text(encoding="utf-8")
                    patched = route_bundle_source(original)
                    if patched != original:
                        source.write_text(patched, encoding="utf-8", newline="\n")
            except ValueError as error:
                raise ValueError(f"{row['id']} {row['version']}: {error}") from error
        manifest.write_text(text, encoding="utf-8", newline="\n")
    return native_bundles


def module_at(path):
    spec = importlib.util.spec_from_file_location("base_overlay", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_base(args, revision, *, prepared=False):
    base = module_at(args.base_pack / "integration/steam/apply_horizon_overlay.py")
    if base.COMMIT != revision:
        raise ValueError("Steam overlay and mod inputs target different Horizon revisions")
    if not prepared:
        originals = base.locked_inputs(args.source_archive)
        patched = base.changes(originals, args.steam_transport)
        base.apply_or_validate(args.base_source, originals, patched, False)
        base.verify_build_tree(args.base_source, args.source_archive, patched)
    return base.recipe_sha()


def verify_downloaded_lock(reference, candidate):
    def external(path):
        return {(package["name"], package["version"], package["source"], package.get("checksum"))
                for package in tomllib.loads(path.read_text(encoding="utf-8"))["package"]
                if "source" in package}
    extra = external(candidate) - external(reference)
    if extra:
        raise ValueError(f"The build wants crates outside the locked Horizon dependency set: {sorted(extra)}")


def tree_hash(root):
    if not root.is_dir() or root.is_symlink() or root.is_junction():
        raise ValueError(f"Expected a regular prepared directory: {root.name}")
    files = {}
    for path in root.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"A prepared directory contains a link: {path.name}")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = sha(path)
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode("ascii")).hexdigest()


def restore_partial_source(base, work, revision):
    from multiplayer_warning import EDITION_ACTION, PACK_ACTION, SET_MODE_ACTION, SET_MODE_EDITION_ACTION, TITLE
    from server_seed_compat import OFFICIAL_SOURCE, SOURCE, adapt, adapt_official

    source = work / "source"
    if (work.is_symlink() or work.is_junction() or not work.is_dir()
            or list(work.iterdir()) != [source] or source.resolve() == base):
        raise ValueError("Resume requires an isolated source-only preparation directory")

    def entries(root):
        if root.is_symlink() or root.is_junction() or not root.is_dir():
            raise ValueError("Resume requires an ordinary source directory")
        result = set()
        for directory, folders, files in os.walk(root, followlinks=False):
            for name in (*folders, *files):
                item = Path(directory) / name
                if item.is_symlink() or item.is_junction() or not (item.is_dir() or item.is_file()):
                    raise ValueError("Resume cannot reuse linked or special source entries")
                result.add((item.relative_to(root).as_posix(), item.is_dir()))
        return result

    marker = (".horizon-revision", False)
    expected = entries(base)
    if (marker in expected or entries(source) != expected | {marker}
            or (source / marker[0]).read_bytes() != (revision + "\n").encode("ascii")):
        raise ValueError("Resume source tree does not match the verified base")

    changes = {}
    for name, directory in expected:
        if directory or sha(base / name) == sha(source / name):
            continue
        original = (base / name).read_bytes()
        known = None
        if name == SOURCE:
            known = adapt(original.decode("utf-8")).encode("utf-8")
        elif name == OFFICIAL_SOURCE:
            known = adapt_official(original)
        elif name == TITLE and original.count(EDITION_ACTION) == 1:
            known = original.replace(EDITION_ACTION, PACK_ACTION.encode("utf-8"))
        elif name == TITLE and original.count(SET_MODE_EDITION_ACTION) == 1:
            known = original.replace(SET_MODE_EDITION_ACTION, SET_MODE_ACTION.encode("utf-8"))
        if known is None or (source / name).read_bytes() != known:
            raise ValueError(f"Resume source contains an unreviewed change: {name}")
        changes[name] = original

    for name, original in changes.items():
        (source / name).write_bytes(original)


def inject_latest_packaged_content(stage):
    from legacy_native_hooks import replace_once

    game = stage / "crates/horizon-client/src/game.rs"
    mods = stage / "crates/horizon-client/src/game/modding/mods/mod.rs"
    selection = stage / "crates/horizon-mod-package/src/selection.rs"
    game_text = replace_once(game.read_text(encoding="utf-8"),
        "    options.mods.native = Arc::new(native);",
        '''    let installed = std::env::current_exe()?.with_file_name("horizon-mods");
    let mut packaged = horizon_mod_package::find(&installed);
    packaged.retain(|path| path.is_file());
    packaged.append(&mut options.mods.dev);
    options.mods.dev = packaged;
    options.mods.native = Arc::new(native);''')
    mods_text = replace_once(mods.read_text(encoding="utf-8"),
        "            Ok(_) if source.content_only => {\n                content_mods.push(",
        '            Ok(_) if source.content_only => {\n'
        '                log::info!("LANMinecraft content mod accepted: {} {}", m.id, m.version);\n'
        '                content_mods.push(')
    selection_text = replace_once(selection.read_text(encoding="utf-8"),
        '''    let mut paths = find(&game_dir.join(DIR));
    paths.extend(find(&game_dir.join(NATIVE_DIR)));
    paths.retain(|path| !dev.contains(path));
    paths.extend(dev.iter().cloned());''',
        '''    let mut paths = dev.to_vec();
    paths.extend(find(&game_dir.join(DIR)).into_iter().filter(|path| !dev.contains(path)));
    paths.extend(find(&game_dir.join(NATIVE_DIR)).into_iter().filter(|path| !dev.contains(path)));''')
    for path, contents in ((game, game_text), (mods, mods_text), (selection, selection_text)):
        path.write_text(contents, encoding="utf-8", newline="\n")


def inject_packaged_content(stage):
    game = stage / "crates/horizon-client/src/game.rs"
    source = game.read_text(encoding="utf-8")
    anchor = """    let Some(mut options) = Options::read()? else {
        return Ok(());
    };"""
    if source.count(anchor) != 1:
        raise ValueError("The packaged content launch anchor changed")
    replacement = anchor + """
    let installed = std::env::current_exe()?.with_file_name("horizon-mods");
    let mut packaged = horizon_mods::find(&installed);
    packaged.retain(|path| path.is_file());
    packaged.append(&mut options.dev_mods);
    options.dev_mods = packaged;
""".rstrip()
    game.write_text(source.replace(anchor, replacement), encoding="utf-8", newline="\n")
    mods = stage / "crates/horizon-client/src/game/mods.rs"
    source = mods.read_text(encoding="utf-8")
    anchor = """    let mut paths = horizon_mods::find(&here.join(mod_list::DIR));
    paths.retain(|p| !dev.contains(p));
    paths.extend(dev.iter().cloned());"""
    if source.count(anchor) != 1:
        raise ValueError("The content load order anchor changed")
    replacement = """    let mut paths = dev.to_vec();
    paths.extend(horizon_mods::find(&here.join(mod_list::DIR)).into_iter().filter(|p| !dev.contains(p)));"""
    source = source.replace(anchor, replacement)
    announcement = "                horizon_protocol::mod_list::set_local(announced);"
    if source.count(announcement) != 1:
        raise ValueError("The content registration observation anchor changed")
    observation = """                for entry in &announced {
                    log::info!("LANMinecraft content mod accepted: {} {}", entry.id, entry.version);
                }
"""
    mods.write_text(source.replace(announcement, observation + announcement), encoding="utf-8", newline="\n")
    hooks = stage / "crates/horizon-client/src/game/native_hooks.rs"
    source = hooks.read_text(encoding="utf-8")
    anchor = """        for m in mods {
            let mut buttons = Vec::new();
            self.native_settings(&m.id, &mut buttons);"""
    if source.count(anchor) != 1:
        raise ValueError("The native mod registration observation anchor changed")
    replacement = anchor.replace("            let mut buttons", '            log::info!("LANMinecraft native mod registered: {}", m.id);\n            let mut buttons')
    hooks.write_text(source.replace(anchor, replacement), encoding="utf-8", newline="\n")


def build_runtime(stage, package, output, env, target_arguments):
    run(["cargo", "build", "--locked", "--offline", "--release", "--manifest-path", stage / "Cargo.toml",
         "-p", package, "--bin", "horizon-client", *target_arguments], env)
    target = Path(env["CARGO_TARGET_DIR"])
    if target_arguments:
        target /= WINDOWS_TARGET
    output.mkdir(exist_ok=True)
    executable = output / ("horizon-client" + EXE)
    pending = executable.with_suffix(".pending")
    if pending.exists():
        raise ValueError("Unexpected pending client from a previous build")
    shutil.copyfile(target / "release" / executable.name, pending)
    pending.replace(executable)


def materialize_game_modules(source, env):
    # Rust discovers these modules through OUT_DIR. The mixin planner reads source without Cargo.
    name = "lanminecraft_materialize_modules"
    crate = source / "crates/horizon-mod-api-gen"
    entry = crate / "examples" / (name + ".rs")
    entry.parent.mkdir(exist_ok=True)
    with entry.open("xb") as file:
        file.write((TOOLS / "materialize_game_modules.rs").read_bytes())
    try:
        run(["cargo", "run", "--manifest-path", crate / "Cargo.toml", "--locked", "--offline",
             "--release", "--example", name, "--", source.resolve()], env)
    finally:
        entry.unlink()


def apply_mixins(source, bundles, stage, mixins, env):
    if not any(bundles.iterdir()):
        shutil.copytree(source, stage)
        return {}
    result = run([mixins, "apply", "--workspace", source, "--mods", bundles, "--out", stage], env, capture=True)
    print(result.stdout, end="", flush=True)
    report = json.loads(result.stdout)
    if report.get("status") != "ok":
        raise ValueError("The mixin stage did not pass")
    return {key: report[key] for key in ("injections", "modules", "crates", "bridges", "states")}


def execute(args):
    from legacy_native_hooks import REVISION, prepare_source as prepare_legacy_hooks, prepare_wheel
    from multiplayer_warning import prepare_source as prepare_multiplayer_menu, verify_prepared_source

    input_bytes = INPUT_LOCK.read_bytes()
    lock = json.loads(input_bytes)
    rows = {row["id"]: row for row in lock["projects"]}
    ids = sorted(selected_ids(args.selection, input_bytes))
    selected = [rows[name] for name in ids]
    if any(row["eligibility"] not in ("candidate", "compile-probe") for row in selected):
        raise ValueError("The selection contains rejected or unreviewed mods")
    decisions = load(DECISIONS)
    if decisions["horizonRevision"] != lock["horizonRevision"]:
        raise ValueError("Conflict choices apply to another Horizon revision")
    excluded = {(component["id"], component["version"]) for decision in decisions["decisions"]
                for component in [decision["excluded"], *decision.get("excludedContent", [])]}
    if any((row["id"], row["version"]) in excluded for row in selected):
        raise ValueError("The selection contains a mod the player excluded after a confirmed conflict")
    inspection = load(args.inspection)
    if inspection.get("horizonRevision") != lock["horizonRevision"]:
        raise ValueError("The archive inspection covered another Horizon revision")
    inspected = {(row["id"], row["version"], row["sha256"]) for row in inspection["verified"]}
    if any((row["id"], row["version"], row["file"]["sha256"]) not in inspected for row in selected):
        raise ValueError("A selected archive has no verified inspection for its exact bytes")
    if not args.target.is_dir():
        raise ValueError("Use the existing shared Cargo target directory")
    recipe = verify_base(args, lock["horizonRevision"], prepared=args.build_prepared)
    source = args.work / "source"
    if not args.build_prepared:
        if args.resume_source_preparation:
            restore_partial_source(args.base_source, args.work, lock["horizonRevision"])
        else:
            args.work.mkdir()
            shutil.copytree(args.base_source, source)
            marker = source / ".horizon-revision"
            marker.write_text(lock["horizonRevision"] + "\n", encoding="ascii", newline="\n")
        from server_seed_compat import prepare_source as prepare_server_policy
        prepare_server_policy(source)
        prepare_multiplayer_menu(source)
        if lock["horizonRevision"] == REVISION:
            prepare_legacy_hooks(source)
        if "horizon_physics" in ids:
            from physics_title_compat import prepare_source as prepare_physics_source
            prepare_physics_source(source)
        if "spider_man" in ids:
            from spider_compat import prepare_source as prepare_spider_source
            prepare_spider_source(source)
        if lock["horizonRevision"] == REVISION and "tectonic_horizon" in ids:
            from tectonic_compat import prepare_source as prepare_tectonic_source
            prepare_tectonic_source(source)
        if "jei" in ids:
            from jei_compat import prepare_source as prepare_jei_source
            prepare_jei_source(source)
        if "orbital" in ids:
            from orbital_compat import prepare_source as prepare_orbital_source
            from orbital_controls import apply as apply_orbital_controls
            from orbital_world_guard import apply as apply_orbital_world_guard
            prepare_orbital_source(source, revision=lock["horizonRevision"])
            orbital = rows["orbital"]
            if lock["horizonRevision"] == REVISION and orbital["version"] == "0.5.17":
                from orbital_compat import prepare_latest_source as prepare_latest_orbital
                prepare_latest_orbital(source)
            apply_orbital_controls(source)
            apply_orbital_world_guard(source)
        if lock["horizonRevision"] == REVISION:
            prepare_wheel(source)
    env = os.environ.copy()
    env.pop("HORIZON_MOD_API_INJECTIONS", None)
    latest = lock["horizonRevision"] == REVISION
    if latest:
        from mixins_tool import REVISION as pinned_mixins_revision
        version_name = "nightly-2026-11-19-f00125f"
        nightly = "2026-11-19 f00125f"
        rust_version = "1.99.1"
        mixins_revision = pinned_mixins_revision
        package = "horizon-game"
        target_arguments = ["--target", WINDOWS_TARGET]
    else:
        version_name, nightly = HORIZON_VERSION, "2026-11-07 2ebf00a"
        rust_version, mixins_revision = RUST, MIXINS_REVISION
        package = "horizon-client"
        target_arguments = []
    env.update(CARGO_TARGET_DIR=str(args.target), RUSTFLAGS="-C target-feature=+crt-static",
               HORIZON_VERSION=version_name, HORIZON_NIGHTLY=nightly)
    version = run(["rustc", "--version"], env, capture=True).stdout.split()
    if version[1] != rust_version:
        raise ValueError(f"The reviewed toolchain uses Rust {rust_version}")
    if latest:
        from mixins_tool import sdk_tool
        mixins, sdk_identity = sdk_tool(source, args.mixins_root, EXE, env)
    else:
        mixins = args.mixins_root / "bin" / ("horizon-mixins" + EXE)
        if not mixins.is_file():
            run(["cargo", "install", "--locked", "--git", MIXINS_REPO, "--rev", mixins_revision,
                 "--root", args.mixins_root, "horizon-mixins-cli"], env)
        installed = tomllib.loads((args.mixins_root / ".crates.toml").read_text(encoding="utf-8"))["v1"]
        if not any(tool.startswith("horizon-mixins-cli ") and tool.endswith(f"#{mixins_revision})")
                   and mixins.name in executables for tool, executables in installed.items()):
            raise ValueError("The cached horizon-mixins tool comes from a different revision")
    stage = args.work / "stage"
    evidence = {"schemaVersion": 1, "inputLockSha256": sha(INPUT_LOCK), "horizonRevision": lock["horizonRevision"],
                "steamOverlayRecipeSha256": recipe, "mixinsRevision": mixins_revision, "mixinsSha256": sha(mixins),
                "conflictDecisionsSha256": sha(DECISIONS), "buildRecipeSha256": build_recipe_sha(), "rust": rust_version,
                "rustFlags": env["RUSTFLAGS"], "ids": ids, "package": package}
    if latest:
        evidence["mixinsTool"] = sdk_identity
    if args.build_prepared:
        prepared = load(args.work / "build.json")
        if (any(prepared[key] != value for key, value in evidence.items()) or
                prepared["stageTreeSha256"] != tree_hash(stage) or
                prepared["contentTreeSha256"] != tree_hash(args.work / "content") or
                (latest and prepared["compiledNativeTreeSha256"] != tree_hash(args.work / "compiled-native"))):
            raise ValueError("The source or content differs from the verified prepared stage")
        evidence["mixins"] = prepared["mixins"]
    else:
        bundles = unpack_inputs(selected, args.archives, args.work, lock["horizonRevision"])
        if latest:
            materialize_game_modules(source, env)
        evidence["mixins"] = apply_mixins(source, bundles, stage, mixins, env)
        if latest:
            inject_latest_packaged_content(stage)
            from negotiated_native_mods import install_source as install_native_mods, stage_packages
            install_native_mods(stage, selected)
            stage_packages(args.work, selected, mixins_revision)
        else:
            inject_packaged_content(stage)
        verify_downloaded_lock(source / "Cargo.lock", stage / "Cargo.lock")
    evidence["cargoLockSha256"] = sha(stage / "Cargo.lock")
    if latest:
        evidence["compiledNativeTreeSha256"] = tree_hash(args.work / "compiled-native")
    evidence["multiplayerMenuActionSha256"] = verify_prepared_source(stage)
    build = args.build or args.build_prepared
    post_build = stage / "native-post-build.json"
    if args.check or build:
        run(["cargo", "check", "--locked", "--offline", "--release", "--manifest-path", stage / "Cargo.toml",
             "-p", package, "--bin", "horizon-client", *target_arguments], env)
        evidence["compileCheck"] = True
    if build:
        if post_build.is_file() and load(post_build):
            raise ValueError("Selected mods require post-build execution; review their exact scripts before a release build")
        output = args.work / "runtime"
        build_runtime(stage, package, output, env, target_arguments)
        evidence["clientSha256"] = sha(output / ("horizon-client" + EXE))
    else:
        evidence["stageTreeSha256"] = tree_hash(stage)
        evidence["contentTreeSha256"] = tree_hash(args.work / "content")
    pending = args.work / "build.pending.json"
    write(pending, evidence)
    pending.replace(args.work / "build.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("base-pack", "base-source", "source-archive", "steam-transport", "archives", "selection", "inspection",
                  "work", "target", "mixins-root"):
        parser.add_argument("--" + field, required=True, type=lambda value: Path(value).resolve())
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true")
    action.add_argument("--build", action="store_true")
    action.add_argument("--build-prepared", action="store_true", help="Compile the same verified stage prepared without --build")
    action.add_argument("--resume-source-preparation", action="store_true",
                        help="Recheck a source-only interrupted preparation against the verified base and resume in place")
    args = parser.parse_args()
    try:
        execute(args)
    except subprocess.CalledProcessError as error:
        if error.stdout:
            print(error.stdout, file=sys.stderr)
        if error.stderr:
            print(error.stderr, file=sys.stderr)
        parser.exit(1, f"Native build failed with exit {error.returncode}\n")
    except (KeyError, OSError, ValueError) as error:
        parser.exit(1, f"Native build: {error}\n")


if __name__ == "__main__":
    main()
