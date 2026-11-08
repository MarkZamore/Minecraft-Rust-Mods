"""Lock Horizon's registry and inspect reviewed mod archives without executing them."""

import argparse
import base64
import collections
import concurrent.futures
import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import tempfile
import tomllib
import urllib.parse
import urllib.request
import zipfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
LOCK = ROOT / "tools/mod-input-lock.json"
OFFICIAL_SOURCES = ROOT / "tools/official-mod-sources.json"
REGISTRY = "https://horizon-rust.github.io/horizon-mods/index.json"
HORIZON_REVISION = "2ebf00a0281c592f79191e76fce0e7d522c6f425"
HORIZON_HOOKS = 432
MC_VERSION = "26.3"
MOD_KINDS = {"mod": ".hmod", "native-mod": ".hnmod"}
LICENSES = {"MIT", "Apache-2.0", "CC0-1.0"}
MAX_ASSET = 64 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
ID = re.compile(r"[a-z0-9_]{1,64}")
HEX256 = re.compile(r"[a-f0-9]{64}")
NATIVE_API = re.compile(r"native-1/([a-f0-9]{40})/([a-f0-9]{40})")
GITHUB_ASSET = re.compile(r"/[\w.-]+/[\w.-]+/releases/download/[\w.+-]+/([\w.+-]+)")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_https(url, limit):
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError("An input URL must use HTTPS")
    request = urllib.request.Request(url, headers={"User-Agent": "LANMinecraft/HorizonModInventory",
                                                  "Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=45) as response:
        if urllib.parse.urlsplit(response.geturl()).scheme != "https":
            raise ValueError("An input URL redirected outside HTTPS")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"HTTP body exceeds the input size bound: {url}")
    return data


def write_json(path, value):
    data = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    path.write_text(data, encoding="utf-8", newline="\n")


def project_page(source):
    parsed = urllib.parse.urlsplit(source)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"Invalid official project source: {source}")
    if parsed.hostname == "github.com":
        return source.rstrip("/").split("/commit/", maxsplit=1)[0]
    return source


def validate_asset(project, version):
    file = version["file"]
    url = urllib.parse.urlsplit(file["url"])
    asset = GITHUB_ASSET.fullmatch(urllib.parse.unquote(url.path))
    if (url.scheme != "https" or url.netloc != "github.com" or not asset or asset[1] != file["name"] or
            url.query or url.fragment or not HEX256.fullmatch(file["sha256"]) or
            type(file["size"]) is not int or not 0 < file["size"] <= MAX_ASSET):
        raise ValueError(f"Invalid registry file for {project['id']} {version['version']}")
    suffix = MOD_KINDS.get(project["kind"])
    if suffix is not None and not file["name"].endswith(suffix):
        raise ValueError(f"Wrong extension for {project['id']} {version['version']}")
    if bool(project.get("key")) != bool(file.get("signature")):
        raise ValueError(f"Signature and key do not match for {project['id']} {version['version']}")


def eligibility(project, version, revoked):
    file = version["file"]
    for entry in revoked:
        if entry["id"] == project["id"] and entry.get("sha256", file["sha256"]) == file["sha256"]:
            return "revoked", entry["reason"]
    if project["kind"] not in MOD_KINDS:
        return "out-of-scope", f"Registry kind {project['kind']} is a separate pack type"
    if version.get("game") != MC_VERSION:
        return "wrong-game", f"Game version is {version.get('game')}"
    for failure in version.get("broken_on", []):
        if failure["horizon"] == HORIZON_REVISION:
            return "broken-upstream", f"Registry's exact-revision check failed: {failure['code']}"
    if project["kind"] == "mod" and version.get("api_level") is not None:
        return "wasm-removed", "The registry reports a WebAssembly component; this Nightly removed that runtime"
    if project["license"] not in LICENSES:
        return "license-review", "Redistribution in the composed game needs a reviewed license record"
    if project["kind"] == "mod":
        return "candidate", "Content package matches the Minecraft version"
    pins = NATIVE_API.fullmatch(version.get("api", ""))
    if pins is None:
        raise ValueError(f"Missing native source pins for {project['id']}")
    if version.get("hooks", 0) > HORIZON_HOOKS:
        return "hooks-too-new", f"Needs hook API {version['hooks']}; Nightly provides {HORIZON_HOOKS}"
    if version.get("hooks") or pins[1] == HORIZON_REVISION:
        return "candidate", "Declares supported hook API or the exact Horizon revision"
    return "compile-probe", f"Uses source pinned to {pins[1]}; test against the selected Nightly"


