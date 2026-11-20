"""Verify Orbital, foliage and HUD acceptance for the exact composed Rust Mods payload."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from generate_manifest import verify_orbital_abilities, verify_vegetation_motion
from native_build import INPUT_LOCK, ROOT, load, sha
from shared_qa_identity import shared_identity
from smoke_mod_hud import inspect_frames
from stage_shared_pack import CLIENT, COMPOSITION, require


FIRST_COMPOSITION = "5cf7863d3d316481a107ae91ec5ed3da861d0d8d7ce6c8dcba9f76b490508ac2"


def static_approval(composition, revision, provisional):
    require(re.fullmatch(r"[0-9a-f]{40}", revision), "Supply the exact approved static source revision")
    raw = subprocess.check_output(["git", "-C", str(ROOT), "cat-file", "blob", f"{revision}:tools/distribution-lock.json"])
    approved = json.loads(raw)
    digest = hashlib.sha256(raw).hexdigest()
    require(approved["schemaVersion"] == 1 and not approved.get("provisional")
            and approved["receiptSha256"] == composition["staticBuildReceiptSha256"],
            "The approved static source belongs to a different build")
    composed_digest = composition["staticDistributionLockSha256"]
    if provisional is None:
        require(composition["staticFileLockStatus"] == "approved" and composed_digest == digest,
                "Stage the shared candidate from the exact committed static lock bytes")
    else:
        earlier = load(provisional)
        require(composition["staticFileLockStatus"] == "provisional-file-verification"
                and earlier.get("provisional") is True and sha(provisional) == composed_digest
                and all(earlier[key] == approved[key] for key in ("receiptSha256", "files", "library")),
                "The provisional static candidate differs from the approved static file inventory")
    return {"revision": revision, "distributionLockSha256": digest,
            "composedFromDistributionLockSha256": composed_digest}


def legacy_preflight(path, binding, composition):
    if path is None:
        return None
    proof = load(path)
    require(binding.get("sharedCompositionSha256") == FIRST_COMPOSITION
            and proof["schemaVersion"] == 1
            and proof["compositionSha256"] == FIRST_COMPOSITION
            and proof["clientSha256"] == composition["clientSha256"]
            and proof["abiBuildId"] == composition["abiBuildId"]
            and proof["sidecarReceiptSha256"] == composition["sidecarReceiptSha256"]
            and proof["noticeRuntimeArchiveSha256"] == composition["sharedRuntimeArchiveSha256"]
            and proof["fullFileCount"] == len(composition["files"]),
            "The original migration preflight does not identify this exact composed profile")
    return sha(path)


def verify_report_binding(report, name, client, binding, preflight):
    require(report.get("schemaVersion") == 1 and report.get("clientSha256") == client,
            f"{name} evidence belongs to a different client")
    observed = {key: report[key] for key in binding if key in report}
    require(observed == binding or (not observed and preflight is not None),
            f"{name} evidence belongs to a different or unidentified shared profile")
    if "firstMigrationPreflightSha256" in report:
        require(preflight is not None and report["firstMigrationPreflightSha256"] == preflight,
                f"{name} merge did not retain the original migration preflight")
        return "merged-first-migration-preflight"
    return "report" if observed else "first-migration-preflight"


def verify_hud(report, folder):
    require(report.get("disposableWorldRemoved") is True and report.get("renderer", {}).get("backend")
            and report.get("jeiWidgetCount", 0) >= 8, "HUD reported no rendered gameplay or Creative item panel")
    paths = []
    for key in ("wheelOpen", "wheelClosed", "creative"):
        name = report["frames"][key]["file"]
        require(Path(name).name == name, "Invalid HUD capture filename")
        paths.append(folder / name)
    observed = inspect_frames(*paths)
    require(observed["result"] == "PASS" and all(observed[key] == report[key] for key in ("result", "checks", "frames")),
            "Actual HUD, wheel and JEI pixels differ from the accepted report")


def verify(args):
    candidate = args.candidate.resolve(strict=True)
    client = sha(candidate / CLIENT)
    binding = shared_identity(candidate, client)
    require(binding, "This verifier requires the composed shared Mods QA view")
    build = load(candidate / "build-receipt.json")
    composition = load(candidate / COMPOSITION)
    selection = load(ROOT / "tools/mod-selection.json")
    inputs = load(INPUT_LOCK)
    projects = {row["id"]: row["version"] for row in inputs["projects"]}
    require(selection["schemaVersion"] == 1 and selection["inputLockSha256"] == build["inputLockSha256"] == sha(INPUT_LOCK)
            and selection["ids"] == build["ids"] and inputs["horizonRevision"] == build["horizonRevision"]
            and {row["id"]: row["version"] for row in composition["compiledMods"]}
            == {mod: projects[mod] for mod in selection["ids"]},
            "The shared candidate differs from the reviewed author inputs and selected mods")
    approval = static_approval(composition, args.static_revision, args.provisional_lock)
    preflight = legacy_preflight(args.first_migration_preflight, binding, composition)
    evidence = {}
    for name in ("solo", "network", "scaled", "vegetation", "hud"):
        path = getattr(args, name)
        report = load(path)
        provenance = verify_report_binding(report, name, client, binding, preflight)
        if name == "vegetation":
            verify_vegetation_motion(report, build)
        elif name == "hud":
            verify_hud(report, path.parent)
        else:
            verify_orbital_abilities(report, build, guest=name == "network",
                                     resolution_scale=75 if name == "scaled" else None)
        evidence[name] = {"sha256": sha(path), "compositionEvidence": provenance}
    result = {"schemaVersion": 1, "status": "PASS", "clientSha256": client, **binding, "staticApproval": approval,
              "horizonRevision": build["horizonRevision"], "inputLockSha256": build["inputLockSha256"],
              **{key: composition[key] for key in ("abiBuildId", "sidecarReceiptSha256", "sharedRuntimeArchiveSha256")},
              "modCount": len(build["ids"]), "fileCount": len(composition["files"]), "reports": evidence}
    if preflight:
        result["firstMigrationPreflightSha256"] = preflight
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-revision", required=True)
    for name in ("candidate", "solo", "network", "scaled", "vegetation", "hud"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--provisional-lock", type=Path)
    parser.add_argument("--first-migration-preflight", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args), indent=2))
    except (KeyError, OSError, TypeError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Shared Mods acceptance: {error}\n")


if __name__ == "__main__":
    main()
