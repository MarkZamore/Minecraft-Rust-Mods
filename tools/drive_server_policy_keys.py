"""Send one Escape per owned Rust Mods server-policy QA launch."""

import argparse
import os
from pathlib import Path
import subprocess
import time

from native_build import ROOT, load, module_at


def fit_owned_window(driver, desktop, request, qa_pid, qa, executable):
    pid, title = request["processId"], request["title"]
    with desktop.process(pid) as process:
        if (desktop.parent_pid(pid) != qa_pid or desktop.executable(process) != executable
                or not desktop.started(qa) <= desktop.started(process) <= request["createdUnixMs"]):
            raise RuntimeError("The requested window is not a live child of this Mods QA process")
        hwnd = desktop.window(pid, title)
        c, w, user = driver.c, driver.w, desktop.user
        client, outer, origin = w.RECT(), w.RECT(), w.POINT()
        if not (user.GetClientRect(hwnd, c.byref(client)) and user.GetWindowRect(hwnd, c.byref(outer))
                and user.ClientToScreen(hwnd, c.byref(origin))):
            raise c.WinError(c.get_last_error())
        class MonitorInfo(c.Structure):
            _fields_ = [("size", w.DWORD), ("monitor", w.RECT), ("work", w.RECT), ("flags", w.DWORD)]

        monitor_for = user.MonitorFromWindow
        monitor_for.argtypes, monitor_for.restype = (w.HWND, w.DWORD), w.HANDLE
        info_of = user.GetMonitorInfoW
        info_of.argtypes, info_of.restype = (w.HANDLE, c.POINTER(MonitorInfo)), w.BOOL
        info = MonitorInfo(size=c.sizeof(MonitorInfo))
        if not info_of(monitor_for(hwnd, 2), c.byref(info)):
            raise c.WinError(c.get_last_error())
        working = info.work
        left, top = working.left, working.top
        width, height = working.right - left, working.bottom - top
        if not (0 < client.right <= width and 0 < client.bottom <= height):
            raise RuntimeError("The owned Mods client cannot fit the available desktop")
        horizontal = min(8, (width - client.right) // 2)
        vertical = min(8, (height - client.bottom) // 2)
        dx = min(max(origin.x, left + horizontal), left + width - client.right - horizontal) - origin.x
        dy = min(max(origin.y, top + vertical), top + height - client.bottom - vertical) - origin.y
        if dx or dy:
            move = user.SetWindowPos
            move.argtypes = (w.HWND, w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, w.UINT)
            move.restype = w.BOOL
            desktop.require_running(qa)
            desktop.require_running(process)
            if desktop.window(pid, title) != hwnd or not move(hwnd, None, outer.left + dx, outer.top + dy, 0, 0, 0x15):
                raise RuntimeError("Could not fit the verified Mods QA window on the desktop")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-repo", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--requests", required=True, type=Path)
    parser.add_argument("--qa-pid", required=True, type=int)
    args = parser.parse_args()
    if os.name != "nt" or args.qa_pid <= 0:
        raise ValueError("Use the owned Windows server-policy QA process")

    base = args.base_repo.resolve(strict=True)
    helper = "tools/server_list_desktop_driver.py"
    pin = load(ROOT / "tools/base-lock.json")
    git = ["git", "-C", str(base)]
    tracked = subprocess.check_output([*git, "show", f"{pin['revision']}:{helper}"])
    helper_path = base / helper
    if helper_path.is_symlink() or helper_path.is_junction() or helper_path.read_bytes() != tracked:
        raise ValueError("The Windows desktop helper differs from the pinned Edition revision")

    candidate = args.candidate.resolve(strict=True)
    executable = candidate / "native/horizon-client.exe"
    requests = args.requests.resolve()
    stand = next((path for path in candidate.parents if path.parent.name == "agent-runs"
                  and path.parent.parent.name == "artifacts" and (path / "RUN.json").is_file()), None)
    title = "Minecraft Rust Mods"
    if (stand is None or not requests.is_relative_to(stand) or requests.is_relative_to(candidate)
            or requests.name != "desktop-keys" or executable.resolve(strict=True) != executable
            or load(candidate / "native/presentation.json")["name"] != title):
        raise ValueError("Use the Mods candidate and desktop-keys directory in one registered QA stand")

    driver = module_at(helper_path)
    driver.KEY_STEPS = ("Escape",)
    driver.KEYS = {"Escape": (0x1B,)}
    desktop, deadline, remaining = driver.Desktop(), time.monotonic() + 900, driver.CAPTURE_BUDGET
    with desktop.process(args.qa_pid) as qa:
        if not desktop.executable(qa).name.lower().startswith("python"):
            raise ValueError("The QA PID must be the owned Python runner")
        for run in range(1, 7):
            path = requests / f"run-{run}-key-1.request.json"
            driver.await_request(path, desktop, qa, deadline)
            request = driver.read_request(path, run, 1, executable, title)
            fit_owned_window(driver, desktop, request, args.qa_pid, qa, executable)
            remaining = driver.service_request(desktop, request, args.qa_pid, qa, executable, requests, remaining)


if __name__ == "__main__":
    main()
