"""Describe the accepted Minecraft Rust Mods runtime, library and immutable release assets."""

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

from inspect_mods import content_file
from native_build import DECISIONS, INPUT_LOCK, ROOT, build_recipe_sha, load, module_at, sha, write
from stage_mod_pack import CONTROLS_PRESET, OPTIONS_TEMPLATE, presentation, verify_environment_evidence


FEATURES = ["native-runtime-v1", "native-steam-v1", "minecraft-26-world-layout-v1",
            "native-launcher-notices-v1"]
LIBRARY_FILES = ("content.json", "mod-pages.json", "loaded-mods.json", "preview.jpg")
FILE_SEGMENT = re.compile(r"[A-Za-z0-9_.$-]+")
ORBITAL_CONTENT = "native/horizon-mods/orbital.hmod"
NATIVE_WITHOUT_MODS_ROW = {"dynamic_fps_for_rust", "veinminer"}
PACK_OVERLAY_MODS = {"horizon_physics", "orbital", "jei"}


def file_info(path):
    return {"sizeBytes": path.stat().st_size, "sha256": sha(path)}


def inventory(candidate):
    payload = candidate / "native"
    if not payload.is_dir() or payload.is_symlink() or payload.is_junction():
        raise ValueError("Expected a regular native payload directory")
    files = {"portable-pack.json": file_info(candidate / "portable-pack.json")}
    for path in sorted(payload.rglob("*"), key=lambda entry: entry.relative_to(candidate).as_posix()):
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"Distribution contains a link: {path.name}")
        if not path.is_file():
            continue
        relative = path.relative_to(candidate).as_posix()
        if not all(FILE_SEGMENT.fullmatch(part) and not part.endswith(".")
                   and not pathlib.PureWindowsPath(part).is_reserved() for part in relative.split("/")):
            raise ValueError(f"Invalid Windows payload path: {relative}")
        files[relative] = file_info(path)
    if len(files) != len({name.casefold() for name in files}):
        raise ValueError("Distribution has duplicate Windows file paths")
    return dict(sorted(files.items()))


def accepted_build(candidate):
    path = candidate / "build-receipt.json"
    build = load(path)
    approval = load(ROOT / "tools/build-lock.json")
    selection = load(ROOT / "tools/mod-selection.json")
    if (approval["receiptSha256"] != sha(path) or build["compileCheck"] is not True or
            build["inputLockSha256"] != sha(INPUT_LOCK) or build["conflictDecisionsSha256"] != sha(DECISIONS) or
            build["buildRecipeSha256"] != build_recipe_sha() or selection.get("schemaVersion") != 1 or
            selection.get("inputLockSha256") != sha(INPUT_LOCK) or selection.get("ids") != build["ids"]):
        raise ValueError("Build receipt does not match the accepted mod inputs and build")
    return build, approval


