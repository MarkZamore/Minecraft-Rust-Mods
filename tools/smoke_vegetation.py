"""Measure rendered foliage at the same viewpoint with Physics sway on and off."""

import argparse
import json
from math import hypot, isclose, isfinite
from pathlib import Path
import re
from statistics import median
import sys
import time

from PIL import Image, ImageChops

from native_build import sha
from smoke_mod_hud import capture_root
from smoke_multiplayer import Client, cleanup_game, until
from smoke_orbital import wait_for_world_ticks
from smoke_startup import PHYSICS_SETTINGS


WORLD_SEED = "137248"
VEGETATION = {"leaves": (0.26, 0.32, 0.45, 0.61),
              "grass": (0.55, 0.41, 0.74, 0.51),
              "static": (0.49, 0.28, 0.51, 0.66)}
SAMPLES = 16
ORIGIN = (0, 180, 0)
FRAME_MS = 1000 / 60
SHADER_CLOCKS = (4, 60, 300, 600)


def command(client, action, *args):
    return client("command", " ".join(map(str, (action, *args))))


def fixture(client, origin, scripted):
    client("open", "VegetationQA", WORLD_SEED, "creative")
    client("wait-for", "loaded", "--timeout", "90")
    x, y, z = origin
    client("tp", str(x + 0.5), str(y + 6), str(z + 0.5))
    until(lambda: client("block", x, y, z)["loaded"], "Vegetation scene did not load", 90)
    command(client, "gamerule", "minecraft:send_command_feedback", "false")
    command(client, "weather", "clear", 999999)
    command(client, "time", "set", "noon")
    command(client, "gamerule", "minecraft:advance_time", "false")
    command(client, "fill", x - 10, y - 1, z - 2, x + 10, y - 1, z + 14, "minecraft:quartz_block")
    command(client, "fill", x - 10, y, z - 2, x + 10, y + 4, z + 13, "minecraft:air")
    command(client, "fill", x - 20, y - 5, z + 14, x + 20, y + 12, z + 14, "minecraft:white_concrete")
    # Facing south, positive world X is on the left of the screen.
    command(client, "fill", x + 2, y + 1, z + 9, x + 4, y + 3, z + 9,
            "minecraft:jungle_leaves[persistent=true]")
    command(client, "fill", x - 4, y, z + 9, x - 2, y + 1, z + 9, "minecraft:dirt")
    command(client, "fill", x - 4, y + 2, z + 9, x - 2, y + 2, z + 9, "minecraft:short_grass")
    command(client, "setblock", x, y + 2, z + 9, "minecraft:lime_wool")
    client("tp", str(x + 0.5), str(y), str(z + 0.5))
    client("look-at", str(x + 0.5), str(y + 2.1), str(z + 9.5))
    client("key", "key.keyboard.f1", "tap")
    if not scripted:
        wait_for_world_ticks(client, 25)
    for offset, block in (((2, 2, 9), "minecraft:jungle_leaves"),
                          ((-2, 2, 9), "minecraft:short_grass"),
                          ((0, 2, 9), "minecraft:lime_wool")):
        point = [a + b for a, b in zip((x, y, z), offset, strict=True)]
        observed = client("block", *point)
        if not observed["loaded"] or observed["block"] != block:
            raise ValueError(f"The vegetation sample at {point} should be {block}: {observed}")
    return client("state")["player"]


def mask(image, bounds):
    coordinates = tuple(round(edge * size) for edge, size in zip(bounds, image.size * 2, strict=True))
    crop = image.crop(coordinates)
    pixels = bytes(255 if g - max(r, b) >= 18 else 0 for r, g, b in crop.get_flattened_data())
    return Image.frombytes("L", crop.size, pixels)


def transition(previous, current):
    previous_area = previous.histogram()[255]
    area = current.histogram()[255]
    changed = ImageChops.difference(previous, current).histogram()[255]
    centers = []
    for image, count in ((previous, previous_area), (current, area)):
        if not count:
            raise ValueError("Fixture was obscured or outside the vegetation crop")
        points = [divmod(i, image.width) for i, value in enumerate(image.tobytes()) if value]
        centers.append(tuple(sum(axis) / count for axis in zip(*points, strict=True)))
    return {"area": area, "changedFraction": round(2 * changed / (previous_area + area + changed), 5),
            "centroidStepPixels": round(hypot(*(a - b for a, b in zip(*centers, strict=True))), 4)}


