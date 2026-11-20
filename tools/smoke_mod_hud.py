"""Check rendered mod HUD, Orbital wheel and JEI in an isolated Horizon client.

Requires Pillow 12 or newer for PNG inspection. The inspect mode accepts saved captures
and does not start the client. Capture mode uses the native control socket and
keeps only screenshots and a compact report under a registered agent run.
"""

import argparse
import json
from pathlib import Path

from native_build import sha
from shared_qa_identity import shared_identity
import smoke_orbital


# Page-one rims and center text from Orbital Laser's lasers.rs and wheel.rs.
WHEEL_COLORS = {
    "plasma": (85, 216, 255),
    "godly": (255, 213, 90),
    "blackhole": (176, 112, 255),
    "meteor": (255, 122, 58),
    "mothership": (63, 224, 90),
}
TERRAIN_LABEL_COLOR = (184, 190, 200)
WHEEL_REGION = (0.27, 0.14, 0.73, 0.86)
TERRAIN_LABEL_REGION = (0.42, 0.46, 0.58, 0.51)
HUD_REGION = (0.003, 0.003, 0.165, 0.115)
JEI_REGION = (0.71, 0.20, 0.99, 0.89)


def region(image, bounds):
    width, height = image.size
    return image.crop(tuple(int(edge * size) for edge, size in zip(bounds, (width, height) * 2)))


def colors(image, bounds):
    crop = region(image, bounds)
    return crop.getcolors(crop.width * crop.height)


def palette_count(histogram, target, tolerance):
    return sum(count for count, rgb in histogram
               if max(abs(value - wanted) for value, wanted in zip(rgb, target)) <= tolerance)


def read_frame(path):
    from PIL import Image

    with Image.open(path) as image:
        if image.format != "PNG" or min(image.size) < 100:
            raise ValueError(f"Expected native PNG capture: {path}")
        return image.convert("RGB")


def frame_identity(path, image):
    return {"file": path.name, "size": list(image.size), "sha256": sha(path)}


def minimum_counts(width, height):
    scale = width * height / (1920 * 1080)
    return max(40, round(100 * scale)), max(35, round(65 * scale)), max(45, round(120 * scale))


def inspect_frames(opened, closed=None, creative=None):
    images = {name: (path, read_frame(path)) for name, path in
              (("wheelOpen", opened), ("wheelClosed", closed), ("creative", creative)) if path is not None}
    wheel = images["wheelOpen"][1]
    if any(image.size != wheel.size for _, image in images.values()):
        raise ValueError("Compare frames captured at the same resolution")
    if creative is not None and closed is None:
        raise ValueError("The Creative comparison needs the closed-wheel baseline from the same viewpoint")
    baseline = images.get("wheelClosed", images["wheelOpen"])[1]
    min_accent, min_label, min_hud = minimum_counts(wheel.width, wheel.height)
    wheel_colors = colors(wheel, WHEEL_REGION)
    baseline_colors = colors(baseline, WHEEL_REGION) if closed is not None else []
    accents = {
        name: max(0, palette_count(wheel_colors, rgb, 8) - palette_count(baseline_colors, rgb, 8))
        for name, rgb in WHEEL_COLORS.items()
    }
    visible_accents = sum(count >= min_accent for count in accents.values())
    label_pixels = palette_count(colors(wheel, TERRAIN_LABEL_REGION), TERRAIN_LABEL_COLOR, 5)
    hud_pixels = sum(count for count, rgb in colors(baseline, HUD_REGION)
                     if max(rgb) - min(rgb) < 12 and min(rgb) >= 100)
    checks = {
        "orbitalWheel": {"pass": visible_accents >= 3 and label_pixels >= min_label,
                         "rimPixels": accents, "visibleRims": visible_accents, "centerLabelPixels": label_pixels},
        "topLeftHud": {"pass": hud_pixels >= min_hud,
                       "brightNeutralPixels": hud_pixels},
    }
    if creative is not None:
        before = region(baseline, JEI_REGION).get_flattened_data()
        after = region(images["creative"][1], JEI_REGION).get_flattened_data()
        panel_pixels = sum(sum(pixel) - sum(previous) >= 60 and max(pixel) - min(pixel) > 40
                           and max(pixel) > 120 for previous, pixel in zip(before, after))
        scale = wheel.width * wheel.height / (1920 * 1080)
        checks["jeiCreative"] = {"pass": panel_pixels >= max(100, round(4_000 * scale)),
                                 "newBrightColoredPixels": panel_pixels}
    return {"schemaVersion": 1, "frames": {name: frame_identity(path, image) for name, (path, image) in images.items()},
            "checks": checks, "result": "PASS" if all(row["pass"] for row in checks.values()) else "FAIL"}


def verify_gameplay_frame(report, wheel_frame):
    """Require the exact captured gameplay frame and adequate observed pixels."""
    import re

    if (report.get("schemaVersion") != 1 or report.get("result") != "PASS" or
            report.get("frames") != {"wheelOpen": wheel_frame}):
        raise ValueError("The gameplay smoke report must verify pixels in its exact Orbital wheel frame")
    size = wheel_frame.get("size", [])
    if (len(size) != 2 or any(type(axis) is not int or axis < 100 for axis in size) or
            re.fullmatch(r"[0-9a-f]{64}", wheel_frame.get("sha256", "")) is None):
        raise ValueError("Invalid Orbital wheel screenshot identity")
    checks = report.get("checks", {})
    wheel = checks.get("orbitalWheel", {})
    hud = checks.get("topLeftHud", {})
    accents = wheel.get("rimPixels", {})
    label = wheel.get("centerLabelPixels")
    text = hud.get("brightNeutralPixels")
    if (wheel.get("pass") is not True or hud.get("pass") is not True or
            set(accents) != WHEEL_COLORS.keys() or
            any(type(count) is not int or count < 0 for count in (*accents.values(), label, text))):
        raise ValueError("Release needs valid Orbital wheel and HUD pixel counts")
    min_accent, min_label, min_hud = minimum_counts(*size)
    visible_accents = sum(count >= min_accent for count in accents.values())
    if (visible_accents != wheel.get("visibleRims") or visible_accents < 3 or
            label < min_label or text < min_hud):
        raise ValueError("Release needs visible Orbital wheel rims, center label and top-left HUD text")


