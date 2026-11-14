"""Build ``Lili.app`` — the desktop window as a Mac app (LUMI-270, after the v2.12.1 release, the owner's request).

A small bundle in ``~/Applications`` that runs a client checkout's desktop window — by default the Mac prod client,
``~/lumi/prod/app`` — so Лілі opens from Launchpad, Spotlight or the Dock, with her portrait as the icon. The bundle
holds no code of the client: it runs ``uv run --extra desktop`` in that checkout, so ``scripts/prod_update.py``
moving the checkout to a new tag updates what the app runs; the app is rebuilt only to change its icon or target.

    Lili.app/Contents/
      Info.plist              the name, the icon, LSUIElement (the launcher shows no Dock tile of its own)
      MacOS/Lili              the launcher (bash): PATH for uv, cd into the checkout, uv run … launch.py
      Resources/AppIcon.icns  her portrait in the macOS rounded square (sips/iconutil-free: Pillow + iconutil)
      Resources/icon.png      the same at 1024 px — launch.py sets it as the running window's Dock icon
      Resources/launch.py     sets the Dock icon + the name "Lili", then runs ``desktop.__main__.main``

Usage (from any tree; the target is the checkout it runs):

    uv run python scripts/make_mac_app.py [--app-dir ~/lumi/prod/app] [--portrait viewer/faces/calm.png]
                                          [--dest ~/Applications] [--name Lili]

macOS only for the build (``iconutil``); the pure parts are unit-tested anywhere.
"""

from __future__ import annotations

import argparse
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORTRAIT = ROOT / "viewer" / "faces" / "calm.png"  # the default theme's calm face
DEFAULT_APP_DIR = Path("~/lumi/prod/app")
DEFAULT_DEST = Path("~/Applications")
BUNDLE_ID = "io.github.ichmaster.lumi.lili"
ICON_PX = 1024
TILE_PX = 824  # the macOS app icon grid: an 824 px rounded square on a 1024 canvas
TILE_RADIUS = 185
# The face in the default pack sits in the upper middle: a square crop around it (fractions of the image).
DEFAULT_CROP = (0.55, 0.30, 0.62)  # centre x, centre y, side
ICONSET = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2), (512, 1), (512, 2)]
LOG = "~/Library/Logs/Lili.log"


def crop_box(width: int, height: int, crop: tuple[float, float, float]) -> tuple[int, int, int, int]:
    """The square ``(left, top, right, bottom)`` around ``crop`` (centre x, centre y, side — fractions of the shorter
    side for the side), moved inside the image."""
    cx, cy, frac = crop
    side = max(1, min(int(round(min(width, height) * frac)), width, height))
    left = min(max(int(round(cx * width - side / 2)), 0), width - side)
    top = min(max(int(round(cy * height - side / 2)), 0), height - side)
    return left, top, left + side, top + side


def icon_image(portrait: Path, crop: tuple[float, float, float] = DEFAULT_CROP):
    """Her portrait as a 1024 px macOS app icon: the face cropped square, in an 824 px rounded tile, transparent
    around it."""
    from PIL import Image, ImageDraw

    with Image.open(portrait) as src:
        face = src.convert("RGBA").crop(crop_box(src.width, src.height, crop))
    face = face.resize((TILE_PX, TILE_PX), Image.LANCZOS)
    mask = Image.new("L", (TILE_PX, TILE_PX), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, TILE_PX - 1, TILE_PX - 1), radius=TILE_RADIUS, fill=255)
    canvas = Image.new("RGBA", (ICON_PX, ICON_PX), (0, 0, 0, 0))
    at = (ICON_PX - TILE_PX) // 2
    canvas.paste(face, (at, at), mask)
    return canvas


def iconset_names() -> list[tuple[str, int]]:
    """``(file name, pixels)`` for every image an ``.iconset`` holds."""
    out = []
    for size, scale in ICONSET:
        suffix = "" if scale == 1 else "@2x"
        out.append((f"icon_{size}x{size}{suffix}.png", size * scale))
    return out


def info_plist(name: str, version: str) -> bytes:
    return plistlib.dumps({
        "CFBundleName": name, "CFBundleDisplayName": name, "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": name, "CFBundleIconFile": "AppIcon", "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version, "CFBundleVersion": version, "LSMinimumSystemVersion": "11.0",
        "LSUIElement": True,  # the launcher is a shell: the running window brings its own Dock tile (launch.py)
        "NSHighResolutionCapable": True,
    })


