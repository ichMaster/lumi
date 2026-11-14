"""LUMI-270 — ``scripts/make_mac_app.py``: Lili.app's icon geometry, the iconset names, the Info.plist, the launcher
and the bundle layout. ``iconutil`` is faked (CI is Linux); a tiny generated portrait stands in for her face."""

from __future__ import annotations

import plistlib
import py_compile
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import make_mac_app as mk  # noqa: E402


@pytest.fixture
def portrait(tmp_path):
    path = tmp_path / "face.png"
    Image.new("RGB", (768, 768), (200, 30, 90)).save(path)
    return path


def test_the_crop_is_square_and_stays_inside_the_image():
    assert mk.crop_box(768, 768, (0.5, 0.5, 0.5)) == (192, 192, 576, 576)
    left, top, right, bottom = mk.crop_box(768, 768, (0.95, 0.02, 0.62))  # pushed back inside at the corner
    assert right - left == bottom - top and left >= 0 and top == 0 and right <= 768
    assert mk.crop_box(800, 600, (0.5, 0.5, 2.0)) == (100, 0, 700, 600)  # never larger than the image


def test_the_icon_is_a_rounded_tile_on_a_transparent_canvas(portrait):
    icon = mk.icon_image(portrait)
    assert icon.size == (1024, 1024) and icon.mode == "RGBA"
    assert icon.getpixel((5, 5))[3] == 0 and icon.getpixel((512, 512))[3] == 255
    edge = (1024 - mk.TILE_PX) // 2
    assert icon.getpixel((edge + 2, edge + 2))[3] == 0  # the tile's corner is rounded away


def test_the_iconset_has_every_size_macos_wants():
    names = dict(mk.iconset_names())
    assert len(names) == 10
    assert names["icon_16x16.png"] == 16 and names["icon_16x16@2x.png"] == 32
    assert names["icon_512x512@2x.png"] == 1024


def test_the_info_plist_names_the_app_and_its_icon():
    info = plistlib.loads(mk.info_plist("Lili", "2.12.1"))
    assert info["CFBundleName"] == info["CFBundleExecutable"] == "Lili"
    assert info["CFBundleIconFile"] == "AppIcon" and info["CFBundleShortVersionString"] == "2.12.1"
    assert info["LSUIElement"] is True and info["CFBundlePackageType"] == "APPL"


def test_the_launcher_runs_the_checkouts_window(tmp_path):
    text = mk.launcher(Path("/Users/me/lumi/prod/app"))
    assert "cd /Users/me/lumi/prod/app || exit 1" in text
    assert 'exec uv run --extra desktop python "$RES/launch.py"' in text and ".local/bin" in text
    assert "Library/Logs/Lili.log" in text
    assert "cd '/Users/me/my lumi/app'" in mk.launcher(Path("/Users/me/my lumi/app"))  # a space is quoted
    if shutil.which("bash"):
        script = tmp_path / "Lili"
        script.write_text(text)
        assert subprocess.run(["bash", "-n", str(script)]).returncode == 0


def test_the_bundle_is_built_and_rebuilt(tmp_path, portrait):
    made = []

    def fake_iconutil(iconset: Path, out: Path) -> None:
        made.append(sorted(p.name for p in iconset.iterdir()))
        out.write_bytes(b"icns")

    for _ in range(2):  # a second build replaces the first
        app = mk.build(portrait=portrait, app_dir=Path("/Users/me/lumi/prod/app"), dest=tmp_path / "Applications",
                       version="2.12.1", iconutil=fake_iconutil)
    contents = app / "Contents"
    assert app == tmp_path / "Applications" / "Lili.app"
    exe = contents / "MacOS" / "Lili"
    assert exe.stat().st_mode & stat.S_IXUSR
    assert (contents / "Resources" / "AppIcon.icns").read_bytes() == b"icns"
    assert Image.open(contents / "Resources" / "icon.png").size == (1024, 1024)
    py_compile.compile(str(contents / "Resources" / "launch.py"), doraise=True)
    launch = (contents / "Resources" / "launch.py").read_text()
    assert "from desktop.__main__ import main" in launch and "setApplicationIconImage_" in launch
    assert made[0] == sorted(name for name, _ in mk.iconset_names())


def test_the_builder_refuses_a_checkout_without_the_window(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(mk.sys, "platform", "darwin")
    monkeypatch.setattr(mk.shutil, "which", lambda name: "/usr/bin/iconutil")
    assert mk.main(["--app-dir", str(tmp_path), "--dest", str(tmp_path / "out")]) == 1
    assert "v2.12.1+" in capsys.readouterr().err and not (tmp_path / "out").exists()
