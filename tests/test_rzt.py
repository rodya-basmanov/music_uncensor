"""Тесты парсинга РЗТ (services/rzt.py) — без сети."""

from services.rzt import (
    _clean_query,
    _parse_host_score,
    _parse_nominations,
    _parse_rating,
    _parse_tier,
    _pick_slug,
)

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

NOMINATIONS = (
    r'transition-colors bg-white/5 hover:bg-white/10\",\"children\":['
    r'\"className\":\"truncate text-sm leading-tight font-medium\",\"children\":\"Альбом года\"'
    r'\"className\":\"text-muted-foreground mt-0.5 text-xs leading-tight font-medium\",\"children\":\"Итоги 2025 года\"'
    r'\"className\":\"ml-auto shrink-0 text-xs font-semibold text-zinc-100\",\"children\":\"2 место\"'
    r'transition-colors winner-surface bg-zinc-950\",\"children\":['
    r'\"className\":\"truncate text-sm leading-tight font-medium\",\"children\":\"Альбом месяца\"'
    r'\"className\":\"text-muted-foreground mt-0.5 text-xs leading-tight font-medium\",\"children\":\"Ноябрь 2025\"'
    r'\"className\":\"ml-auto shrink-0 text-xs font-semibold text-zinc-100\",\"children\":\"Победитель\"'
)


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
    assert len(noms) == 2
    assert noms[0]["title"] == "Альбом года"
    assert noms[0]["result"] == "2 место"
    assert noms[0]["period"] == "Итоги 2025 года"
    assert noms[0]["winner"] is False
    assert noms[1]["title"] == "Альбом месяца"
    assert noms[1]["result"] == "Победитель"
    assert noms[1]["winner"] is True


def test_parse_rating_combines():
    html = HOST_SCORES + TIER + NOMINATIONS
    data = _parse_rating(html)
    assert data is not None
    assert data["rzt"] == 83
    assert data["tier"] == ("Сапфировый альбом", "19.13")
    assert data["nominations"][0]["title"] == "Альбом года"


def test_parse_rating_empty():
    assert _parse_rating("") is None
    assert _parse_rating("<html>no data</html>") is None


def test_pick_slug():
    slugs = ["bolshie-kurtki-deluxe", "euphoria-3", "kogda-nastupit-konets-sveta"]
    assert _pick_slug("SALUKI EUPHORIA", slugs) == "euphoria-3"


def test_pick_slug_no_match():
    assert _pick_slug("someone unknown", ["a-b", "c-d"]) is None


def test_pick_slug_translit_cyrillic():
    slugs = ["partizan", "haski-ljubimye-pesni-voobrazhaemyh-ljudej", "euphoria-3"]
    assert _pick_slug("Хаски Партизан", slugs) == "partizan"


def test_clean_query_strips_symbols():
    assert _clean_query("SALUKI WILD EA$T") == "SALUKI WILD EA T"
    assert _clean_query('"EUPHORIA"!') == "EUPHORIA"
