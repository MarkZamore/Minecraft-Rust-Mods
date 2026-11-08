"""Run two rendered Windows clients over local TCP; real Steam is a separate check.

Uses the exact staged client and the engine's control socket. Guest commands go
through keyboard/chat, never the owner's console command endpoint. All mutable
files belong to a fresh directory inside a registered LANMinecraft agent run.
"""

import argparse
from contextlib import ExitStack
import json
from math import floor
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from native_build import load, sha
from native_qa_environment import environment_policy, prepare_client_environment
import smoke_orbital
import smoke_startup


WORLD = "RustModsMultiplayerQA"
GUEST = "RustGuestQA"
HOST = "RustHostQA"


def until(predicate, label, seconds=30):
    deadline = time.monotonic() + seconds
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise TimeoutError(label)
        time.sleep(0.1)


class Client:
    def __init__(self, candidate, controller, resources, root, name, common_only=False):
        self.root = root
        self.game = root / "game"
        self.game.mkdir(parents=True)
        self.endpoint = root / "control.json"
        self.controller = controller
        self.log = root / "client.log"
        self.process = None
        self.output = None
        self.common_only = common_only
        # Fresh test settings only: do not read or change a player's instance.
        seed = (candidate / "configureddefaults/options.txt").read_text(encoding="utf-8")
        preset = (candidate / "launcher/controls-preset.txt").read_text(encoding="utf-8")
        (self.game / "options.txt").write_text(seed + "lang:en_us\n" + preset,
                                               encoding="utf-8", newline="\n")
        env = dict(os.environ, HORIZON_NO_DISCORD="1", HORIZON_TEST_LOOPBACK="1")
        for key in tuple(env):
            if key.upper().startswith("LANMINECRAFT_"):
                del env[key]
        self.args = [str(candidate / "native/horizon-client.exe"), "--resources", str(resources),
                     "--home", str(self.game), "--name", name, "--game-dir", str(self.game),
                     "--control-socket", str(self.endpoint)]
        self.env, self.environment_evidence = prepare_client_environment(candidate, env)

    def __enter__(self):
        self.output = self.log.open("wb")
        self.process = subprocess.Popen(self.args, cwd=self.game, env=self.env,
                                        stdout=self.output, stderr=self.output)
        try:
            until(self.ready, "Client did not expose control socket", 120)
            self("wait-for", "screen", "title", "--timeout", "90")
            ui = self("ui")
            if any(row["id"] == "continue" for row in ui["widgets"]):
                if self.common_only:
                    # Edition has no Physics first-run screen. Its full
                    # accessibility acceptance belongs to the Edition suite.
                    time.sleep(2.1)
                    smoke_startup.click(self, smoke_startup.widget(self, "continue"))
                    until(lambda: any(w["id"] == "singleplayer" for w in self("ui")["widgets"]),
                          "Edition did not leave onboarding")
                else:
                    smoke_startup.exercise(self, self.root)
            else:
                # Existing UI test covers first-run onboarding. This path is
                # for engines that skip it after reading the version template.
                until(lambda: any(w["id"] == "singleplayer" for w in self("ui")["widgets"]),
                      "Client did not reach title menu")
            return self
        except BaseException as error:
            self.__exit__(type(error), error, error.__traceback__)
            raise

    def ready(self):
        if self.process.poll() is not None:
            raise RuntimeError(f"Client exited before ready ({self.process.returncode}); see {self.log}")
        return self.endpoint.is_file()

    def __call__(self, *args, timeout=120):
        reply = subprocess.run([str(self.controller), "--socket", str(self.endpoint), *map(str, args)],
                               cwd=self.game, capture_output=True, encoding="utf-8",
                               check=True, timeout=timeout)
        value = json.loads(reply.stdout)
        if isinstance(value, dict) and "error" in value:
            raise RuntimeError(value["error"])
        return value

    def __exit__(self, exc_type, *_):
        exit_error = None
        try:
            if self.process is not None and self.process.poll() is None:
                try:
                    self("release", timeout=10)
                    self("quit", timeout=15)
                    self.process.wait(timeout=30)
                except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                    # Only the process created by this object is stopped.
                    self.process.kill()
                    self.process.wait(timeout=15)
                    exit_error = error
            if exc_type is None and self.process is not None:
                if exit_error is not None:
                    raise RuntimeError(f"Client failed graceful shutdown: {exit_error}")
                if self.process.returncode != 0:
                    raise RuntimeError(f"Client exited with {self.process.returncode}; see {self.log}")
        finally:
            if self.output is not None:
                self.output.close()

    def typed_command(self, command):
        self("key", "t", "tap")
        self("wait-for", "screen", "chat", "--timeout", "5")
        self("type", command)
        self("key", "enter", "tap")
        self("wait-for", "screen", "playing", "--timeout", "5")


