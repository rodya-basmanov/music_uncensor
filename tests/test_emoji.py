"""Тесты кастомных эмодзи и билдера entities (utils/emoji.py) — без сети."""

from utils.emoji import CUSTOM, TextBuilder, result_key, set_custom_enabled, tier_key


def test_tier_key():
    assert tier_key("Рубиновый альбом") == "ruby"
    assert tier_key("Сапфировый альбом") == "sapphire"
    assert tier_key("Изумрудный альбом") == "emerald"
    assert tier_key("Золотой альбом") == "gold"
    assert tier_key("Серебряный альбом") == "silver"
    assert tier_key("Неизвестный тир") == ""


def test_result_key():
    assert result_key("Победитель", True) == "winner"
    assert result_key("1 место") == "place1"
    assert result_key("2 место") == "place2"
    assert result_key("3 место") == "place3"
    assert result_key("топ-10") == "medal"


def test_builder_custom_emoji_offsets_utf16():
    set_custom_enabled(True)
    try:
        b = TextBuilder()
        b.add("", emoji_key="rzt")
        b.add(" РЗТ: 86/90")
        text, ents = b.build()
        assert text == "😎 РЗТ: 86/90"
        custom = [e for e in ents if e.type == "custom_emoji"]
        assert len(custom) == 1
        assert custom[0].offset == 0
        assert custom[0].length == 2  # эмодзи = суррогатная пара (UTF-16)
        assert custom[0].custom_emoji_id == CUSTOM["rzt"]
    finally:
        set_custom_enabled(False)


def test_builder_nice_unicode_when_custom_disabled():
    set_custom_enabled(False)
    b = TextBuilder()
    b.add("", emoji_key="ruby")
    b.add(" Рубиновый")
    text, ents = b.build()
    assert text == "❤️ Рубиновый"
    assert not [e for e in ents if e.type == "custom_emoji"]


def test_builder_bold_offset_after_emoji():
    set_custom_enabled(False)
    b = TextBuilder()
    b.add("🎯 ")
    b.add("RZT", bold=True)
    text, ents = b.build()
    assert text == "🎯 RZT"
    bold = [e for e in ents if e.type == "bold"][0]
    assert bold.offset == 3   # "🎯 " -> 2 + 1
    assert bold.length == 3


def test_builder_expandable_blockquote():
    b = TextBuilder()
    b.add("head\n")
    b.open_blockquote(expandable=True)
    b.add("line1\nline2")
    b.close_blockquote()
    text, ents = b.build()
    bq = [e for e in ents if e.type == "expandable_blockquote"]
    assert len(bq) == 1
    assert text[bq[0].offset:bq[0].offset + bq[0].length] == "line1\nline2"