def verify_release_startup(candidate, build, bootstrap_sha, *, base_repo=None):
    if not PACK_OVERLAY_MODS <= set(build["ids"]):
        raise ValueError("The release omits a mod with a required pack compatibility overlay")
    report = load(ROOT / "tools/qa/orbital-startup.json")
    presentation_file = candidate / "native/presentation.json"
    if load(presentation_file) != presentation():
        raise ValueError("Restage the exact candidate with its mandatory pack presentation environment defaults")
    policy_sha = sha(presentation_file)
    verify_environment_evidence(report, policy_sha, ("bootstrap", "bootstrap", "direct-client"))
    preset = candidate / CONTROLS_PRESET
    if (report["clientSha256"] != build["clientSha256"] or report["bootstrapSha256"] != bootstrap_sha or
            report["windowTitle"] != presentation()["name"] or
            report["controlsPreset"]["sha256"] != sha(preset) or
            report["controlsPreset"]["runtimeRoundTrip"] != "PASS" or
            "key_orbital.wheel:key.keyboard.insert" not in preset.read_text(encoding="utf-8").splitlines()):
        raise ValueError("Run the Windows startup and controls checks for this exact candidate")
    mouse = report["startupMouse"]
    narrator = mouse["onboardingNarratorButton"]
    if (narrator["enabled"] is not False or narrator["speechBackendActive"] is not True or
            narrator["statusAfterClick"] != "off" or
            not {"language", "accessibility", "sound", "continue"} <= set(mouse["onboardingMouse"]) or
            mouse["physicsQuickSetup"]["action"] != "Skip Setup" or
            not {"language", "accessibility", "options", "controls"} <= set(mouse["titleMouse"])):
        raise ValueError("The Windows report must verify the startup screen and Physics menu fix")
    verify_gameplay_acceptance(report, build, preset)
    multiplayer = load(ROOT / "tools/qa/multiplayer.json")
    backup = load(ROOT / "tools/qa/backup-guard.json")
    servers = load(ROOT / "tools/qa/server-policy.json")
    verify_environment_evidence(multiplayer, policy_sha, ("direct-client",) * 2)
    verify_environment_evidence(backup, policy_sha, ("direct-client",) * 2)
    verify_environment_evidence(servers, policy_sha, ("direct-client",) * 6)
    verify_multiplayer_acceptance(multiplayer, build)
    verify_backup_acceptance(backup, build)
    verify_server_policy_acceptance(servers, build, base_repo=base_repo)
    if base_repo is None:
        raise ValueError("Native notice acceptance requires the explicit pinned --base-repo checkout")
    from native_notice_acceptance import verify_launcher_support, verify_native_notices
    verify_launcher_support(load(ROOT / "tools/qa/launcher-notices-support.json"))
    verify_native_notices(load(ROOT / "tools/qa/native-notices.json"), candidate, build, base_repo)


def verify_gameplay_acceptance(report, build, preset):
    required_keys = {"key_orbital.wheel": "key.keyboard.insert",
                     "key_waypoints.create": "key.keyboard.f7", "key_waypoints.list": "key.keyboard.f8"}
    mappings = dict(line.split(":", 1) for line in preset.read_text(encoding="utf-8").splitlines()
                    if line.startswith("key_"))
    if (report.get("schemaVersion") != 1 or report.get("clientSha256") != build["clientSha256"] or
            report.get("mode") != "gameplay" or report.get("gameplay") != "PASS" or
            report["controlsPreset"].get("mappingCount") != 65 or len(mappings) != 65 or
            any(mappings.get(key) != value for key, value in required_keys.items())):
        raise ValueError("Release needs full gameplay and the approved 65-key controls on the exact client")
    pause = report["pauseMenu"]
    port, chat = pause.get("localPort"), pause.get("publicationChat")
    if (type(port) is not int or not 1 <= port <= 65535 or
            pause.get("loopback") != "PASS" or pause.get("returnedToGame") is not True or
            pause.get("before") not in {"Open to LAN", "Открыть для сети"} or
            pause.get("after") not in {"Player Reporting", "Жалобы"} or
            not isinstance(chat, list) or not all(isinstance(line, str) for line in chat) or
            not any(re.search(rf"(?<!\d){port}(?!\d)", line) for line in chat)):
        raise ValueError("Release needs the actual numeric publication port in chat and the closing/replaced pause menu")
    orbital = report["orbitalLaser"]
    if (orbital["controls"] != {"orbitalWheel": "insert", "horizonmaticaMoveMode": "n", "rotation": "r"} or
            orbital.get("terrainOffUnchanged") is not True or orbital.get("shaderErrors") != [] or
            not all_fixture_blocks_changed(orbital) or
            orbital["reopened"]["terrain"] != orbital["blocksAfter"]):
        raise ValueError("Release needs verified Insert/R controls, Orbital rendering and persistent terrain edits")


def all_fixture_blocks_changed(evidence):
    before, after = evidence["blocksBefore"], evidence["blocksAfter"]
    return bool(before) and len(before) == len(after) and all(a != b for a, b in zip(before, after, strict=True))


