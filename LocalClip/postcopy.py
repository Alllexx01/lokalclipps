"""Fertige Bildunterschriften (Beschreibung + Hashtags) für Posts.

Rein lokal und kostenlos: aus Titel, Hook-Satz und Wortlaut des Clips werden mehrere
Varianten mit passenden Hashtags gebaut. Kein Sprachmodell, keine Cloud-API.
"""
from __future__ import annotations

import re
from collections import Counter

STOPWORDS = {
    # Deutsch
    "aber", "alle", "allem", "alles", "also", "auch", "auf", "aus", "bei", "beim", "bin", "bist", "damit", "dann",
    "dass", "dein", "deine", "denn", "der", "die", "das", "dem", "den", "des", "dich", "dir", "doch", "dort",
    "durch", "eine", "einem", "einen", "einer", "eines", "einfach", "etwas", "euch", "für", "gar", "geht", "gibt",
    "habe", "haben", "hast", "hat", "hatte", "hier", "ihr", "ihre", "immer", "ist", "ja", "jetzt", "kann",
    "kannst", "kein", "keine", "machen", "macht", "mal", "man", "mehr", "mein", "meine", "mich", "mir", "mit",
    "muss", "nach", "nicht", "noch", "nur", "oder", "ohne", "schon", "sehr", "sein", "seine", "sich", "sie",
    "sind", "so", "sollte", "und", "uns", "unser", "von", "vor", "war", "waren", "was", "weil", "weiss", "weiß",
    "wenn", "wer", "werde", "werden", "wie", "wieder", "will", "wir", "wird", "wirklich", "wollen", "wurde",
    "zum", "zur", "über", "dieser", "diese", "dieses", "genau", "eigentlich", "ganz", "gut", "ding", "dinge",
    "leute", "halt", "irgendwie", "quasi", "okay", "ähm", "naja", "mhm", "dazu", "davon", "dafür", "darum",
    # Englisch
    "about", "after", "again", "all", "also", "and", "are", "because", "been", "but", "can", "could", "did",
    "does", "for", "from", "get", "had", "has", "have", "here", "how", "into", "just", "like", "more", "not",
    "now", "one", "only", "our", "out", "really", "she", "that", "the", "their", "them", "then", "there",
    "these", "they", "this", "was", "were", "what", "when", "which", "who", "will", "with", "would", "you", "your",
}

NICHES = {
    "gaming": ("game", "gaming", "zocken", "spiel", "gameplay", "level", "boss", "stream", "ranked", "fortnite",
               "minecraft", "valorant", "fifa", "ea fc", "gta", "cod", "warzone", "controller", "noob", "clutch"),
    "podcast": ("podcast", "folge", "interview", "gast", "gespräch", "diskussion", "host", "episode"),
    "comedy": ("lustig", "witz", "comedy", "lachen", "haha", "peinlich", "sketch", "funny", "joke"),
    "reaction": ("reaction", "reagiere", "reaktion", "react", "krass", "unglaublich", "schockiert"),
}
NICHE_TAGS = {
    "de": {
        "gaming": ["gaming", "zocken", "gamingdeutsch"],
        "podcast": ["podcast", "podcastdeutsch", "interview"],
        "comedy": ["comedy", "lustig", "humor"],
        "reaction": ["reaction", "reaktion", "krass"],
    },
    "en": {
        "gaming": ["gaming", "gamer", "gameplay"],
        "podcast": ["podcast", "interview", "podcasts"],
        "comedy": ["comedy", "funny", "humor"],
        "reaction": ["reaction", "react", "reacts"],
    },
}

TEXT = {
    "de": {
        "question": "Wie seht ihr das? 👇",
        "teaser": "Das musst du gesehen haben 👀",
        "follow": "Folge für mehr!",
        "names": ("Frage an die Community", "Teaser", "Kurz und knapp"),
    },
    "en": {
        "question": "What do you think? 👇",
        "teaser": "You need to see this 👀",
        "follow": "Follow for more!",
        "names": ("Question for the community", "Teaser", "Short and simple"),
    },
}


def _clean_tag(word: str) -> str:
    return re.sub(r"[^0-9a-zA-ZÄÖÜäöüß]", "", word).lower()[:24]


def keywords(text: str, limit: int = 3) -> list[str]:
    """Wichtigste Themenwörter. Im Deutschen sind das die mitten im Satz großgeschriebenen Hauptwörter
    (Adjektive wie "richtig" ergeben schlechte Hashtags); sonst die häufigsten längeren Wörter."""
    nouns: Counter = Counter()
    general: Counter = Counter()
    for match in re.finditer(r"[A-Za-zÄÖÜäöüß]{4,}", text):
        word = match.group(0)
        lower = word.lower()
        if lower in STOPWORDS:
            continue
        start = match.start()
        mid_sentence = start > 1 and not re.search(r"[.!?]\s*$", text[:start])
        if word[0].isupper() and mid_sentence:
            nouns[lower] += 1
        if len(word) >= 5:
            general[lower] += 1
    pool = nouns if nouns else general
    ranked = sorted(pool.items(), key=lambda item: (-item[1], -len(item[0])))
    return [word for word, _ in ranked[:limit]]


def detect_niche(text: str) -> str | None:
    lower = text.lower()
    scores = {niche: sum(lower.count(term) for term in terms) for niche, terms in NICHES.items()}
    niche, score = max(scores.items(), key=lambda item: item[1])
    return niche if score else None


def hashtags(text: str, limit: int = 5, language: str = "de") -> list[str]:
    """Wenige, passende Hashtags statt Massen: Thema, Nische und ein Reichweiten-Tag."""
    tags: list[str] = []
    for word in keywords(text, 2):
        tag = _clean_tag(word)
        if tag and tag not in tags:
            tags.append(tag)
    niche = detect_niche(text)
    if niche:
        tags.extend(tag for tag in NICHE_TAGS[language][niche][:2] if tag not in tags)
    for fallback in ("fyp", "shorts"):
        if len(tags) < limit and fallback not in tags:
            tags.append(fallback)
    return tags[:limit]


def _short(text: str, limit: int = 60) -> str:
    text = re.sub(r"\s+", " ", text).strip(" -–—,.")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def build_variants(title: str, transcript: str, hook: str = "", language: str = "de") -> list[dict]:
    """Drei fertige Bildunterschriften mit unterschiedlichem Ziel (Kommentare, Neugier, Klarheit)."""
    language = "en" if str(language).lower().startswith("en") else "de"
    copy = TEXT[language]
    base = f"{title} {transcript}"
    tags = hashtags(base, 5, language)
    tag_line = " ".join(f"#{tag}" for tag in tags)
    short_tags = " ".join(f"#{tag}" for tag in tags[:3])
    lead = _short(hook or title, 90)
    if not lead.endswith(("?", "!", ".", "…")):
        lead += "…" if lead.count(" ") > 3 else ""
    headline = _short(title or hook, 60)
    return [
        {"name": copy["names"][0], "text": f"{lead}\n\n{copy['question']}\n\n{tag_line}"},
        {"name": copy["names"][1], "text": f"{copy['teaser']}\n{headline}\n\n{copy['follow']}\n\n{tag_line}"},
        {"name": copy["names"][2], "text": f"{headline} {short_tags}".strip()},
    ]
