"""v2.11 LUMI-259 — the panel's settings editor (``admin/edit.py``): the schema-driven view, validate → diff → apply
with the previous files kept in ``config-history/``, rollback (and the rollback of a rollback), write-only secrets.
Fixture folders under ``tmp_path`` only."""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta

import pytest

from admin import edit as ed
from core import settings as st

TOKEN = "tok-OLD-SECRET-abcd1234"
NEW_TOKEN = "tok-NEW-SECRET-wxyz9876"
GEMINI = "g-GEMINI-SECRET-0002xx"
SETTINGS_TEXT = """# the dev instance
instance:
  env: dev

memory:
  window: 40   # recent messages
  # a comment that stays

scheduler:
  enabled: true
"""
SECRETS_TEXT = f"server:\n  token: '{TOKEN}'\nproviders:\n  gemini_api_key: '{GEMINI}'\n"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 11, 15, 30, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "dev"
    root.mkdir()
    (root / "settings.yaml").write_text(SETTINGS_TEXT, encoding="utf-8")
    (root / "secrets.yaml").write_text(SECRETS_TEXT, encoding="utf-8")
    (root / "secrets.yaml").chmod(0o600)
    return root


def _snapshot(root):
    return {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}


def _no_secret(obj) -> None:
    text = json.dumps(obj, ensure_ascii=False, default=str)
    for value in (TOKEN, NEW_TOKEN, GEMINI):
        assert value not in text


def _setting(view, key):
    return next(s for g in view["groups"] for s in g["settings"] if s["key"] == key)


# --- the view --------------------------------------------------------------------------------------------------


def test_the_view_covers_the_schema_with_values_sources_and_masked_secrets(root):
    v = ed.view(root)
    keys = [s["key"] for g in v["groups"] for s in g["settings"]]
    assert keys == [s.key for g in st.GROUPS for s in st.SETTINGS if s.group == g]
    window = _setting(v, "memory.window")
    assert (window["value"], window["source"], window["default"], window["type"]) == (40, "settings.yaml", 40, "int")
    assert window["restart"] is True and window["min"] == 1
    think = _setting(v, "persona.think_show")
    assert think["source"] == "default" and think["choices"] and think["value"] == think["default"]
    token = _setting(v, "server.token")
    assert token == token | {"set": True, "last4": "1234", "source": "secrets.yaml", "secret": True}
    assert "value" not in token and "default" not in token
    assert _setting(v, "telegram.token")["set"] is False
    assert "LUMI_SERVER_PORT" in _setting(v, "server.port")["container"]
    assert v["problems"] == [] and set(v["hashes"]) == {"settings.yaml", "secrets.yaml"}
    json.dumps(v)  # JSON-safe
    _no_secret(v)


def test_a_short_secret_shows_no_last4(root):
    (root / "secrets.yaml").write_text("server:\n  token: short-1\n", encoding="utf-8")
    token = _setting(ed.view(root), "server.token")
    assert token["set"] is True and token["last4"] is None


# --- preview -----------------------------------------------------------------------------------------------------


def test_a_preview_is_the_exact_diff_and_writes_nothing(root):
    before = _snapshot(root)
    plan = ed.plan_settings(root, {"memory.window": "30", "LUMI_THINK_SHOW": "open"})
    pub = plan.public()
    assert pub["ok"] and pub["restart"] is True
    assert pub["changes"] == {"memory.window": [40, 30], "persona.think_show": [None, "open"]}
    assert pub["diff"].splitlines() == [
        "--- settings.yaml (now)", "+++ settings.yaml (after)", "@@ -3,8 +3,11 @@", "   env: dev", " ",
        " memory:", "-  window: 40   # recent messages", "+  window: 30", "   # a comment that stays",
        " ", " scheduler:", "   enabled: true", "+", "+persona:", "+  think_show: open"]
    assert _snapshot(root) == before


