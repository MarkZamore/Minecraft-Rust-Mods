"""Exercise the combined Windows native client and record loaded mod evidence."""

import argparse
import os
from pathlib import Path
import re
import sys
import time

from inspect_mods import content_file
from native_build import INPUT_LOCK, load, module_at, sha, write
import smoke_orbital
import smoke_previous_world
import smoke_startup
from stage_mod_pack import CONTROLS_PRESET, OPTIONS_TEMPLATE


WORLD = "RustModsSmoke"
CONTENT = re.compile(r"LANMinecraft content mod accepted: ([A-Za-z0-9_-]+) ([^\s]+)")
NATIVE = re.compile(r"LANMinecraft native mod registered: ([A-Za-z0-9_-]+)")
TECTONIC_PATH = re.compile(r"^\[Tectonic-Horizon\] (.+?): .+; using defaults$", re.MULTILINE)
CONTENT_ITEMS = {"deepbore_quarry": "deepbore_quarry:quarry",
                 "wayfarer_stones": "wayfarer_stones:wayfarer_stone"}
TECTONIC_ENV = "TECTONIC_HORIZON_CONFIG"


def options(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    return dict(line.split(":", 1) for line in lines if ":" in line and not line.startswith("#"))


def smoke(args):
    client = args.candidate / "native/horizon-client.exe"
    receipt = load(args.candidate / "build-receipt.json")
    if sha(client) != receipt["clientSha256"] or sha(INPUT_LOCK) != receipt["inputLockSha256"]:
        raise ValueError("The staged client differs from the build receipt")
    projects = {row["id"]: row for row in load(INPUT_LOCK)["projects"]}
    expected_content = {(project, projects[project]["version"]) for project in receipt["ids"]
                        if content_file(projects[project]) is not None}
    expected_native = {project for project in receipt["ids"] if projects[project]["kind"] == "native-mod"}
    if args.previous_qa is not None and smoke_orbital.ID not in expected_native:
        raise ValueError("The prior-world smoke test requires the Orbital update candidate")
    registered_items = [CONTENT_ITEMS[mod] for mod, _ in sorted(expected_content) if mod in CONTENT_ITEMS]
    orbital_evidence = None
    startup_evidence = None
    sys.path.insert(0, str(args.base_repo / "tools"))
    try:
        base = module_at(args.base_repo / "tools/smoke_runtime.py")
    finally:
        sys.path.pop(0)
    preset_file = args.candidate / CONTROLS_PRESET
    preset = options(preset_file)
    expected_keys = 63 if smoke_orbital.ID in expected_native else 62
    if len(preset) != expected_keys or any(not name.startswith("key_") for name in preset):
        raise ValueError(f"Expected the exact {expected_keys}-key Horizon controls preset")
    if expected_keys == 63 and preset.get(smoke_orbital.WHEEL_MAPPING) != f"key.keyboard.{smoke_orbital.WHEEL_KEY}":
        raise ValueError("Expected the published Orbital Laser wheel binding on Insert")
    args.qa.mkdir(exist_ok=args.retry_startup)
    game = args.qa / "game"
    game.mkdir(exist_ok=args.retry_startup)
    game_options = game / "options.txt"
    if args.retry_startup:
        physics = game / smoke_startup.PHYSICS_SETTINGS
        paths = (args.qa, game, game_options, physics.parent, physics)
        if (not game_options.is_file() or not (args.qa / "client-1.log").is_file()
                or (args.qa / "smoke.json").exists() or any((game / "saves").glob("*"))
                or any(path.is_symlink() or path.is_junction() for path in paths)):
            raise ValueError("Retry requires an unfinished startup-only QA directory with no saves")
        if physics.is_file():
            settings = physics.read_text(encoding="utf-8")
            physics.write_text(settings.replace("setup_done=1", "setup_done=0"), encoding="utf-8", newline="\n")
    seed = (args.candidate / OPTIONS_TEMPLATE).read_text(encoding="utf-8")
    game_options.write_text(seed + "invertYMouse:true\nsoundCategory_voice:0.0\n" +
                            "".join(f"{key}:{value}\n" for key, value in preset.items()),
                            encoding="utf-8", newline="\n")
    preview = args.qa / "preview.png"
    standalone = None
    expected_title = None
    external_config = None
    instance_config = None
    if args.bootstrap or args.minecraft is not None:
        descriptor = load(args.candidate / "portable-pack.json")
        standalone = (args.candidate / descriptor["nativeRuntime"]["executable"], args.minecraft, args.java, args.cache)
        presentation = load(args.candidate / "native/presentation.json")
        expected_title = presentation["name"]
    if standalone is not None and not args.startup_only:
        instance_config = args.qa / "game" / presentation["instanceConfig"][TECTONIC_ENV]
        instance_config.parent.mkdir(parents=True)
        instance_config.write_text("{instance QA sentinel\n", encoding="ascii")
        external_config = args.qa / "outside-game-config.json"
        external_config.write_text("{external QA sentinel\n", encoding="ascii")
        external_hash = sha(external_config)

    def observe_registered_items(control):
        counts = control("inventory")["counts"]
        if any(counts.get(item) != 1 for item in registered_items):
            raise ValueError("The registry and saved inventory must contain each tested modded item")

    def photograph(control):
        x, y, z = control("state")["player"]["position"]
        control("command", "gamemode spectator")
        control("command", "time set noon")
        control("command", "weather clear")
        control("tp", str(x + 8), str(y + 26), str(z - 12))
        control("look", "-20", "28")
        control("key", "f1", "tap")
        control("wait-for", "loaded", "--timeout", "90")
        control("screenshot", str(preview))

    def create(control):
        nonlocal orbital_evidence, startup_evidence
        if "horizon_physics" in expected_native:
            startup_evidence = smoke_startup.exercise(control, args.qa)
        control("options", "mods")
        widgets = control("ui")
        if args.startup_only and smoke_orbital.ID in expected_native:
            for _ in range(12):
                row = next((row for row in widgets["widgets"] if "Orbital Laser 0.5.9" in (row.get("label") or "")), None)
                if row is not None:
                    widgets["orbitalRow"] = row
                    break
                width, height = widgets["gui_size"]
                control("scroll-at", str(width / 2), str(height / 2), "-5")
                widgets = control("ui")
            else:
                raise ValueError("Orbital Laser 0.5.9 did not appear on the Windows Mods page")
        control("click", "done")
        if args.startup_only:
            return None, widgets, None
        control("open", WORLD, "137248", "creative")
        control("wait-for", "loaded", "--timeout", "90")
        for item in registered_items:
            control("give", item, "1")
        control("wait", "4")
        observe_registered_items(control)
        veinminer = control("command", "/veinminer status")["chat"]
        if veinminer != ["Veinminer: on"]:
            raise ValueError("Veinminer did not answer its status command")
        if smoke_orbital.ID in expected_native:
            original_position = control("state")["player"]["position"]
            orbital_evidence = smoke_orbital.exercise(control, args.qa)
            control("tp", *map(str, original_position))
        photograph(control)
        control("save")
        return control("state"), widgets, veinminer[0]

    def reopen(control):
        if startup_evidence is not None:
            smoke_startup.verify_reopened(control, game, startup_evidence)
        if args.startup_only:
            return None, None
        worlds = control("worlds")
        if not (args.qa / "game/saves" / WORLD / "level.dat").is_file():
            raise ValueError("The combined client did not save its world")
        control("open", WORLD)
        control("wait-for", "loaded", "--timeout", "90")
        observe_registered_items(control)
        if orbital_evidence is not None:
            smoke_orbital.verify_reopened(control, game, orbital_evidence)
            if not (game / "saves" / WORLD / smoke_previous_world.MARKER).is_file():
                raise ValueError("A new world did not record the current Rust Mods format")
            if any((game / "backups").glob("*.zip")):
                raise ValueError("A newly created world unexpectedly needed a migration backup")
            orbital_evidence["newWorldMigrationBackups"] = 0
        return control("state"), worlds

    def pause_loopback(control):
        control("open", WORLD)
        control("wait-for", "loaded", "--timeout", "90")
        result = base.check_pause_menu(control)
        if not control("lan", "status")["open"]:
            raise ValueError("The direct pause action did not start a loopback listener")
        control("lan", "close")
        return {**result, "liveSteamPublication": "NOT RUN"}

    title_times = []

    def run(index, action):
        started = time.monotonic()

        def measure(control):
            title_times.append(round(time.monotonic() - started, 2))
            return action(control)

        return base.run_client(client, args.controller, args.resources, args.qa, index,
                               measure, standalone, expected_title)

    old_environment = {name: os.environ.pop(name) for name in tuple(os.environ)
                       if name.upper().startswith("LANMINECRAFT_")}
    old_environment[TECTONIC_ENV] = os.environ.get(TECTONIC_ENV)
    if external_config is not None:
        os.environ[TECTONIC_ENV] = str(external_config)
    pause_menu = None
    try:
        before, widgets, veinminer = run(1, create)
        after, worlds = run(2, reopen)
        # The bootstrap drops Horizon's harness variables. Run the same verified executable
        # directly for the network UI check, so the listener is confined to loopback.
        if instance_config is not None:
            os.environ[TECTONIC_ENV] = str(instance_config)
        resources = args.resources if standalone is None else args.cache / "resources"
        if not args.startup_only:
            pause_menu = base.run_client(client, args.controller, resources, args.qa, 3, pause_loopback)
        if args.previous_qa is not None:
            migration = smoke_previous_world.verify(base.run_client, client, args.controller, resources,
                                                    args.previous_qa, standalone, expected_title)
            write(args.qa / "previous-world-migration.json", migration)
    finally:
        for name, value in old_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    if args.startup_only and any((game / "saves").glob("*")):
        raise ValueError("Startup-only QA created a world")
    if not args.startup_only:
        same_player = all(before[key] == after[key] for key in ("dimension", "gamemode"))
        same_position = all(abs(a - b) <= 1 for a, b in zip(before["player"]["position"], after["player"]["position"]))
        if not same_player or not same_position:
            raise ValueError("Reopening the modded world did not preserve the test state")
    loaded_content = set()
    registered_native = set()
    logs = []
    for index in (1, 2):
        log = (args.qa / f"client-{index}.log").read_text(encoding="utf-8", errors="replace")
        logs.append(log)
        if external_config is not None:
            config_paths = {Path(path) for path in TECTONIC_PATH.findall(log)}
            if external_config.name in log or config_paths != {instance_config}:
                raise ValueError("Tectonic did not use the game-local configuration when an inherited override was supplied")
        content = set(CONTENT.findall(log))
        if content != expected_content:
            raise ValueError(f"Content load mismatch in run {index}: missing {expected_content - content}, extra {content - expected_content}")
        loaded_content.update(content)
        registered_native.update(NATIVE.findall(log))
    if registered_native - expected_native:
        raise ValueError("Unexpected native module registrations in the modded client")
    if orbital_evidence is not None:
        smoke_orbital.verify_logs(*logs, registered_native, orbital_evidence)
    if external_config is not None and sha(external_config) != external_hash:
        raise ValueError("Tectonic changed the out-of-instance sentinel")
    actual_options = options(game_options)
    if {key: value for key, value in actual_options.items() if key.startswith("key_")} != preset:
        raise ValueError("The exact game did not retain the supported controls preset")
    if actual_options.get("invertYMouse") != "true":
        raise ValueError("The exact game did not retain the unrelated controls-test option")
    fps_path = args.qa / "game/config/dynamic-fps-for-rust.json"
    probes = []
    if fps_path.is_file():
        fps = load(fps_path)
        if fps["enabled"] is not True or not all(type(fps[key]) is int for key in ("focused_fps", "idle_fps")):
            raise ValueError("Dynamic FPS did not create valid native settings")
        probes.append({"id": "dynamic_fps_for_rust", "status": "PASS",
                       "observation": "The client created game/config/dynamic-fps-for-rust.json with focused_fps and idle_fps."})
    elif not args.startup_only:
        raise ValueError("Dynamic FPS did not create its native settings")
    if not args.startup_only:
        probes.append({"id": "veinminer", "status": "PASS", "observation": f"/veinminer status replied {veinminer!r}."})
    write(args.qa / "native-probes.json", {
        "schemaVersion": 1, "clientSha256": receipt["clientSha256"], "results": probes,
    })
    write(args.qa / "smoke.json", {
        "schemaVersion": 1, "clientSha256": receipt["clientSha256"],
        "mode": "startup-menu" if args.startup_only else "gameplay",
        "reusedQaDirectory": args.retry_startup,
        "gameplay": "NOT RUN" if args.startup_only else "PASS",
        "previousWorldMigration": "PASS" if args.previous_qa is not None else "NOT RUN",
        "previewSha256": None if args.startup_only else sha(preview),
        "loadedContent": sorted(loaded_content), "registeredNative": sorted(registered_native),
        "nativeNeedingDirectProbes": sorted(expected_native - registered_native),
        "modsPage": widgets, "worldsOnReopen": worlds, "firstState": before, "reopenedState": after,
        "bootstrapSha256": sha(standalone[0]) if standalone else None, "windowTitle": expected_title,
        "persistedModItems": [] if args.startup_only else [*registered_items, *([smoke_orbital.DESIGNATOR] if orbital_evidence else [])],
        "tectonicInstanceConfig": "PASS" if external_config is not None else "NOT RUN",
        "pauseMenu": pause_menu,
        "controlsPreset": {"mappingCount": len(preset), "sha256": sha(preset_file), "runtimeRoundTrip": "PASS"},
        "timeToTitleSeconds": title_times,
        **({"orbitalLaser": orbital_evidence} if orbital_evidence is not None else {}),
        **({"startupMouse": startup_evidence} if startup_evidence is not None else {}),
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("base-repo", "candidate", "controller", "qa"):
        parser.add_argument("--" + field, required=True, type=lambda value: Path(value).resolve())
    for field in ("resources", "minecraft", "java", "cache"):
        parser.add_argument("--" + field, type=lambda value: Path(value).resolve())
    parser.add_argument("--bootstrap", action="store_true",
                        help="run the packaged bootstrap, allowing independent preparation without Minecraft or Java")
    parser.add_argument("--previous-qa", type=lambda value: Path(value).resolve(),
                        help="prior public release smoke instance with old-save-evidence.json")
    parser.add_argument("--startup-only", action="store_true", help="verify startup and menu without opening a world")
    parser.add_argument("--retry-startup", action="store_true",
                        help="reset the test settings and reuse an unfinished startup-only QA directory")
    args = parser.parse_args()
    paths = (args.bootstrap, args.minecraft, args.java, args.cache)
    if (args.resources is not None and any(paths)) or (args.resources is None and args.cache is None):
        parser.error("Use prepared --resources, or --bootstrap with --cache and optional --minecraft and --java")
    if args.resources is None:
        args.bootstrap = True
    if args.startup_only and (args.previous_qa is not None or not args.bootstrap):
        parser.error("Startup-only QA uses the bootstrap and excludes prior-world migration")
    if args.retry_startup and not args.startup_only:
        parser.error("Retry-startup applies only to startup-only QA")
    smoke(args)


if __name__ == "__main__":
    main()
