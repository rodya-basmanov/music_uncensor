"""Тесты парсинга плейлиста в хендлере (bot/handlers/playlist.py)."""

from bot.handlers.playlist import _parse_playlist


def test_parse_playlist_with_chat_id_and_tracks():
    text = "-1001234567890\nArtist - Title\nArtist2 - Title2"
    parsed = _parse_playlist(text)
    assert parsed.channel_id == -1001234567890
    assert len(parsed.tracks) == 2
    assert parsed.tracks[0].artist == "Artist"
    assert parsed.tracks[0].title == "Title"


def test_parse_playlist_durations_and_comments():
    text = "Artist - Title [100]\n# комментарий\nOther - Song [50]"
    parsed = _parse_playlist(text)
    assert parsed.channel_id is None
    assert len(parsed.tracks) == 2
    assert parsed.total_duration == 150


def test_parse_playlist_free_query_lines():
    parsed = _parse_playlist("saluki огней\nmacan flagman widee flip")
    assert len(parsed.tracks) == 2
    assert parsed.tracks[0].query == "saluki огней"
    assert parsed.tracks[1].query == "macan flagman widee flip"
