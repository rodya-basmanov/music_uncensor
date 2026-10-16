"""Тесты парсинга альбомов (services/album.py) — без сети."""

from bot.handlers.album import _album_caption, _album_tracks, _fallback_queries, _fmt_release
from services.album import (
    AlbumInfo,
    _pick_casing,
    _query_variants,
    _rank_albums,
    parse_deezer_album,
    parse_deezer_artist,
    parse_deezer_artist_albums,
    parse_deezer_search,
)
from utils.helpers import Track

DEEZER_SEARCH = {
    "data": [
        {
            "id": 123, "title": "Красивое Зло", "nb_tracks": 7,
            "cover_xl": "https://cdn/1000x", "artist": {"name": "villian"},
        },
        {"id": None, "title": "skip me", "artist": {"name": "x"}},
    ]
}

DEEZER_ALBUM = {
    "id": 123, "title": "Красивое Зло", "release_date": "2026-07-24",
    "label": "Koala Music", "duration": 805, "cover_xl": "https://cdn/1000x",
    "artist": {"name": "villian"},
    "genres": {"data": [{"name": "Rap/Hip Hop"}]},
    "contributors": [
        {"name": "villian", "role": "Main"},
        {"name": "madk1d", "role": "Featured"},
    ],
    "tracks": {"data": [
        {"title": "Лицо Затоптали", "duration": 104},
        {"title": "Общество Мертвых Поэтов", "duration": 127},
        {"title": "", "duration": 10},
    ]},
}

def test_parse_deezer_search_skips_invalid():
    albums = parse_deezer_search(DEEZER_SEARCH)
    assert len(albums) == 1
    a = albums[0]
    assert a.source == "deezer"
    assert a.id == "123"
    assert a.artist == "villian"
    assert a.title == "Красивое Зло"
    assert a.cover_url == "https://cdn/1000x"


def test_parse_deezer_album():
    a = parse_deezer_album(DEEZER_ALBUM)
    assert a is not None
    assert a.year == "2026"
    assert a.genre == "Rap/Hip Hop"
    assert a.label == "Koala Music"
    assert a.total_duration == 805
    assert [t.title for t in a.tracks] == ["Лицо Затоптали", "Общество Мертвых Поэтов"]
    assert a.tracks[0].duration == 104
    assert all(t.artist == "villian" for t in a.tracks)
    # соавтор с ролью, сам артист исключён
    assert a.contributors == ["madk1d (Featured)"]


def test_parse_deezer_album_error():
    assert parse_deezer_album({"error": {"message": "not found"}}) is None
    assert parse_deezer_album(None) is None


def test_parse_deezer_artist_skips_invalid():
    data = {"data": [
        {"id": 5, "name": "SALUKI"},
        {"id": None, "name": "nope"},
        {"id": 7, "name": ""},
    ]}
    assert parse_deezer_artist(data) == [{"id": "5", "name": "SALUKI", "fans": 0}]
    assert parse_deezer_artist(None) == []


def test_parse_deezer_artist_albums():
    data = {"data": [
        {"id": 1, "title": "EUPHORIA", "release_date": "2025-04-11",
         "artist": {"name": "SALUKI"}, "cover_xl": "https://cdn/xl"},
        {"id": None, "title": "skip"},
    ]}
    albums = parse_deezer_artist_albums(data)
    assert len(albums) == 1
    a = albums[0]
    assert a.title == "EUPHORIA"
    assert a.year == "2025"
    assert a.release_date == "2025-04-11"
    assert a.cover_url == "https://cdn/xl"
    assert a.source == "deezer"
    assert a.tracks == []
    # имя артиста подставляется, если Deezer его не вернул
    al2 = parse_deezer_artist_albums({"data": [{"id": 2, "title": "X"}]}, "SALUKI")
    assert al2[0].artist == "SALUKI"


def test_search_artist_prefers_popular(monkeypatch):
    import asyncio

    import services.album as album_mod

    async def fake_fetch(url, proxy=""):
        return {"data": [
            {"id": 1, "name": "SALUKI", "nb_fan": 11},
            {"id": 2, "name": "SALUKI", "nb_fan": 15813},
        ]}

    monkeypatch.setattr(album_mod, "_fetch", fake_fetch)
    assert asyncio.run(album_mod.search_artist("saluki")) == {"id": "2", "name": "SALUKI"}


def test_search_artist_rejects_weak_match(monkeypatch):
    import asyncio

    import services.album as album_mod

    async def fake_fetch(url, proxy=""):
        return {"data": [{"id": 9, "name": "Some Other Band", "nb_fan": 99999}]}

    monkeypatch.setattr(album_mod, "_fetch", fake_fetch)
    assert asyncio.run(album_mod.search_artist("saluki")) is None


def test_parse_itunes_search_removed():
    import services.album as album

    assert not hasattr(album, "parse_itunes_search")


def test_pick_casing_prefers_uppercase():
    name = "villian"
    cands = ["Villian", "VILLIAN", "villian", "Villian$"]
    assert _pick_casing(name, cands) == "VILLIAN"


def test_pick_casing_no_match_keeps_original():
    assert _pick_casing("villian", ["Someone Else"]) == "villian"