def capture_root(path):
    allowed = Path(__file__).resolve().parents[3] / "artifacts/agent-runs"
    allowed = allowed.resolve(strict=True)
    path = path.resolve()
    if not path.is_relative_to(allowed) or path == allowed or path.exists():
        raise ValueError("Use a fresh capture directory under artifacts/agent-runs")
    task = allowed / path.relative_to(allowed).parts[0]
    if not (task / "RUN.json").is_file():
        raise ValueError("Register the capture task in RUN.json first")
    return path


def wait_for_world_draw(control, timeout=90):
    from smoke_multiplayer import until

    first_frame = None

    def visible_terrain():
        nonlocal first_frame
        status = control("status", timeout=15)
        renderer = status.get("renderer") or {}
        if (status["screen"] != "playing" or not renderer.get("backend")
                or renderer.get("drawn_sections", 0) <= 0 or renderer.get("section_draw_calls", 0) <= 0):
            first_frame = None
            return None
        # The game can report Playing and advance world ticks while the Physics
        # resource reload still replaces its world with the Mojang progress UI.
        # Require terrain in successive observations of actual rendered frames.
        frame = status["frames"]
        if first_frame is None:
            first_frame = frame
        return renderer if frame > first_frame else None

    return until(visible_terrain, "The HUD fixture did not render the loaded terrain", timeout)


def capture(args):
    from smoke_multiplayer import Client, cleanup_game, until

    candidate, controller, resources = (getattr(args, name).resolve(strict=True)
                                        for name in ("candidate", "controller", "resources"))
    qa = capture_root(args.qa)
    client_sha = sha(candidate / "native/horizon-client.exe")
    if client_sha != args.client_sha256.lower():
        raise ValueError("The tested client differs from the expected SHA-256")
    report = {"schemaVersion": 1, "result": "FAIL", "clientSha256": client_sha,
              "controllerSha256": sha(controller), **shared_identity(candidate, client_sha)}
    qa.mkdir(parents=True)
    try:
        with Client(candidate, controller, resources, qa, "HudQA") as client:
            client("open", "HudQA", "137248", "creative")
            client("wait-for", "loaded", "--timeout", "90")
            smoke_orbital.wait_for_world_ticks(client, 8)
            smoke_orbital.command(client, "/orbital give", "Orbital Laser Designator added to your inventory")
            smoke_orbital.item_present(client)
            client("select", smoke_orbital.DESIGNATOR)
            client("command", "time set noon")
            client("command", "weather clear")
            smoke_orbital.wait_for_world_ticks(client, 12)
            x, y, z = client("state")["player"]["position"]
            # A randomly oriented spawn can face empty sky. Keep one terrain-
            # facing camera for the closed wheel, open wheel and inventory.
            client("look-at", x, y - 6.0, z + 6.0)
            report["renderer"] = wait_for_world_draw(client)
            closed = smoke_orbital.photograph(client, qa, "closed")
            client("key", f"key.keyboard.{smoke_orbital.WHEEL_KEY}", "tap")
            client("wait-for", "screen", "mod", "--timeout", "10")
            opened = smoke_orbital.photograph(client, qa, "open")
            client("key", "esc", "tap")
            client("wait-for", "screen", "playing", "--timeout", "10")
            client("key", "key.keyboard.f8", "tap")
            client("wait-for", "screen", "mod", "--timeout", "10")
            report["waypointsFrame"] = smoke_orbital.photograph(client, qa, "waypoints")
            client("key", "esc", "tap")
            client("wait-for", "screen", "playing", "--timeout", "10")
            client("open-inventory")
            client("wait-for", "screen", "creative", "--timeout", "10")

            def jei_widgets():
                ui = client("ui")
                width = ui["gui_size"][0]
                rows = [row for row in ui["widgets"] if row["kind"] == "button"
                        and row["bounds"][0] > width / 2 + 88 and row["bounds"][2:4] == [16.0, 16.0]]
                return rows if len(rows) >= 8 else None

            report["jeiWidgetCount"] = len(until(jei_widgets, "JEI Creative item panel did not load", 30))
            creative = smoke_orbital.photograph(client, qa, "creative")
        report.update(inspect_frames(qa / opened["file"], qa / closed["file"], qa / creative["file"]))
    except BaseException as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        try:
            cleanup_game(qa / "game", qa, resources)
            report["disposableWorldRemoved"] = True
        except BaseException as error:
            report["result"] = "FAIL"
            report["cleanupError"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            (qa / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    live = commands.add_parser("capture", help="capture fresh rendered frames with an isolated game profile")
    for name in ("candidate", "controller", "resources", "qa"):
        live.add_argument(f"--{name}", required=True, type=Path)
    live.add_argument("--client-sha256", required=True)
    replay = commands.add_parser("inspect", help="inspect existing frames without starting the game")
    replay.add_argument("--open", required=True, type=Path)
    replay.add_argument("--closed", type=Path)
    replay.add_argument("--creative", type=Path)
    args = parser.parse_args()
    report = capture(args) if args.mode == "capture" else inspect_frames(args.open, args.closed, args.creative)
    print(json.dumps(report, indent=2))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
