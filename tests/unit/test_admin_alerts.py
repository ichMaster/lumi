"""v2.11 LUMI-262 — alerts through the dedicated admin bot (``admin/alerts.py``): one failing message per episode,
one recovery, the rate limit, prod only unless listed, a sender that never raises and never logs its token.
No network, no real Telegram."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from admin import alerts as al
from admin import health as hl
from admin import registry as rg
from admin.health import AMBER, GREEN, RED, CheckResult
from admin.registry import AlertsConfig

TZ = timezone(timedelta(hours=2))
BOT = "123456:ADMIN-BOT-SECRET-TOKEN-xyz"
CFG = AlertsConfig(chat_id=4242, instances=("prod",), token=BOT)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 11, 18, 0, tzinfo=TZ)

    def __call__(self) -> datetime:
        return self.now

    def tick(self, minutes: float) -> None:
        self.now += timedelta(minutes=minutes)


def _run(**checks: str) -> list[CheckResult]:
    return [CheckResult(name, status, "down — unreachable" if status == RED else "") for name, status in checks.items()]


def _machine(cfg=CFG):
    clock, sender = Clock(), al.FakeSender()
    return al.Alerts(cfg, sender, clock=clock), clock, sender


def test_one_failing_and_one_recovery_message_per_episode():
    alerts, clock, sender = _machine()
    alerts.observe({"prod": _run(server=GREEN, state=GREEN)})
    clock.tick(1)
    alerts.observe({"prod": _run(server=RED, state=RED, mood=AMBER)})
    clock.tick(1)
    alerts.observe({"prod": _run(server=RED, state=GREEN)})  # the red set changes: nothing more
    clock.tick(1)
    alerts.observe({"prod": _run(server=GREEN, state=GREEN)})
    assert sender.sent == ["⚠ Lumi prod failing: server (down — unreachable), state (down — unreachable)",
                           "✓ Lumi prod recovered after 2 min"]
    assert alerts.status() | {"last_sent": {}} == {"on": True, "why": None, "instances": ["prod"], "failing": [],
                                                   "last_sent": {}}


def test_a_flapping_instance_sends_one_failing_message_within_the_window():
    alerts, clock, sender = _machine()
    for _ in range(3):  # red 1 min, green 1 min, three times — within 10 minutes
        alerts.observe({"prod": _run(server=RED)})
        clock.tick(1)
        alerts.observe({"prod": _run(server=GREEN)})
        clock.tick(1)
    assert [m[0] for m in sender.sent] == ["⚠", "✓"]
    alerts.observe({"prod": _run(server=RED)})  # still red when the window ends → its message goes then
    clock.tick(5)
    alerts.observe({"prod": _run(server=RED)})
    assert [m[0] for m in sender.sent] == ["⚠", "✓", "⚠"]


def test_a_recovery_is_never_sent_without_a_failing_message():
    alerts, clock, sender = _machine()
    alerts.observe({"prod": _run(server=RED)})
    clock.tick(1)
    alerts.observe({"prod": _run(server=GREEN)})
    clock.tick(1)
    alerts.observe({"prod": _run(server=RED)})  # suppressed by the rate limit
    clock.tick(1)
    alerts.observe({"prod": _run(server=GREEN)})  # so no recovery for it
    assert [m[0] for m in sender.sent] == ["⚠", "✓"]


def test_dev_is_ignored_unless_listed():
    alerts, clock, sender = _machine()
    alerts.observe({"dev": _run(server=RED), "prod": _run(server=GREEN)})
    assert sender.sent == []
    both, _, sender2 = _machine(AlertsConfig(chat_id=1, instances=("prod", "dev"), token=BOT))
    both.observe({"dev": _run(server=RED)})
    assert sender2.sent == ["⚠ Lumi dev failing: server (down — unreachable)"]


def test_a_failed_send_is_retried_on_the_next_run():
    alerts, clock, sender = _machine()
    sender.fail = True
    alerts.observe({"prod": _run(server=RED)})
    sender.fail = False
    clock.tick(1)
    alerts.observe({"prod": _run(server=RED)})
    assert len(sender.sent) == 1


def test_alerts_are_off_without_a_token_or_a_chat_id_and_the_runner_is_unaffected(tmp_path):
    for cfg in (AlertsConfig(chat_id=None, token=BOT), AlertsConfig(chat_id=1, token="")):
        alerts = al.Alerts(cfg, None)
        alerts.observe({"prod": _run(server=RED)})
        assert alerts.status()["on"] is False and "alerts are off" in alerts.status()["why"]
    calls = []
    runner = hl.HealthRunner([], tmp_path / "health.jsonl", on_run=lambda latest: calls.append(latest))
    runner.run_once()
    boom = hl.HealthRunner([], tmp_path / "h2.jsonl", on_run=lambda latest: 1 / 0)
    assert boom.run_once() == {} and calls == [{}]  # a failing hook never stops the checks


def test_a_message_is_redacted_and_its_details_capped():
    clock, sender = Clock(), al.FakeSender()
    alerts = al.Alerts(CFG, sender, clock=clock, redact=lambda name, text: text.replace("SECRET-1", "•••"))
    alerts.observe({"prod": [CheckResult("model", RED, "x" * 300 + " SECRET-1")]})
    assert "SECRET-1" not in sender.sent[0] and len(sender.sent[0]) < 200


# --- the sender ------------------------------------------------------------------------------------------------


def test_the_sender_posts_to_the_one_chat_with_the_token_only_in_the_path():
    calls = []

    def post(url, body, timeout):
        calls.append((url, json.loads(body), timeout))
        return 200

    sender = al.TelegramSender(BOT, 4242, post=post)
    assert sender.send("⚠ Lumi prod failing: server") is True
    [(url, body, timeout)] = calls
    assert url == f"https://api.telegram.org/bot{BOT}/sendMessage"
    assert body == {"chat_id": 4242, "text": "⚠ Lumi prod failing: server", "disable_web_page_preview": True}
    assert BOT not in json.dumps(body) and timeout == 10.0 and BOT not in repr(sender)


@pytest.mark.parametrize("post", [
    lambda url, body, timeout: (_ for _ in ()).throw(OSError(f"connect to {url} failed")),
    lambda url, body, timeout: 401,
])
def test_a_failing_send_is_swallowed_and_logged_without_the_token(caplog, post):
    caplog.set_level(logging.WARNING, logger="lumi.admin.alerts")
    assert al.TelegramSender(BOT, 4242, post=post).send("x") is False
    assert caplog.records and BOT not in caplog.text


# --- the registry: the alerts block ---------------------------------------------------------------------------------


_ADMIN = """instances:
  prod: {root: /instances/prod, url: "http://192.168.1.197:8765", project: lumi-prod}