def test_fallback_queries_strips_punctuation():
    assert _fallback_queries("VILLIAN — Красивое Зло!")[0] == "VILLIAN Красивое Зло"


def test_fallback_queries_drops_artist_word():
    alts = _fallback_queries("красивое зло виллиан")
    assert "красивое зло" in alts


def test_fallback_queries_empty_for_single_word():
    assert _fallback_queries("villian") == []


def test_fmt_release_full_date():
    assert _fmt_release("2026-07-24") == "24 июля 2026"
    assert _fmt_release("2026-01-05T07:00:00Z") == "5 января 2026"


def test_fmt_release_year_fallback():
    assert _fmt_release("2026") == "2026"
    assert _fmt_release("") == ""


def test_parse_deezer_album_release_date():
    a = parse_deezer_album(DEEZER_ALBUM)
    assert a.release_date == "2026-07-24"


def test_album_tracks_force_meta_and_cover():
    from utils.helpers import Track

    album = AlbumInfo(
        source="deezer", id="1", artist="VILLIAN", title="Красивое Зло",
        cover_url="https://cdn/1000x",
        tracks=[Track(artist="villian", title="A", duration=100),
                Track(artist="villian", title="B", duration=110)],
    )
    tracks = _album_tracks(album)
    assert [t.title for t in tracks] == ["A", "B"]
    assert all(t.artist == "VILLIAN" for t in tracks)
    assert all(t.cover_url == "https://cdn/1000x" for t in tracks)
    assert all(t.force_meta for t in tracks)

    only_b = _album_tracks(album, {1})
    assert [t.title for t in only_b] == ["B"]


def test_query_variants_with_translit():
    variants = _query_variants("салуки эйфория")
    assert variants[0] == "салуки эйфория"
    assert "saluki eyforiya" in variants
    assert "saluki" in variants
    assert len(variants) <= 4


def test_rank_albums_prefers_match():
    good = AlbumInfo(source="deezer", id="1", artist="SALUKI", title="EUPHORIA")
    bad = AlbumInfo(source="deezer", id="2", artist="Random", title="Something Else")
    ranked = _rank_albums("салуки эйфория", [bad, good])
    assert ranked[0] is good


def test_album_caption_keeps_duration_visible():
    album = AlbumInfo(
        source="deezer", id="1", artist="VILLIAN", title="Красивое Зло",
        tracks=[
            Track(artist="VILLIAN", title="Очень Длинное Название Трека Которое Надо Обрезать", duration=104),
            Track(artist="VILLIAN", title="Короткий", duration=127),
        ],
    )
    cap, ents = _album_caption(album)
    lines = [ln for ln in cap.splitlines() if " · " in ln]
    assert len(lines) == 2
    assert "1:44" in lines[0] and "2:07" in lines[1]
    assert all(len(ln) <= 32 for ln in lines)


def test_album_caption_shows_feat():
    album = AlbumInfo(
        source="deezer", id="1", artist="SALUKI", title="EUPHORIA",
        tracks=[Track(artist="SALUKI", title="weekend", duration=200, feat=["Sabu"])],
    )
    cap, _ = _album_caption(album)
    assert "(feat. Sabu)" in cap


def test_album_tracks_feat_in_title():
    album = AlbumInfo(
        source="deezer", id="1", artist="SALUKI", title="EUPHORIA",
        tracks=[Track(artist="SALUKI", title="weekend", duration=200, feat=["Sabu"])],
    )
    tracks = _album_tracks(album)
    assert tracks[0].title == "weekend (feat. Sabu)"


def test_norm_title_matches_case_and_punct():
    from services.album import _norm_title

    assert _norm_title("L. O. LUX") == _norm_title("l o lux")
    assert _norm_title("GOOD FCK") == "good fck"


def test_split_artists():
    from services.album import _split_artists

    assert _split_artists("SALUKI, Sabu") == ("SALUKI", ["Sabu"])
    assert _split_artists("SALUKI") == ("SALUKI", [])
    assert _split_artists("SALUKI feat. Joviee") == ("SALUKI", ["Joviee"])


def test_album_serialization_roundtrip():
    from services.album import _album_from_dict, _album_to_dict

    a = AlbumInfo(
        source="deezer", id="1", artist="SALUKI", title="EUPHORIA", year="2025",
        release_date="2025-04-11",
        tracks=[Track(artist="SALUKI", title="weekend", duration=200, feat=["Sabu"])],
    )
    back = _album_from_dict(_album_to_dict(a))
    assert back.title == "EUPHORIA"
    assert back.release_date == "2025-04-11"
    assert back.tracks[0].title == "weekend"
    assert back.tracks[0].feat == ["Sabu"]


def test_maybe_artist_albums_guards():
    import asyncio

    from bot.handlers import playlist

    class _Msg:
        class from_user:
            id = 1

    # длинный запрос и короткий — не трогаем сеть, сразу False
    assert asyncio.run(playlist._maybe_artist_albums(_Msg(), "one two three", -100)) is False
    assert asyncio.run(playlist._maybe_artist_albums(_Msg(), "ab", -100)) is False