def verify_multiplayer_acceptance(report, build):
    if (report.get("schemaVersion") != 1 or report.get("clientSha256") != build["clientSha256"] or
            report.get("horizonRevision") != build["horizonRevision"] or report.get("mode") != "mods" or
            report.get("result") != "PASS" or report.get("diagnosticOnly") is not False or
            report.get("processExitCodes") != {"host": 0, "guest": 0} or
            report.get("disposableWorldsRemoved") is not True):
        raise ValueError("Release needs a complete, non-diagnostic multiplayer report for the exact client")
    commands = {(row["typed"], row["observed"]) for row in report["guestCommands"]}
    published = report["publishMenu"]
    port, notice = published.get("port"), published.get("numericPortNotice", {})
    component = notice.get("component", {})
    if (type(port) is not int or not 1 <= port <= 65535 or
            type(notice.get("chatPort")) is not int or type(notice.get("statusPort")) is not int or
            notice["chatPort"] != port or notice["statusPort"] != port or notice.get("matchesStatus") is not True or
            notice.get("translationKey") != "menu.multiplayerOptions.publish.started.lan" or
            component.get("translate") != "menu.multiplayerOptions.publish.started.lan" or
            not any((isinstance(arg, dict) and arg.get("text") == str(port)) or
                    (isinstance(arg, str) and arg == str(port)) or (type(arg) is int and arg == port)
                    for arg in component.get("with", []))):
        raise ValueError("Release needs the actual multiplayer listener port in its system chat component")
    denied = report["guestCheatsDisabled"]
    if (report["publishMenu"].get("returnedToGame") is not True or
            not {("/gamemode survival", "survival"), ("/gamemode creative", "creative")} <= commands or
            denied["before"] != "creative" or denied["after"] != "creative" or
            denied.get("typed") != "/gamemode spectator" or denied.get("denialObserved") is not True or
            not denied.get("denialEventSeq")):
        raise ValueError("Release needs the closing publish menu and positive/negative guest command checks")
    jei = report["jei"]
    if (jei.get("gamemode") != "creative" or jei.get("inventoryScreen") != "creative" or
            jei.get("creativeInventory") is not True or jei.get("itemButtons", 0) <= 0 or
            jei.get("search") != "minecraft:stone" or set(jei.get("shortcuts", {})) != {"R", "U"} or
            any(not jei["shortcuts"][key].get("recipeSlots") for key in ("R", "U"))):
        raise ValueError("Release needs the visible JEI Creative panel, search, recipes and usages")
    give, focus, search, wheel = (jei[name] for name in ("give", "reopenFocus", "creativeSearch", "wheelIsolation"))
    clicks = give["clicks"]
    if (give.get("item") != "minecraft:stone" or len(clicks) != 2 or
            any(row.get("delta") != 1 or row["after"] - row["before"] != 1 for row in clicks) or
            any(focus.get(key) != value for key, value in {"closedToPlaying": True, "reopenedCreative": True,
                "typedAfterReopen": "x", "searchUnchanged": True}.items()) or
            any(search.get(key) != value for key, value in {"outsideText": "diamond", "outsideLeavesJeiSearch": True,
                "jeiTextOnSearchTab": "minecraft:dirt", "jeiSearchOnSearchTab": True,
                "restoredSearch": "minecraft:stone"}.items()) or
            any(wheel.get(key) is not True for key in ("insideChangesPanelPage", "insideReverseRestoresPanelPage",
                                                     "outsideKeepsPanelPage"))):
        raise ValueError("Release needs JEI click-give, focus, Creative search priority and panel wheel isolation checks")
    orbital = report["orbital"]
    concurrent = orbital["concurrentEffects"]
    if (not all_fixture_blocks_changed(orbital) or
            orbital["guestObservedBlocksAfter"] != orbital["blocksAfter"] or not orbital.get("hostRemoteVisual") or
            not concurrent.get("hostReceivedGuest") or not concurrent.get("guestReceivedHost") or
            concurrent.get("ownEngineStrikes") != {"host": "plasma", "guest": "plasma"} or
            concurrent.get("hostTerrainOffFixtureUnchanged") is not True or
            set(concurrent.get("framesOwnAndRemote", {})) != {"host", "guest"} or
            report.get("shaderErrors") != {"host": [], "guest": []}):
        raise ValueError("Release needs guest terrain edits and simultaneous local/remote Orbital effects on both clients")


