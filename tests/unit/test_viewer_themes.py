"""Theme manifest + folder auto-discovery (v0.11, LUMI-043)."""

from viewer.themes import Themes, discover_themes, load_themes


def _mk(faces, *theme_emotions):
    """Create theme/emotion folders, e.g. _mk(dir, ('cozy', 'calm'), ('cozy', 'joy'))."""
    for theme, emotion in theme_emotions:
        (faces / theme / emotion).mkdir(parents=True, exist_ok=True)


def test_discover_themes_finds_folders_with_calm(tmp_path):
    faces = tmp_path / "faces"
    _mk(faces, ("cozy", "calm"), ("cozy", "joy"), ("3am", "calm"))
    (faces / "no-calm" / "joy").mkdir(parents=True)  # not a theme — no calm/
    assert discover_themes(faces) == ["3am", "cozy"]
    assert discover_themes(tmp_path / "missing") == []  # no faces dir → empty


def test_load_themes_parses_manifest_and_default(tmp_path):
    faces = tmp_path / "faces"
    _mk(faces, ("cozy", "calm"), ("3am", "calm"))
    (faces / "themes.md").write_text(
        "default: cozy\n\n## cozy\nWarm, soft, intimate.\n\n## 3am\nRooftop loneliness at 3AM.\n",
        encoding="utf-8",
    )
    themes = load_themes(faces)
    assert themes.default == "cozy"
    assert themes.descriptions["cozy"] == "Warm, soft, intimate."
    assert themes.descriptions["3am"] == "Rooftop loneliness at 3AM."
    assert themes.names == ["3am", "cozy"]


def test_load_themes_discovered_without_manifest_entry(tmp_path):
    faces = tmp_path / "faces"
    _mk(faces, ("cozy", "calm"), ("extra", "calm"))
    (faces / "themes.md").write_text("default: cozy\n\n## cozy\nWarm.\n", encoding="utf-8")
    themes = load_themes(faces)
    assert set(themes.names) == {"cozy", "extra"}  # 'extra' discovered though undescribed
    assert themes.descriptions["extra"] == ""


def test_load_themes_missing_manifest_is_flat(tmp_path):
    faces = tmp_path / "faces"  # no themes.md, no theme folders
    faces.mkdir()
    themes = load_themes(faces)
    assert themes == Themes(descriptions={}, default=None)  # → flat v0.7 behavior


def test_load_themes_unknown_default_falls_back_to_first_discovered(tmp_path):
    faces = tmp_path / "faces"
    _mk(faces, ("aaa", "calm"), ("bbb", "calm"))
    (faces / "themes.md").write_text("default: ghost\n\n## aaa\nA.\n", encoding="utf-8")
    themes = load_themes(faces)
    assert themes.default == "aaa"  # 'ghost' isn't a real theme → first discovered
