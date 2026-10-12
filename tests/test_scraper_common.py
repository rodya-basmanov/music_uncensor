"""Тесты источника (core/scraper/common.py): ранжирование, метаданные, парсинг вывода."""

from core.scraper.common import (
    Candidate,
    is_remix,
    normalize,
    parse_search_output,
    rank_candidates,
    split_artist_title,
    uploader_matches,
)


def test_is_remix():
    assert is_remix("Track (slowed + reverb)") is True
    assert is_remix("Track bootleg") is True
    assert is_remix("Обычный трек") is False


def test_uploader_matches():
    assert uploader_matches("SALUKI", "SALUKI") is True
    assert uploader_matches("s-a-l-u-k-i", "saluki") is True
    assert uploader_matches("Other Channel", "saluki") is False
    assert uploader_matches("", "saluki") is False
    assert uploader_matches("SALUKI", "") is False


def test_normalize():
    assert normalize("Hello, World!") == "hello world"
    assert normalize("Ёлка") == "елка"


def test_split_artist_title_dash():
    assert split_artist_title("Artist - Title", "") == ("Artist", "Title")


def test_split_artist_title_no_dash_uses_uploader():
    assert split_artist_title("Title", "Uploader") == ("Uploader", "Title")


def test_split_artist_title_strips_noise():
    assert split_artist_title("Artist - Title (Official Video)", "") == ("Artist", "Title")


def test_rank_candidates_prefers_non_remix_and_uploader():
    cands = [
        Candidate(url="u1", title="Song (slowed)", uploader="random", duration=180, source="youtube"),
        Candidate(url="u2", title="Song", uploader="SALUKI", duration=181, source="youtube"),
        Candidate(url="u3", title="Song", uploader="other", duration=500, source="youtube"),
    ]
    ranked = rank_candidates(cands, "SALUKI Song", expected_duration=181, artist_hint="SALUKI", limit=3)
    assert ranked[0].url == "u2"


def test_parse_search_output():
    stdout = "https://a/1|||Track One|||181|||SALUKI|||SALUKI\nhttps://a/2|||Track Two|||NA|||Chan|||"
    cands = parse_search_output(stdout, "youtube")
    assert len(cands) == 2
    assert cands[0].url == "https://a/1"
    assert cands[0].duration == 181
    assert cands[0].uploader == "SALUKI"
    assert cands[0].source == "youtube"
    assert cands[1].duration == 0.0
    assert cands[1].uploader == "Chan"


def test_parse_search_output_skips_bad_lines():
    assert parse_search_output("no delimiters here", "vk") == []
    assert parse_search_output("", "vk") == []
