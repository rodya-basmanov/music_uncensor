"""Тесты вспомогательных утилит (utils/helpers.py)."""

from utils.helpers import (
    Track,
    clean_artist_for_search,
    clean_title_for_display,
    clean_title_for_search,
    crop_text,
    format_duration,
    generate_hash,
    parse_track_line,
    translit,
)


def test_parse_track_line_with_dash_and_duration():
    t = parse_track_line("SALUKI - ОГНЕЙ [181]")
    assert isinstance(t, Track)
    assert t.artist == "SALUKI"
    assert t.title == "ОГНЕЙ"
    assert t.duration == 181
    assert t.query is None


def test_parse_track_line_en_dash():
    t = parse_track_line("Artist – Title")
    assert t.artist == "Artist"
    assert t.title == "Title"


def test_parse_track_line_feat_kept_in_artist():
    t = parse_track_line("J.ROUH feat. SALUKI - SALUT [220]")
    assert t.artist == "J.ROUH feat. SALUKI"
    assert t.title == "SALUT"
    assert t.duration == 220


def test_parse_track_line_free_query():
    t = parse_track_line("saluki огней")
    assert t.artist == ""
    assert t.title == "saluki огней"
    assert t.query == "saluki огней"


def test_parse_track_line_comment_and_blank():
    assert parse_track_line("# comment") is None
    assert parse_track_line("   ") is None
    assert parse_track_line("") is None


def test_generate_hash_case_insensitive_and_stable():
    a = generate_hash("SALUKI", "Огней")
    b = generate_hash("saluki", "огней")
    assert a == b
    assert len(a) == 64
    assert generate_hash("A", "B") != generate_hash("A", "C")


def test_clean_artist_for_search():
    assert clean_artist_for_search("J.ROUH feat. SALUKI") == "J.ROUH"
    assert clean_artist_for_search("A, B") == "A"
    assert clean_artist_for_search("A & B") == "A"
    assert clean_artist_for_search("SALUKI") == "SALUKI"
    assert clean_artist_for_search("") == ""


def test_clean_title_for_search():
    assert clean_title_for_search("Track (feat. X)") == "Track"
    assert clean_title_for_search("Track feat. X") == "Track"
    assert clean_title_for_search("Track (remix)") == "Track"
    assert clean_title_for_search("Track") == "Track"


def test_clean_title_for_display_keeps_remix():
    assert clean_title_for_display("Track (prod. by X)") == "Track"
    assert clean_title_for_display("Track feat. Y") == "Track"
    assert clean_title_for_display("Track (remix)") == "Track (remix)"


def test_format_duration():
    assert format_duration(30) == "30 сек"
    assert format_duration(90) == "1мин"
    assert format_duration(3900) == "1ч 5мин"


def test_read_text_file_encodings(tmp_path):
    from utils.helpers import read_text_file

    p = tmp_path / "a.txt"
    for enc in ("utf-8", "utf-8-sig", "utf-16"):
        p.write_bytes("Artist - Title\nB - C\n".encode(enc))
        assert read_text_file(str(p)) == "Artist - Title\nB - C\n"


def test_translit_cyrillic():
    assert translit("салуки") == "saluki"
    assert translit("Эйфория") == "Eyforiya"


def test_translit_keeps_latin():
    assert translit("SALUKI euphoria") == "SALUKI euphoria"


def test_crop_text():
    assert crop_text("abcdef", 10) == "abcdef"
    assert crop_text("abcdefghij", 5) == "abcd…"
    assert crop_text("abcdef", 1) == "…"