def capture(client, qa, count, clocks):
    screenshot = qa / "frame.png"
    previous = None
    samples = []
    saved_frames = []
    start = time.monotonic()
    clock_ms = [seconds * 1000 + FRAME_MS * step for seconds in clocks for step in range(count)] if clocks else [None] * count
    for index, milliseconds in enumerate(clock_ms):
        try:
            if milliseconds is not None:
                def sequence():
                    try:
                        return client("screenshot-sequence", 2, milliseconds, str(screenshot))
                    except RuntimeError as error:
                        if "with nothing left to load" in str(error):
                            return None
                        raise

                reply = until(sequence, "World did not settle for the scripted shader clock", 90)
                seconds = milliseconds / 1000
                if reply["pack"]["kind"] != "native" or not isclose(reply["frame"]["elapsed"], seconds, abs_tol=0.001):
                    raise ValueError(f"Expected the native material at shader time {seconds}: {reply}")
            else:
                client("screenshot", str(screenshot))
                seconds = time.monotonic() - start
            with Image.open(screenshot) as frame:
                image = frame.convert("RGB")
            current = {name: mask(image, bounds) for name, bounds in VEGETATION.items()}
            if previous is not None and index % count != 0:
                elapsed = seconds - samples[-1]["seconds"]
                samples.append({"seconds": seconds, "stepSeconds": elapsed,
                                **{name: transition(previous[name], new) for name, new in current.items()}})
            else:
                samples.append({"seconds": seconds})
            if milliseconds is not None:
                samples[-1]["shaderClock"] = reply["frame"]
            previous = current
            if index % count in (0, count - 1):
                width, height = image.size
                path = qa / f"vegetation-{index:02}.png"
                image.crop((round(width * 0.17), round(height * 0.24),
                            round(width * 0.83), round(height * 0.69))).save(path)
                saved_frames.append({"file": path.name, "sha256": sha(path)})
        finally:
            screenshot.unlink(missing_ok=True)
    windows = []
    for index in range(0, len(samples), count):
        steps = samples[index + 1:index + count]
        summary = {
            name: {"medianChangedFraction": median(step[name]["changedFraction"] for step in steps),
                   "peakCentroidStepPixels": max(step[name]["centroidStepPixels"] for step in steps)}
            for name in VEGETATION
        }
        windows.append({"startSeconds": samples[index]["seconds"], **summary})
    return {"size": list(image.size), "frames": saved_frames, "windows": windows, "samples": samples}


