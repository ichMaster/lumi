"""Unit tests for the ambient WorldContext provider (LUMI-020) — no network."""

from datetime import UTC, datetime

from core.clock import fixed_clock
from core.worldcontext import fetch_world_context

_CLK = fixed_clock(datetime(2026, 6, 7, 14, 30, tzinfo=UTC))

_OPEN_METEO = '{"current": {"temperature_2m": 15.4, "weather_code": 0}}'
_RSS = (
    "<rss><channel><title>Feed</title>"
    "<item><title>Перша новина</title></item>"
    "<item><title>Друга</title></item>"
    "<item><title>Третя</title></item>"
    "<item><title>Четверта</title></item>"
    "</channel></rss>"
)


def test_now_and_calendar_come_from_the_clock():
    wc = fetch_world_context(_CLK)
    assert wc.now == "2026-06-07 14:30"
    assert "7 червня" in wc.calendar  # weekday + day + month (Ukrainian)


def test_sources_off_by_default():
    wc = fetch_world_context(_CLK)  # nothing configured
    assert wc.location is None and wc.weather is None and wc.news == ()
    assert wc.now and wc.calendar  # the clock bits still populate


def test_location_is_a_static_string_no_http():
    assert fetch_world_context(_CLK, location="Львів").location == "Львів"


def test_weather_from_open_meteo():
    wc = fetch_world_context(_CLK, lat=49.84, lon=24.03, http_get=lambda url: _OPEN_METEO)
    assert wc.weather == "15°C, ясно"


def test_weather_url_is_configurable_with_lat_lon_substitution():
    seen = {}

    def fake_get(url):
        seen["url"] = url
        return _OPEN_METEO

    fetch_world_context(
        _CLK, lat=1.5, lon=2.5, weather_url="https://x/w?la={lat}&lo={lon}", http_get=fake_get
    )
    assert seen["url"] == "https://x/w?la=1.5&lo=2.5"


def test_news_drops_channel_title_and_caps():
    wc = fetch_world_context(_CLK, news_url="http://x/rss", news_cap=2, http_get=lambda url: _RSS)
    assert wc.news == ("Перша новина", "Друга")  # channel title dropped; capped to 2


def test_news_skips_channel_and_image_titles():
    # Real feeds repeat the title in <channel> and <image>; only <item> titles count.
    rss = (
        "<rss><channel><title>Газета</title>"
        "<image><title>Газета</title><url>x</url></image>"
        "<item><title>Перша</title></item>"
        "<item><title>Друга</title></item>"
        "</channel></rss>"
    )
    wc = fetch_world_context(_CLK, news_url="http://x", news_cap=5, http_get=lambda url: rss)
    assert wc.news == ("Перша", "Друга")  # neither the feed nor the image title


def test_a_failing_source_degrades_to_none_and_never_raises():
    def boom(url):
        raise RuntimeError("source down")

    wc = fetch_world_context(_CLK, lat=1.0, lon=2.0, news_url="http://x", http_get=boom)
    assert wc.weather is None and wc.news == ()
    assert wc.now == "2026-06-07 14:30"  # the clock bits are unaffected
