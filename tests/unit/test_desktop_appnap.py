"""v4.8.1 — the window's shell opts out of App Nap while it is open.

A napped app in the background got her voice paused mid-line by WebKit. On macOS the shell begins an
``NSProcessInfo`` activity before the window opens and ends it on close; off macOS, or without PyObjC, nothing is
asked, and a failing call never keeps the window from opening."""

import sys

from desktop.__main__ import APP_NAP_REASON, end_awake, keep_awake


class FakeProcessInfo:
    def __init__(self) -> None:
        self.calls: list = []

    def beginActivityWithOptions_reason_(self, options, reason):
        self.calls.append(("begin", options, reason))
        return "token"

    def endActivity_(self, token):
        self.calls.append(("end", token))


def test_the_window_opts_out_of_app_nap_and_ends_it_on_close():
    info = FakeProcessInfo()
    token = keep_awake(info, options=7)
    assert token == "token" and info.calls == [("begin", 7, APP_NAP_REASON)]
    end_awake(token, info)
    assert info.calls[-1] == ("end", "token")


def test_off_macos_nothing_is_asked(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert keep_awake() is None
    end_awake(None)  # nothing to end, no error


def test_a_failing_call_never_keeps_the_window_from_opening():
    class Broken:
        def beginActivityWithOptions_reason_(self, *args):
            raise RuntimeError("no")

        def endActivity_(self, token):
            raise RuntimeError("no")

    assert keep_awake(Broken(), options=1) is None
    end_awake("token", Broken())  # a failing end is swallowed: the process is closing anyway