def verify_backup_acceptance(report, build):
    count = report.get("sourceFilesVerified", 0)
    if (report.get("schemaVersion") != 1 or report.get("clientSha256") != build["clientSha256"] or
            report.get("status") != "PASS" or report.get("firstOpenBackup") != "PASS" or
            report.get("reopenWithoutDuplicate") != "PASS" or report.get("markerRecreatedZeroBytes") is not True or
            count <= 0 or report.get("backupMembersVerified") != count or
            report.get("newBackupsAfterReopen") != 1 or report.get("personalSettingsPreserved", 0) <= 0):
        raise ValueError("Release needs the exact-client first-open backup and personal-settings preservation check")


def verify_server_policy_acceptance(report, build, *, base_repo=None):
    if base_repo is None:
        raise ValueError("Server Escape acceptance requires the explicit pinned --base-repo checkout")
    from native_notice_acceptance import source_proof
    proof = source_proof(base_repo, build, published=True)
    arguments = report.get("launchArguments", [])
    harness_flags = {"--control", "--control-socket", "--frames", "--world-frames", "--benchmark",
                     "--offscreen", "--headless", "--dedicated"}
    if (report.get("schemaVersion") != 1 or report.get("clientSha256") != build["clientSha256"] or
            report.get("horizonRevision") != build["horizonRevision"] or
            report.get("buildReceiptSha256") != load(ROOT / "tools/build-lock.json")["receiptSha256"] or
            report.get("nativeRuntime") != load(ROOT / "portable-pack.json")["nativeRuntime"] or
            report.get("status") != "PASS" or len(arguments) != 6 or report.get("fixtureRemoved") is not True or
            report.get("desktopKeyProtocol") != "edition-server-list-qa/checkpoint-v1" or
            report.get("desktopKeyHelperSha256") != proof["desktopKeyHelperSha256"] or
            report.get("processExitCodes") != [0] * len(arguments) or
            report.get("ordinaryLaunch") is not True or
            any(not run or any(arg.split("=", 1)[0] in harness_flags for arg in run) for run in arguments)):
        raise ValueError("Release needs the exact-client fresh/manual/restarted server-list policy check")
    fresh, manual = report["freshProfile"], report["manualRestart"]
    before = manual.get("beforeSha256", "")
    if (fresh.get("savedServerCount") != 0 or fresh.get("automaticSeedFilesCreated") != [] or
            manual.get("savedServerCount", 0) <= 0 or manual.get("bytePreserved") is not True or
            manual.get("unknownFieldsPreserved") is not True or report.get("manualLegacyMarkersPreserved") is not True or
            not re.fullmatch(r"[0-9a-f]{64}", before) or
            manual.get("afterSha256") != before):
        raise ValueError("Release needs no automatic public rows and byte-preserved manual servers.dat through restart")
    verify_saved_server_views(report)


