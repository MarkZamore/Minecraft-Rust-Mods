"""Pin and stage authoritative notices for the reviewed Horizon mod archives."""

import argparse
import concurrent.futures
import json
import pathlib
import re
import subprocess
import urllib.error
import urllib.parse
import zipfile

from inspect_mods import LOCK, MOD_KINDS, ROOT, read_archive, read_https, selected_ids, sha256, write_json


LICENSE_LOCK = ROOT / "tools/mod-license-lock.json"
LIMIT = 1024 * 1024
GIT_SHA = re.compile(r"[a-f0-9]{40}")
NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "NOTICE", "NOTICE.md",
         "NOTICE.txt", "THIRD_PARTY_NOTICES.md")


def source_coordinates(row):
    source = urllib.parse.urlsplit(row["source"])
    if source.netloc != "github.com":
        raise ValueError("Source is outside the pinned GitHub notice resolver")
    owner, repo, *rest = source.path.strip("/").split("/")
    if rest:
        kind, ref, *path = rest
        if kind not in {"tree", "commit"}:
            raise ValueError("Source link does not identify a project tree")
    else:
        ref, path = None, []
    return owner, repo, ref, path


def source_commits(rows):
    """Resolve the publisher's exact source ref or release tag, then default HEAD."""
    expressions = []
    for index, row in enumerate(rows):
        owner, repo, ref, _ = source_coordinates(row)
        if ref and GIT_SHA.fullmatch(ref):
            continue
        refs = [f"refs/tags/v{row['version']}", f"refs/tags/{row['version']}"]
        if ref:
            refs = [f"refs/heads/{ref}"]
        fields = " ".join(f"r{number}:ref(qualifiedName:{json.dumps(name)})"
                          "{target{oid ... on Tag{target{oid}}}}"
                          for number, name in enumerate(refs))
        expressions.append(f"p{index}:repository(owner:{json.dumps(owner)},name:{json.dumps(repo)})"
                           "{defaultBranchRef{target{oid}}" + fields + "}")
    if not expressions:
        return {}
    query = "query {" + "\n".join(expressions) + "}"
    result = subprocess.run(["gh", "api", "graphql", "--input", "-"], check=False, text=True,
                            input=json.dumps({"query": query}), capture_output=True)
    response = json.loads(result.stdout).get("data")
    if response is None:
        raise ValueError("Could not read official source revisions through GitHub")
    return response


def source_revision(row, result):
    owner, repo, ref, path = source_coordinates(row)
    if ref and GIT_SHA.fullmatch(ref):
        return owner, repo, ref, path, "source-commit"
    if not result:
        raise ValueError("Official GitHub source is unavailable")
    for name in ("r0", "r1", "defaultBranchRef"):
        if item := result.get(name):
            target = item["target"]
            revision = target.get("target", target)["oid"]
            if GIT_SHA.fullmatch(revision):
                basis = "source-ref" if ref else "release-tag" if name != "defaultBranchRef" else "source-head"
                return owner, repo, revision, path, basis
    raise ValueError("Could not pin the source commit")


def is_license(data, declared):
    text = data.decode("utf-8-sig").lower()
    if declared == "MIT":
        return "permission is hereby granted" in text and "copyright" in text
    if declared == "Apache-2.0":
        return "apache license" in text and "2.0" in text
    if declared == "CC0-1.0":
        return "cc0" in text and ("public domain" in text or "creative commons" in text)
    return False


def archive_path(row, cache):
    return cache / (row["file"]["sha256"] + MOD_KINDS[row["kind"]])


def checked_archive(row, cache):
    path = archive_path(row, cache)
    data = path.read_bytes()
    if path.is_symlink() or len(data) != row["file"]["size"] or sha256(data) != row["file"]["sha256"]:
        raise ValueError("Mod archive differs from its input lock")
    return path


def notice_candidates(row, cache):
    path = checked_archive(row, cache)
    names = read_archive(row, path)["licenses"]
    entries = []
    with zipfile.ZipFile(path) as archive:
        for path in names:
            data = archive.read(path)
            if len(data) > LIMIT:
                raise ValueError(f"Oversized packaged notice: {path}")
            name = pathlib.PurePosixPath(path).name
            entries.append((name, data, {"kind": "mod-archive", "path": path}))
    return entries


def fetch_notice(url):
    try:
        return read_https(url, LIMIT)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
    return None


