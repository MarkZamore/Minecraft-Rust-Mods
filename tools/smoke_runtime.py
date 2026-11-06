"""Exercise the combined native client with a technical world and record loaded mod evidence."""

import argparse
import os
from pathlib import Path
import re
import sys
import time

from native_build import INPUT_LOCK, load, module_at, sha, write
from stage_mod_pack import CONTROLS_PRESET, OPTIONS_TEMPLATE


WORLD = "RustModsSmoke"
CONTENT = re.compile(r"LANMinecraft content mod accepted: ([A-Za-z0-9_-]+) ([^\s]+)")
NATIVE = re.compile(r"LANMinecraft native mod registered: ([A-Za-z0-9_-]+)")
TECTONIC_PATH = re.compile(r"^\[Tectonic-Horizon\] (.+?): .+; using defaults$", re.MULTILINE)
REGISTERED_ITEMS = ("deepbore_quarry:quarry", "wayfarer_stones:wayfarer_stone")
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
                        if projects[project]["kind"] == "mod"}
    expected_native = {project for project in receipt["ids"] if projects[project]["kind"] == "native-mod"}
    sys.path.insert(0, str(args.base_repo / "tools"))
    try:
        base = module_at(args.base_repo / "tools/smoke_runtime.py")
    finally:
        sys.path.pop(0)
    preset_file = args.candidate / CONTROLS_PRESET
    preset = options(preset_file)
    if len(preset) != 62 or any(not name.startswith("key_") for name in preset):
        raise ValueError("Expected the exact 62-key Horizon controls preset")
    args.qa.mkdir()
    game = args.qa / "game"
    game.mkdir()
    game_options = game / "options.txt"
    seed = (args.candidate / OPTIONS_TEMPLATE).read_text(encoding="utf-8")
    game_options.write_text(seed + "invertYMouse:true\n" + "".join(f"{key}:{value}\n" for key, value in preset.items()),
                            encoding="utf-8", newline="\n")
    preview = args.qa / "preview.png"
    standalone = None
    expected_title = None
    external_config = None
    instance_config = None
    if args.minecraft is not None:
        descriptor = load(args.candidate / "portable-pack.json")
        standalone = (args.candidate / descriptor["nativeRuntime"]["executable"], args.minecraft, args.java, args.cache)
        presentation = load(args.candidate / "native/presentation.json")
        expected_title = presentation["name"]
        instance_config = args.qa / "game" / presentation["instanceConfig"][TECTONIC_ENV]
        instance_config.parent.mkdir(parents=True)
        instance_config.write_text("{instance QA sentinel\n", encoding="ascii")
        external_config = args.qa / "outside-game-config.json"
        external_config.write_text("{external QA sentinel\n", encoding="ascii")
        external_hash = sha(external_config)

    def observe_registered_items(control):
        counts = control("inventory")["counts"]
        if any(counts.get(item) != 1 for item in REGISTERED_ITEMS):
            raise ValueError("The registry and saved inventory must contain both modded items")

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
        control("options", "mods")
        widgets = control("ui")
        control("click", "done")
        control("open", WORLD, "137248", "creative")
        control("wait-for", "loaded", "--timeout", "90")
        for item in REGISTERED_ITEMS:
            control("give", item, "1")
        control("wait", "4")
        observe_registered_items(control)
        veinminer = control("command", "/veinminer status")["chat"]
        if veinminer != ["Veinminer: on"]:
            raise ValueError("Veinminer did not answer its status command")
        photograph(control)
        control("save")
        return control("state"), widgets, veinminer[0]

    def reopen(control):
        worlds = control("worlds")
        if not (args.qa / "game/saves" / WORLD / "level.dat").is_file():
            raise ValueError("The combined client did not save its world")
        control("open", WORLD)
        control("wait-for", "loaded", "--timeout", "90")
        observe_registered_items(control)
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
    try:
        before, widgets, veinminer = run(1, create)
        after, worlds = run(2, reopen)
        # The bootstrap drops Horizon's harness variables. Run the same verified executable
        # directly for the network UI check, so the listener is confined to loopback.
        if instance_config is not None:
            os.environ[TECTONIC_ENV] = str(instance_config)
        resources = args.resources if standalone is None else args.cache / "resources"
        pause_menu = base.run_client(client, args.controller, resources, args.qa, 3, pause_loopback)
    finally:
        for name, value in old_environment.items():
            if value is None:
                del os.environ[name]
            else:
                os.environ[name] = value
    if any(before[key] != after[key] for key in ("dimension", "gamemode")) or any(
            abs(a - b) > 1 for a, b in zip(before["player"]["position"], after["player"]["position"])):
        raise ValueError("Reopening the modded world did not preserve the test state")
    loaded_content = set()
    registered_native = set()
    for index in (1, 2):
        log = (args.qa / f"client-{index}.log").read_text(encoding="utf-8", errors="replace")
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
    if external_config is not None and sha(external_config) != external_hash:
        raise ValueError("Tectonic changed the out-of-instance sentinel")
    actual_options = options(game_options)
    if {key: value for key, value in actual_options.items() if key.startswith("key_")} != preset:
        raise ValueError("The exact game did not retain the supported controls preset")
    if actual_options.get("invertYMouse") != "true":
        raise ValueError("The exact game did not retain the unrelated controls-test option")
    fps_path = args.qa / "game/config/dynamic-fps-for-rust.json"
    fps = load(fps_path)
    if fps["enabled"] is not True or not all(type(fps[key]) is int for key in ("focused_fps", "idle_fps")):
        raise ValueError("Dynamic FPS did not create its native settings")
    write(args.qa / "native-probes.json", {
        "schemaVersion": 1, "clientSha256": receipt["clientSha256"], "results": [
            {"id": "dynamic_fps_for_rust", "status": "PASS",
             "observation": "The first client frame created game/config/dynamic-fps-for-rust.json with focused_fps and idle_fps."},
            {"id": "veinminer", "status": "PASS", "observation": f"/veinminer status replied {veinminer!r}."},
        ],
    })
    write(args.qa / "smoke.json", {
        "schemaVersion": 1, "clientSha256": receipt["clientSha256"], "previewSha256": sha(preview),
        "loadedContent": sorted(loaded_content), "registeredNative": sorted(registered_native),
        "nativeNeedingDirectProbes": sorted(expected_native - registered_native),
        "modsPage": widgets, "worldsOnReopen": worlds, "firstState": before, "reopenedState": after,
        "bootstrapSha256": sha(standalone[0]) if standalone else None, "windowTitle": expected_title,
        "persistedModItems": list(REGISTERED_ITEMS),
        "tectonicInstanceConfig": "PASS" if external_config is not None else "NOT RUN",
        "pauseMenu": pause_menu,
        "controlsPreset": {"mappingCount": len(preset), "sha256": sha(preset_file), "runtimeRoundTrip": "PASS"},
        "timeToTitleSeconds": title_times,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("base-repo", "candidate", "controller", "qa"):
        parser.add_argument("--" + field, required=True, type=lambda value: Path(value).resolve())
    for field in ("resources", "minecraft", "java", "cache"):
        parser.add_argument("--" + field, type=lambda value: Path(value).resolve())
    args = parser.parse_args()
    paths = (args.minecraft, args.java, args.cache)
    if (args.resources is not None and any(paths)) or (args.resources is None and not all(paths)):
        parser.error("Use prepared --resources, or bootstrap with --minecraft, --java and the shared --cache")
    smoke(args)


if __name__ == "__main__":
    main()