def verify_saved_server_views(report):
    """Require actual UI observations for both views, not just an empty new file."""
    view = report.get("savedServerView", {})
    required = ("defaultSteamOnly", "savedAllRows", "refreshRetainsView", "explicitSteamReturn",
                "backAndEscapeReturnToTitle", "reopenDefaultsSteam", "bytesUnchanged")
    widget_ids = {"steam-worlds", "saved-servers", "server-list-refresh", "server-list-back"}
    if (view.get("status") != "PASS" or
            view.get("contractRevision") != "00e1c09d028720370d525cf3c2dae741c02fd9f2" or
            set(view.get("widgetIds", [])) != widget_ids or view.get("ordinaryRunsVerified") != 6 or
            any(view.get(key) is not True for key in required)):
        raise ValueError("Release needs the actual default Steam and explicit Saved server-list UI acceptance")
    legacy, manual = report.get("legacyUpgrade", {}), report["manualRestart"]
    # This hash identifies only our explicitly reconstructed QA input. It must
    # never classify or delete an existing player's row or servers.dat file.
    legacy_sha = "1f8109f98d5ad63d570fb81d7eb6dab039a63495cd77358d8346fcb58ae8bc6d"
    if (legacy.get("copiedFromUserFile") is not False or
            not str(legacy.get("fixtureOrigin", "")).startswith("Reconstructed author-format list") or
            legacy.get("savedServerCount") != 2 or legacy.get("sizeBytes") != 134 or
            legacy.get("beforeSha256") != legacy_sha or legacy.get("afterSha256") != legacy_sha or
            legacy.get("bytePreserved") is not True or legacy.get("defaultSteamRows") != [] or
            len(legacy.get("savedViewRows", [])) != 2 or len(manual.get("savedViewRows", [])) != 1 or
            manual.get("savedServerCount") != 1):
        raise ValueError("Release needs byte-preserved reconstructed legacy and unknown-field manual NBT with all Saved rows")
    actions = (("open-multiplayer", "steam"), ("refresh-steam", "steam"), ("open-saved", "saved"),
               ("refresh-saved", "saved"), ("explicit-steam", "steam"), ("saved-before-back", "saved"),
               ("reopen-after-back", "steam"), ("saved-before-escape", "saved"),
               ("reopen-after-escape", "steam"))
    runs = report.get("runs", [])
    if len(runs) != 6:
        raise ValueError("Release needs all six fresh, legacy and manual ordinary UI runs")
    for index, run in enumerate(runs):
        expected_names = [] if index < 2 else legacy["savedViewRows"] if index < 4 else manual["savedViewRows"]
        expected_sha = None if index < 2 else legacy_sha if index < 4 else manual["beforeSha256"]
        actual = run.get("serverView", {})
        observations = actual.get("observations", [])
        if (run.get("arguments") != report["launchArguments"][index] or run.get("exitCode") != 0 or
                [row.get("name") for row in run.get("servers", [])] != expected_names or
                actual.get("status") != "PASS" or actual.get("expectedSavedRows") != expected_names or
                actual.get("backToTitle") is not True or actual.get("escapeToTitle") is not True or
                actual.get("bytePreserved") is not True or len(observations) != len(actions)):
            raise ValueError("A server-list run lacks its actual saved rows, navigation or byte-preservation observations")
        if index < 2:
            expected_sha = observations[0].get("serversDatSha256")
            if expected_sha is not None and not re.fullmatch(r"[0-9a-f]{64}", str(expected_sha)):
                raise ValueError("Fresh server-list UI has no valid unchanged-file observation")
        for observation, (action, mode) in zip(observations, actions, strict=True):
            if (observation.get("action") != action or observation.get("mode") != mode or
                    observation.get("rows") != (expected_names if mode == "saved" else []) or
                    observation.get("steamButtonEnabled") is not (mode == "saved") or
                    observation.get("savedButtonEnabled") is not (mode == "steam") or
                    observation.get("serversDatSha256") != expected_sha):
                raise ValueError("Server UI changed its view, hid saved rows or rewrote NBT during an action")
        verify_server_desktop_key(actual.get("desktopKeys", []), index + 1, run["arguments"][0])