def repository_notices(row, result):
    owner, repo, revision, source_path, basis = source_revision(row, result)
    root = f"https://raw.githubusercontent.com/{owner}/{repo}/{revision}/"
    paths = ["/".join(source_path[:length]) for length in range(len(source_path), -1, -1)]
    for directory in paths:
        notices = []
        found_license = False
        for name in NAMES:
            path = f"{directory}/{name}" if directory else name
            url = root + urllib.parse.quote(path, safe="/")
            if data := fetch_notice(url):
                notices.append((name, data, {"kind": "official-source", "url": url, "revision": revision, "basis": basis}))
                found_license |= is_license(data, row["license"])
        if found_license:
            return notices
    raise ValueError("No matching license text in the pinned official source")


def build_lock(input_bytes, rows, cache, destination):
    refs = source_commits(rows)
    files, sources, failures = {}, {}, {}

    def collect(entry):
        index, row = entry
        mod_id = row["id"]
        try:
            notices = notice_candidates(row, cache)
            if not any(is_license(data, row["license"]) for _, data, _ in notices):
                notices += repository_notices(row, refs.get(f"p{index}"))
            digest_by_name, source_by_name = {}, {}
            for name, data, provenance in notices:
                digest = sha256(data)
                if name in digest_by_name:
                    if digest_by_name[name] != digest:
                        raise ValueError(f"Conflicting author notices named {name}")
                    continue
                digest_by_name[name] = digest
                source_by_name[name] = provenance
            target = destination / mod_id
            target.mkdir(parents=True, exist_ok=True)
            for name, data, _ in notices:
                (target / name).write_bytes(data)
            return mod_id, digest_by_name, source_by_name, None
        except (ValueError, OSError) as error:
            return mod_id, None, None, str(error)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as workers:
        for mod_id, names, provenance, failure in workers.map(collect, enumerate(rows)):
            if failure:
                failures[mod_id] = failure
            else:
                files[mod_id] = names
                sources[mod_id] = provenance
    return {"schemaVersion": 1, "inputLockSha256": sha256(input_bytes),
            "projects": files, "sources": sources, "unresolved": failures}


def restore_cache(lock, rows, cache, destination):
    def restore(project):
        mod_id, names = project
        row = rows[mod_id]
        target = destination / mod_id
        target.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.is_junction():
            raise ValueError(f"Linked notice cache for {mod_id}")
        for name, digest in names.items():
            if pathlib.PureWindowsPath(name).name != name or name in (".", ".."):
                raise ValueError(f"Unsafe notice path for {mod_id}")
            path = target / name
            if path.is_symlink() or path.is_junction():
                raise ValueError(f"Linked notice in cache for {mod_id}/{name}")
            if path.is_file() and sha256(path.read_bytes()) == digest:
                continue
            provenance = lock["sources"][mod_id][name]
            if provenance["kind"] == "mod-archive":
                with zipfile.ZipFile(checked_archive(row, cache)) as archive:
                    data = archive.read(provenance["path"])
            elif provenance["kind"] == "official-source":
                data = read_https(provenance["url"], LIMIT)
            else:
                raise ValueError(f"Unknown notice source for {mod_id}")
            if sha256(data) != digest:
                raise ValueError(f"Pinned notice bytes changed for {mod_id}/{name}")
            path.write_bytes(data)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as workers:
        for _ in workers.map(restore, lock["projects"].items()):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archives", type=pathlib.Path, required=True)
    parser.add_argument("--destination", type=pathlib.Path, required=True)
    parser.add_argument("--write-lock", action="store_true", help="Record the reviewed current notice inputs")
    parser.add_argument("--selection", type=pathlib.Path, help="Restore notices only for the selected pack inputs")
    args = parser.parse_args()
    if args.selection and args.write_lock:
        parser.error("A partial restore must not rewrite the complete notice lock")
    inputs = LOCK.read_bytes()
    rows = [row for row in json.loads(inputs)["projects"] if row["eligibility"] in {"candidate", "compile-probe"}]
    if args.write_lock:
        lock = build_lock(inputs, rows, args.archives, args.destination)
        write_json(LICENSE_LOCK, lock)
    else:
        lock = json.loads(LICENSE_LOCK.read_text(encoding="utf-8"))
        if lock["schemaVersion"] != 1 or lock["inputLockSha256"] != sha256(inputs):
            raise ValueError("License lock differs from the mod input lock")
        if args.selection:
            ids = selected_ids(args.selection, inputs)
            if not ids <= lock["projects"].keys() or not ids <= {row["id"] for row in rows}:
                raise ValueError("A selected project has no verified notice")
            lock["projects"] = {mod_id: files for mod_id, files in lock["projects"].items() if mod_id in ids}
        restore_cache(lock, {row["id"]: row for row in rows}, args.archives, args.destination)
    print(f"Verified notices for {len(lock['projects'])} mods; {len(lock['unresolved'])} unresolved")
    for mod_id, reason in lock["unresolved"].items():
        print(f"{mod_id}: {reason}")


if __name__ == "__main__":
    main()