"""


def _home(tmp_path, alerts_yaml: str = "", bot: str | None = BOT):
    home = tmp_path / "admin"
    home.mkdir(parents=True)
    (home / "admin.yaml").write_text(_ADMIN + alerts_yaml, encoding="utf-8")
    secrets = "admin:\n  token: " + "t" * 40 + "\n"
    if bot is not None:
        secrets += f"alerts:\n  telegram_token: '{bot}'\n"
    (home / "secrets.yaml").write_text(secrets, encoding="utf-8")
    return home


def test_the_alerts_block_loads_and_the_token_stays_out_of_repr(tmp_path):
    cfg = rg.load(_home(tmp_path, "alerts:\n  chat_id: 4242\n"), environ={})
    assert cfg.alerts == AlertsConfig(chat_id=4242, instances=("prod",), token=BOT)
    assert cfg.alerts.off_reason is None and BOT not in repr(cfg) and "t" * 40 not in repr(cfg)


def test_no_alerts_block_means_alerts_off(tmp_path):
    cfg = rg.load(_home(tmp_path, bot=None), environ={})
    assert "chat_id" in cfg.alerts.off_reason
    cfg = rg.load(_home(tmp_path / "x", "alerts:\n  chat_id: 1\n", bot=None), environ={})
    assert "telegram_token" in cfg.alerts.off_reason


@pytest.mark.parametrize("text, key", [
    ("alerts:\n  chat_id: me\n", "alerts.chat_id"),
    ("alerts:\n  chat_id: 1\n  instances: [staging]\n", "alerts.instances"),
    ("alerts: [1]\n", "alerts must be a mapping"),
])
def test_a_bad_alerts_block_refuses_naming_the_key(tmp_path, text, key):
    with pytest.raises(rg.AdminConfigError) as err:
        rg.load(_home(tmp_path, text), environ={})
    assert key in str(err.value)
