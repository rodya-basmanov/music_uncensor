"""Тесты парсинга альбомов (services/album.py) — без сети."""

from bot.handlers.album import _album_tracks, _fallback_queries
from services.album import (
    AlbumInfo,
    _pick_casing,
    parse_deezer_album,
    parse_deezer_search,
    parse_itunes_album,
    parse_itunes_search,
)

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

ITUNES_SEARCH = {
    "results": [
        {
            "collectionId": 555, "artistName": "VILLIAN", "collectionName": "Красивое Зло",
            "releaseDate": "2026-07-24T07:00:00Z", "primaryGenreName": "Hip-Hop/Rap",
            "artworkUrl100": "https://art/100x100bb.jpg",
        },
        {"artistName": "no id"},
    ]
}

ITUNES_ALBUM = {
    "results": [
        {
            "wrapperType": "collection", "collectionId": 555, "artistName": "VILLIAN",
            "collectionName": "Красивое Зло", "releaseDate": "2026-07-24T07:00:00Z",
            "primaryGenreName": "Hip-Hop/Rap", "artworkUrl100": "https://art/100x100bb.jpg",
        },
        {"wrapperType": "track", "trackName": "Лицо Затоптали", "artistName": "VILLIAN", "trackTimeMillis": 104000},
        {"wrapperType": "track", "trackName": "Оторву Руки", "artistName": "VILLIAN", "trackTimeMillis": 110000},
    ]
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


def test_parse_itunes_search():
    albums = parse_itunes_search(ITUNES_SEARCH)
    assert len(albums) == 1
    a = albums[0]
    assert a.source == "itunes"
    assert a.id == "555"
    assert a.artist == "VILLIAN"
    assert a.year == "2026"
    assert a.cover_url == "https://art/600x600bb.jpg"


def test_parse_itunes_album():
    a = parse_itunes_album(ITUNES_ALBUM)
    assert a is not None
    assert a.title == "Красивое Зло"
    assert [t.title for t in a.tracks] == ["Лицо Затоптали", "Оторву Руки"]
    assert a.tracks[0].duration == 104
    assert a.total_duration == 214


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