@pytest.mark.parametrize("changes, words", [
    ({"memory.window": "0"}, ["memory.window", "an integer ≥ 1"]),
    ({"memory.window": "lots"}, ["memory.window", "'lots'"]),
    ({"persona.think_show": "loud"}, ["persona.think_show", "one of debug"]),
    ({"no.such": "1"}, ["unknown setting 'no.such'"]),
    ({"server.token": "x" * 20}, ["server.token is a secret"]),
    ({"server.port": "9999"}, ["LUMI_SERVER_PORT", "no effect"]),
    ({"memory.window": "40"}, ["nothing changes"]),
])
def test_an_invalid_change_is_refused_naming_the_key_and_nothing_is_written(root, changes, words):
    before = _snapshot(root)
    plan = ed.plan_settings(root, changes)
    assert not plan.ok
    text = " ".join(plan.problems)
    for word in words:
        assert word in text
    with pytest.raises(ed.EditError):
        ed.apply(plan, ed.hashes(plan.old))
    assert _snapshot(root) == before
    _no_secret(plan.public())


def test_a_value_that_would_stop_the_instance_is_refused(root):
    (root / "settings.yaml").write_text(SETTINGS_TEXT + "voice:\n  enabled: maybe\n", encoding="utf-8")
    plan = ed.plan_settings(root, {"memory.window": "30"})
    assert not plan.ok and any("voice.enabled" in p for p in plan.problems)


def test_none_takes_a_key_back_to_its_default(root):
    plan = ed.plan_settings(root, {"scheduler.enabled": None})
    assert plan.ok and plan.changes == {"scheduler.enabled": [True, None]}
    assert "scheduler:" not in plan.new.settings and "# a comment that stays" in plan.new.settings


# --- apply, history, rollback --------------------------------------------------------------------------------------


def test_apply_keeps_the_previous_files_and_rollback_restores_them(root):
    clock = Clock()
    plan = ed.plan_settings(root, {"memory.window": "30"})
    result = ed.apply(plan, plan.public()["hashes"], clock=clock)
    assert result["restart"] is True and result["changes"] == {"memory.window": [40, 30]}
    assert "  window: 30" in (root / "settings.yaml").read_text()
    hist = root / "config-history"
    copy = hist / f"{result['ts']}-settings.yaml"
    assert copy.read_text() == SETTINGS_TEXT and stat.S_IMODE(copy.stat().st_mode) == 0o600
    assert stat.S_IMODE(hist.stat().st_mode) == 0o700
    assert not (hist / f"{result['ts']}-secrets.yaml").exists()  # only the files it changed
    [entry] = ed.history(root)
    assert entry == {"ts": result["ts"], "action": "settings", "target": "memory.window",
                     "files": ["settings.yaml"], "changes": {"memory.window": [40, 30]}}

    back = ed.plan_rollback(root, result["ts"])
    assert back.changes == {"memory.window": [30, 40]}
    undo = ed.apply(back, back.public()["hashes"], clock=clock)
    assert (root / "settings.yaml").read_text() == SETTINGS_TEXT
    assert [e["action"] for e in ed.history(root)] == ["rollback", "settings"]
    assert ed.history(root)[0]["of"] == result["ts"]

    again = ed.plan_rollback(root, undo["ts"])  # the rollback of the rollback
    ed.apply(again, again.public()["hashes"], clock=clock)
    assert "  window: 30" in (root / "settings.yaml").read_text()


def test_a_stale_preview_is_refused_and_nothing_is_written(root):
    plan = ed.plan_settings(root, {"memory.window": "30"})
    expect = plan.public()["hashes"]
    (root / "settings.yaml").write_text(SETTINGS_TEXT.replace("40", "41"), encoding="utf-8")  # someone else
    before = _snapshot(root)
    with pytest.raises(ed.Stale):
        ed.apply(plan, expect)
    fresh = ed.plan_settings(root, {"memory.window": "30"})
    with pytest.raises(ed.Stale):
        ed.apply(fresh, expect)  # the old hashes against the new files
    assert _snapshot(root) == before and not (root / "config-history").exists()


