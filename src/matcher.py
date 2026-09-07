#!/usr/bin/env python3
"""Smart Title Matching (Steam <-> Twitch) — Phase 2.

Normalizes titles (strips ™, ®, publisher prefixes, leading articles, punctuation)
and handles fuzzy/token-set matching + gaming alias maps so Steam library titles
seamlessly match Twitch directory names. Stdlib only.
"""
import re

# Common gaming aliases: maps varied names to a canonical root key
ALIASES = {
    "cs2": "counter strike",
    "counter strike 2": "counter strike",
    "counter strike global offensive": "counter strike",
    "csgo": "counter strike",
    "pubg battlegrounds": "pubg",
    "playerunknowns battlegrounds": "pubg",
    "call of duty warzone": "call of duty",
    "call of duty warzone 2": "call of duty",
    "cod warzone": "call of duty",
    "grand theft auto v legacy": "grand theft auto 5",
    "grand theft auto v": "grand theft auto 5",
    "gta v": "grand theft auto 5",
    "gta 5": "grand theft auto 5",
    "tom clancys rainbow six siege": "rainbow six siege",
    "rainbow six siege": "rainbow six siege",
    "tom clancys the division 2": "the division 2",
    "the division 2": "the division 2",
    "dying light the beast": "dying light",
    "dying light 2 stay human": "dying light",
    "dying light 2": "dying light",
    "path of exile 2": "path of exile",
    "the elder scrolls online": "elder scrolls online",
}

# Prefixes to strip (publishers, franchises)
PREFIXES = [
    r"^tom clancy's\s+",
    r"^tom clancys\s+",
    r"^sid meier's\s+",
    r"^sid meiers\s+",
    r"^warhammer\s*40,000:\s*",
    r"^warhammer\s*40000:\s*",
    r"^warhammer:\s*",
    r"^the elder scrolls:\s*",
    r"^the elder scrolls\s+",
]

ROMAN_NUMERALS = {
    " ii": " 2",
    " iii": " 3",
    " iv": " 4",
    " v": " 5",
    " vi": " 6",
    " vii": " 7",
}


def clean_title(s: str) -> str:
    """Normalize a game title for robust matching."""
    if not s:
        return ""
    # Lowercase & strip
    s = s.lower().strip()

    # Remove trademark, registered, copyright symbols
    s = re.sub(r"[™®©]", "", s)

    # Normalize hyphens and dashes to spaces
    s = s.replace("-", " ").replace("–", " ").replace("—", " ")

    # Replace common unicode quotes
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')

    # Strip known publisher prefixes
    for pat in PREFIXES:
        s = re.sub(pat, "", s, flags=re.IGNORECASE)

    # Strip leading "the "
    if s.startswith("the "):
        s = s[4:].strip()

    # Normalize Roman numerals
    for r, n in ROMAN_NUMERALS.items():
        if s.endswith(r):
            s = s[:-len(r)] + n
        s = s.replace(r + ":", n + ":").replace(r + " ", n + " ")

    # Strip trailing subtitles after ' - ' or ' : ' if any (e.g. "Game: Subtitle")
    s = re.sub(r"\s*[:]\s+.*$", "", s)

    # Remove remaining punctuation except alphanumeric & spaces
    s = re.sub(r"[^\w\s]", "", s)

    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def base_root(s: str) -> str:
    """Strip trailing version/sequel digit from cleaned title (e.g. 'overwatch 2' -> 'overwatch')."""
    cleaned = clean_title(s)
    root = re.sub(r"\s+\d+$", "", cleaned).strip()
    return root if root else cleaned


def canonical_alias(name: str) -> str:
    """Return alias map canonical name if present, else cleaned name."""
    cleaned = clean_title(name)
    if cleaned in ALIASES:
        return ALIASES[cleaned]
    # Check base root in aliases
    root = base_root(name)
    if root in ALIASES:
        return ALIASES[root]
    return cleaned


def matches_game(name_a: str, name_b: str) -> bool:
    """Determine whether two game titles refer to the same game."""
    if not name_a or not name_b:
        return False

    # Exact match
    if name_a.strip().lower() == name_b.strip().lower():
        return True

    # Alias match
    canon_a = canonical_alias(name_a)
    canon_b = canonical_alias(name_b)
    if canon_a and canon_b and canon_a == canon_b:
        return True

    # Cleaned match
    clean_a = clean_title(name_a)
    clean_b = clean_title(name_b)
    if clean_a and clean_b and clean_a == clean_b:
        return True

    # Base root match (sequel matching, e.g. Overwatch vs Overwatch 2)
    root_a = base_root(name_a)
    root_b = base_root(name_b)
    if root_a and root_b and len(root_a) >= 4 and len(root_b) >= 4 and root_a == root_b:
        return True

    # Token-subset match
    tokens_a = set(clean_a.split())
    tokens_b = set(clean_b.split())
    if tokens_a and tokens_b:
        min_set = tokens_a if len(tokens_a) <= len(tokens_b) else tokens_b
        max_set = tokens_b if len(tokens_a) <= len(tokens_b) else tokens_a
        # If smaller set has >= 2 tokens and is fully contained in larger set
        if len(min_set) >= 2 and min_set.issubset(max_set):
            return True
        # If smaller set has 1 token but is >= 5 chars (e.g. 'warframe' in 'warframe lotus')
        if len(min_set) == 1 and list(min_set)[0] in max_set and len(list(min_set)[0]) >= 5:
            return True

    return False


def is_favorite_match(game_name: str, favorites: set[str] | list[str]) -> bool:
    """Check if a Twitch game matches any game in the favorites set."""
    if not game_name or not favorites:
        return False
    if game_name in favorites:
        return True
    for fav in favorites:
        if matches_game(game_name, fav):
            return True
    return False


def is_owned_match(game_name: str, steam_games: set[str] | list[str]) -> bool:
    """Check if a Twitch game matches any game in the Steam owned set."""
    if not game_name or not steam_games:
        return False
    if game_name in steam_games:
        return True
    for owned in steam_games:
        if matches_game(game_name, owned):
            return True
    return False