def lock_registry(raw):
    registry = json.loads(raw)
    if registry["schema"] != 1:
        raise ValueError("Unsupported Horizon registry schema")
    projects = []
    ids = set()
    official = json.loads(OFFICIAL_SOURCES.read_bytes())
    if official["schemaVersion"] != 1:
        raise ValueError("Unsupported official mod source schema")
    for project in sorted(registry["projects"] + official["projects"], key=lambda value: value["id"]):
        mod_id = project["id"]
        if not ID.fullmatch(mod_id) or mod_id in ids:
            raise ValueError(f"Invalid or duplicate registry ID {mod_id!r}")
        ids.add(mod_id)
        versions = []
        for version in project["versions"]:
            validate_asset(project, version)
            status, reason = eligibility(project, version, registry["revoked"])
            versions.append({
                **{key: version[key] for key in ("version", "api", "api_level", "game", "file", "side",
                                                   "hooks", "capabilities") if key in version},
                "eligibility": status,
                "reason": reason,
            })
        if not versions:
            raise ValueError(f"Registry project has no versions: {mod_id}")
        selected = next((item for item in versions if item["eligibility"] in {"candidate", "compile-probe"}),
                        versions[0])
        projects.append({
            **{key: project[key] for key in ("id", "kind", "name", "license", "source", "authors", "key",
                                            "officialRelease", "contentCompanion")
               if key in project},
            "websiteUrl": project_page(project["source"]),
            **selected,
            "otherVersions": [item for item in versions if item is not selected],
        })
    return {
        "schemaVersion": 1,
        "horizonRevision": HORIZON_REVISION,
        "horizonHooks": HORIZON_HOOKS,
        "minecraftVersion": MC_VERSION,
        "registry": {"url": REGISTRY, "generated": registry["generated"], "sha256": sha256(raw)},
        "projects": projects,
    }


def verify_signature(row, data, openssl, scratch):
    key = row.get("key")
    if not key:
        return "unsigned"
    public = base64.b64decode(key, validate=True)
    lines = row["file"]["signature"].splitlines()
    if (len(public) != 42 or public[:2] != b"Ed" or len(lines) != 4 or
            not lines[0].startswith("untrusted comment: ") or not lines[2].startswith("trusted comment: ")):
        raise ValueError("Malformed minisign key or signature")
    signature = base64.b64decode(lines[1], validate=True)
    global_sig = base64.b64decode(lines[3], validate=True)
    if len(signature) != 74 or signature[:2] not in (b"Ed", b"ED") or signature[2:10] != public[2:10] or len(global_sig) != 64:
        raise ValueError("Malformed minisign signature or unexpected key ID")
    signed = hashlib.blake2b(data).digest() if signature[:2] == b"ED" else data
    trusted = signature[10:] + lines[2][len("trusted comment: "):].encode("utf-8")
    with tempfile.TemporaryDirectory(dir=scratch) as directory:
        work = pathlib.Path(directory)
        message, key_file, signature_file = (work / name for name in ("message", "key.der", "signature"))
        key_file.write_bytes(bytes.fromhex("302a300506032b6570032100") + public[10:])
        for contents, expected in ((signed, signature[10:]), (trusted, global_sig)):
            message.write_bytes(contents)
            signature_file.write_bytes(expected)
            result = subprocess.run([openssl, "pkeyutl", "-verify", "-rawin", "-pubin", "-keyform", "DER",
                                     "-in", str(message), "-inkey", str(key_file), "-sigfile", str(signature_file)],
                                    capture_output=True, check=False)
            if result.returncode != 0:
                raise ValueError("Minisign signature does not match the project key")
    return "verified"


def validate_members(archive):
    expanded = 0
    paths = set()
    for entry in archive.infolist():
        path = entry.filename.rstrip("/")
        parts = path.split("/")
        if (not path or any(not part or part.endswith((" ", ".")) or
                any(char in '\\<>:"|?*' or ord(char) < 32 for char in part) or
                pathlib.PureWindowsPath(part).is_reserved() for part in parts) or
                path.casefold() in paths or stat.S_ISLNK(entry.external_attr >> 16)):
            raise ValueError(f"Unsafe or duplicate archive entry: {path!r}")
        paths.add(path.casefold())
        expanded += entry.file_size
    if expanded > MAX_EXPANDED:
        raise ValueError("Expanded archive exceeds size bound")


