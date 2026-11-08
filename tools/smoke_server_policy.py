"""Verify saved-server policy in a fresh, disposable native client profile.

Start twice empty, twice with a reconstructed legacy list, then twice with an
explicitly written loopback-only manual servers.dat containing unknown fields.
No existing profile or previously saved entry is removed or classified. The exact
NBT bytes include unknown fields to catch an unintended startup rewrite.

All six starts use ordinary launch arguments. Agent Control is enabled only in
this disposable profile's horizon-extras.txt, preserving normal startup seeding.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time

from native_build import load, module_at, sha, write
from native_qa_environment import environment_policy, prepare_client_environment
from smoke_backup_guard import ordinary
import smoke_startup


MANUAL_NAME = "Synthetic Manual QA"
MANUAL_ADDRESS = "127.0.0.1:9"
SEED_FILES = ("horizon_official_server.txt", "horizon_partner_servers.txt")
VIEW_CONTRACT_REVISION = "00e1c09d028720370d525cf3c2dae741c02fd9f2"
VIEW_IDS = ("steam-worlds", "saved-servers", "server-list-refresh", "server-list-back")
LEGACY_ROWS = [("Horizon Official", "play.playhorizon.dev"),
               ("Afri-Network (South Africa)", "afri-network.co.za")]
LEGACY_SHA256 = "1f8109f98d5ad63d570fb81d7eb6dab039a63495cd77358d8346fcb58ae8bc6d"


def utf(text):
    data = text.encode("utf-8")
    return struct.pack(">H", len(data)) + data


def tag(kind, name, payload):
    return bytes([kind]) + utf(name) + payload


def manual_nbt():
    custom = tag(8, "note", utf("preserve unknown nested fields"))
    custom += tag(7, "bytes", struct.pack(">i", 5) + b"\x00\xff\x03\x7f\x80") + b"\x00"
    entry = tag(8, "name", utf(MANUAL_NAME)) + tag(8, "ip", utf(MANUAL_ADDRESS))
    entry += tag(1, "acceptTextures", b"\x01") + tag(1, "hidden", b"\x00")
    entry += tag(3, "acceptedCodeOfConduct", struct.pack(">i", 1234567))
    entry += tag(10, "customManualData", custom) + b"\x00"
    servers = tag(9, "servers", b"\x0a" + struct.pack(">i", 1) + entry)
    root_extra = tag(8, "customRootData", utf("not an engine-owned server-list field"))
    return tag(10, "", root_extra + servers + b"\x00")


def legacy_nbt():
    # A reconstruction, not a copy of a user's file: these two names and
    # addresses were observed in the exact 5a9dd000 ordinary-startup baseline.
    # The author writes compound keys in BTreeMap order (ip, then name).
    entries = b"".join(tag(8, "ip", utf(address)) + tag(8, "name", utf(name)) + b"\x00"
                       for name, address in LEGACY_ROWS)
    raw = tag(10, "", tag(9, "servers", b"\x0a" + struct.pack(">i", 2) + entries) + b"\x00")
    if len(raw) != 134 or hashlib.sha256(raw).hexdigest() != LEGACY_SHA256:
        raise ValueError("The reconstructed author-format legacy fixture changed")
    return raw


class Nbt:
    """Read ordinary uncompressed Java NBT; used only for this small QA fixture."""

    def __init__(self, data):
        if len(data) > 1024 * 1024:
            raise ValueError("Unexpected oversized servers.dat in the disposable profile")
        self.data = memoryview(data)
        self.at = 0

    def take(self, count):
        if count < 0 or self.at + count > len(self.data):
            raise ValueError("Truncated QA servers.dat")
        value = self.data[self.at:self.at + count]
        self.at += count
        return value

    def number(self, fmt):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def text(self):
        return bytes(self.take(self.number(">H"))).decode("utf-8")

    def count(self):
        count = self.number(">i")
        if count < 0 or count > len(self.data):
            raise ValueError("Invalid QA NBT count")
        return count

    def payload(self, kind, depth=0):
        if depth > 32:
            raise ValueError("Unexpected nested QA NBT")
        if kind in (1, 2, 3, 4, 5, 6):
            return self.number({1: ">b", 2: ">h", 3: ">i", 4: ">q", 5: ">f", 6: ">d"}[kind])
        if kind == 7:
            return bytes(self.take(self.count()))
        if kind == 8:
            return self.text()
        if kind == 9:
            item, count = self.number(">B"), self.count()
            return [self.payload(item, depth + 1) for _ in range(count)]
        if kind == 10:
            result = {}
            while (item := self.number(">B")) != 0:
                name = self.text()
                if name in result:
                    raise ValueError("Duplicate QA NBT compound field")
                result[name] = self.payload(item, depth + 1)
            return result
        if kind in (11, 12):
            return [self.number(">i" if kind == 11 else ">q") for _ in range(self.count())]
        raise ValueError(f"Unexpected QA NBT tag: {kind}")

    def root(self):
        if self.number(">B") != 10:
            raise ValueError("QA servers.dat is not a compound")
        self.text()
        result = self.payload(10)
        if self.at != len(self.data):
            raise ValueError("Trailing bytes in QA servers.dat")
        return result


def saved_servers(file):
    if not file.exists():
        return [], None
    ordinary(file, directory=False)
    root = Nbt(file.read_bytes()).root()
    servers = root.get("servers", [])
    if not isinstance(servers, list) or any(not isinstance(entry, dict) for entry in servers):
        raise ValueError("The QA saved-server list is malformed")
    return servers, root


def seed_files(game):
    return [name for name in SEED_FILES if (game / name).exists()]


def wait_ui(control, predicate, label, seconds=30):
    deadline = time.monotonic() + seconds
    while True:
        ui = control("ui")
        if predicate(ui):
            return ui
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{label}; visible IDs: {[row['id'] for row in ui['widgets']]}")
        time.sleep(0.08)


def server_view(control, file, expected_names, desktop_keys):
    """Observe actual native view buttons/rows; never click a server or join it."""
    initial = file.read_bytes() if file.exists() else None
    observed = []

    def unchanged():
        current = file.read_bytes() if file.exists() else None
        if current != initial:
            raise ValueError("Viewing or refreshing saved servers changed servers.dat bytes")
        return hashlib.sha256(current).hexdigest() if current is not None else None

    def title():
        return wait_ui(control, lambda ui: {"singleplayer", "multiplayer"}
                       <= {row["id"] for row in ui["widgets"]}, "Server view did not return to the title menu")

    def snapshot(action, mode):
        def ready(ui):
            nodes = {row["id"]: row for row in ui["widgets"]}
            if not set(VIEW_IDS) <= nodes.keys():
                return False
            return (nodes["steam-worlds"]["enabled"] is (mode == "saved")
                    and nodes["saved-servers"]["enabled"] is (mode == "steam"))

        ui = wait_ui(control, ready, f"The {mode} server view did not render its native controls")
        rows = [row.get("label", "") for row in ui["widgets"] if row.get("kind") == "row"]
        wanted = list(expected_names) if mode == "saved" else []
        if rows != wanted:
            raise ValueError(f"The {mode} view has incorrect rows: {rows!r}, expected {wanted!r}")
        nodes = {row["id"]: row for row in ui["widgets"]}
        observed.append({"action": action, "mode": mode, "rows": rows,
                         "steamButtonEnabled": nodes["steam-worlds"]["enabled"],
                         "savedButtonEnabled": nodes["saved-servers"]["enabled"],
                         "serversDatSha256": unchanged()})

    title()
    control("click", "multiplayer")
    snapshot("open-multiplayer", "steam")
    control("click", "server-list-refresh")
    snapshot("refresh-steam", "steam")
    control("click", "saved-servers")
    snapshot("open-saved", "saved")
    control("click", "server-list-refresh")
    snapshot("refresh-saved", "saved")
    control("click", "steam-worlds")
    snapshot("explicit-steam", "steam")
    control("click", "saved-servers")
    snapshot("saved-before-back", "saved")
    control("click", "server-list-back")
    title()
    unchanged()
    control("click", "multiplayer")
    snapshot("reopen-after-back", "steam")
    control("click", "saved-servers")
    snapshot("saved-before-escape", "saved")
    # The ordinary title screen intentionally rejects native control `key`.
    # A real desktop input action must target this owned client's window.
    desktop_keys.press("Escape")
    title()
    unchanged()
    control("click", "multiplayer")
    snapshot("reopen-after-escape", "steam")
    return {"status": "PASS", "expectedSavedRows": list(expected_names), "observations": observed,
            "backToTitle": True, "escapeToTitle": True, "bytePreserved": True,
            "desktopKeys": desktop_keys.evidence}


def ordinary_launch(client, controller, resources, qa, game, index, action, desktop_keys_class,
                    key_requests, expected_title):
    endpoint = qa / f"control-{index}.json"
    log = qa / f"client-{index}.log"
    if endpoint.exists() or log.exists():
        raise ValueError("Policy QA requires unused endpoint and log files")
    environment = dict(os.environ)
    for key in tuple(environment):
        if key.upper().startswith("LANMINECRAFT_") or key in {
            "HORIZON_DISPENSE_FIXTURE", "HORIZON_SCULK_FIXTURE", "HORIZON_COMMAND_SCRIPT",
            "HORIZON_NET_REPORT", "HORIZON_TEST_LOOPBACK",
        }:
            del environment[key]
    environment.update(HORIZON_CONTROL=str(endpoint), HORIZON_NO_DISCORD="1")
    environment, environment_evidence = prepare_client_environment(client.parent.parent, environment)
    # Never use --control/--control-socket/frames/offscreen: those select a
    # harness startup path which intentionally skips automatic server seeding.
    arguments = [str(client), "--resources", str(resources), "--game-dir", str(game),
                 "--home", str(game), "--name", "RustServerPolicyQA"]
    with log.open("wb") as output:
        process = subprocess.Popen(arguments, cwd=game, env=environment, stdout=output, stderr=output)

        def control(*words, timeout=120):
            reply = subprocess.run([str(controller), "--socket", str(endpoint), *words], cwd=game,
                                   capture_output=True, encoding="utf-8", timeout=timeout, check=True)
            value = json.loads(reply.stdout)
            if isinstance(value, dict) and "error" in value:
                raise RuntimeError(value["error"])
            return value

        try:
            deadline = time.monotonic() + 120
            while not endpoint.is_file():
                if process.poll() is not None:
                    raise RuntimeError(f"Client exited before ready: {process.returncode}; see {log}")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Ordinary startup did not expose the technical control endpoint")
                time.sleep(0.25)
            control("wait-for", "screen", "title", "--timeout", "90")
            # The Mods candidate also has Physics' first-run page. All actions
            # affect only this new profile and keep the process in ordinary mode.
            ui = wait_ui(control, lambda value: any(row.get("id") in {"continue", "singleplayer"}
                         for row in value["widgets"]), "Ordinary startup did not render its first-run or title controls")
            if any(row.get("id") == "continue" for row in ui["widgets"]):
                smoke_startup.exercise(control, qa)
            servers = control("servers")["servers"]
            desktop_keys = desktop_keys_class(key_requests, process, client, expected_title, index)
            observed = action(control, desktop_keys)
            control("quit", timeout=20)
            if process.wait(timeout=45) != 0:
                raise RuntimeError(f"Client exited with code {process.returncode}; see {log}")
            return {"servers": servers, "arguments": arguments, "exitCode": process.returncode,
                    "environment": environment_evidence,
                    **({"serverView": observed} if observed is not None else {})}
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=15)


def new_qa(qa, run_owner):
    qa = Path(os.path.abspath(qa))
    run = qa.parent
    if run.parent.name != "agent-runs" or run.parent.parent.name != "artifacts":
        raise ValueError("Use a new QA directory directly under a registered project run")
    workspace = run.parent.parent.parent
    for path in (workspace, workspace / "artifacts", run.parent, run):
        ordinary(path, directory=True)
    ordinary(run / "RUN.json", directory=False)
    registration = load(run / "RUN.json")
    if (registration.get("status") != "active" or not registration.get("owner")
            or (run_owner is not None and registration["owner"] != run_owner)):
        raise ValueError("Use the matching active RUN.json for the saved-server policy QA")
    if qa.exists():
        raise ValueError("Never reuse or clean an existing profile for this policy probe")
    qa.mkdir()
    return qa, registration["owner"]


def cleanup_profile(game, qa):
    if game.parent != qa or game.name != "game":
        raise ValueError("Refusing cleanup outside this probe's new technical game directory")
    ordinary(qa, directory=True)
    ordinary(game, directory=True)
    target = Path("\\\\?\\" + str(game)) if os.name == "nt" else game
    for folder, directories, files in os.walk(target, topdown=True, followlinks=False):
        for name in directories:
            ordinary(Path(folder) / name, directory=True)
        for name in files:
            ordinary(Path(folder) / name, directory=False)
    # The only directory removed is the profile created after new_qa refused
    # every existing destination. Shared resources and candidate files stay out.
    shutil.rmtree(target)


def verify(args):
    client_file = args.candidate / "native/horizon-client.exe"
    receipt_file = args.candidate / "build-receipt.json"
    receipt = load(receipt_file)
    ordinary(client_file, directory=False)
    ordinary(args.controller, directory=False)
    ordinary(args.resources, directory=True)
    client_hash = sha(client_file)
    if client_hash != receipt.get("clientSha256"):
        raise ValueError("The candidate client does not match its accepted build receipt")
    descriptor = load(args.candidate / "portable-pack.json")
    environment = environment_policy(args.candidate)
    presentation = load(args.candidate / "native/presentation.json")
    desktop_helper = args.base_repo / "tools/server_list_qa.py"
    ordinary(desktop_helper, directory=False)
    desktop_helper_hash = sha(desktop_helper)
    # Share Edition's tested real-desktop input handshake rather than invent a
    # second protocol. Importing the helper does not execute its CLI or a client.
    sys.path.insert(0, str(args.base_repo / "tools"))
    try:
        desktop_keys_class = module_at(desktop_helper).DesktopKeys
    finally:
        sys.path.pop(0)
    qa, owner = new_qa(args.qa, args.run_owner)
    key_requests = args.ui_key_requests or qa / "desktop-keys"
    if not key_requests.is_relative_to(qa) or key_requests.exists():
        raise ValueError("Use a new desktop-key request directory inside this fresh QA output")
    report = {"schemaVersion": 1, "status": "RUNNING", "clientSha256": client_hash,
              **environment, "environmentApplications": [],
              "nativeRuntime": descriptor["nativeRuntime"], "buildReceiptSha256": sha(receipt_file),
              "horizonRevision": receipt["horizonRevision"], "runOwner": owner,
              "methodology": "Six ordinary starts; fresh, reconstructed legacy and custom manual NBT byte comparison",
              "ordinaryLaunch": True, "launchArguments": [], "processExitCodes": [],
              "controlVia": "technical horizon-extras.txt agentControl:true; HORIZON_CONTROL endpoint",
              "desktopKeyProtocol": "edition-server-list-qa/checkpoint-v1",
              "desktopKeyHelperSha256": desktop_helper_hash,
              "runs": []}
    game = qa / "game"
    try:
        game.mkdir()
        (game / "horizon-extras.txt").write_text("agentControl:true\n", encoding="utf-8")
        file = game / "servers.dat"

        def run(index, names):
            result = ordinary_launch(client_file, args.controller, args.resources, qa, game, index,
                                     lambda control, desktop_keys: server_view(control, file, names, desktop_keys),
                                     desktop_keys_class, key_requests, presentation["name"])
            report["runs"].append(result)
            report["launchArguments"].append(result["arguments"])
            report["processExitCodes"].append(result["exitCode"])
            report["environmentApplications"].append({"index": index, **result["environment"]})
            return result

        for index in (1, 2):
            result = run(index, [])
            saved, _ = saved_servers(file)
            report["freshProfile"] = {"savedServerCount": len(saved), "serversDatAbsent": not file.exists(),
                                      "automaticSeedFilesCreated": seed_files(game),
                                      "nativeSavedServers": result["servers"]}
            if saved or result["servers"] or seed_files(game):
                raise ValueError("Fresh startup inserted automatic saved servers or offer markers")

        # A faithful author-layout reconstruction is separately identified in
        # evidence; it must not be presented as an actual user's captured file.
        legacy = legacy_nbt()
        file.write_bytes(legacy)
        for index in (3, 4):
            result = run(index, [name for name, _ in LEGACY_ROWS])
            expected_rows = [{"name": name, "address": address} for name, address in LEGACY_ROWS]
            if file.read_bytes() != legacy or result["servers"] != expected_rows or seed_files(game):
                raise ValueError("Ordinary startup rewrote the reconstructed legacy saved-server list")
        report["legacyUpgrade"] = {
            "fixtureOrigin": "Reconstructed author-format list from recorded 5a9dd000 ordinary-startup names/addresses",
            "historicalBaselineClientSha256": "5a9dd0001d9ccce3183561da4349a22751f5515efd3a9f3ed4988756e2b4ff81",
            "copiedFromUserFile": False,
            "savedServerCount": 2, "beforeSha256": LEGACY_SHA256, "afterSha256": sha(file),
            "sizeBytes": len(legacy), "bytePreserved": True,
            "savedViewRows": next(row["rows"] for row in result["serverView"]["observations"]
                                  if row["action"] == "open-saved"),
            "defaultSteamRows": result["serverView"]["observations"][0]["rows"],
        }

        # The only servers.dat written by this script belongs to its brand-new
        # disposable profile. Existing player files are neither read nor deleted.
        payload = manual_nbt()
        file.write_bytes(payload)
        before_hash = hashlib.sha256(payload).hexdigest()
        # Marker contents do not establish ownership of a server entry. The
        # policy must leave these pre-existing manual bytes alone as well.
        legacy_markers = {name: (MANUAL_ADDRESS + "\n").encode("ascii") for name in SEED_FILES}
        for name, data in legacy_markers.items():
            (game / name).write_bytes(data)
        for index in (5, 6):
            result = run(index, [MANUAL_NAME])
            saved, root = saved_servers(file)
            if (len(saved) != 1 or saved[0].get("name") != MANUAL_NAME
                    or saved[0].get("ip") != MANUAL_ADDRESS
                    or root.get("customRootData") != "not an engine-owned server-list field"
                    or saved[0].get("customManualData", {}).get("bytes") != b"\x00\xff\x03\x7f\x80"):
                raise ValueError("The manual server or unknown NBT fields did not survive startup")
            if result["servers"] != [{"name": MANUAL_NAME, "address": MANUAL_ADDRESS}]:
                raise ValueError("The native saved-server list did not retain exactly the manual entry")
            if file.read_bytes() != payload:
                raise ValueError("Ordinary startup or shutdown rewrote the exact manual NBT bytes")
            if any((game / name).read_bytes() != data for name, data in legacy_markers.items()):
                raise ValueError("Ordinary startup rewrote pre-existing manual marker files")
        if (game / "saves").exists() and any((game / "saves").iterdir()):
            raise ValueError("The menu-only server policy QA unexpectedly created a world")
        report["manualRestart"] = {"savedServerCount": 1, "beforeSha256": before_hash,
                                   "afterSha256": sha(file), "sizeBytes": len(payload),
                                   "bytePreserved": True, "unknownFieldsPreserved": True,
                                   "nativeSavedServers": result["servers"], "address": MANUAL_ADDRESS,
                                   "savedViewRows": next(row["rows"] for row in result["serverView"]["observations"]
                                                         if row["action"] == "open-saved")}
        report["manualLegacyMarkersPreserved"] = True
        views = [run["serverView"] for run in report["runs"]]
        if len(views) != 6 or any(view["status"] != "PASS" for view in views):
            raise ValueError("Saved-server UI acceptance did not complete for all six ordinary starts")
        report["savedServerView"] = {
            "status": "PASS", "contractRevision": VIEW_CONTRACT_REVISION,
            "widgetIds": list(VIEW_IDS), "ordinaryRunsVerified": len(views),
            "defaultSteamOnly": True, "savedAllRows": True, "refreshRetainsView": True,
            "explicitSteamReturn": True, "backAndEscapeReturnToTitle": True,
            "reopenDefaultsSteam": True, "bytesUnchanged": True,
        }
        if (sha(client_file) != client_hash or sha(receipt_file) != report["buildReceiptSha256"]
                or sha(desktop_helper) != desktop_helper_hash):
            raise ValueError("The accepted client, build receipt or desktop-key helper changed during the policy probe")
        report["status"] = "PASS"
    except BaseException as error:
        report["status"] = "FAIL"
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        # ordinary_launch stops only this probe's own process, including on
        # failed startup. Remove its reproducible profile/cache, keeping logs
        # and this compact receipt for the run owner's audit.
        try:
            if game.exists():
                cleanup_profile(game, qa)
            report["fixtureRemoved"] = True
        except Exception as error:
            report["status"] = "FAIL"
            report["fixtureRemoved"] = False
            report["cleanupError"] = str(error)
            raise
        finally:
            write(qa / "server-policy.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base-repo", "candidate", "controller", "resources", "qa"):
        parser.add_argument("--" + name, required=True, type=lambda value: Path(os.path.abspath(value)))
    parser.add_argument("--ui-key-requests", type=lambda value: Path(os.path.abspath(value)),
                        help="new request directory inside --qa; defaults to qa/desktop-keys")
    parser.add_argument("--run-owner", help="optional exact owner check against the active RUN.json")
    args = parser.parse_args()
    print(json.dumps(verify(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
