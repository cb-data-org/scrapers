"""Is this shop that venue? The same bar under two names ("BAR E RESTAURANTE DO ZE LTDA", "Bar do Zé") at two
nearby points (a receipt's address is geocoded, so tens to a few hundred metres off): the venue whose
distinctive name words match best, close enough.
"""

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from .core import normalize


def word_set(text: str) -> frozenset[str]:
    return frozenset(text.split())


#: Words in company names that don't say which business it is.
LEGAL = word_set(
    "ltda me epp eireli sa s a cia comercio comercial servicos alimentos alimentacao bebidas "
    "refeicoes industria empreendimentos participacoes eventos gastronomia gastronomicos gastronomica "
    "representacoes filial matriz unidade loja"
)
CONNECTORS = word_set("e de da do das dos du o a os as the and y")
#: Words that many bars share.
GENERIC = word_set(
    "bar bares boteco buteco botequim restaurante restaurant lanchonete lanches pub choperia "
    "chopperia chopp chope cervejaria brewpub pizzaria petiscaria espetinho espetaria churrascaria "
    "cafe cafeteria bistro grill cozinha gastrobar beer beers house lounge club clube point ponto "
    "espaco salvador bahia ba"
)
EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True, slots=True)
class Candidate:
    name: str
    lat: float
    lng: float
    data: Mapping[str, Any]


def distinctive_words(name: str, ignore: Iterable[str] = ()) -> set[str]:
    """ "BAR E RESTAURANTE DO ZÉ LTDA" → {"ze"}; long numbers (tax ids) are dropped too."""
    skip = LEGAL | CONNECTORS | GENERIC | set(ignore)
    return {
        word
        for word in re.findall(r"[a-z0-9]+", normalize(name))
        if word not in skip and not (word.isdigit() and len(word) >= 6)
    }


def _same(a: str, b: str) -> bool:
    return a == b or (min(len(a), len(b)) >= 5 and SequenceMatcher(None, a, b).ratio() >= 0.85)


def _words(a: str, b: str, ignore: Iterable[str]) -> tuple[set[str], set[str]]:
    """Both names' distinctive words; place names count when ignoring them would leave a name without any
    ("Espetinho da Barra")."""
    words_a, words_b = distinctive_words(a, ignore), distinctive_words(b, ignore)
    if not words_a or not words_b:
        words_a, words_b = distinctive_words(a), distinctive_words(b)
    return words_a, words_b


def similarity(a: str, b: str, ignore: Iterable[str] = ()) -> float:
    """Dice coefficient over distinctive words, small typos forgiven: 1 for the same words, 0 for none."""
    words_a, words_b = _words(a, b, ignore)
    if not words_a or not words_b:
        return 0.0
    shared = sum(1 for word in words_a if any(_same(word, other) for other in words_b))
    return min(1.0, 2 * shared / (len(words_a) + len(words_b)))


def contained(a: str, b: str, ignore: Iterable[str] = ()) -> bool:
    """One name's distinctive words, each of at least 5 letters, all in the other's: "Boteco do Caranguejo"
    and "Boteco do Caranguejo Itaigara"."""
    smaller, larger = sorted(_words(a, b, ignore), key=len)
    return (
        bool(smaller)
        and all(len(word) >= 5 for word in smaller)
        and all(any(_same(word, other) for other in larger) for word in smaller)
    )


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    half = (
        math.sin((phi2 - phi1) / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(half))


def best_match(
    names: Sequence[str],
    lat: float,
    lng: float,
    candidates: Iterable[Candidate],
    *,
    max_m: float = 400.0,
    min_score: float = 0.8,
    near_m: float = 60.0,
    ignore: Iterable[str] = (),
) -> Candidate | None:
    """The candidate within `max_m` whose name is most like any of `names` (at least `min_score`), the nearest
    on a tie; within `near_m`, a name contained in the other's is enough. None when nothing is close enough in
    both senses."""
    ignore = set(ignore)
    best: Candidate | None = None
    best_key: tuple[float, float] | None = None
    for candidate in candidates:
        metres = distance_m(lat, lng, candidate.lat, candidate.lng)
        if metres > max_m:
            continue
        score = max(similarity(name, candidate.name, ignore) for name in names)
        if (
            score < min_score
            and metres <= near_m
            and any(contained(n, candidate.name, ignore) for n in names)
        ):
            score = min_score
        if score < min_score:
            continue
        key = (score, -metres)
        if best_key is None or key > best_key:
            best, best_key = candidate, key
    return best