def launcher(app_dir: Path) -> str:
    """The bundle's executable: a Finder-launched app has no shell PATH, so uv's usual homes are added."""
    target = shlex.quote(str(app_dir))
    return f"""#!/usr/bin/env bash
# Lili.app's launcher (scripts/make_mac_app.py): runs the desktop window of the client checkout {target}.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
cd {target} || exit 1
mkdir -p "$HOME/Library/Logs"
exec uv run --extra desktop python "$RES/launch.py" >>"$HOME/Library/Logs/Lili.log" 2>&1
"""


LAUNCH_PY = '''"""Lili.app (scripts/make_mac_app.py): her icon and name on the running window, then the desktop window of the
checkout this runs in (the current directory)."""

import os
import sys

sys.path.insert(0, os.getcwd())
HERE = os.path.dirname(os.path.abspath(__file__))
NAME = {name!r}

try:  # the Dock tile and the menu name — the window is a Python process, not this bundle
    from AppKit import NSApplication, NSImage
    from Foundation import NSProcessInfo

    NSProcessInfo.processInfo().setProcessName_(NAME)
    image = NSImage.alloc().initWithContentsOfFile_(os.path.join(HERE, "icon.png"))
    if image is not None:
        NSApplication.sharedApplication().setApplicationIconImage_(image)
except Exception as exc:  # never stop the window over its icon
    print(f"Lili.app: no Dock icon ({{type(exc).__name__}})", file=sys.stderr)

from desktop.__main__ import main

sys.exit(main())
'''


def build(*, portrait: Path, app_dir: Path, dest: Path, name: str = "Lili", version: str = "0",
          crop: tuple[float, float, float] = DEFAULT_CROP,
          iconutil: Callable[[Path, Path], None] | None = None) -> Path:
    """Write ``<dest>/<name>.app`` (replacing an older one) and return its path."""
    bundle = dest / f"{name}.app"
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / f"{name}.app" / "Contents"
        (stage / "MacOS").mkdir(parents=True)
        (stage / "Resources").mkdir()
        (stage / "Info.plist").write_bytes(info_plist(name, version))
        exe = stage / "MacOS" / name
        exe.write_text(launcher(app_dir), encoding="utf-8")
        exe.chmod(0o755)
        (stage / "Resources" / "launch.py").write_text(LAUNCH_PY.format(name=name), encoding="utf-8")
        icon = icon_image(portrait, crop)
        icon.save(stage / "Resources" / "icon.png")
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        from PIL import Image

        for file, px in iconset_names():
            icon.resize((px, px), Image.LANCZOS).save(iconset / file)
        (iconutil or _iconutil)(iconset, stage / "Resources" / "AppIcon.icns")
        dest.mkdir(parents=True, exist_ok=True)
        if bundle.exists():
            shutil.rmtree(bundle)
        shutil.copytree(stage.parent, bundle)
    return bundle


def _iconutil(iconset: Path, out: Path) -> None:
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build Lili.app — the desktop window as a Mac app.")
    p.add_argument("--app-dir", type=Path, default=DEFAULT_APP_DIR, help="the client checkout it runs")
    p.add_argument("--portrait", type=Path, default=DEFAULT_PORTRAIT, help="the image the icon is cut from")
    p.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="where the .app goes")
    p.add_argument("--name", default="Lili")
    args = p.parse_args(argv)
    if sys.platform != "darwin" or shutil.which("iconutil") is None:
        print("make_mac_app: macOS only (iconutil)", file=sys.stderr)
        return 1
    app_dir = args.app_dir.expanduser()
    if not (app_dir / "desktop" / "__main__.py").is_file():
        print(f"make_mac_app: {app_dir} has no desktop window — move it to v2.12.1+ first "
              "(scripts/prod_update.py)", file=sys.stderr)
        return 1
    version = (app_dir / "VERSION").read_text(encoding="utf-8").strip() if (app_dir / "VERSION").is_file() else "0"
    bundle = build(portrait=args.portrait.expanduser(), app_dir=app_dir, dest=args.dest.expanduser(),
                   name=args.name, version=version)
    print(f"built {bundle} → runs {app_dir} (v{version}); log: {LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