def verify_server_desktop_key(events, run, executable):
    def positive(value):
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    if not isinstance(events, list) or len(events) != 1:
        raise ValueError("Each ordinary server-view run needs its real desktop Escape action")
    request, ack = events[0].get("request", {}), events[0].get("acknowledgement", {})
    created, performed = request.get("createdUnixMs"), ack.get("performedUnixMs")
    if (request.get("id") != f"run-{run}-key-1" or request.get("action") != "press_key" or
            request.get("key") != "Escape" or type(request.get("run")) is not int or request["run"] != run or
            not positive(request.get("processId")) or request.get("timeoutMs") != 120_000 or
            pathlib.PureWindowsPath(request.get("executable", "")) != pathlib.PureWindowsPath(executable) or
            request.get("title") != presentation()["name"] or not positive(created) or not positive(performed) or
            not created <= performed <= created + 120_000 or ack.get("status") != "PASS" or
            ack.get("id") != request["id"] or not positive(ack.get("processId")) or
            ack["processId"] != request["processId"] or
            ack.get("key") != "Escape"):
        raise ValueError("Desktop Escape was not acknowledged in time for this exact owned client/window/run")


def describe(candidate, *, base_repo=None):
    runtime = load(ROOT / "portable-pack.json")
    content = load(ROOT / "library/content.json")
    build, approval = accepted_build(candidate)
    files = inventory(candidate)
    descriptor = runtime["nativeRuntime"]
    if (load(candidate / "portable-pack.json") != runtime or descriptor["executable"] not in files or
            "native/steam_api64.dll" not in files or files["native/horizon-client.exe"]["sha256"] != build["clientSha256"]):
        raise ValueError("Candidate differs from the accepted runtime")
    if (content["nativeProject"]["revision"] != build["horizonRevision"] or
            load(candidate / "native/presentation.json") != presentation()):
        raise ValueError("Candidate presentation does not match the published library content")
    bootstrap = load(ROOT / "tools/bootstrap-lock.json")
    if files[descriptor["executable"]] != {"sizeBytes": bootstrap["sizeBytes"], "sha256": bootstrap["sha256"]}:
        raise ValueError("The staged bootstrap differs from the verified isolated native wrapper")
    verify_release_startup(candidate, build, bootstrap["sha256"], base_repo=base_repo)
    for name in LIBRARY_FILES:
        if file_info(candidate / "library" / name) != file_info(ROOT / "library" / name):
            raise ValueError(f"The staged library file differs from source: {name}")
    with (candidate / "library/preview.jpg").open("rb") as image:
        if image.read(3) != b"\xff\xd8\xff":
            raise ValueError("The accepted game preview must be JPEG")
    loaded = load(candidate / "library/loaded-mods.json")
    projects = {row["id"]: row for row in load(INPUT_LOCK)["projects"]}
    expected = {mod: projects[mod]["version"] for mod in build["ids"]}
    actual = {mod["id"]: mod["version"] for mod in loaded["mods"]}
    if (loaded["schemaVersion"] != 1 or loaded["minecraftVersion"] != runtime["minecraftVersion"] or
            loaded["nativeRuntime"] != descriptor or actual != expected or len(actual) != len(loaded["mods"]) or
            loaded["nativeFiles"] != {path: item["sha256"] for path, item in files.items() if path.startswith("native/")}):
        raise ValueError("Actual mod inventory does not cover the accepted build")
    for mod in loaded["mods"]:
        project = projects[mod["id"]]
        companion_path = f"native/horizon-mods/{mod['id']}.hmod"
        path = "native/horizon-client.exe" if project["kind"] == "native-mod" else companion_path
        if mod["source"] != path or path not in files:
            raise ValueError(f"Wrong payload origin for loaded mod {mod['id']}")
        if expected_content_file := content_file(project):
            if files.get(companion_path) != {"sizeBytes": expected_content_file["size"],
                                             "sha256": expected_content_file["sha256"]}:
                raise ValueError(f"Content mod byte mismatch: {mod['id']}")
    for relative in (CONTROLS_PRESET, OPTIONS_TEMPLATE):
        path = candidate / relative
        if (path.parent.is_symlink() or path.parent.is_junction() or path.is_symlink() or
                path.is_junction() or not path.is_file() or file_info(path) != file_info(ROOT / relative)):
            raise ValueError(f"The staged controls file differs from its approved source: {relative}")
        files[relative] = file_info(path)
    files = dict(sorted(files.items()))
    return runtime, content, len(actual), {
        "schemaVersion": 1, "receiptSha256": approval["receiptSha256"], "files": files,
        "bootstrapLockSha256": sha(ROOT / "tools/bootstrap-lock.json"),
        "library": {name: file_info(candidate / "library" / name) for name in LIBRARY_FILES},
    }


