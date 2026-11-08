"""Verify real release acceptance, then reject regressions using in-memory copies.

Reads the exact candidate and the source QA receipts. It changes no files,
starts no game, and prints a compact JSON receipt only after every check passes.
"""

import argparse
from copy import deepcopy
import json
from pathlib import Path

import generate_manifest as release


class TextPreset:
    """Supply changed preset bytes to the same validator without writing a file."""

    def __init__(self, value):
        self.value = value

    def read_text(self, *, encoding):
        return self.value


def verify(candidate, base_repo):
    build, _ = release.accepted_build(candidate)
    if release.sha(candidate / "native/horizon-client.exe") != build["clientSha256"]:
        raise ValueError("The candidate EXE differs from the accepted build")
    bootstrap = release.load(release.ROOT / "tools/bootstrap-lock.json")
    # A missing, stale, diagnostic or failed on-disk baseline aborts before any
    # mutation cases. The mutation fixtures never replace that baseline.
    release.verify_release_startup(candidate, build, bootstrap["sha256"], base_repo=base_repo)
    qa = release.ROOT / "tools/qa"
    gameplay = release.load(qa / "orbital-startup.json")
    multiplayer = release.load(qa / "multiplayer.json")
    backup = release.load(qa / "backup-guard.json")
    server_policy = release.load(qa / "server-policy.json")
    native_notices = release.load(qa / "native-notices.json")
    launcher_support = release.load(qa / "launcher-notices-support.json")
    from native_notice_acceptance import verify_launcher_support, verify_native_notices
    from stage_mod_pack import verify_environment_evidence
    presentation_sha = release.sha(candidate / "native/presentation.json")
    preset = candidate / release.CONTROLS_PRESET
    checks = []

    def reject(name, validator, original, mutate):
        changed = deepcopy(original)
        mutate(changed)
        try:
            validator(changed)
        except ValueError as error:
            checks.append({"case": name, "status": "PASS", "rejectedWith": str(error)})
        else:
            raise ValueError(f"The release gate incorrectly accepted: {name}")

    game_check = lambda report: release.verify_gameplay_acceptance(report, build, preset)
    network_check = lambda report: release.verify_multiplayer_acceptance(report, build)
    backup_check = lambda report: release.verify_backup_acceptance(report, build)
    server_check = lambda report: release.verify_server_policy_acceptance(report, build, base_repo=base_repo)
    notice_check = lambda report: verify_native_notices(report, candidate, build, base_repo)
    game_environment_check = lambda report: verify_environment_evidence(
        report, presentation_sha, ("bootstrap", "bootstrap", "direct-client"))

    def remove_environment_proof(value):
        for name in ("environmentPolicy", "environmentDefaults", "presentationSha256", "environmentApplications"):
            value.pop(name, None)

    reject("Same-core gameplay predates the mandatory presentation policy", game_environment_check, gameplay,
           remove_environment_proof)
    reject("QA presentation differs from the released presentation bytes", game_environment_check, gameplay,
           lambda value: value.update(presentationSha256="0" * 64))
    reject("QA omits the pack-owned environment default", game_environment_check, gameplay,
           lambda value: value.update(environmentDefaults={}))
    reject("Direct QA relies on an inherited diagnostic mask", game_environment_check, gameplay,
           lambda value: value["environmentApplications"][2].update(environmentDefaultOrigins={
               name: "inherited" for name in value["environmentDefaults"]}))
    reject("Bootstrap QA claims a child default that it did not observe", game_environment_check, gameplay,
           lambda value: value["environmentApplications"][0].update(
               effectiveEnvironmentDefaults=value["environmentDefaults"]))
    for name, validator, report in (("gameplay", game_check, gameplay),
                                     ("multiplayer", network_check, multiplayer),
                                     ("backup", backup_check, backup),
                                     ("server policy", server_check, server_policy),
                                     ("native notices", notice_check, native_notices)):
        reject(f"Different client: {name}", validator, report,
               lambda value: value.update(clientSha256="0" * 64))
    reject("Startup-only report", game_check, gameplay,
           lambda value: value.update(mode="startup-menu", gameplay="NOT RUN"))
    reject("Publication chat has no actual numeric listener port", game_check, gameplay,
           lambda value: value["pauseMenu"].update(publicationChat=[]))
    reject("Multiplayer system chat port differs from the actual listener", network_check, multiplayer,
           lambda value: value["publishMenu"]["numericPortNotice"].update(statusPort=value["publishMenu"]["port"] + 1))
    reject("Diagnostic multiplayer report", network_check, multiplayer,
           lambda value: value.update(diagnosticOnly=True))
    reject("Skipped JEI in an otherwise complete multiplayer report", network_check, multiplayer,
           lambda value: value.update(jei={"status": "NOT RUN"}))

    changed_preset = preset.read_text(encoding="utf-8").replace(
        "key_waypoints.create:key.keyboard.f7", "key_waypoints.create:key.keyboard.n").replace(
        "key_waypoints.list:key.keyboard.f8", "key_waypoints.list:key.keyboard.m")
    if changed_preset == preset.read_text(encoding="utf-8"):
        raise ValueError("The real preset lacks the approved Waypoints bindings")
    reject("Lost Waypoints F7/F8 separation",
           lambda report: release.verify_gameplay_acceptance(report, build, TextPreset(changed_preset)),
           gameplay, lambda value: None)

    reject("One participant receives no remote Orbital visual", network_check, multiplayer,
           lambda value: value["orbital"]["concurrentEffects"].update(guestReceivedHost=[]))

    def unchanged_guest_block(value):
        value["orbital"]["blocksAfter"][0] = value["orbital"]["blocksBefore"][0]

    reject("A guest terrain fixture block remains unchanged", network_check, multiplayer,
           unchanged_guest_block)
    reject("Unproven negative guest permission check", network_check, multiplayer,
           lambda value: value["guestCheatsDisabled"].update(denialObserved=False, denialEventSeq=[]))
    reject("Missing native backup", backup_check, backup,
           lambda value: value.update(firstOpenBackup="NOT RUN", backupMembersVerified=0))
    reject("Duplicate native backup on reopening", backup_check, backup,
           lambda value: value.update(reopenWithoutDuplicate="FAIL", newBackupsAfterReopen=2))
    reject("Automatic public server row on a fresh profile", server_check, server_policy,
           lambda value: value["freshProfile"].update(savedServerCount=1))
    reject("Control-socket harness bypasses ordinary server seeding", server_check, server_policy,
           lambda value: value["launchArguments"][0].append("--control-socket=synthetic.sock"))
    reject("Personal servers.dat bytes changed after restart", server_check, server_policy,
           lambda value: value["manualRestart"].update(afterSha256="0" * 64, bytePreserved=False))
    reject("Old startup-only policy report has no Saved-view acceptance", server_check, server_policy,
           lambda value: value.pop("savedServerView"))
    reject("Legacy saved rows leak into the automatic Steam view", server_check, server_policy,
           lambda value: value["runs"][2]["serverView"]["observations"][0].update(rows=value["legacyUpgrade"]["savedViewRows"]))
    reject("Explicit Saved view hides an existing legacy row", server_check, server_policy,
           lambda value: value["runs"][2]["serverView"]["observations"][2].update(rows=[]))
    reject("Saved refresh unexpectedly switches to Steam", server_check, server_policy,
           lambda value: value["runs"][2]["serverView"]["observations"][3].update(mode="steam"))
    reject("Explicit Steam action remains in Saved", server_check, server_policy,
           lambda value: value["runs"][2]["serverView"]["observations"][4].update(mode="saved"))
    reject("Saved Back/Escape fail to return to Title", server_check, server_policy,
           lambda value: value["runs"][2]["serverView"].update(backToTitle=False, escapeToTitle=False))
    reject("Reopening multiplayer remembers Saved instead of default Steam", server_check, server_policy,
           lambda value: value["runs"][2]["serverView"]["observations"][-1].update(mode="saved"))
    reject("A UI refresh rewrites manual NBT despite a later matching final hash", server_check, server_policy,
           lambda value: value["runs"][4]["serverView"]["observations"][3].update(serversDatSha256="0" * 64))
    reject("The reconstructed legacy list was rewritten", server_check, server_policy,
           lambda value: value["legacyUpgrade"].update(afterSha256="0" * 64, bytePreserved=False))
    reject("Manual unknown NBT fields were lost", server_check, server_policy,
           lambda value: value["manualRestart"].update(unknownFieldsPreserved=False))
    reject("Server Escape has no actual desktop acknowledgement", server_check, server_policy,
           lambda value: value["runs"][0]["serverView"].update(desktopKeys=[]))
    reject("Server Escape was serviced by another unaccepted helper source", server_check, server_policy,
           lambda value: value.update(desktopKeyHelperSha256="0" * 64))
    reject("Server Escape acknowledgement belongs to another process", server_check, server_policy,
           lambda value: value["runs"][0]["serverView"]["desktopKeys"][0]["acknowledgement"].update(processId=0))
    reject("Server Escape acknowledgement arrived after the owned checkpoint expired", server_check, server_policy,
           lambda value: value["runs"][0]["serverView"]["desktopKeys"][0]["acknowledgement"].update(
               performedUnixMs=value["runs"][0]["serverView"]["desktopKeys"][0]["request"]["createdUnixMs"] + 120_001))
    reject("Native notice fullscreen was not observed", notice_check, native_notices,
           lambda value: value["checks"].update(actualFullscreenVisibleNotice="NOT RUN"))
    reject("Native notice heartbeat belongs to another process", notice_check, native_notices,
           lambda value: value["captures"][0]["heartbeatAfter"].update(pid=value["nativeProcessId"] + 1))
    reject("Native notice renderer reports an error", notice_check, native_notices,
           lambda value: value["captures"][0]["heartbeatAfter"].update(error="renderer failed"))
    reject("Native notice heartbeats advance without visible pixels", notice_check, native_notices,
           lambda value: value["captures"][0]["pixels"].update(count=0, boxes=[]))
    reject("Native notice UI/input proof is absent", notice_check, native_notices,
           lambda value: value.update(pauseUiAfter={}, foregroundAfterChat=0))
    reject("Native notice focus proof uses no foreground window", notice_check, native_notices,
           lambda value: value.update(foregroundBeforeChat=0, foregroundAfterChat=0))
    reject("Native notice physical width does not double at DPI 2", notice_check, native_notices,
           lambda value: value["dpi"].update(twoWidth=value["dpi"]["oneWidth"]))
    reject("Native notice lifetime was not observed", notice_check, native_notices,
           lambda value: value["checks"].update(threeCardsAndSevenSecondExpiry="NOT RUN"))
    reject("Supporting launcher public bytes differ", verify_launcher_support, launcher_support,
           lambda value: value["publicReadback"].update(sha256="0" * 64))
    reject("Supporting launcher source lacks the native capability", verify_launcher_support, launcher_support,
           lambda value: value["sourceChecks"].update(nativeCapabilityConstant=False))
    return {
        "schemaVersion": 1, "status": "PASS", "clientSha256": build["clientSha256"],
        "horizonRevision": build["horizonRevision"], "realFinalBaseline": "PASS",
        "gateSourceSha256": release.sha(Path(release.__file__)),
        "verificationSourceSha256": release.sha(Path(__file__)),
        "sourceReceipts": {name: release.sha(qa / name) for name in
                           ("orbital-startup.json", "multiplayer.json", "backup-guard.json", "server-policy.json",
                            "native-notices.json", "launcher-notices-support.json")},
        "rejectionChecks": checks,
        "scope": "Release acceptance guards; no game, Steam or GPU scenarios are executed by this command.",
        "temporaryDataBytes": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=lambda value: Path(value).resolve())
    parser.add_argument("--base-repo", required=True, type=lambda value: Path(value).resolve())
    args = parser.parse_args()
    try:
        report = verify(args.candidate, args.base_repo)
    except (KeyError, OSError, ValueError) as error:
        parser.exit(1, f"Release gate verification: {error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
