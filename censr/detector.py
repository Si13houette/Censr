# -*- coding: utf-8 -*-
"""Детектор русского мата в распознанных словах.

Алгоритм для каждого слова:
  1. Нормализация (lowercase, ё→е, только буквы).
  2. Быстрая проверка паттернами (сильные корни-подстроки + якорные регулярки).
  3. Если совпало — лемма-оверрайд: слово известно pymorphy3 и ни одна его
     лемма не матчится паттернами → ложное срабатывание, пропускаем
     (страхуй→страховать, психуй→психовать).
  4. Если не совпало — опциональный fuzzy-матчинг для слов, неизвестных
     морфологии (искажения ASR: "блать", "пиздц").

Принцип: пропустить мат хуже, чем заглушить лишнее, но базовые паттерны
построены так, чтобы не трогать обычную речь (тебя/ребята/колебался/хуже).
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from functools import lru_cache

__all__ = ["ProfanityDetector", "Match", "prewarm"]


@dataclass(frozen=True)
class Match:
    word: str          # слово как распознано
    norm: str          # нормализованная форма
    reason: str        # "pattern" | "fuzzy"
    pattern: str       # какой паттерн/эталон сработал
    spans: tuple = ()  # позиции матных подстрок в norm (для CTC-склеек)


def _normalize(word: str) -> str:
    return re.sub(r"[^а-я]", "", word.lower().replace("ё", "е"))  # «е» уже входит в а-я


# --- Сильные корни: уникально-матные подстроки, безопасны в любом месте слова.
# Покрывают и склейки CTC без пробелов ("блятьпиздец").
_STRONG = re.compile(
    r"хуй|ху[яеи]в|[оа]ху[еи]|нахуя|похуист"   # ахуе: аканье ASR; охуи: ужасохуительный
    r"|пизд|пизж"
    r"|(?<![а-я])блят|бляд"                   # блят только с начала слова: не цеплять
    #                                           оскор-блять/осла-блять/усугу-блять/упо-треблять
    #                                           (склейки ловят др. корни и «бля-склейка»)
    r"|(?<!н)[её]бл[оаыу]"                    # ебло, ебла; (?<!н): не цеплять неблаг- (неблагоприятный)
    r"|залуп"
    r"|г[ао]ндон"
    r"|пид[оа]р"
    r"|муда[кчц]|мудил"                       # мудац-: к→ц у прилагательных (мудацкий)
    r"|долбо[ёе]б"
)

# --- Якорные паттерны: матчат слово целиком (с учётом продуктивных приставок).
_PREFIX = r"(?:за|на|до|по|у|вы|от[ьъ]?|об[ьъ]?|под[ьъ]?|пере|при|про|раз[ьъ]?|разо|с[ьъ]|в[ьъ]|из[ьъ]?|вз[ьъ]?|недо)"
_ANCHORED = [
    # еб-корень: либо в начале слова, либо после известной приставки
    re.compile(rf"^{_PREFIX}?[её]б[а-яе]*$"),
    # сложные слова с соединительной "о": долбоеб, мозгоеб, злоебучий
    re.compile(r"^[а-яе]{2,}о[её]б[а-яе]*$"),
    # хуй-корень с приставками: охуел, нахуя, дохуя, нихуя, схуяли, захуячить;
    # «ю» — датив/локатив «хую» (по хую, на хую) и приставочные (похую, нахую)
    re.compile(rf"^(?:{_PREFIX}|ни|не|о|с)?ху[яеию][а-яе]*$"),
    # бля как отдельное слово/междометие
    re.compile(r"^бля$"),
    # манда (но не мандарин/мандат/мандраж/мандолина)
    re.compile(r"^манд(?:а|е|ой|у|ы)$"),
    # елда
    re.compile(r"^елд[а-яе]+$"),
    # дрочить и производные
    re.compile(rf"^{_PREFIX}?дроч[а-яе]*$"),
]

# Эталонные формы для fuzzy (искажения ASR у неизвестных морфологии слов)
_FUZZY_CORE = [
    "блять", "бляди", "нахуй", "похуй", "пиздец", "пизда", "ебать", "ебал",
    "охуел", "хуйня", "заебал", "пиздато", "охуенно", "ебанутый", "мудак",
]
_FUZZY_MAX_DISTANCE = 1  # макс. расстояние Левенштейна до эталона

# Человекочитаемый список встроенных корней (для показа пользователю в GUI).
# Детектор ловит их по корню + морфологии + опечаткам, поэтому это основы,
# а не все словоформы (падежи, приставки и искажения учитываются сами).
BUILTIN_ROOTS = [
    "хуй", "пизда", "блядь", "бля", "ёб / ебать", "залупа", "гондон",
    "пидор", "мудак / мудила", "долбоёб", "манда", "елда", "дрочить",
]


def _matches_patterns(norm: str) -> str | None:
    m = _STRONG.search(norm)
    if m:
        return m.group(0)
    for rx in _ANCHORED:
        if rx.match(norm):
            return rx.pattern
    return None


def _strong_spans(norm: str) -> tuple:
    return tuple(m.span() for m in _STRONG.finditer(norm))


_ROOT_RX = re.compile(r"[её]б|ху[яеи]|дроч|манд|елд|бля")


def _anchored_span(norm: str) -> tuple:
    """Якорный матч: от начала слова до корня + 4 буквы (хвост склейки — не мат)."""
    m = _ROOT_RX.search(norm)
    if not m:
        return ()
    return ((0, min(m.end() + 4, len(norm))),)


_MORPH = None
_MORPH_LOCK = threading.Lock()


def _shared_morph():
    """Единый MorphAnalyzer на процесс. Его построение (загрузка словаря
    OpenCorpora) — самая дорогая часть инициализации детектора (~0.5–1.5 с),
    а раньше он строился заново на КАЖДЫЙ запуск обработки. parse() — это
    неизменяемые словарные запросы, поэтому один экземпляр безопасно
    переиспользовать между прогонами и потоками."""
    global _MORPH
    if _MORPH is None:
        with _MORPH_LOCK:
            if _MORPH is None:
                import pymorphy3  # noqa: PLC0415
                _MORPH = pymorphy3.MorphAnalyzer()
    return _MORPH


def prewarm() -> None:
    """Построить словарь заранее (фоновая предзагрузка в GUI), чтобы первый
    «Начать» не ждал pymorphy3."""
    _shared_morph()


class ProfanityDetector:
    def __init__(self, *, use_morphology: bool = True, use_fuzzy: bool = True,
                 extra_words: set[str] | None = None, whitelist: set[str] | None = None):
        """extra_words — свой список запрещённых слов (точные нормализованные формы).

        whitelist — слова, которые никогда не глушим.

        Списки учитывают морфологию: «дебил» в extra_words ловит и «дебилы»,
        слово в whitelist исключает все свои словоформы.
        """
        self.use_fuzzy = use_fuzzy
        self._morph = None
        if use_morphology:
            self._morph = _shared_morph()
        if use_fuzzy:
            from rapidfuzz.distance import Levenshtein  # noqa: PLC0415
            self._lev = Levenshtein
        self.extra = self._expand({_normalize(w) for w in (extra_words or set())})
        self.whitelist = self._expand({_normalize(w) for w in (whitelist or set())})
        # кэш на экземпляре: умирает вместе с детектором (self не попадает в ключ)
        self.check = lru_cache(maxsize=65536)(self._check)

    def _lemmas(self, norm: str) -> set:
        """Само слово + все его нормальные формы (для морфо-сопоставления словарей)."""
        if not norm or self._morph is None:
            return {norm} if norm else set()
        out = {norm}
        for p in self._morph.parse(norm):
            out.add(_normalize(p.normal_form))
        return out

    def _expand(self, norms: set) -> set:
        out: set = set()
        for w in norms:
            out |= self._lemmas(w)
        return out

    def _check(self, word: str) -> Match | None:
        """Проверка одного слова. None — слово чистое."""
        norm = _normalize(word)
        if not norm:
            return None
        lemmas = self._lemmas(norm) if (self.whitelist or self.extra) else None
        if norm in self.whitelist or (lemmas is not None and lemmas & self.whitelist):
            return None
        if norm in self.extra or (lemmas is not None and lemmas & self.extra):
            return Match(word, norm, "pattern", "extra_words", ((0, len(norm)),))

        pat = _matches_patterns(norm)
        if pat is not None:
            # лемма-оверрайд: известное слово, чьи леммы чисты → ложное срабатывание
            if self._morph is not None and self._lemma_override(norm):
                return None
            return Match(word, norm, "pattern", pat,
                         _strong_spans(norm) or _anchored_span(norm))

        # склейки CTC с "бля" внутри ("говоритьбля"): неизвестное словарю слово
        # с "бля"-подстрокой; известные (рубля, корабля) сюда не попадают
        if len(norm) > 4 and "бля" in norm and not self._is_known(norm):
            i = norm.find("бля")
            return Match(word, norm, "pattern", "бля-склейка", ((i, i + 3),))

        if self.use_fuzzy and len(norm) >= 4 and not self._is_known(norm):
            # строго 1 правка: дистанция 2 давала ложные ("бопять"="опять",
            # "зачекал", "срать") — приоритет пользователя: минимум ложных
            for core in _FUZZY_CORE:
                if self._lev.distance(norm, core, score_cutoff=_FUZZY_MAX_DISTANCE) <= _FUZZY_MAX_DISTANCE:
                    return Match(word, norm, "fuzzy", core)
        return None

    def _is_known(self, norm: str) -> bool:
        if self._morph is None:
            return False
        return self._morph.word_is_known(norm)

    def _lemma_override(self, norm: str) -> bool:
        """True, если у слова есть хотя бы одна известная словарю чистая лемма.

        (страхуй→страховать, гребло→грести; у мата чистых лемм нет: хуем→хуй)
        """
        parses = self._morph.parse(norm)
        known = [p for p in parses if p.is_known]
        if not known:
            return False
        return any(_matches_patterns(_normalize(p.normal_form)) is None for p in known)