def test_review_1_rolling_back_an_older_entry_names_the_later_changes_it_also_undoes(root):
    clock = Clock()
    first = ed.apply(p := ed.plan_settings(root, {"memory.window": "30"}), p.public()["hashes"], clock=clock)
    second = ed.apply(p := ed.plan_settings(root, {"scheduler.enabled": "false"}), p.public()["hashes"], clock=clock)
    ed.apply(p := ed.plan_secret(root, "server.token", NEW_TOKEN), p.public()["hashes"], clock=clock)
    older = ed.plan_rollback(root, first["ts"])
    assert older.changes == {"memory.window": [30, 40], "scheduler.enabled": [False, True]}  # the file goes back whole
    [note] = older.notes
    assert second["ts"] in note and "scheduler.enabled" in note
    assert "server.token" not in note  # a later change to the other file is not undone, so not named
    assert ed.plan_rollback(root, second["ts"]).notes == []  # the newest change of its file: an exact undo
    _no_secret(older.public())


def test_rollback_refuses_an_unknown_or_malformed_entry(root):
    for ts in ("nope", "../../etc/passwd", "20261111-000000Z"):
        with pytest.raises(ed.EditError):
            ed.plan_rollback(root, ts)


def test_history_is_pruned_to_the_newest(root):
    clock = Clock()
    for n in range(5):
        plan = ed.plan_settings(root, {"memory.window": str(30 + n)})
        ed.apply(plan, plan.public()["hashes"], clock=clock, keep=3)
    entries = ed.history(root)
    assert len(entries) == 3 and entries[0]["changes"] == {"memory.window": [33, 34]}
    assert len(list((root / "config-history").iterdir())) == 6  # 3 manifests + 3 copies


# --- secrets: write-only -----------------------------------------------------------------------------------------


def test_a_secret_is_rotated_cleared_and_set_without_ever_being_returned(root):
    clock = Clock()
    rotate = ed.plan_secret(root, "server.token", NEW_TOKEN)
    pub = rotate.public()
    assert pub["ok"] and pub["changes"] == {"server.token": "(secret)"}
    assert pub["diff"] == "secrets.yaml  server.token: set •••1234 → set •••9876"
    assert any("client" in n for n in pub["notes"])
    result = ed.apply(rotate, pub["hashes"], clock=clock)
    assert st.parse_mapping((root / "secrets.yaml").read_text(), "s")["server"]["token"] == NEW_TOKEN
    assert stat.S_IMODE((root / "secrets.yaml").stat().st_mode) == 0o600
    copy = root / "config-history" / f"{result['ts']}-secrets.yaml"
    assert copy.read_text() == SECRETS_TEXT and stat.S_IMODE(copy.stat().st_mode) == 0o600
    token = _setting(ed.view(root), "server.token")
    assert (token["set"], token["last4"]) == (True, "9876")

    clear = ed.plan_secret(root, "providers.gemini_api_key", None)
    assert clear.public()["diff"] == "secrets.yaml  providers.gemini_api_key: set •••02xx → unset"
    ed.apply(clear, clear.public()["hashes"], clock=clock)
    assert _setting(ed.view(root), "providers.gemini_api_key")["set"] is False

    new = ed.plan_secret(root, "telegram.token", "999:BOT-SECRET-qrst")
    ed.apply(new, new.public()["hashes"], clock=clock)
    assert _setting(ed.view(root), "telegram.token")["last4"] == "qrst"

    for obj in (pub, result, clear.public(), ed.history(root), ed.view(root)):
        _no_secret(obj)
    for p in (root / "config-history").glob("*.json"):
        _no_secret(json.loads(p.read_text()))


@pytest.mark.parametrize("key, value, word", [
    ("memory.window", "x" * 20, "not a secret"),
    ("server.token", "", "one non-empty line"),
    ("server.token", " padded-secret-value ", "one non-empty line"),
    ("server.token", "two\nlines-secret", "one non-empty line"),
    ("server.token", TOKEN, "nothing changes"),
])
def test_a_bad_secret_is_refused(root, key, value, word):
    plan = ed.plan_secret(root, key, value)
    assert not plan.ok and word in " ".join(plan.problems)
    _no_secret(plan.public())


