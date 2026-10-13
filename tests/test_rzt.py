"""Тесты парсинга РЗТ (services/rzt.py) — без сети."""

from services.rzt import _parse_host_score, _parse_nominations, _parse_rating, _parse_tier, _pick_slug

# Фрагменты, снятые с реальной страницы альбома (SALUKI – EUPHORIA)
HOST_SCORES = (
    r'\"avg\":81,\"count\":137,'
    r'\"avg\":80,\"count\":505,'
    r'\"avg\":83,\"count\":0,'
)

TIER = (
    '>Сапфировый альбом</div><div class="cursor-default text-xl '
    'leading-[28px] font-semibold lg:text-[32px]">19.13</div>'
)

NOMINATIONS = r'\"nominations\":[{\"title\":\"Альбом года\"},{\"title\":\"Лучший рэп-альбом\"}]'


def test_parse_host_score_uses_count_zero():
    assert _parse_host_score(HOST_SCORES) == 83


def test_parse_host_score_none_when_absent():
    assert _parse_host_score("nothing here") is None


def test_parse_tier():
    assert _parse_tier(TIER) == ("Сапфировый альбом", "19.13")


def test_parse_tier_none():
    assert _parse_tier("<div>обычный альбом</div>") is None


def test_parse_nominations():
    noms = _parse_nominations(NOMINATIONS)
    assert "Альбом года" in noms
    assert "Лучший рэп-альбом" in noms


def test_parse_rating_combines():
    html = HOST_SCORES + TIER + NOMINATIONS
    data = _parse_rating(html)
    assert data is not None
    assert data["rzt"] == 83
    assert data["tier"] == ("Сапфировый альбом", "19.13")
    assert "Альбом года" in data["nominations"]


def test_parse_rating_empty():
    assert _parse_rating("") is None
    assert _parse_rating("<html>no data</html>") is None


def test_pick_slug():
    slugs = ["bolshie-kurtki-deluxe", "euphoria-3", "kogda-nastupit-konets-sveta"]
    assert _pick_slug("SALUKI EUPHORIA", slugs) == "euphoria-3"


def test_pick_slug_no_match():
    assert _pick_slug("someone unknown", ["a-b", "c-d"]) is None
