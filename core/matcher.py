"""Fuzzy-матчинг треков через rapidfuzz."""

import re
from typing import Dict, List, Optional

from rapidfuzz import fuzz, process

from utils.logger import get_logger

log = get_logger("matcher")

_RE_BRACKETS = re.compile(r"\(.*?\)")
_RE_FEAT = re.compile(r"\s*(?:feat\.?|ft\.?|featuring|prod\.?|produced by)\s+.*", re.I)
_RE_COLLAB = re.compile(r"\s*(?:x|&|vs\.?|with)\s+.*", re.I)


def _normalize(text: str) -> str:
    """Нормализует строку для fuzzy-сравнения."""
    text = text.lower().replace("ё", "е")
    text = _RE_BRACKETS.sub("", text)
    text = _RE_FEAT.sub("", text)
    text = _RE_COLLAB.sub("", text)
    text = text.replace(",", " ")
    return " ".join(text.split())


def _score_match(query_artist: str, query_title: str, cand_artist: str, cand_title: str) -> float:
    """
    Вычисляет score матча с учётом совпадения и artist, и title.

    Возвращает взвешенный score: 0.4 * artist_score + 0.6 * title_score.
    Это предотвращает ложные матчи когда artist совпадает, а title — нет.
    """
    qa = _normalize(query_artist)
    qt = _normalize(query_title)
    ca = _normalize(cand_artist)
    ct = _normalize(cand_title)

    artist_score = fuzz.token_set_ratio(qa, ca)
    title_score = fuzz.token_set_ratio(qt, ct)

    return 0.4 * artist_score + 0.6 * title_score


def match_track(
    artist: str,
    title: str,
    candidates: List[Dict[str, str]],
    threshold: int = 70,
) -> Optional[Dict[str, str]]:
    """
    Находит лучшее совпадение среди кандидатов.

    Сначала пробует weighted scorer (artist + title отдельно),
    затем fallback на token_set_ratio по полной строке.

    Args:
        artist: Артист из VK
        title: Название из VK
        candidates: Список словарей с ключами 'artist' и 'title'
        threshold: Минимальный порог совпадения (0-100)

    Returns:
        Лучший кандидат или None
    """
    if not candidates:
        return None

    # 1. Weighted scorer: artist и title отдельно
    best_score = 0
    best_idx = -1
    for i, c in enumerate(candidates):
        score = _score_match(artist, title, c["artist"], c["title"])
        if score > best_score:
            best_score = score
            best_idx = i

    if best_idx >= 0 and best_score >= threshold:
        log.info(
            "match_found",
            query=f"{artist} - {title}",
            match=f"{candidates[best_idx]['artist']} - {candidates[best_idx]['title']}",
            score=round(best_score, 1),
        )
        result = candidates[best_idx].copy()
        result["score"] = best_score
        return result

    # 2. Fallback: token_set_ratio по полной строке
    query = _normalize(f"{artist} - {title}")
    candidate_strings = [
        _normalize(f"{c['artist']} - {c['title']}")
        for c in candidates
    ]
    best2 = process.extractOne(
        query,
        candidate_strings,
        scorer=fuzz.token_set_ratio,
    )
    if best2 and best2[1] >= threshold:
        idx = candidate_strings.index(best2[0])
        log.info(
            "match_found_fallback",
            query=query,
            match=candidate_strings[idx],
            score=best2[1],
        )
        result = candidates[idx].copy()
        result["score"] = best2[1]
        return result

    log.info("match_not_found", query=f"{artist} - {title}", best_score=round(best_score, 1))
    return None