def test_a_secret_into_a_missing_file_starts_it_private(tmp_path):
    root = tmp_path / "fresh"
    root.mkdir()
    plan = ed.plan_secret(root, "server.token", NEW_TOKEN)
    ed.apply(plan, plan.public()["hashes"], clock=Clock())
    assert stat.S_IMODE((root / "secrets.yaml").stat().st_mode) == 0o600
    assert st.check_texts("", (root / "secrets.yaml").read_text()) == ([], [])


# --- core.settings: the helpers the editor stands on -----------------------------------------------------------------


def test_unset_in_text_removes_the_line_and_an_emptied_group():
    text = "a:\n  b: 1\n\nmemory:\n  window: 40\n  history_max: [1,\n    2]\n\nscheduler:\n  enabled: true\n"
    out = st.unset_in_text(text, "scheduler.enabled")
    assert out == "a:\n  b: 1\n\nmemory:\n  window: 40\n  history_max: [1,\n    2]\n"
    assert st.unset_in_text(text, "memory.window") == text.replace("  window: 40\n", "")
    assert st.unset_in_text(text, "voice.enabled") == text  # not set: unchanged
    with pytest.raises(st.SettingsError):
        st.unset_in_text("memory: {window: 40}\n", "memory.window")  # inline: refused, never rewritten


def test_check_texts_is_the_start_check_and_never_shows_a_secret():
    assert st.check_texts(SETTINGS_TEXT, SECRETS_TEXT) == ([], [])
    problems, _ = st.check_texts("memory:\n  window: 0\nserver:\n  token: in-the-wrong-file-SECRET\n", "")
    assert any("memory.window" in p for p in problems) and any("belongs in secrets.yaml" in p for p in problems)
    assert not any("in-the-wrong-file-SECRET" in p for p in problems)
    problems, _ = st.check_texts("", "server: [unclosed SECRET-VALUE\n")
    assert problems and "SECRET-VALUE" not in problems[0]


def test_last4_only_for_a_long_secret():
    assert st.last4("x" * 11) == "" and st.last4("abcdefgh1234") == "1234" and st.last4(None) == ""


# --- LUMI-260: the schedule and model-profile editors ---------------------------------------------------------------


def test_a_schedule_section_is_edited_previewed_applied_and_rolled_back(root):
    clock = Clock()
    text = "- name: morning-brief\n  enabled: false\n- name: evening-walk\n  directive: think\n  at: '19:00'\n"
    plan = ed.plan_section(root, "schedule", text)
    pub = plan.public()
    assert pub["ok"] and pub["changes"] == {"schedule": "(section edited)"} and "+schedule:" in pub["diff"]
    result = ed.apply(plan, pub["hashes"], clock=clock)
    assert "evening-walk" in ed.section_text(root, "schedule")
    assert "# a comment that stays" in (root / "settings.yaml").read_text()
    rows = {r["name"]: r for r in ed.merged_section(root, "schedule")}
    assert rows["morning-brief"]["enabled"] is False and rows["morning-brief"].get("directive")  # authored + override
    assert rows["evening-walk"]["at"] == "19:00" and "muse-silent" in rows  # added; authored ids kept
    back = ed.plan_rollback(root, result["ts"])
    ed.apply(back, back.public()["hashes"], clock=clock)
    assert (root / "settings.yaml").read_text() == SETTINGS_TEXT and ed.section_text(root, "schedule") == ""


@pytest.mark.parametrize("name, text, word", [
    ("schedule", "- name: walk\n  at: 19:00\n", "quoted time"),
    ("schedule", "name: walk\n", "list of rows"),
    ("models", "profiles:\n  anthropic: claude-sonnet-5\n", "must be a mapping"),
    ("models", "aliases: [unclosed\n", "not valid YAML"),
    ("crons", "- a\n", "unknown section"),
])
def test_an_invalid_section_is_refused_and_nothing_is_written(root, name, text, word):
    before = _snapshot(root)
    plan = ed.plan_section(root, name, text)
    assert not plan.ok and word in " ".join(plan.problems)
    with pytest.raises(ed.EditError):
        ed.apply(plan, ed.hashes(plan.old))
    assert _snapshot(root) == before