def checked_content_manifest(archive, expected):
    data = archive.read("horizon-mod.toml")
    manifest = tomllib.loads(data.decode("utf-8"))
    for field in ("id", "version", "api", "side"):
        if manifest[field] != expected[field]:
            raise ValueError(f"Content manifest mismatch: {field}")
    if sorted(manifest.get("capabilities", [])) != sorted(expected.get("capabilities", [])):
        raise ValueError("Content manifest mismatch: capabilities")
    if "manifestSha256" in expected and sha256(data) != expected["manifestSha256"]:
        raise ValueError("Content manifest differs from the official bundled bytes")


def content_file(row):
    if row["kind"] == "mod":
        return row["file"]
    return row.get("contentCompanion", {}).get("file")


def selected_ids(path, input_bytes):
    selection = json.loads(path.read_bytes())
    ids = selection["ids"]
    if (selection.get("schemaVersion") != 1 or selection.get("inputLockSha256") != sha256(input_bytes) or
            not ids or len(set(ids)) != len(ids)):
        raise ValueError("Selection must name unique projects in this exact input lock")
    return set(ids)


def read_archive(row, path):
    with zipfile.ZipFile(path) as archive:
        validate_members(archive)
        names = set(archive.namelist())
        licenses = sorted(name for name in names if
                          pathlib.PurePosixPath(name).name.upper().startswith(("LICENSE", "COPYING", "NOTICE")))
        if row["kind"] == "mod":
            checked_content_manifest(archive, row)
            return {"licenses": licenses, "wasm": "mod.wasm" in names,
                    "namespaces": sorted({name.split("/")[1] for name in names
                        if name.startswith(("assets/", "data/")) and name.count("/") >= 2})}

        manifest = json.loads(archive.read("horizon-native.json"))
        pins = NATIVE_API.fullmatch(row["api"])
        expected = {"schema": 1, "id": row["id"], "version": row["version"], "game": row["game"],
                    "hooks": row.get("hooks", 0), "horizon": pins[1], "mixins": pins[2]}
        if any(manifest.get(key, 0 if key == "hooks" else None) != value for key, value in expected.items()):
            raise ValueError("Native manifest differs from registry pins")
        if companion := row.get("contentCompanion"):
            checked_content_manifest(archive, {**companion, "id": row["id"], "version": row["version"]})
            if "mod.wasm" in names or not any(name.startswith(("assets/", "data/")) for name in names):
                raise ValueError("Expected an asset-only content companion in the native archive")
        mixins = []
        for name in sorted(names):
            if pathlib.PurePosixPath(name).name != "horizon-mixin.toml":
                continue
            bundle = tomllib.loads(archive.read(name).decode("utf-8"))
            mod = bundle["mod"]
            compatibility = bundle.get("compatibility", {})
            if compatibility.get("horizon_revision") != manifest["horizon"]:
                raise ValueError(f"Mixin source pin differs from registry pin: {name}")
            mixins.append({
                "manifest": name,
                "mod": mod,
                "dependencies": bundle.get("dependencies", {}),
                "modules": [{key: module[key] for key in ("file", "name")} for module in bundle.get("module", [])],
                "injections": [{key: injection[key] for key in ("id", "kind", "target", "hook") if key in injection}
                               for injection in bundle.get("injection", [])],
                "crates": [crate["path"] for crate in bundle.get("crate", [])],
            })
        if not mixins or not any(bundle["mod"]["id"] == row["id"] for bundle in mixins):
            raise ValueError("Expected project mixin manifest is absent")
        return {"licenses": licenses, "mixins": mixins}


def inspect(row, cache, openssl):
    file = row["file"]
    path = cache / (file["sha256"] + MOD_KINDS[row["kind"]])
    if path.is_symlink() or path.is_junction():
        raise ValueError("Linked archive in candidate cache")
    data = path.read_bytes() if path.is_file() else read_https(file["url"], file["size"])
    if len(data) != file["size"] or sha256(data) != file["sha256"]:
        raise ValueError("Archive size or SHA-256 differs from the lock")
    trust = verify_signature(row, data, openssl, cache)
    if not path.is_file():
        path.write_bytes(data)
    return {"id": row["id"], "version": row["version"], "sha256": file["sha256"], "signature": trust,
            **read_archive(row, path)}