def propose_inventory(candidate, report_path, probe_path):
    report = load(report_path)
    build, _ = accepted_build(candidate)
    files = inventory(candidate)
    runtime = load(candidate / "portable-pack.json")
    client = files["native/horizon-client.exe"]["sha256"]
    bootstrap = files[runtime["nativeRuntime"]["executable"]]["sha256"]
    if (build["clientSha256"] != client or
            report.get("schemaVersion") != 1 or report["clientSha256"] != client or
            report["bootstrapSha256"] != bootstrap or report["windowTitle"] != load(candidate / "native/presentation.json")["name"]):
        raise ValueError("Inventory needs a Windows smoke report from this exact client and bootstrap")
    projects = {row["id"]: row for row in load(INPUT_LOCK)["projects"]}
    selected = [projects[mod] for mod in build["ids"]]
    expected_content = {(mod["id"], mod["version"]) for mod in selected if content_file(mod) is not None}
    expected_native = {mod["id"] for mod in selected if mod["kind"] == "native-mod"}
    if set(map(tuple, report["loadedContent"])) != expected_content:
        raise ValueError("The runtime did not accept the selected content mods")
    observed = set(report["registeredNative"])
    missing = expected_native - observed
    if observed - expected_native or set(report["nativeNeedingDirectProbes"]) != missing:
        raise ValueError("Native runtime observations differ from the expected set")
    proven = set()
    if probe_path is not None:
        probes = load(probe_path)
        proven = {probe["id"] for probe in probes["results"] if probe["status"] == "PASS" and probe["observation"].strip()}
        if probes.get("schemaVersion") != 1 or probes["clientSha256"] != client or proven - missing:
            raise ValueError("Direct runtime evidence differs from the accepted build")
    startup_only = report.get("mode") == "startup-menu"
    if startup_only:
        if missing - NATIVE_WITHOUT_MODS_ROW:
            raise ValueError(f"Expected Mods registrations are missing: {sorted(missing - NATIVE_WITHOUT_MODS_ROW)}")
    elif missing - proven:
        raise ValueError(f"Add direct runtime evidence for native mods without a Mods row: {sorted(missing - proven)}")
    inventory_document = {
        "schemaVersion": 1,
        "minecraftVersion": runtime["minecraftVersion"],
        "nativeRuntime": runtime["nativeRuntime"],
        "nativeFiles": {path: item["sha256"] for path, item in files.items() if path.startswith("native/")},
        "mods": [{"id": mod["id"], "name": mod["name"], "version": mod["version"],
                  "source": "native/horizon-client.exe" if mod["kind"] == "native-mod" else f"native/horizon-mods/{mod['id']}.hmod"}
                 for mod in selected],
    }
    if startup_only:
        inventory_document["verification"] = {
            "mode": "startup-menu", "registeredNative": sorted(observed),
            "observedWithoutMenu": sorted(proven), "compiledWithoutRuntimeProbe": sorted(missing - proven),
            "gameplay": "NOT RUN",
        }
    return inventory_document


