"""Fuzzy-матчинг треков через rapidfuzz."""

from typing import Dict, List, Optional

from rapidfuzz import fuzz, process

from utils.logger import get_logger

log = get_logger("matcher")


def match_track(
    artist: str,
    title: str,
    candidates: List[Dict[str, str]],
    threshold: int = 85,
) -> Optional[Dict[str, str]]:
    """
    Находит лучшее совпадение среди кандидатов.
    
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

    query = f"{artist} - {title}".lower().strip()
    candidate_strings = [
        f"{c['artist']} - {c['title']}".lower().strip()
        for c in candidates
    ]

    best = process.extractOne(
        query,
        candidate_strings,
        scorer=fuzz.token_sort_ratio,
    )

    if best and best[1] >= threshold:
        idx = candidate_strings.index(best[0])
        log.info(
            "match_found",
            query=query,
            match=candidate_strings[idx],
            score=best[1],
        )
        return candidates[idx]

    log.info("match_not_found", query=query, best_score=best[1] if best else 0)
    return None