def frame(client, name):
    return smoke_orbital.photograph(client, client.root, name)


def open_from_menu(host):
    until(lambda: not (server := host("status").get("server")) or server.get("loaded"),
          "Host world did not load", 90)
    host("key", "esc", "tap")
    host("wait-for", "screen", "paused", "--timeout", "5")
    button = until(lambda: next((w for w in host("ui")["widgets"]
                                 if w.get("label") == "Open to LAN"), None),
                   "Pause menu has no Open to LAN")
    host("click", button["id"])
    if host("ui")["screen"] != "playing":
        raise AssertionError("Opening the world did not close the pause menu")
    status = until(lambda: (s if (s := host("lan", "status")).get("open") else None),
                   "Host did not open listener")
    port = status["port"]
    if type(port) is not int or not 1 <= port <= 65535:
        raise AssertionError(f"LAN status returned an invalid listener port: {port!r}")

    def numeric_notice():
        messages = host("messages", "30")

        def published_component(value):
            if isinstance(value, dict):
                if value.get("translate") == "menu.multiplayerOptions.publish.started.lan":
                    # The actual system chat component's translation argument,
                    # rather than a port guessed from a log or launcher data.
                    args = value.get("with", [])
                    if any((isinstance(arg, dict) and arg.get("text") == str(port))
                           or (isinstance(arg, str) and arg == str(port))
                           or (type(arg) is int and arg == port) for arg in args):
                        return value
                for child in value.values():
                    if found := published_component(child):
                        return found
            elif isinstance(value, list):
                for child in value:
                    if found := published_component(child):
                        return found
            return None

        component = published_component(messages.get("styled", []))
        plain = next((line for line in messages.get("chat", []) if isinstance(line, str)
                      and re.search(rf"(?<!\d){port}(?!\d)", line)), None)
        if component is None:
            return None
        return {"chatPort": port, "statusPort": port, "matchesStatus": True,
                "translationKey": "menu.multiplayerOptions.publish.started.lan",
                "component": component, "renderedLine": plain}

    try:
        status["chatPortNotice"] = until(numeric_notice,
                                          "System chat did not announce the actual numeric listener port", 10)
    except BaseException:
        details = {"status": status, "messages": host("messages", "30"), "ui": host("ui"),
                   "frame": frame(host, "publish-port-failed")}
        (host.root / "publish-notice-failure.json").write_text(
            json.dumps(details, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        raise
    return status


def join(guest, port):
    guest("connect", f"127.0.0.1:{port}", GUEST)
    guest("wait-for", "screen", "playing", "--timeout", "90")
    guest("wait-for", "loaded", "--timeout", "90")


def guest_modes(guest):
    evidence = []
    for mode in ("survival", "creative"):
        guest.typed_command(f"/gamemode {mode}")
        until(lambda: guest("state").get("gamemode") == mode,
              f"Typed guest /gamemode {mode} did not execute")
        evidence.append({"typed": f"/gamemode {mode}", "observed": mode})
    return evidence


def visible_messages(client, count=10):
    # Native client mods write the rendered rich-text history; those messages
    # need not appear in the server/session chat list or chat event stream.
    reply = client("messages", str(count))
    plain = [line for line in reply.get("chat", []) if isinstance(line, str)]
    styled = [line["text"] for line in reply.get("styled", [])
              if isinstance(line, dict) and isinstance(line.get("text"), str)]
    return list(dict.fromkeys([*plain, *styled]))


def has_translation(value, key):
    if isinstance(value, dict):
        return value.get("translate") == key or any(has_translation(child, key) for child in value.values())
    if isinstance(value, list):
        return any(has_translation(child, key) for child in value)
    return False


def jei(guest):
    guest.typed_command("/jei")
    counts = until(lambda: next((s for s in visible_messages(guest)
                                 if re.search(r"\b[1-9]\d* items, [1-9]\d* recipes", s)), None),
                   "JEI did not report a nonempty catalog")
    # The original ordinary container path remains supported alongside the
    # pack's Creative extension. Exercise it through the real inventory key.
    guest.typed_command("/gamemode survival")
    until(lambda: guest("state")["gamemode"] == "survival", "JEI Survival check did not enter Survival")
    guest("key", "e", "tap")
    guest("wait-for", "screen", "inventory", "--timeout", "5")
    guest("wait", "3")
    def survival_panel():
        screen = guest("ui")
        rows = [w for w in screen["widgets"] if w.get("kind") == "button"
                and w["bounds"][0] > screen["gui_size"][0] / 2 + 88
                and w["bounds"][2:4] == [16.0, 16.0]]
        return (screen, rows) if screen["screen"] == "inventory" and rows else None

    # A logical screen can be ready while the native pack-loading overlay is
    # still drawing. Wait for the actual item widgets, rather than a few ticks.
    try:
        survival_ui, survival_items = until(survival_panel, "Survival JEI panel did not render", 60)
    except BaseException:
        frame(guest, "jei-survival-inventory-failed")
        raise
    survival = {"gamemode": guest("state")["gamemode"], "inventoryScreen": survival_ui["screen"],
                "itemButtons": len(survival_items), "frame": frame(guest, "jei-survival-inventory")}
    if not survival_items or survival["gamemode"] != "survival":
        raise AssertionError("The ordinary Survival inventory did not show the JEI item panel")
    guest("key", "e", "tap")
    guest("wait-for", "screen", "playing", "--timeout", "5")
    guest.typed_command("/gamemode creative")
    until(lambda: guest("state")["gamemode"] == "creative", "JEI Creative check did not restore Creative")

    def give_toggle():
        guest.typed_command("/jei give")
        return until(lambda: next((line for line in visible_messages(guest, 5)
                                   if "Click gives items: " in line), None),
                     "JEI did not report its click-give setting").endswith("ON")

    # A real click provides CursorMoved but also activates the item. Turn on
    # JEI's own creative click-give mode in this disposable profile so that
    # the click positions the pointer without opening the recipe itself.
    # R and U must then be the actions which open the two pages.
    toggled_on = give_toggle()
    initial_give = not toggled_on
    if not toggled_on and not give_toggle():
        raise AssertionError("Could not enable JEI's creative click-give mode")
    try:
        guest("open-inventory")
        guest("wait", "3")
        ui = guest("ui")
        gamemode = guest("state")["gamemode"]
        if gamemode != "creative" or ui["screen"] != "creative":
            raise AssertionError(f"JEI acceptance requires the real Creative inventory, got {gamemode}/{ui['screen']}")
        width, height = ui["gui_size"]

        def item_buttons(screen):
            # Native recipe-browser items are recorded as 16x16 buttons;
            # creative inventory slots use a different widget kind.
            return [w for w in screen["widgets"] if w.get("kind") == "button"
                    and w["bounds"][0] > width / 2 + 88
                    and w["bounds"][2:4] == [16.0, 16.0]]

        def creative_panel():
            screen = guest("ui")
            rows = item_buttons(screen)
            return (screen, rows) if screen["screen"] == "creative" and rows else None

        try:
            ui, buttons = until(creative_panel, "Creative JEI panel did not render", 60)
        except BaseException:
            frame(guest, "jei-creative-inventory-failed")
            raise
        screenshot = frame(guest, "jei-inventory")
        (guest.root / "jei-diagnostic.json").write_text(
            json.dumps({"about": counts, "ui": ui, "frame": screenshot}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8", newline="\n")
        if not buttons:
            raise AssertionError(f"JEI has a catalog but no rendered item panel; GUI {width}x{height}")
        search = next((w for w in ui["widgets"] if w.get("kind") == "field"
                       and w["bounds"][0] > width / 2 + 88
                       and w["bounds"][1] > height / 2), None)
        if search is None:
            raise AssertionError("JEI item panel has no rendered search field")
        smoke_startup.click_bounds(guest, search["bounds"])
        guest("type", "minecraft:stone")

        def stone():
            return next((w for w in item_buttons(guest("ui")) if w.get("label") == "Stone"), None)

        until(stone, "JEI search did not display vanilla Stone")
        # Close with a real Escape while the panel field owns input. The
        # existing filter remains, but reopening must not leave it focused.
        guest("key", "esc", "tap")
        guest("wait-for", "screen", "playing", "--timeout", "5")
        guest("open-inventory")
        guest("wait-for", "screen", "creative", "--timeout", "5")
        try:
            guest("type", "x")
        except (RuntimeError, subprocess.CalledProcessError) as error:
            # The socket deliberately rejects text when Creative has no open
            # field. That is the expected result after closing JEI's field.
            details = str(error) + str(getattr(error, "stdout", "")) + str(getattr(error, "stderr", ""))
            if "type needs" not in details:
                raise
        until(stone, "Reopening Creative retained JEI input focus and changed its search")
        reopen_focus = {"closedToPlaying": True, "reopenedCreative": True,
                        "typedAfterReopen": "x", "searchUnchanged": True}

        # The pinned controller's key endpoint does not implement EditBox
        # Ctrl+A. Use its documented backspace sentinel through the same text
        # routing as ordinary type input, never write the mod's search state.
        def set_panel_search(old, new):
            field = next(w for w in guest("ui")["widgets"] if w.get("kind") == "field"
                         and w["bounds"][0] > width / 2 + 88 and w["bounds"][1] > height / 2)
            # EditBox.click positions the caret at the clicked character.
            # Click its right edge, not the middle of the old search text,
            # before deleting the complete known ASCII query.
            fx, fy, fw, fh = field["bounds"]
            guest("click-at", fx + fw - 2, fy + fh / 2)
            for _ in old:
                guest("type", r"\b")
            if new:
                guest("type", new)

        # Creative's chat key opens its Search Items tab. A harmless click
        # inside its header first lets go of the JEI field without selecting,
        # giving, dropping or moving an inventory stack.
        guest("click-at", width / 2, (height - 136) // 2 + 7)
        guest("key", "t", "tap")

        def creative_search_field():
            return next((w for w in guest("ui")["widgets"] if w.get("kind") == "field"
                         and w["bounds"][0] < width / 2 + 88
                         and w["bounds"][1] < height / 2), None)

        native_search = until(creative_search_field, "Creative Search Items tab did not render its own field")
        smoke_startup.click_bounds(guest, native_search["bounds"])
        # Match the key's following character event: selectSearchTab ignores
        # this T, not the later query. The key endpoint itself emits no text.
        guest("type", "t")
        guest("type", "diamond")
        until(stone, "Creative's own search input was sent to JEI")
        set_panel_search("minecraft:stone", "minecraft:dirt")
        until(lambda: any(w.get("label") == "Dirt" for w in item_buttons(guest("ui"))),
              "JEI search lost input to Creative's Search Items field")
        creative_search = {"outsideText": "diamond", "outsideLeavesJeiSearch": True,
                           "jeiTextOnSearchTab": "minecraft:dirt", "jeiSearchOnSearchTab": True,
                           "restoredSearch": "minecraft:stone", "frame": frame(guest, "jei-creative-search")}
        set_panel_search("minecraft:dirt", "minecraft:stone")
        until(stone, "JEI filter was not restored on the Creative Search Items tab")
        checks = {}
        given = []
        for key, label in (("r", "recipes"), ("u", "uses")):
            row = until(stone, "JEI Back did not restore the searched item list")
            before = guest("inventory")["counts"].get("minecraft:stone", 0)
            smoke_startup.click_bounds(guest, row["bounds"])
            after = until(lambda: (n if (n := guest("inventory")["counts"].get("minecraft:stone", 0)) > before else None),
                          "Clicking JEI Stone did not give a creative item")
            if after != before + 1:
                raise AssertionError(f"JEI creative give changed Stone count {before} -> {after}, expected +1")
            given.append({"before": before, "after": after, "delta": after - before})
            # Creative give leaves the list visible and releases search focus.
            if any(w.get("label") == "Back" for w in guest("ui")["widgets"]):
                raise AssertionError("JEI item click opened recipes before the shortcut was pressed")
            guest("key", key, "tap")

            def recipe_page():
                screen = guest("ui")
                back = next((w for w in screen["widgets"] if w.get("label") == "Back"), None)
                slots = [w for w in item_buttons(screen)
                         if re.fullmatch(r".+ \d+:\d+", w.get("label", ""))]
                return (screen, back, slots) if back is not None and len(slots) >= 2 else None

            _, back, slots = until(recipe_page, f"JEI {key.upper()} did not open populated {label}")
            names = [re.sub(r" \d+:\d+$", "", w["label"]) for w in slots]
            if "Stone" not in names or not any(name != "Stone" for name in names):
                raise AssertionError(f"JEI {key.upper()} page has no Stone recipe inputs/results: {names!r}")
            if key == "r" and "Cobblestone" not in names:
                raise AssertionError(f"JEI R did not show Stone's vanilla smelting input: {names!r}")
            checks[key.upper()] = {"action": label, "recipeSlots": names,
                                   "frame": frame(guest, f"jei-{label}")}
            guest("click", back["id"])
            until(stone, f"JEI Back from {label} did not restore item search")
        # With no filter there are multiple JEI pages. The wheel over those
        # pages must not be handled by the creative list behind them; moving
        # the wheel over creative's own grid must leave the JEI page alone.
        set_panel_search("minecraft:stone", "")
        all_items = until(lambda: (rows if len(rows := item_buttons(guest("ui"))) > 2 else None),
                          "JEI clearing its search did not restore the item catalog")
        first_page = [w.get("label") for w in all_items]
        x, y, w, h = all_items[0]["bounds"]
        guest("scroll-at", x + w / 2, y + h / 2, "-1")
        second_page = until(lambda: (names if (names := [w.get("label") for w in item_buttons(guest("ui"))])
                                    and names != first_page else None), "Wheel over JEI did not change its page")
        guest("scroll-at", x + w / 2, y + h / 2, "1")
        until(lambda: [w.get("label") for w in item_buttons(guest("ui"))] == first_page,
              "Reversing the JEI wheel did not restore its page")
        guest("scroll-at", width / 2, height / 2, "-1")
        guest("wait", "2")
        if [w.get("label") for w in item_buttons(guest("ui"))] != first_page:
            raise AssertionError("Scrolling outside JEI changed its page")
        wheel = {"insideChangesPanelPage": True, "insideReverseRestoresPanelPage": True,
                 "outsideKeepsPanelPage": True, "firstPageFirstItem": first_page[0],
                 "secondPageFirstItem": second_page[0]}
        set_panel_search("", "minecraft:stone")
        until(stone, "JEI search was not restored after wheel acceptance")
        return {"about": counts, "itemButtons": len(buttons), "frame": screenshot,
                "gamemode": gamemode, "inventoryScreen": ui["screen"], "creativeInventory": True,
                "guiSize": [width, height], "search": "minecraft:stone", "shortcuts": checks,
                "survival": survival,
                "give": {"item": "minecraft:stone", "clicks": given}, "reopenFocus": reopen_focus,
                "creativeSearch": creative_search, "wheelIsolation": wheel}
    except BaseException as error:
        try:
            details = {"error": f"{type(error).__name__}: {error}", "ui": guest("ui"),
                       "state": guest("state"), "messages": guest("messages", "10"),
                       "frame": frame(guest, "jei-failed")}
            (guest.root / "jei-failure.json").write_text(
                json.dumps(details, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        except Exception:
            pass  # Preserve the original failure if its client already exited.
        raise
    finally:
        guest("close")
        if not initial_give:
            if give_toggle():
                raise AssertionError("JEI click-give test setting was not restored")


def orbital(host, guest, concurrent_effects=False):
    guest.typed_command("/orbital give")
    until(lambda: guest("inventory")["counts"].get(smoke_orbital.DESIGNATOR) == 1,
          "Guest did not receive Designator")
    guest("select", smoke_orbital.DESIGNATOR)
    for cmd in ("/orbital seed 7", "/orbital knockback off", "/orbital plasma", "/orbital terrain on"):
        guest.typed_command(cmd)
    x, y, z = map(floor, host("state")["player"]["position"])
    y = min(y + 5, 252)
    target = (x, y, z)
    points = [target, (x + 1, y - 3, z)]
    expected = ["minecraft:gold_block", "minecraft:stone"]
    for point, block in zip(points, expected, strict=True):
        host("command", f"setblock {' '.join(map(str, point))} {block}")
    cy, cz = y + 38, z + 72
    # Its block is suspended above surrounding hills so the second long ray
    # cannot hit intervening natural terrain. No line-of-sight corridor is cut.
    second_target = (x + 64, cy + 16, z)
    if concurrent_effects:
        # Outside Plasma's authored crater/scorch footprint (max radius 46),
        # within its authored Designator range 200. The first fixture stays
        # unchanged; only its guest strike is allowed to mutate that terrain.
        host("command", f"setblock {' '.join(map(str, second_target))} minecraft:gold_block")
        for cmd in ("/orbital give", "/orbital seed 11", "/orbital knockback off",
                    "/orbital plasma", "/orbital terrain off"):
            host.typed_command(cmd)
        until(lambda: host("inventory")["counts"].get(smoke_orbital.DESIGNATOR) == 1,
              "Host did not receive its concurrent-strike Designator")
        host("select", smoke_orbital.DESIGNATOR)
    # Match the proven single-player fixture: a wider platform intercepts
    # the descending Designator ray before it leaves the platform's edge.
    host("command", f"fill {x - 2} {cy - 1} {cz - 2} {x + 2} {cy - 1} {cz + 2} smooth_stone")
    host("tp", x + 1.5, cy, cz + 0.5)
    # Teleport issued by the guest follows its real server command path.
    guest.typed_command(f"/tp {x + 0.5} {cy} {cz + 0.5}")
    for client in (host, guest):
        client("wait-for", "loaded", "--timeout", "90")
        smoke_orbital.camera(client, target)
    until(lambda: smoke_orbital.sample(guest, points) == expected,
          "Fixture did not reach guest", 30)
    before = {"host": frame(host, "before"), "guest": frame(guest, "before")}
    log_offset = host.log.stat().st_size
    guest_offset = guest.log.stat().st_size

    def own_strikes(client, offset):
        text = client.log.read_bytes()[offset:].decode("utf-8", errors="replace")
        return [(laser, tuple(map(int, (a, b, c))))
                for laser, a, b, c in smoke_orbital.STRIKE.findall(text)]

    def exact_target(client, offset, expected_target):
        strikes = until(lambda: own_strikes(client, offset), "Designator did not fire", 5)
        if strikes != [("plasma", expected_target)]:
            raise AssertionError(f"QA line of sight hit {strikes!r}, expected Plasma target {expected_target!r}")

    smoke_orbital.hold_designator(guest)
    exact_target(guest, guest_offset, target)
    concurrent = None
    if concurrent_effects:
        # Plasma lasts 26 s and its beam starts at 4 s (author lasers.rs /
        # strike.rs). Start the host about one second behind the guest; capture
        # about five seconds after the host starts, while both beams burn.
        guest("wait", "20")
        smoke_orbital.camera(host, second_target)
        smoke_orbital.hold_designator(host)
        exact_target(host, log_offset, second_target)

        def applied(client, offset):
            text = client.log.read_bytes()[offset:].decode("utf-8", errors="replace")
            return [line for line in text.splitlines() if "[mod orbital] remote visual applied" in line]

        until(lambda: applied(host, log_offset) and applied(guest, guest_offset),
              "Both actors did not receive the other's complete concurrent effect", 5)
        midpoint = (x + 32, (y + second_target[1]) / 2, z)
        for client in (host, guest):
            smoke_orbital.camera(client, midpoint)
        host("wait", "100")
        concurrent = {"hostTarget": list(second_target), "guestTarget": list(target),
                      "hostTerrain": "off", "guestTerrain": "on",
                      "authorPlasmaSeconds": 26, "authorBeamStartsAtSeconds": 4,
                      "cameraTargetDuringCapture": list(midpoint),
                      "hostReceivedGuest": applied(host, log_offset)[:8],
                      "guestReceivedHost": applied(guest, guest_offset)[:8]}
    else:
        guest("wait", "90")
    during = {"host": frame(host, "strike"), "guest": frame(guest, "strike")}
    guest("wait", "460", timeout=150)
    changed = smoke_orbital.sample(host, points)
    if any(a == b for a, b in zip(expected, changed, strict=True)):
        raise AssertionError(f"Guest Orbital did not change server terrain: {changed}")
    until(lambda: smoke_orbital.sample(guest, points) == changed,
          "Server terrain edits did not reach guest", 20)
    tail = host.log.read_bytes()[log_offset:].decode("utf-8", errors="replace")
    applied = [line for line in tail.splitlines() if "[mod orbital] remote visual applied" in line]
    if not applied:
        raise AssertionError("Host did not log applying the guest's complete visual snapshot")
    if concurrent is not None:
        host_strikes = [(laser, tuple(map(int, (a, b, c))))
                        for laser, a, b, c in smoke_orbital.STRIKE.findall(tail)]
        guest_tail = guest.log.read_bytes()[guest_offset:].decode("utf-8", errors="replace")
        guest_strikes = [(laser, tuple(map(int, (a, b, c))))
                         for laser, a, b, c in smoke_orbital.STRIKE.findall(guest_tail)]
        if host_strikes != [("plasma", second_target)] or guest_strikes != [("plasma", target)]:
            raise AssertionError(f"Concurrent own-engine strikes differ: {host_strikes!r}, {guest_strikes!r}")
        if smoke_orbital.sample(host, [second_target]) != ["minecraft:gold_block"]:
            raise AssertionError("The host's terrain-off concurrent strike changed its separate fixture")
        until(lambda: smoke_orbital.sample(guest, [second_target]) == ["minecraft:gold_block"],
              "Concurrent terrain-off fixture differs between clients")
        concurrent["ownEngineStrikes"] = {"host": "plasma", "guest": "plasma"}
        concurrent["hostTerrainOffFixtureUnchanged"] = True
        concurrent["framesOwnAndRemote"] = during
    return {"fixture": [list(p) for p in points], "blocksBefore": expected, "blocksAfter": changed,
            "guestObservedBlocksAfter": changed, "framesBefore": before, "framesDuring": during,
            "hostRemoteVisual": applied[:8], "concurrentEffects": concurrent}


def exact_client(args):
    candidate = args.candidate
    client = candidate / "native/horizon-client.exe"
    if args.common_only:
        if args.edition_receipt is None:
            raise ValueError("Common-only mode requires --edition-receipt from the exact Edition build")
        receipt = load(args.edition_receipt)
        revision = receipt["upstreamCommit"]
        library = load(candidate / "library/content.json")
        dll = candidate / "native/steam_api64.dll"
        if (receipt.get("schemaVersion") != 1 or not re.fullmatch(r"[0-9a-f]{40}", revision)
                or library["nativeProject"]["revision"] != revision
                or client.stat().st_size != receipt["clientSizeBytes"]
                or sha(dll) != receipt["steamDllSha256"]
                or dll.stat().st_size != receipt["steamDllSizeBytes"]):
            raise ValueError("Edition candidate differs from the exact overlay receipt")
    else:
        if args.edition_receipt is not None:
            raise ValueError("--edition-receipt only applies to --common-only")
        receipt = load(candidate / "build-receipt.json")
        revision = receipt["horizonRevision"]
    actual = sha(client)
    if actual != receipt["clientSha256"]:
        raise ValueError("Exact candidate client differs from build receipt")
    return actual, revision


def run(args):
    candidate, qa = args.candidate, args.qa
    if args.common_only and args.concurrent_effects:
        raise ValueError("--concurrent-effects requires the full Mods mode")
    if args.common_only and args.skip_jei:
        raise ValueError("--skip-jei is a Mods diagnostic flag; Edition has no JEI")
    client_sha, revision = exact_client(args)
    project = Path(__file__).resolve().parents[3]
    allowed = (project / "artifacts/agent-runs").resolve()
    if not qa.is_relative_to(allowed) or qa == allowed or qa.exists():
        raise ValueError("Use a fresh QA directory below a registered artifacts/agent-runs task")
    task = allowed / qa.relative_to(allowed).parts[0]
    if not (task / "RUN.json").is_file():
        raise ValueError("The QA task has no RUN.json ownership and expiry")
    qa.mkdir(parents=True)
    report = {"schemaVersion": 1, "clientSha256": client_sha, "horizonRevision": revision,
              **environment_policy(candidate),
              "mode": "common-only" if args.common_only else "mods",
              "diagnosticOnly": args.skip_jei or args.diagnostic,
              "transport": "local TCP, two rendered clients, Steam bridge inactive",
              "realSteamTwoAccounts": "NOT RUN", "result": "FAIL"}
    report["executionContext"] = {
        "entryPoint": "direct native/horizon-client.exe",
        "bootstrapAcceptance": "NOT RUN by this two-client helper",
        "processEnvironment": {
            key: os.environ[key] for key in ("DISABLE_VULKAN_OW_OVERLAY_LAYER",) if key in os.environ
        },
        "environmentScope": "Inherited only by this QA process and its own child clients",
    }
    try:
        with ExitStack() as stack:
            host = stack.enter_context(Client(candidate, args.controller, args.resources, qa / "host", HOST,
                                               common_only=args.common_only))
            guest = stack.enter_context(Client(candidate, args.controller, args.resources, qa / "guest", GUEST,
                                                common_only=args.common_only))
            report["executionContext"]["clients"] = {
                "host": host.environment_evidence, "guest": guest.environment_evidence,
            }
            report["environmentApplications"] = [
                {"client": "host", **host.environment_evidence},
                {"client": "guest", **guest.environment_evidence},
            ]
            report["executionContext"]["processEnvironment"] = host.environment_evidence[
                "effectiveEnvironmentDefaults"]
            report["executionContext"]["environmentScope"] = (
                "Pack presentation defaults applied to own child clients; inherited overrides preserved; parent unchanged")
            host("open", WORLD, "137248", "creative")
            host("wait-for", "loaded", "--timeout", "90")
            status = open_from_menu(host)
            report["publishMenu"] = {"returnedToGame": True, "port": status["port"],
                                     "numericPortNotice": status["chatPortNotice"]}
            join(guest, status["port"])
            report["guestCommands"] = guest_modes(guest)
            if args.common_only:
                report["jei"] = {"status": "NOT RUN", "reason": "Edition common-only mode has no mods"}
                report["orbital"] = {"status": "NOT RUN", "reason": "Edition common-only mode has no mods"}
            else:
                if args.skip_jei:
                    report["jei"] = {"status": "NOT RUN",
                                     "reason": "diagnostic skip: known Creative panel bug"}
                else:
                    report["jei"] = jei(guest)
                report["orbital"] = orbital(host, guest, concurrent_effects=args.concurrent_effects)
            guest("disconnect")
            host("lan", "close")
            host("lan", "open", "0")  # Explicitly omit guest cheats.
            closed_cheats = host("lan", "status")
            join(guest, closed_cheats["port"])
            prior = guest("state")["gamemode"]
            denial_since = guest("events")["last"]
            guest.typed_command("/gamemode spectator")
            guest("wait", "5")
            after = guest("state")["gamemode"]
            if after != prior:
                raise AssertionError("Guest command bypassed the server's disabled cheats")

            def denied_message():
                messages = guest("messages", "5")
                events = [event for event in guest("events", "--since", str(denial_since))["events"]
                          if event.get("event") == "chat"]
                return (messages, events) if events and has_translation(messages, "command.unknown.command") else None

            denial, denial_events = until(denied_message,
                                          "No new structured UnknownCommand denial after the guest command", 5)
            report["guestCheatsDisabled"] = {"typed": "/gamemode spectator", "before": prior, "after": after,
                                             "denialObserved": True,
                                             "denialEventSeq": [event["seq"] for event in denial_events],
                                             "messages": denial}
            guest("disconnect")
            host("lan", "close")
        # Both ExitStack contexts must finish with a clean game exit before
        # recording PASS; a process crash is a failed acceptance run.
        report["processExitCodes"] = {"host": host.process.returncode, "guest": guest.process.returncode}
        report["shaderErrors"] = {
            name: [line for line in client.log.read_text(encoding="utf-8", errors="replace").splitlines()
                   if smoke_orbital.SHADER_FAILURE.search(line)]
            for name, client in (("host", host), ("guest", guest))
        }
        if any(report["shaderErrors"].values()):
            raise AssertionError("The exact two-client render run logged a shader/device failure")
        report["result"] = "PASS"
    except BaseException as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        # Keep compact evidence and frames, remove only this test's disposable
        # game directories after processes have exited. No links are followed.
        try:
            for name in ("host", "guest"):
                game = qa / name / "game"
                if game.is_dir():
                    if game.is_symlink() or game.is_junction() or not game.resolve().is_relative_to(qa):
                        raise RuntimeError("Refusing cleanup outside this QA directory")
                    for directory, dirs, files in os.walk(game):
                        if ".git" in dirs or any((Path(directory) / n).is_symlink()
                                                  or (Path(directory) / n).is_junction() for n in dirs + files):
                            raise RuntimeError("Refusing cleanup of linked/source data")
                    shutil.rmtree("\\\\?\\" + str(game))
            report["disposableWorldsRemoved"] = True
        except BaseException as error:
            report["result"] = "FAIL"
            report["cleanupError"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            (qa / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                             encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("candidate", "controller", "resources", "qa"):
        parser.add_argument("--" + field, required=True, type=lambda v: Path(v).resolve())
    parser.add_argument("--common-only", action="store_true",
                        help="run shared menu/guest-command checks against Edition without mod checks")
    parser.add_argument("--edition-receipt", type=lambda v: Path(v).resolve(),
                        help="exact Edition overlay receipt; required with --common-only")
    parser.add_argument("--concurrent-effects", action="store_true",
                        help="also render simultaneous host and guest Plasma effects on both clients")
    parser.add_argument("--skip-jei", action="store_true",
                        help="DIAGNOSTIC ONLY: skip known JEI failure; does not complete release acceptance")
    parser.add_argument("--diagnostic", action="store_true",
                        help="run all selected checks, but mark evidence diagnostic-only for a candidate with other blockers")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
