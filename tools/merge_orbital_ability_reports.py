"""Validate and combine exact Orbital strike reports from independent QA runs."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re

from generate_manifest import verify_orbital_abilities
from native_build import ROOT, load, sha
from smoke_orbital_abilities import ABILITIES, DESIGNATOR_SEED, SEED, SOURCE, WIP


REPORT_FIELDS = {"requested", "abilities", "result"}
MODES = {"solo": ("host", "on"), "network": ("guest", "both")}


def source_name(path):
    workspace = ROOT.parent.parent
    return (path.relative_to(workspace) if path.is_relative_to(workspace) else path).as_posix()


def report_identity(candidate, mode):
    build = load(candidate / "build-receipt.json")
    client = sha(candidate / "native/horizon-client.exe")
    pin = load(ROOT / "tools/orbital_compat/upstream-0.5.17.json")
    companion = pin["contentCompanion"]
    asset = candidate / "native/horizon-mods/orbital.hmod"
    if (client != build["clientSha256"] or "orbital" not in build["ids"]
            or asset.stat().st_size != companion["size"] or sha(asset) != companion["sha256"]):
        raise ValueError("Candidate bytes differ from the built client or the pinned Orbital companion")
    caster, terrain = MODES[mode]
    return build, {"schemaVersion": 1, "clientSha256": client, "horizonRevision": build["horizonRevision"],
                   "orbitalVersion": pin["version"], "authorSource": SOURCE, "seed": SEED,
                   "designatorSeed": DESIGNATOR_SEED, "observer": caster == "guest",
                   "terrainModes": terrain, "castFrom": caster, "requestedResolutionScale": None,
                   "orbitalContentSha256": companion["sha256"], "unavailableNative": WIP}


def merge(candidate, mode, paths):
    build, expected = report_identity(candidate, mode)
    controller = None
    accepted, origins = {}, {}
    source_reports = []
    seen_paths = set()
    for path in paths:
        path = path.resolve(strict=True)
        name = source_name(path)
        if path in seen_paths:
            raise ValueError(f"Source report provided twice: {name}")
        seen_paths.add(path)
        data = path.read_bytes()
        report = json.loads(data)
        if not isinstance(report, dict):
            raise ValueError(f"Expected an Orbital ability report object: {name}")
        identity = {key: value for key, value in report.items() if key not in REPORT_FIELDS}
        digest = identity.pop("controllerSha256", "")
        if (identity != expected or type(report["schemaVersion"]) is not int
                or report["observer"] is not expected["observer"]
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None or controller not in (None, digest)):
            raise ValueError(f"Candidate, controller, author or test configuration differs: {name}")
        controller = digest
        requested, rows = report["requested"], report["abilities"]
        if (not isinstance(requested, list) or not isinstance(rows, dict) or not requested
                or len(requested) != len(set(requested)) or set(requested) != set(rows)
                or set(rows) - ABILITIES.keys()):
            raise ValueError(f"Requested abilities and rows must match the fixed roster: {name}")
        states = {key: row["status"] for key, row in rows.items()}
        if any(status not in ("PASS", "FAIL", "NOT RUN") for status in states.values()):
            raise ValueError(f"Unexpected ability status in {name}")
        actual_result = "PASS" if all(status == "PASS" for status in states.values()) else "FAIL"
        if report["result"] != actual_result:
            raise ValueError(f"Report result disagrees with its ability rows: {name}")
        copied = []
        for key in requested:
            if states[key] != "PASS":
                continue
            if key in accepted:
                raise ValueError(f"Duplicate PASS for {key}: {origins[key]} and {name}")
            # Copy each successful cast with its complete terrain modes intact.
            # A retry must supply the whole cast if an earlier attempt failed.
            accepted[key] = rows[key]
            origins[key] = name
            copied.append(key)
        source_reports.append({"path": name, "sha256": sha256(data).hexdigest(), "result": actual_result,
                               "acceptedAbilities": copied,
                               "otherAttempts": {key: status for key, status in states.items() if status != "PASS"}})
    missing = [key for key in ABILITIES if key not in accepted]
    if missing:
        raise ValueError("Missing complete PASS evidence for: " + ", ".join(missing))
    combined = {**expected, "controllerSha256": controller, "requested": list(ABILITIES),
                "result": "PASS", "abilities": {key: accepted[key] for key in ABILITIES},
                "sourceReports": source_reports}
    verify_orbital_abilities(combined, build, guest=mode == "network")
    return combined


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=MODES)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("reports", nargs="+", type=Path)
    args = parser.parse_args()
    try:
        combined = merge(args.candidate.resolve(strict=True), args.mode, args.reports)
        with args.output.open("x", encoding="utf-8", newline="\n") as target:
            json.dump(combined, target, ensure_ascii=False, indent=2)
            target.write("\n")
    except (KeyError, OSError, TypeError, ValueError) as error:
        parser.exit(1, f"Orbital ability evidence: {error}\n")
    print(f"{len(combined['abilities'])} verified abilities · {args.output}")


if __name__ == "__main__":
    main()
