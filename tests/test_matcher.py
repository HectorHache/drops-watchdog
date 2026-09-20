#!/usr/bin/env python3
"""Unit tests: title normalization, root/alias canonicalization, matching matrix."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import matcher as mt


class TestCleanTitle(unittest.TestCase):
    def test_trademark_symbols_removed(self):
        self.assertEqual(mt.clean_title("Apex Legends™"), "apex legends")
        self.assertEqual(mt.clean_title("Tom Clancy's The Division® 2"), "division 2")
        self.assertEqual(mt.clean_title("FIFA©"), "fifa")

    def test_dashes_normalized(self):
        self.assertEqual(mt.clean_title("Counter-Strike 2"), "counter strike 2")
        self.assertEqual(mt.clean_title("Horizon – Zero Dawn"), "horizon zero dawn")
        self.assertEqual(mt.clean_title("Word — Long Dash"), "word long dash")

    def test_unicode_quotes_normalized(self):
        self.assertEqual(mt.clean_title("Dying Light ’The Beast’"), "dying light the beast")
        self.assertEqual(mt.clean_title("“Quoted” Game"), "quoted game")

    def test_publisher_prefixes_stripped(self):
        self.assertEqual(mt.clean_title("Tom Clancy's Rainbow Six Siege"), "rainbow six siege")
        self.assertEqual(mt.clean_title("Sid Meier's Civilization"), "civilization")
        self.assertEqual(mt.clean_title("Warhammer 40,000: Space Marine"), "space marine")
        self.assertEqual(mt.clean_title("Warhammer: Vermintide"), "vermintide")
        self.assertEqual(mt.clean_title("The Elder Scrolls V: Skyrim"), "skyrim")

    def test_leading_article_stripped(self):
        self.assertEqual(mt.clean_title("The Witcher"), "witcher")
        self.assertEqual(mt.clean_title("The The Last of Us"), "the last of us")

    def test_roman_numerals_normalized(self):
        self.assertEqual(mt.clean_title("Overwatch II"), "overwatch 2")
        self.assertEqual(mt.clean_title("Diablo IV"), "diablo 4")
        self.assertEqual(mt.clean_title("Final Fantasy VII"), "final fantasy 7")
        self.assertEqual(mt.clean_title("Civilization VI"), "civilization 6")
        # roman numeral inside a word must NOT be rewritten
        self.assertEqual(mt.clean_title("Civilization"), "civilization")

    def test_subtitle_after_colon_stripped(self):
        self.assertEqual(mt.clean_title("Dying Light: The Beast"), "dying light")
        self.assertEqual(mt.clean_title("Halo: Infinite"), "halo")

    def test_punctuation_and_whitespace(self):
        self.assertEqual(mt.clean_title("  Grand   Theft  Auto  V!  "), "grand theft auto 5")
        self.assertEqual(mt.clean_title("Genshin Impact!"), "genshin impact")
        self.assertEqual(mt.clean_title(""), "")

    def test_bare_numeral_colon_after_franchise_prefix(self):
        # regression: prefix strip left a bare "v" that collapsed to "v" alone
        self.assertEqual(mt.clean_title("The Elder Scrolls V: Skyrim"), "skyrim")
        self.assertEqual(mt.clean_title("Battlefield V: Definitive Edition"), "battlefield 5")
        self.assertTrue(mt.matches_game("The Elder Scrolls V: Skyrim", "Skyrim"))

    def test_trailing_punctuation_does_not_shadow_numeral(self):
        # regression: a trailing "!" blocked the roman-numeral pass
        self.assertEqual(mt.clean_title("Grand Theft Auto V!"), "grand theft auto 5")
        self.assertTrue(mt.matches_game("Grand Theft Auto V!", "GTA 5"))

    def test_base_root_strips_trailing_digits(self):
        self.assertEqual(mt.base_root("Overwatch 2"), "overwatch")
        self.assertEqual(mt.base_root("FIFA 24"), "fifa")
        # no trailing digit -> cleaned title unchanged
        self.assertEqual(mt.base_root("Warframe"), "warframe")

    def test_canonical_alias_map(self):
        self.assertEqual(mt.canonical_alias("CS2"), "counter strike")
        self.assertEqual(mt.canonical_alias("GTA V"), "grand theft auto 5")
        self.assertEqual(mt.canonical_alias("PUBG: Battlegrounds"), "pubg")
        # unmapped names fall through to cleaned form
        self.assertEqual(mt.canonical_alias("Rust"), "rust")


class TestMatchesGame(unittest.TestCase):
    def test_exact_case_insensitive(self):
        self.assertTrue(mt.matches_game("Warframe", "WARFRAME"))
        self.assertTrue(mt.matches_game("  Rust  ", "rust"))

    def test_alias_pairs(self):
        self.assertTrue(mt.matches_game("Counter-Strike 2", "Counter-Strike"))
        self.assertTrue(mt.matches_game("CSGO", "Counter Strike"))
        self.assertTrue(mt.matches_game("PlayerUnknown's Battlegrounds", "PUBG"))
        self.assertTrue(mt.matches_game("COD: Warzone", "Call of Duty"))
        self.assertTrue(mt.matches_game("COD", "Call of Duty"))
        self.assertTrue(mt.matches_game("Rainbow Six Siege", "Tom Clancy's Rainbow Six Siege"))

    def test_sequel_via_base_root(self):
        self.assertTrue(mt.matches_game("Overwatch", "Overwatch 2"))
        self.assertTrue(mt.matches_game("Dying Light", "Dying Light 2 Stay Human"))
        self.assertTrue(mt.matches_game("Path of Exile 2", "Path of Exile"))

    def test_token_subset(self):
        # smaller set fully contained (>= 2 tokens)
        self.assertTrue(mt.matches_game("Call of Duty", "Call of Duty: Black Ops"))
        # single token >= 5 chars contained
        self.assertTrue(mt.matches_game("Warframe", "Warframe Lotus"))

    def test_short_single_token_not_absorbed(self):
        # 1 token but too short — must NOT match (avoids 'Rust' vs 'Rust Racer' noise)
        self.assertFalse(mt.matches_game("Rust", "Rust Racer"))
        self.assertFalse(mt.matches_game("Poe", "Path of Exile"))

    def test_distinct_games_do_not_match(self):
        self.assertFalse(mt.matches_game("Starfield", "Starbound"))
        self.assertFalse(mt.matches_game("Valorant", "Counter-Strike"))
        self.assertFalse(mt.matches_game("Grand Theft Auto", "Grand Turismo"))

    def test_empty_inputs(self):
        self.assertFalse(mt.matches_game("", "Rust"))
        self.assertFalse(mt.matches_game("Rust", ""))
        self.assertFalse(mt.matches_game(None, "Rust"))


class TestFavoriteOwnedMatch(unittest.TestCase):
    def test_direct_membership(self):
        self.assertTrue(mt.is_favorite_match("Warframe", {"Warframe"}))
        self.assertTrue(mt.is_owned_match("Rust", ["Rust", "CS2"]))

    def test_alias_and_sequel_matching(self):
        favs = {"Counter-Strike"}
        self.assertTrue(mt.is_favorite_match("Counter-Strike 2", favs))
        self.assertTrue(mt.is_favorite_match("CSGO", favs))
        owned = {"Dying Light"}
        self.assertTrue(mt.is_owned_match("Dying Light 2 Stay Human", owned))

    def test_non_matching(self):
        self.assertFalse(mt.is_favorite_match("Valorant", {"Warframe", "Rust"}))
        self.assertFalse(mt.is_favorite_match("Counter-Strike", set()))

    def test_empty_arguments(self):
        self.assertFalse(mt.is_favorite_match("", {"Rust"}))
        self.assertFalse(mt.is_favorite_match("Rust", []))
        self.assertFalse(mt.is_owned_match(None, {"Rust"}))


if __name__ == "__main__":
    unittest.main()