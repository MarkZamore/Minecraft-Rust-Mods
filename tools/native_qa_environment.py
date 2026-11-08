"""Apply generic staged presentation defaults to direct native QA processes.

The production bootstrap applies these defaults itself. Direct-client probes
must use the same data without replacing any inherited value, including an
empty value. This module neither selects a graphics backend nor edits the OS
environment; its optional context affects only this Python process's children.
"""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata


POLICY = "pack-presentation-defaults-v1"
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")


def unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("The staged presentation repeats a JSON property")
        result[name] = value
    return result


def validate_environment_defaults(value):
    if not isinstance(value, dict) or len(value) > 16:
        raise ValueError("Presentation environmentDefaults must be an object with at most 16 entries")
    seen = set()
    size = 0
    for name, setting in value.items():
        if not isinstance(name, str) or NAME.fullmatch(name) is None:
            raise ValueError("Presentation environmentDefaults contains an invalid environment name")
        if name.casefold() in seen:
            raise ValueError("Presentation environmentDefaults repeats a case-insensitive name")
        seen.add(name.casefold())
        if (not isinstance(setting, str) or len(setting.encode("utf-8")) > 1024
                or any(unicodedata.category(character) == "Cc" for character in setting)):
            raise ValueError("Presentation environmentDefaults contains an invalid string value")
        size += len(name.encode("utf-8")) + len(setting.encode("utf-8"))
    if size > 8192:
        raise ValueError("Presentation environmentDefaults exceeds its total UTF-8 budget")
    return dict(value)


def environment_policy(candidate):
    path = Path(candidate) / "native/presentation.json"
    raw = path.read_bytes()
    presentation = json.loads(raw, object_pairs_hook=unique_object)
    if not isinstance(presentation, dict):
        raise ValueError("The staged presentation is not an object")
    defaults = validate_environment_defaults(presentation.get("environmentDefaults", {}))
    return {"environmentPolicy": POLICY, "environmentDefaults": defaults,
            "presentationSha256": hashlib.sha256(raw).hexdigest()}


def prepare_client_environment(candidate, inherited=None):
    """Return a new environment and source/effective-policy evidence only.

    Compare names as Windows does, preserving inherited key spelling and every
    inherited value. Fixed QA flags can be prepared by the caller before this
    call. A caller with explicit command overlays/removals applies those last;
    the production bootstrap owns that separate precedence step.
    """
    evidence = environment_policy(candidate)
    environment = dict(os.environ if inherited is None else inherited)
    existing = {name.casefold(): name for name in environment}
    effective, origins = {}, {}
    for name, setting in evidence["environmentDefaults"].items():
        inherited_name = existing.get(name.casefold())
        if inherited_name is None:
            environment[name] = setting
            effective[name], origins[name] = setting, "default"
        else:
            effective[name], origins[name] = environment[inherited_name], "inherited"
    return environment, {**evidence, "environmentApplication": "direct-client",
                         "effectiveEnvironmentDefaults": effective, "environmentDefaultOrigins": origins}


@contextmanager
def direct_client_environment(candidate):
    """Scope defaults around an existing base helper that builds its own env.

    Only absent declared keys are temporarily introduced into this process.
    Inherited keys are not rewritten or removed, and the additions are removed
    when the child has exited, including on a failed QA operation.
    """
    environment, evidence = prepare_client_environment(candidate)
    added = [name for name, origin in evidence["environmentDefaultOrigins"].items() if origin == "default"]
    try:
        for name in added:
            os.environ[name] = environment[name]
        yield evidence
    finally:
        for name in added:
            os.environ.pop(name, None)
