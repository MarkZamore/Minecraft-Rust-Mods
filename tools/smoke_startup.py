"""Verify coordinate mouse input on the modded client's Windows startup screens."""

import time

from native_build import sha


PHYSICS_SETTINGS = "config/horizon_physics.txt"


def find(control, predicate, label, timeout=8):
    deadline = time.monotonic() + timeout
    while True:
        screen = control("ui")
        widget = next((row for row in screen["widgets"] if predicate(row)), None)
        if widget is not None:
            return widget
        if time.monotonic() >= deadline:
            labels = [row.get("label") for row in screen["widgets"]]
            raise ValueError(f"Could not find {label} on the rendered startup screen: {labels!r}")
        time.sleep(0.08)


def widget(control, identity):
    return find(control, lambda row: row["id"] == identity, identity)


def click(control, row):
    if not row["enabled"]:
        raise ValueError(f"Startup control is disabled: {row['label']!r}")
    click_bounds(control, row["bounds"])


def click_bounds(control, bounds):
    x, y, width, height = bounds
    # The pinned controller emits a window CursorMoved then MouseInput down/up.
    control("click-at", str(x + width / 2), str(y + height / 2))


def language(control):
    click(control, widget(control, "language"))
    current = widget(control, "english-us")
    click(control, current)
    click(control, widget(control, "done"))


def narrator_cycle(control):
    def narrator_widget():
        return find(control, lambda row: row["id"].startswith("narrator"), "Narrator setting")

    voice = control("narration")
    narrator = narrator_widget()
    if not voice["active"]:
        if narrator["enabled"]:
            raise ValueError("Narrator is enabled despite the unavailable speech backend")
        return {"available": False, "label": narrator["label"]}
    if voice["status"] != "off":
        raise ValueError("The test profile must start with Narrator off")
    observed = []
    for expected in ("all", "chat", "system", "off"):
        click(control, narrator_widget())
        value = control("narration")["status"]
        if value != expected:
            raise ValueError(f"Narrator click selected {value!r}, expected {expected!r}")
        observed.append(value)
    return {"available": True, "mouseCycle": observed}


def accessibility(control):
    click(control, widget(control, "accessibility-settings"))
    widget(control, "done")
    result = narrator_cycle(control)
    click(control, widget(control, "done"))
    return result


def title_controls(control):
    widget(control, "singleplayer")
    language(control)
    widget(control, "singleplayer")
    narrator = accessibility(control)
    widget(control, "singleplayer")
    click(control, widget(control, "options"))
    click(control, widget(control, "controls"))
    find(control, lambda row: row["id"].startswith("key-binds"), "Key Binds button")
    click(control, widget(control, "done"))
    widget(control, "controls")
    click(control, widget(control, "done"))
    widget(control, "singleplayer")
    return narrator


def exercise(control, qa):
    setup = qa / "game" / PHYSICS_SETTINGS
    if setup.exists() and "setup_done=1" in setup.read_text(encoding="utf-8").splitlines():
        raise ValueError("Use a fresh test instance for Physics Quick Setup")
    narrator = widget(control, "narrator")
    # The accessibility onboarding has an initial two-second fade.
    time.sleep(2.1)
    if narrator["enabled"] or "Not Available" not in narrator["label"]:
        raise ValueError("The first-run screen must keep Horizon's disabled Narrator button")
    before = control("narration")
    click_bounds(control, narrator["bounds"])
    after = control("narration")
    if before["status"] != "off" or after["status"] != "off":
        raise ValueError("Clicking the disabled first-run Narrator changed the narrator option")
    language(control)
    widget(control, "continue")
    onboarding_narrator = accessibility(control)
    widget(control, "continue")
    click(control, widget(control, "music-sounds"))
    find(control, lambda row: row["id"].startswith("master-volume"), "Master Volume slider")
    click(control, widget(control, "done"))
    click(control, widget(control, "continue"))

    # Horizon's Continue fades out for one second. Physics draws its own canvas
    # and exposes no standard widgets. Use the author's Skip Setup bounds.
    time.sleep(1.2)
    screen = control("ui")
    width, height = screen["gui_size"]
    frame = qa / "physics-quick-setup.png"
    control("screenshot", str(frame))
    control("click-at", str(width * 0.9015), str(height * 0.0795))
    widget(control, "singleplayer")
    if "setup_done=1" not in setup.read_text(encoding="utf-8").splitlines():
        raise ValueError("Physics Quick Setup did not record Skip Setup")

    return {"onboardingNarratorButton": {"label": narrator["label"], "enabled": narrator["enabled"],
                                         "speechBackendActive": before["active"], "statusAfterClick": after["status"]},
            "onboardingAccessibilityNarrator": onboarding_narrator,
            "onboardingMouse": ["language", "accessibility", "sound", "continue"],
            "physicsQuickSetup": {"action": "Skip Setup", "frameSha256": sha(frame)},
            "titleMouse": ["language", "accessibility", "options", "controls"],
            "titleAccessibilityNarrator": title_controls(control)}


def verify_reopened(control, game, evidence):
    if "setup_done=1" not in (game / PHYSICS_SETTINGS).read_text(encoding="utf-8").splitlines():
        raise ValueError("Physics Quick Setup was not retained across restart")
    evidence["reopenedTitleAccessibilityNarrator"] = title_controls(control)