def overlaps(records):
    modules = collections.defaultdict(set)
    injections = collections.defaultdict(list)
    for record in records:
        for bundle in record.get("mixins", []):
            for module in bundle["modules"]:
                modules[(module["file"], module["name"])].add(record["id"])
            for injection in bundle["injections"]:
                target = injection.get("target", injection.get("hook"))
                if target:
                    injections[target].append((record["id"], injection["kind"]))
    return {
        "duplicateModules": [{"file": file, "name": name, "mods": sorted(ids)}
                             for (file, name), ids in sorted(modules.items()) if len(ids) > 1],
        "sharedOverwriteTargets": [{"target": target, "injections": sorted(patches)}
                                   for target, patches in sorted(injections.items())
                                   if any(kind == "OVERWRITE" for _, kind in patches)
                                   and len({mod_id for mod_id, _ in patches}) > 1],
    }


def prepare_plan_input(row, source, destination):
    data = source.read_bytes()
    if len(data) != row["file"]["size"] or sha256(data) != row["file"]["sha256"]:
        raise ValueError(f"Unchecked native bundle for planning: {row['id']}")
    report = read_archive(row, source)
    pins = NATIVE_API.fullmatch(row["api"])
    with zipfile.ZipFile(source) as archive:
        for bundle in report["mixins"]:
            prefix = bundle["manifest"].removesuffix("horizon-mixin.toml")
            target = destination / bundle["mod"]["id"]
            if not ID.fullmatch(bundle["mod"]["id"]) or target.exists():
                raise ValueError("Invalid or duplicate mixin identity")
            target.mkdir()
            for entry in archive.infolist():
                if entry.is_dir() or not entry.filename.startswith(prefix):
                    continue
                filename = entry.filename.removeprefix(prefix)
                output = target / filename
                output.parent.mkdir(parents=True, exist_ok=True)
                payload = archive.read(entry)
                if entry.filename == bundle["manifest"]:
                    # Match Horizon launcher's on-base mode, changing only the
                    # exact source pin in this disposable copy for the probe.
                    text = payload.decode("utf-8")
                    needle = r'(?m)^(\s*horizon_revision\s*=\s*")' + pins[1] + r'(")'
                    text, count = re.subn(needle, lambda match: match[1] + HORIZON_REVISION + match[2], text)
                    if count != 1:
                        raise ValueError(f"Cannot establish repin for {row['id']}")
                    payload = text.encode("utf-8")
                output.write_bytes(payload)


