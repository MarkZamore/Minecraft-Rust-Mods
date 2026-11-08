"""Open one disposable world with the real active Steam account for manual UI QA.

Does not invite anybody. Writes ready.json after a real Steam lobby is published,
then keeps only its own client alive for --hold-seconds or until qa/STOP exists.
The root operator can inspect Shift+Tab and the invitation dialog through the
official computer-use surface. Those checks remain NOT RUN in this script.
"""

import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
import winreg

from native_build import load, sha
from smoke_multiplayer import Client, exact_client, until


STEAM_ID_BASE = 76561197960265728
STEAM_DLL_SHA256 = "eb17909a76668cf9ae0b92a618a34a50f6c73d3a6787cb4dd8ce36a8b10bfb75"
UUID_NAMESPACE = uuid.UUID("3b26fe30-fc0f-44d9-8a9e-08c3bde54df1")
QA_NAME = "RustSteamQA"
WORLD = "RustSteamHostQA"


def portable_uuid(steam_id):
    # Exact lanmc_steam.rs derivation: SHA1(namespace BE bytes + UTF8
    # "steam:<decimal-id>"), first 16 bytes, UUID version 5 / RFC variant.
    # Keep its published Rust test vector as a cross-language check.
    return str(uuid.uuid5(UUID_NAMESPACE, f"steam:{steam_id}"))


