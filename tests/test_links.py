"""Тесты разбора ссылок: YouTube (utils/links.py) и ВК (utils/vk_link.py)."""

from utils.links import find_youtube_link, songlink_url, source_label
from utils.vk_link import find_vk_link, parse_vk_link, parse_vk_track_link


def test_find_youtube_youtu_be():
    assert find_youtube_link("смотри https://youtu.be/dQw4w9WgXcQ круто") == \
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_find_youtube_watch_with_extra_params():
    assert find_youtube_link("https://www.youtube.com/watch?v=H6tNm72cMA8&list=x") == \
        "https://www.youtube.com/watch?v=H6tNm72cMA8"


def test_find_youtube_music_and_shorts():
    assert find_youtube_link("https://music.youtube.com/watch?v=abcdef12345") == \
        "https://www.youtube.com/watch?v=abcdef12345"
    assert find_youtube_link("https://youtube.com/shorts/abcdefghijk") == \
        "https://www.youtube.com/watch?v=abcdefghijk"


def test_find_youtube_none():
    assert find_youtube_link("просто текст") is None
    assert find_youtube_link("") is None


def test_find_vk_link():
    link = find_vk_link("держи https://vk.com/music/playlist/-1_2 пасиб")
    assert link == "https://vk.com/music/playlist/-1_2"
    assert find_vk_link("нет ссылки тут") is None


def test_parse_vk_playlist():
    ref = parse_vk_link("https://vk.com/music/playlist/-123_456")
    assert ref is not None
    assert (ref.owner_id, ref.playlist_id, ref.access_key) == (-123, 456, None)


def test_parse_vk_playlist_with_access_in_path():
    ref = parse_vk_link("https://vk.com/music/playlist/-123_456_abc123")
    assert ref.access_key == "abc123"


def test_parse_vk_album_with_access_query():
    ref = parse_vk_link("https://vk.com/music/album/-2000000001_42?access_key=deadbeef")
    assert (ref.owner_id, ref.playlist_id, ref.access_key) == (-2000000001, 42, "deadbeef")


def test_parse_vk_track():
    ref = parse_vk_track_link("https://vk.com/audio-2001046443_123456_abc123")
    assert ref is not None
    assert (ref.owner_id, ref.audio_id, ref.access_key) == (2001046443, 123456, "abc123")


def test_parse_vk_track_mobile():
    ref = parse_vk_track_link("https://m.vk.com/audio123_456")
    assert (ref.owner_id, ref.audio_id, ref.access_key) == (123, 456, None)


def test_playlist_parser_ignores_track_link():
    assert parse_vk_link("https://vk.com/audio-2001046443_123456_abc123") is None


def test_songlink_url_youtube():
    assert songlink_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "https://song.link/y/dQw4w9WgXcQ"
    assert songlink_url("https://youtu.be/H6tNm72cMA8") == "https://song.link/y/H6tNm72cMA8"


def test_songlink_url_soundcloud_long_form():
    url = "https://soundcloud.com/artist/track"
    assert songlink_url(url) == f"https://song.link/{url}"


def test_songlink_url_other_returns_none():
    assert songlink_url("https://psv4.vkuseraudio.net/s/v1/ac/xxx") is None
    assert songlink_url("") is None


def test_source_label():
    assert source_label("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "YouTube"
    assert source_label("https://youtu.be/H6tNm72cMA8") == "YouTube"
    assert source_label("https://soundcloud.com/artist/track") == "SoundCloud"
    assert source_label("https://psv4.vkuseraudio.net/s/v1/ac/xxx") == ""
    assert source_label("") == ""