def run(args):
    candidate, controller, resources = (getattr(args, name).resolve(strict=True)
                                        for name in ("candidate", "controller", "resources"))
    qa = capture_root(args.qa)
    client_sha = sha(candidate / "native/horizon-client.exe")
    if client_sha != args.client_sha256.lower():
        raise ValueError("The tested client differs from the expected SHA-256")
    result = {"schemaVersion": 1, "clientSha256": client_sha, "origin": list(args.origin),
              "clock": "scripted" if args.scripted else "wall",
              "controllerSha256": sha(controller), "cases": {}}
    qa.mkdir(parents=True)
    try:
        for label in args.cases:
            root = qa / label.removeprefix("sway-")
            client = Client(candidate, controller, resources, root, "VegetationQA")
            enabled = int(label == "sway-on")
            config = {"setup_done": 1, "waves": 1, "plants": enabled, "sway_leaves": enabled,
                      "sway_vines": enabled, "sway_water": 1, "sway_strength": 1}
            try:
                with (client.game / "options.txt").open("a", encoding="utf-8") as options:
                    options.write("onboardAccessibility:false\nchunkSectionFadeInTime:0.0\n")
                if args.scripted:
                    client.env["HORIZON_STILL"] = "1"
                    (client.game / "horizon-graphics.txt").write_text(
                        "preset:custom\nlodDistance:0\nshadowCache:false\ntaa:false\nframeGeneration:off\nhdr:false\n",
                        encoding="utf-8")
                settings = client.game / PHYSICS_SETTINGS
                settings.parent.mkdir()
                settings.write_text("".join(f"{key}={value}\n" for key, value in config.items()), encoding="utf-8")
                with client:
                    player = fixture(client, args.origin, args.scripted)
                    renderer = client("status")["renderer"]
                    if not renderer or not renderer["backend"]:
                        raise ValueError("Expected the actual rendered Horizon client")
                    frames = capture(client, root, args.samples, args.clocks if args.scripted else ())
                    ending = client("state")["player"]
                    if any(player[key] != ending[key] for key in ("eye", "yaw", "pitch")):
                        raise ValueError("The camera moved during vegetation capture")
                    result["cases"][label] = {"settings": config, "renderer": renderer, "viewpoint": player, **frames}
            finally:
                cleanup_game(client.game, root, resources)
        result["capture"] = "COMPLETE"
    except BaseException as error:
        result["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        path = qa / "report.json"
        path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return path


def inspect_case(case):
    windows, samples = case["windows"], case["samples"]
    count, remainder = divmod(len(samples), len(SHADER_CLOCKS))
    if remainder or count < 3 or [window["startSeconds"] for window in windows] != list(SHADER_CLOCKS):
        raise ValueError("Expected all four shader-clock windows with equal frame counts")
    observed = {name: [] for name in VEGETATION}
    for i, window in enumerate(windows):
        steps = samples[i * count:(i + 1) * count]
        for step, sample in enumerate(steps):
            clock = sample["shaderClock"]
            expected = SHADER_CLOCKS[i] + FRAME_MS * step / 1000
            if clock["counter"] != 2 or not isclose(clock["elapsed"], expected, abs_tol=0.001):
                raise ValueError(f"Invalid shader-clock sample at {expected} seconds")
        for name in VEGETATION:
            changed = [sample[name]["changedFraction"] for sample in steps[1:]]
            if (any(not 0 <= fraction <= 1 for fraction in changed) or
                    median(changed) != window[name]["medianChangedFraction"]):
                raise ValueError(f"Invalid summary of changes for {name}")
            observed[name].extend(changed)
    return count, observed


def compare_reports(before, after, before_sha256, after_sha256):
    for report, digest in ((before, before_sha256), (after, after_sha256)):
        if (re.fullmatch(r"[0-9a-f]{64}", digest) is None or report["clientSha256"] != digest or
                report["schemaVersion"] != 1 or report["capture"] != "COMPLETE" or report["clock"] != "scripted"):
            raise ValueError("Expected completed scripted evidence for the supplied exact client SHA-256")
    if before_sha256 == after_sha256 or before["origin"] != after["origin"]:
        raise ValueError("Compare different clients using the same block fixture")
    cases = {"before": before["cases"]["sway-on"], "after": after["cases"]["sway-on"],
             "swayDisabled": after["cases"]["sway-off"]}
    changes = {}
    counts = set()
    first = cases["before"]
    renderer = {key: first["renderer"][key] for key in ("backend", "gpu", "driver", "resolution_scale")}
    camera = {key: first["viewpoint"][key] for key in ("eye", "yaw", "pitch")}
    for label, case in cases.items():
        enabled = int(label != "swayDisabled")
        expected = dict(first["settings"], plants=enabled, sway_leaves=enabled, sway_vines=enabled)
        if (case["size"] != first["size"] or case["settings"] != expected or case["settings"]["waves"] != 1 or
                any(case["renderer"][key] != value for key, value in renderer.items()) or
                any(case["viewpoint"][key] != value for key, value in camera.items())):
            raise ValueError(f"Render settings and camera must match the baseline for {label}")
        count, changes[label] = inspect_case(case)
        counts.add(count)
    if len(counts) != 1:
        raise ValueError("The comparisons require equal numbers of samples")
    checks = {
        "stationaryBlock": not any(value for case in changes.values() for value in case["static"]),
        "swayDisabled": not any(value for name in ("leaves", "grass") for value in changes["swayDisabled"][name]),
    }
    plants = {}
    for name in ("leaves", "grass"):
        before_peak, after_peak = (max(window[name]["medianChangedFraction"] for window in case["windows"][-2:])
                                  for case in (first, cases["after"]))
        retains_motion = any(changes["after"][name])
        checks[name] = bool(retains_motion and before_peak > 0 and after_peak <= 0.5 * before_peak)
        plants[name] = {"retainsMotion": retains_motion, "beforeLateMedian": before_peak, "afterLateMedian": after_peak}
    return {"schemaVersion": 1, "result": "PASS" if all(checks.values()) else "FAIL",
            "baselineClientSha256": before_sha256, "clientSha256": after_sha256,
            "origin": before["origin"], "camera": camera, "resolution": first["size"], "renderer": renderer,
            "shaderClockHz": 60, "windowStartSeconds": list(SHADER_CLOCKS), "samplesPerWindow": count,
            "checks": checks, "plants": plants, "windows": {label: case["windows"] for label, case in cases.items()}}


def compare(argv):
    parser = argparse.ArgumentParser(description="Check paired scripted vegetation reports without starting Horizon.")
    for name in ("before", "after", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--before-sha256", required=True)
    parser.add_argument("--after-sha256", required=True)
    args = parser.parse_args(argv)
    before, after = (json.loads(path.read_text(encoding="utf-8")) for path in (args.before, args.after))
    report = compare_reports(before, after, args.before_sha256.lower(), args.after_sha256.lower())
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0 if report["result"] == "PASS" else 1


def main():
    if sys.argv[1:2] == ["compare"]:
        return compare(sys.argv[2:])
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("candidate", "controller", "resources", "qa"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--client-sha256", required=True)
    parser.add_argument("--samples", type=int, default=SAMPLES, choices=range(3, 65), metavar="3-64")
    parser.add_argument("--cases", nargs="+", choices=("sway-on", "sway-off"), default=("sway-on", "sway-off"))
    parser.add_argument("--origin", nargs=3, metavar=("X", "Y", "Z"), type=int, default=ORIGIN)
    parser.add_argument("--scripted", action="store_true", help="sample at exact 60 Hz shader-clock intervals")
    parser.add_argument("--clocks", nargs="+", type=float, default=SHADER_CLOCKS, metavar="SECONDS")
    args = parser.parse_args()
    if args.scripted and any(not isfinite(seconds) or seconds <= 0 for seconds in args.clocks):
        parser.error("--clocks needs positive finite seconds")
    print(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