def test_a_models_section_merges_over_the_authored_profiles(root):
    plan = ed.plan_section(root, "models", "models:\n  profiles:\n    anthropic:\n      reply: claude-sonnet-5\n")
    ed.apply(plan, plan.public()["hashes"], clock=Clock())
    merged = ed.merged_section(root, "models")
    assert merged["profiles"]["anthropic"]["reply"] == "claude-sonnet-5"
    assert merged["profiles"]["anthropic"]["think"]  # the authored fields kept
    removed = ed.plan_section(root, "models", "")
    assert removed.ok and "models:" not in removed.new.settings


# --- LUMI-260: promote dev → prod ----------------------------------------------------------------------------------


@pytest.fixture
def pair(tmp_path, root):
    prod = tmp_path / "prod"
    prod.mkdir()
    (prod / "settings.yaml").write_text("instance:\n  env: prod\nmemory:\n  window: 40\npersona:\n  think_show: off\n",
                                        encoding="utf-8")
    (prod / "secrets.yaml").write_text("server:\n  token: tok-PROD-SECRET-00000000\n", encoding="utf-8")
    dev = ed.plan_settings(root, {"memory.window": "30", "persona.think_show": "open"})
    ed.apply(dev, dev.public()["hashes"], clock=Clock())
    sec = ed.plan_section(root, "schedule", "- name: morning-brief\n  enabled: false\n")
    ed.apply(sec, sec.public()["hashes"], clock=Clock())
    return root, prod


def test_promotable_lists_the_differences_never_a_secret_or_an_instance_key(pair):
    dev, prod = pair
    keys = {d["key"]: d for d in ed.promotable(dev, prod)}
    assert keys["memory.window"] == {"key": "memory.window", "source": 30, "target": 40}
    assert keys["persona.think_show"]["source"] == "open" and "schedule" in keys and "scheduler.enabled" in keys
    assert "instance.env" not in keys and "server.token" not in keys


def test_promote_is_the_prod_diff_of_the_chosen_keys(pair):
    dev, prod = pair
    before = _snapshot(prod)
    plan = ed.plan_promote(dev, prod, ["memory.window", "schedule"])
    pub = plan.public()
    assert pub["ok"] and pub["changes"] == {"memory.window": [40, 30], "schedule": "(section edited)"}
    assert "env: prod" in plan.new.settings and "think_show: off" in plan.new.settings  # nothing else moves
    assert _snapshot(prod) == before  # a plan writes nothing
    with pytest.raises(ed.EditError):
        ed.check_confirm("prod", None, "a promote")
    with pytest.raises(ed.EditError):
        ed.check_confirm("prod", "yes")
    ed.check_confirm("prod", " prod ")
    ed.check_confirm("dev", None)
    ed.apply(plan, pub["hashes"], clock=Clock())
    assert "  window: 30" in (prod / "settings.yaml").read_text()


@pytest.mark.parametrize("key, word", [
    ("server.token", "never promoted"), ("instance.env", "belongs to each instance"),
    ("server.port", "belongs to each instance"), ("nope.key", "unknown setting"),
])
def test_promote_refuses_a_secret_and_a_per_instance_key(pair, key, word):
    dev, prod = pair
    plan = ed.plan_promote(dev, prod, [key])
    assert not plan.ok and word in " ".join(plan.problems)


def test_promote_a_key_dev_leaves_at_its_default_removes_it_on_prod(pair):
    dev, prod = pair
    (prod / "settings.yaml").write_text("instance:\n  env: prod\nvoice:\n  enabled: true\n", encoding="utf-8")
    plan = ed.plan_promote(dev, prod, ["voice.enabled"])
    assert plan.ok and plan.changes == {"voice.enabled": [True, None]}