def zip_payload(output, candidate, paths, prefix):
    temporary = output / f"{prefix}.tmp"
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in paths:
            entry = zipfile.ZipInfo(relative, (1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, (candidate / relative).read_bytes(), compresslevel=9)
    details = file_info(temporary)
    name = f"{prefix}-{details['sha256']}.zip"
    temporary.rename(output / name)
    return {"name": name, "kind": "zipRoot", **details, "covers": paths}


def export(args, output, runtime, content, count, distribution):
    assets = output / "assets"
    assets.mkdir(parents=True)
    files = distribution["files"]
    runtime_paths = [path for path in files if path not in (CONTROLS_PRESET, ORBITAL_CONTENT)]
    payload = zip_payload(assets, args.candidate, runtime_paths, "minecraft-rust-mods-windows-x64")
    launcher = zip_payload(assets, args.candidate, [CONTROLS_PRESET], "minecraft-rust-mods-launcher")
    packages = [payload, launcher]
    if ORBITAL_CONTENT in files:
        packages.append(zip_payload(assets, args.candidate, [ORBITAL_CONTENT], "minecraft-rust-mods-orbital-content"))
    sys.path.insert(0, str(args.library_tool.parent))
    try:
        library_tool = module_at(args.library_tool)
        library = library_tool.stage_library(args.candidate, output, args.revision)
        library_tool.verify_library(output, library)
    finally:
        sys.path.pop(0)
    write(output / "pack-manifest.json", {
        "schemaVersion": 2, "revision": args.revision, "packId": content["id"], "name": content["name"],
        "relativePath": content["relativePath"], "minecraftVersion": runtime["minecraftVersion"],
        "nativeRuntime": runtime["nativeRuntime"], "requiredLauncherFeatures": FEATURES, "modCount": count,
        "files": [{"path": name, **details} for name, details in files.items()], "assets": packages, "library": library,
    })


def output_files(root):
    return {path.relative_to(root).as_posix(): file_info(path) for path in root.rglob("*") if path.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=pathlib.Path)
    parser.add_argument("--base-repo", type=pathlib.Path,
                        help="Clean pinned Edition checkout supplying the shared notice acceptance contract")
    parser.add_argument("--out", type=pathlib.Path)
    parser.add_argument("--library-tool", type=pathlib.Path)
    parser.add_argument("--revision")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--verify-only", action="store_true")
    action.add_argument("--propose-lock", action="store_true")
    action.add_argument("--propose-inventory", type=pathlib.Path, metavar="SMOKE_JSON")
    parser.add_argument("--probe-evidence", type=pathlib.Path)
    args = parser.parse_args()
    if not args.propose_lock and args.propose_inventory is None and (args.out is None or args.library_tool is None or args.revision is None):
        parser.error("Generating or verifying assets requires output, library tool and exact revision")
    try:
        if args.propose_inventory is not None:
            inventory_document = propose_inventory(args.candidate, args.propose_inventory, args.probe_evidence)
            print(json.dumps(inventory_document, ensure_ascii=False, indent=2))
            return
        runtime, content, count, distribution = describe(args.candidate, base_repo=args.base_repo)
        if args.propose_lock:
            print(json.dumps(distribution, ensure_ascii=False, indent=2))
            return
        def git(*command):
            return subprocess.check_output(["git", "-C", str(ROOT), *command], text=True).strip()
        if git("rev-parse", "HEAD") != args.revision or git("status", "--porcelain", "--untracked-files=all"):
            raise ValueError("Publish from a clean checkout of the exact pack commit")
        if distribution != load(ROOT / "tools/distribution-lock.json"):
            raise ValueError("Candidate differs from the approved distribution lock")
        with tempfile.TemporaryDirectory(dir=args.out.parent) as temporary:
            staged = pathlib.Path(temporary) / "dist"
            export(args, staged, runtime, content, count, distribution)
            if args.verify_only:
                if not args.out.is_dir() or output_files(staged) != output_files(args.out):
                    raise ValueError("Output does not match the verified release")
            else:
                if args.out.exists():
                    raise ValueError("Use a fresh output directory")
                shutil.move(staged, args.out)
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Release manifest: {error}\n")


if __name__ == "__main__":
    main()