def probe_case(work, workspace, mixins, rows, env):
    with tempfile.TemporaryDirectory(dir=work) as directory:
        case = pathlib.Path(directory)
        inputs = case / "mods"
        inputs.mkdir()
        for row in rows:
            prepare_plan_input(row, work / "archives" / (row["file"]["sha256"] + ".hnmod"), inputs)
        run = subprocess.run([str(mixins), "plan", "--workspace", str(workspace), "--mods", str(inputs),
                              "--out", str(case / "plan.json")], capture_output=True, encoding="utf-8",
                             env=env, timeout=180, check=False)
        result = json.loads(run.stdout if run.returncode == 0 else run.stderr)
        result.pop("plan", None)
        if run.returncode == 0 and len(rows) > 1:
            generated = json.loads((case / "plan.json").read_text(encoding="utf-8"))
            sites = collections.defaultdict(list)
            for injection in generated["injections"]:
                sites[(injection["file"], tuple(injection["node"]))].append(injection)
            result["sharedSites"] = [
                {"target": group[0]["target"],
                 "injectionOrder": [{key: patch[key] for key in ("mod_id", "injection_id", "kind", "priority")}
                                    for patch in group]}
                for group in sites.values() if len({patch["mod_id"] for patch in group}) > 1
            ]
        # Diagnostics may refer to temporary files. Keep the semantic evidence
        # and avoid embedding an operator's absolute workspace path.
        for key, value in result.items():
            if isinstance(value, str):
                result[key] = value.replace(str(case), "<case>").replace(str(workspace), "<workspace>")
    return {"mods": [row["id"] for row in rows], "exitCode": run.returncode, "result": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    snapshot = subcommands.add_parser("lock")
    snapshot.add_argument("--registry-file", type=pathlib.Path, required=True,
                          help="Snapshot of the published registry, written if it is absent")
    snapshot.add_argument("--write", action="store_true", help="Replace the tracked input lock")
    check = subcommands.add_parser("inspect")
    check.add_argument("--work", type=pathlib.Path, required=True)
    check.add_argument("--openssl", required=True)
    check.add_argument("--include-broken", action="store_true")
    check.add_argument("--selection", type=pathlib.Path, help="Inspect only the selected pack inputs")
    plan = subcommands.add_parser("plan")
    plan.add_argument("--work", type=pathlib.Path, required=True)
    plan.add_argument("--workspace", type=pathlib.Path, required=True)
    plan.add_argument("--mixins", type=pathlib.Path, required=True)
    plan.add_argument("--revision-git-dir", type=pathlib.Path,
                      help="Bare Git metadata for an attested source tree with no revision marker")
    plan.add_argument("--case", action="append", default=[], help="Comma-separated native mod IDs; repeat for more groups")
    plan.add_argument("--candidate-singles", action="store_true")
    plan.add_argument("--jobs", type=int, choices=(1, 2), default=1)
    plan.add_argument("--result", type=pathlib.Path, required=True)
    args = parser.parse_args()

    if args.command == "lock":
        if not args.registry_file.exists():
            args.registry_file.parent.mkdir(parents=True, exist_ok=True)
            args.registry_file.write_bytes(read_https(REGISTRY, 8 * 1024 * 1024))
        lock = lock_registry(args.registry_file.read_bytes())
        if args.write:
            write_json(LOCK, lock)
        elif lock != json.loads(LOCK.read_text(encoding="utf-8")):
            raise ValueError("Registry snapshot does not reproduce the lock")
        print(dict(collections.Counter(row["eligibility"] for row in lock["projects"])))
        return

    input_bytes = LOCK.read_bytes()
    lock = json.loads(input_bytes)
    if lock["horizonRevision"] != HORIZON_REVISION or lock["schemaVersion"] != 1:
        raise ValueError("Wrong native game revision or lock schema")
    if args.command == "plan":
        workspace = args.workspace.resolve(strict=True)
        marker = workspace / ".horizon-revision"
        env = os.environ.copy()
        if marker.is_file():
            revision = marker.read_text(encoding="utf-8").strip()
        else:
            if args.revision_git_dir:
                env["GIT_DIR"] = str(args.revision_git_dir.resolve(strict=True))
            revision = subprocess.run(["git", "-C", str(workspace), "rev-parse", "HEAD"], env=env,
                                      capture_output=True, text=True, check=True).stdout.strip()
        if revision != HORIZON_REVISION:
            raise ValueError("Wrong prepared Horizon revision")
        rows = {row["id"]: row for row in lock["projects"] if row["kind"] == "native-mod"}
        cases = [case.split(",") for case in args.case]
        if args.candidate_singles:
            cases += [[mod_id] for mod_id, row in rows.items()
                      if row["eligibility"] in {"candidate", "compile-probe"}]
        def run_case(case):
            try:
                return probe_case(args.work.resolve(strict=True), workspace, args.mixins.resolve(strict=True),
                                  [rows[mod_id] for mod_id in case], env)
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                return {"mods": case, "harnessError": str(error)}
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as workers:
            for result in workers.map(run_case, cases):
                results.append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
        write_json(args.result, {"schemaVersion": 1, "horizonRevision": HORIZON_REVISION, "cases": results})
        return any("harnessError" in case for case in results)
    args.work.mkdir(parents=True, exist_ok=True)
    cache = args.work / "archives"
    cache.mkdir(exist_ok=True)
    statuses = {"candidate", "compile-probe", "license-review"}
    if args.include_broken:
        statuses.update(("broken-upstream", "wasm-removed"))
    rows = [row for row in lock["projects"] if row["eligibility"] in statuses]
    if args.selection:
        ids = selected_ids(args.selection, input_bytes)
        rows = [row for row in rows if row["id"] in ids]
        if {row["id"] for row in rows} != ids:
            raise ValueError("Selection contains an unknown or ineligible archive")
    records, failures = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as workers:
        jobs = {workers.submit(inspect, row, cache, args.openssl): row for row in rows}
        for future in concurrent.futures.as_completed(jobs):
            row = jobs[future]
            try:
                records.append(future.result())
            except Exception as error:
                failures.append({"id": row["id"], "version": row["version"], "error": str(error)})
    records.sort(key=lambda record: record["id"])
    failures.sort(key=lambda record: record["id"])
    write_json(args.work / "archive-inspection.json", {
        "schemaVersion": 1,
        "horizonRevision": HORIZON_REVISION,
        "verified": records,
        "failed": failures,
        "overlaps": overlaps(records),
    })
    print(f"Inspected {len(records)} exact archives, {len(failures)} failed")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