def registry_identity_hint():
    """A fallback hint only; a zero/stale registry value proves no SDK failure."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\ActiveProcess") as key:
        account, kind = winreg.QueryValueEx(key, "ActiveUser")
    if kind != winreg.REG_DWORD or type(account) is not int or not 0 < account <= 0xffffffff:
        return None
    return STEAM_ID_BASE + account


def _sdk_identity_in_process(candidate):
    """Read the real SDK identity, shut it down, and restore probe-only env.

    ABI comes from the pinned Steam SDK 1.64 steam_api.h / steam_api_flat.h:
    InitFlat(SteamErrMsg*), SteamUser_v023, BLoggedOn(ISteamUser*), GetSteamID.
    No lobby, invitation, network listener or game world is created here.
    """
    dll = candidate / "native/steam_api64.dll"
    if ctypes.sizeof(ctypes.c_void_p) != 8 or sha(dll) != STEAM_DLL_SHA256:
        raise ValueError("SDK identity query requires the exact verified Windows x64 Steam DLL")
    steam = ctypes.CDLL(str(dll))
    error_type = ctypes.c_char * 1024  # SteamErrMsg in steam_api.h.
    steam.SteamAPI_InitFlat.argtypes = [ctypes.POINTER(error_type)]
    steam.SteamAPI_InitFlat.restype = ctypes.c_int
    steam.SteamAPI_SteamUser_v023.argtypes = []
    steam.SteamAPI_SteamUser_v023.restype = ctypes.c_void_p
    steam.SteamAPI_ISteamUser_BLoggedOn.argtypes = [ctypes.c_void_p]
    steam.SteamAPI_ISteamUser_BLoggedOn.restype = ctypes.c_bool
    steam.SteamAPI_ISteamUser_GetSteamID.argtypes = [ctypes.c_void_p]
    steam.SteamAPI_ISteamUser_GetSteamID.restype = ctypes.c_uint64
    steam.SteamAPI_Shutdown.argtypes = []
    steam.SteamAPI_Shutdown.restype = None
    values = {"SteamAppId": "480", "SteamGameId": "480", "SteamNoOverlayUIDrawing": "1"}
    previous = {name: os.environ.get(name) for name in values}
    initialized = False
    try:
        os.environ.update(values)
        result = steam.SteamAPI_InitFlat(ctypes.byref(error_type()))
        initialized = result == 0
        if not initialized:
            return {"initResult": result, "loggedOn": False, "steamId": None}
        user = steam.SteamAPI_SteamUser_v023()
        logged_on = bool(user) and bool(steam.SteamAPI_ISteamUser_BLoggedOn(user))
        steam_id = int(steam.SteamAPI_ISteamUser_GetSteamID(user)) if user else None
        return {"initResult": result, "loggedOn": logged_on, "steamId": steam_id}
    finally:
        try:
            if initialized:
                steam.SteamAPI_Shutdown()
        finally:
            for name, old in previous.items():
                if old is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = old


def sdk_identity(candidate):
    """Isolate Valve's native diagnostic output from the public console.

    SteamAPI may print the account's SteamID from native code. Capture the
    probe process privately and return only its parsed identity to the caller;
    raw stdout/stderr are neither logged nor attached to public QA evidence.
    """
    marker = "LANMINECRAFT_SDK_IDENTITY="
    code = (
        "import json,sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);"
        "import smoke_steam_host as m;"
        "print('LANMINECRAFT_SDK_IDENTITY='+json.dumps(m._sdk_identity_in_process(Path(sys.argv[2]))),flush=True)"
    )
    probe = subprocess.run(
        [sys.executable, "-B", "-c", code, str(Path(__file__).resolve().parent), str(candidate)],
        capture_output=True, encoding="utf-8", errors="replace", timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if probe.returncode != 0:
        raise RuntimeError("Private Steam SDK identity probe failed")
    lines = [line[len(marker):] for line in probe.stdout.splitlines() if line.startswith(marker)]
    if len(lines) != 1:
        raise RuntimeError("Private Steam SDK probe returned no unique identity result")
    return json.loads(lines[0])


def steam_identity(candidate):
    if portable_uuid(76561197960265729) != "535f8bea-8e65-5367-a3d5-da0dd6c5c31d":
        raise RuntimeError("Steam UUID derivation differs from the Rust source test vector")
    observed = sdk_identity(candidate)
    if observed["initResult"] != 0:
        raise RuntimeError(f"Steam SDK identity initialization failed (result {observed['initResult']})")
    steam_id = observed["steamId"]
    if not observed["loggedOn"] or steam_id is None or steam_id >> 32 != 0x01100001 or steam_id & 0xffffffff == 0:
        raise RuntimeError("The real Steam SDK did not confirm a logged-in desktop account")
    # Bridge initialization validates the canonical identity again in the child.
    return steam_id, portable_uuid(steam_id)


def private_environment(candidate):
    steam_id, player_uuid = steam_identity(candidate)
    variant = load(candidate / "portable-pack.json")["nativeRuntime"]["version"]
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", variant):
        raise ValueError("Invalid exact candidate native variant")
    return {
        "SteamAppId": "480", "SteamGameId": "480",
        "LANMINECRAFT_STEAM_ID": str(steam_id),
        "LANMINECRAFT_PLAYER_UUID": player_uuid,
        "LANMINECRAFT_PLAYER_NAME": QA_NAME,
        "LANMINECRAFT_NATIVE_VARIANT": variant,
    }


class SteamClient(Client):
    def __init__(self, candidate, controller, resources, root, common_only):
        super().__init__(candidate, controller, resources, root, QA_NAME, common_only=common_only)
        for key in tuple(self.env):
            if key.upper() in {"STEAMNOOVERLAYUIDRAWING", "STEAMAPPID", "STEAMGAMEID"}:
                del self.env[key]
        self.env.update(private_environment(candidate))


def published_notice(host):
    messages = host("messages", "30")["chat"]
    # Do not store the lobby address/account/name in the public evidence.
    text = "\n".join(messages)
    return {
        "ready": "Steam address ready:" in text or "Адрес Steam готов:" in text,
        "copyableAddress": bool(re.search(r"\b\d+\.steam\b", text)),
        "inviteAction": "[Invite friends (Steam)]" in text or "[Пригласить друзей (Steam)]" in text,
        "closeAction": "[Close sharing]" in text or "[Закрыть общий доступ]" in text,
    }


def remove_own_game(qa):
    game = qa / "host/game"
    if not game.exists():
        return
    if game.is_symlink() or game.is_junction() or not game.resolve().is_relative_to(qa):
        raise RuntimeError("Refusing cleanup outside this Steam QA directory")
    for directory, dirs, files in os.walk(game):
        if ".git" in dirs or any((Path(directory) / name).is_symlink()
                                  or (Path(directory) / name).is_junction() for name in dirs + files):
            raise RuntimeError("Refusing linked/source data cleanup")
    shutil.rmtree("\\\\?\\" + str(game))


def run(args):
    # Validate exact Edition overlay receipt or exact Mods build receipt first.
    client_sha, revision = exact_client(args)
    project = Path(__file__).resolve().parents[3]
    allowed = (project / "artifacts/agent-runs").resolve()
    qa = args.qa
    if not qa.is_relative_to(allowed) or qa == allowed or qa.exists():
        raise ValueError("Use a fresh Steam QA directory below artifacts/agent-runs")
    task = allowed / qa.relative_to(allowed).parts[0]
    if not (task / "RUN.json").is_file():
        raise ValueError("Register task ownership/expiry in RUN.json before Steam QA")
    if not 1 <= args.hold_seconds <= 600:
        raise ValueError("Hold must be between 1 and 600 seconds")
    qa.mkdir(parents=True)
    report = {
        "schemaVersion": 1, "clientSha256": client_sha, "horizonRevision": revision,
        "transport": "real Steam, one host account", "steamHost": "FAIL",
        "manualShiftTabOverlay": "NOT RUN", "manualInvitationDialog": "NOT RUN",
        "twoAccountJoin": "NOT RUN", "invitesSent": 0,
    }
    try:
        with SteamClient(args.candidate, args.controller, args.resources, qa / "host",
                         common_only=args.common_only) as host:
            host("open", WORLD, "137248", "creative")
            host("wait-for", "loaded", "--timeout", "90")
            until(lambda: not (server := host("status").get("server")) or server.get("loaded"),
                  "Host world did not finish loading", 90)
            host("key", "esc", "tap")
            host("wait-for", "screen", "paused", "--timeout", "5")
            button = until(lambda: next((w for w in host("ui")["widgets"]
                                         if w.get("label") in {"Open to LAN", "Открыть для сети"}), None),
                           "Open to LAN button not available")
            host("click", button["id"])
            if host("ui")["screen"] != "playing":
                raise AssertionError("Publishing did not close the pause menu")
            notice = until(lambda: (value if all((value := published_notice(host)).values()) else None),
                           "Steam sharing notice/actions did not appear", 45)
            report["sharingNotice"] = notice
            report["publishClosedMenu"] = True
            ready = {
                "pid": host.process.pid, "controlSocket": str(host.endpoint),
                "controller": str(args.controller), "stopFile": str(qa / "STOP"),
                "holdSeconds": args.hold_seconds, "clientSha256": client_sha,
                "instructions": "Inspect Shift+Tab and invitation dialog; do not send invitations. Create STOP when finished.",
            }
            (qa / "ready.json").write_text(json.dumps(ready, ensure_ascii=False, indent=2) + "\n",
                                             encoding="utf-8", newline="\n")
            print(json.dumps({"status": "READY", "readyFile": str(qa / "ready.json")}), flush=True)
            deadline = time.monotonic() + args.hold_seconds
            while not (qa / "STOP").exists() and time.monotonic() < deadline:
                if host.process.poll() is not None:
                    raise RuntimeError("Steam host exited during manual inspection")
                time.sleep(0.25)
            host("release")
            host("lan", "close")
            until(lambda: not host("lan", "status").get("open"), "Sharing did not close", 10)
            report["sharingClosed"] = True
        report["steamHost"] = "PASS"
    except BaseException as error:
        # Errors from Steam may contain private identities/addresses. Keep only
        # the exception type here; raw local client logs remain available.
        report["errorType"] = type(error).__name__
        raise
    finally:
        (qa / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8", newline="\n")
        remove_own_game(qa)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("candidate", "controller", "resources", "qa"):
        parser.add_argument("--" + field, required=True, type=lambda value: Path(value).resolve())
    parser.add_argument("--common-only", action="store_true", help="candidate is Edition without mods")
    parser.add_argument("--edition-receipt", type=lambda value: Path(value).resolve())
    parser.add_argument("--hold-seconds", type=int, default=300,
                        help="manual inspection window, 1..600 seconds; qa/STOP ends it early")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
