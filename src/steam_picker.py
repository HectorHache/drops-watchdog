#!/usr/bin/env python3
"""Interactive Terminal UI for Mick's Steam Library & Drops Favorites.

Provides an in-CLI interactive session to browse played Steam games, search,
toggle/batch-select favorites, auto-seed by hours, and sync changes to drops.db
and the public drops.hector.app site. Stdlib only.
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import db as dbm


# ANSI Color Codes
CLR_RESET = "\033[0m"
CLR_BOLD = "\033[1m"
CLR_DIM = "\033[2m"
CLR_PURPLE = "\033[38;5;135m"
CLR_PURPLE_BOLD = "\033[1;38;5;135m"
CLR_CYAN = "\033[38;5;51m"
CLR_GREEN = "\033[38;5;84m"
CLR_GOLD = "\033[38;5;220m"
CLR_AMBER = "\033[38;5;214m"
CLR_RED = "\033[38;5;203m"
CLR_BG_DARK = "\033[48;5;235m"


def clear_term():
    """Clear terminal if supported."""
    if sys.stdout.isatty():
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()


def parse_index_expression(expr: str, max_val: int) -> list[int]:
    """Parse expressions like '1,3,5', '1-5', '1 2 4', 'all' into 1-based indices."""
    expr = expr.strip()
    if not expr:
        return []
    if expr.lower() == "all":
        return list(range(1, max_val + 1))
    
    # split on commas and spaces
    tokens = re.split(r"[\s,]+", expr)
    indices = set()
    for tok in tokens:
        if not tok:
            continue
        if "-" in tok:
            parts = tok.split("-", 1)
            try:
                start = int(parts[0])
                end = int(parts[1])
                for idx in range(min(start, end), max(start, end) + 1):
                    if 1 <= idx <= max_val:
                        indices.add(idx)
            except ValueError:
                continue
        else:
            try:
                idx = int(tok)
                if 1 <= idx <= max_val:
                    indices.add(idx)
            except ValueError:
                continue
    return sorted(indices)


class SteamFavoritesPicker:
    def __init__(self, conn, page_size: int = 20, auto_push: bool = True):
        self.conn = conn
        self.page_size = page_size
        self.auto_push = auto_push
        self.all_games = []
        self.filtered_games = []
        self.current_favs = set()
        self.initial_favs = set()
        self.current_page = 1
        self.search_filter = ""
        self.view_mode = "played"  # 'played', 'all', 'favs'
        self.reload_data()

    def reload_data(self):
        """Load played Steam games and current favorites from DB."""
        # Load favorites
        fav_rows = self.conn.execute("SELECT game_name FROM favorites").fetchall()
        self.current_favs = {r["game_name"] for r in fav_rows}
        self.initial_favs = set(self.current_favs)

        # Load steam games
        min_mins = 1  # only games played at least 1 minute
        rows = dbm.get_steam_played_games(self.conn, "mick", min_minutes=min_mins)
        self.all_games = []
        for r in rows:
            self.all_games.append({
                "appid": r["appid"],
                "name": r["name"],
                "hours": round(r["playtime_forever_min"] / 60.0, 1),
                "hours_2weeks": round(r["playtime_2weeks_min"] / 60.0, 1),
            })
        self.apply_filter()

    def apply_filter(self):
        """Apply view_mode and search query to all_games."""
        query = self.search_filter.lower().strip()
        filtered = []
        for g in self.all_games:
            name_lower = g["name"].lower()
            is_fav = g["name"] in self.current_favs

            if self.view_mode == "favs" and not is_fav:
                continue

            if query and query not in name_lower:
                continue

            filtered.append(g)

        self.filtered_games = filtered
        max_page = max(1, (len(self.filtered_games) + self.page_size - 1) // self.page_size)
        if self.current_page > max_page:
            self.current_page = max_page

    @property
    def total_pages(self) -> int:
        return max(1, (len(self.filtered_games) + self.page_size - 1) // self.page_size)

    def page_items(self) -> list[tuple[int, dict]]:
        """Return (1-based global index, game dict) for the current page."""
        start = (self.current_page - 1) * self.page_size
        end = start + self.page_size
        return list(enumerate(self.filtered_games[start:end], start=start + 1))

    def render(self):
        """Render the beautiful terminal UI."""
        total_played = len(self.all_games)
        total_favs = len(self.current_favs)
        dirty_flag = " ✦ [Unsaved Changes]" if self.current_favs != self.initial_favs else ""

        print()
        print(f"{CLR_PURPLE_BOLD}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓{CLR_RESET}")
        print(f"{CLR_PURPLE_BOLD}┃{CLR_RESET}  🎮 {CLR_BOLD}MICK'S STEAM FAVORITES SELECTOR{CLR_RESET} ── {CLR_CYAN}drops.hector.app{CLR_RESET}        {CLR_PURPLE_BOLD}┃{CLR_RESET}")
        print(f"{CLR_PURPLE_BOLD}┣━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┫{CLR_RESET}")
        status_line = (f"  Total Played: {CLR_BOLD}{total_played}{CLR_RESET}  │  "
                       f"Favorites: {CLR_GOLD}{total_favs}{CLR_RESET}{CLR_AMBER}{dirty_flag}{CLR_RESET}  │  "
                       f"View: {CLR_CYAN}{self.view_mode.upper()}{CLR_RESET}")
        print(status_line)
        if self.search_filter:
            print(f"  🔍 Active Filter: {CLR_GOLD}\"{self.search_filter}\"{CLR_RESET}")
        print(f"{CLR_DIM}────────────────────────────────────────────────────────────────────────{CLR_RESET}")

        items = self.page_items()
        if not items:
            print(f"  {CLR_DIM}No games matched the current filter or search.{CLR_RESET}")
        else:
            for idx, g in items:
                is_fav = g["name"] in self.current_favs
                if is_fav:
                    fav_badge = f"{CLR_GOLD}⭐ [FAV]{CLR_RESET}"
                    name_colored = f"{CLR_BOLD}{CLR_GREEN}{g['name'][:34]:<34}{CLR_RESET}"
                else:
                    fav_badge = f"{CLR_DIM}  [   ]{CLR_RESET}"
                    name_colored = f"{g['name'][:34]:<34}"

                hours_str = f"{g['hours']:>6.1f} hrs"
                recent_str = f" {CLR_CYAN}(+{g['hours_2weeks']}h){CLR_RESET}" if g["hours_2weeks"] > 0 else ""

                print(f"  {CLR_DIM}[{idx:2d}]{CLR_RESET} {fav_badge}  {name_colored}  {hours_str}{recent_str}")

        print(f"{CLR_DIM}────────────────────────────────────────────────────────────────────────{CLR_RESET}")
        print(f"  Page {CLR_BOLD}{self.current_page}{CLR_RESET} of {CLR_BOLD}{self.total_pages}{CLR_RESET} "
              f"({len(self.filtered_games)} games shown)  │  Commands: {CLR_CYAN}n{CLR_RESET}/{CLR_CYAN}p{CLR_RESET} (next/prev), {CLR_CYAN}h{CLR_RESET} (help)")
        print(f"{CLR_PURPLE_BOLD}┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛{CLR_RESET}")

    def toggle_indices(self, indices: list[int]):
        """Toggle favorite state for listed 1-based indices in filtered_games."""
        toggled_on = 0
        toggled_off = 0
        for idx in indices:
            if 1 <= idx <= len(self.filtered_games):
                game_name = self.filtered_games[idx - 1]["name"]
                if game_name in self.current_favs:
                    self.current_favs.remove(game_name)
                    toggled_off += 1
                else:
                    self.current_favs.add(game_name)
                    toggled_on += 1
        print(f"{CLR_GREEN}✔ Toggled: +{toggled_on} added, -{toggled_off} removed.{CLR_RESET}")

    def add_indices(self, indices: list[int]):
        """Explicitly add listed indices to favorites."""
        added = 0
        for idx in indices:
            if 1 <= idx <= len(self.filtered_games):
                game_name = self.filtered_games[idx - 1]["name"]
                if game_name not in self.current_favs:
                    self.current_favs.add(game_name)
                    added += 1
        print(f"{CLR_GREEN}✔ Added {added} game(s) to favorites.{CLR_RESET}")

    def remove_indices(self, indices: list[int]):
        """Explicitly remove listed indices from favorites."""
        removed = 0
        for idx in indices:
            if 1 <= idx <= len(self.filtered_games):
                game_name = self.filtered_games[idx - 1]["name"]
                if game_name in self.current_favs:
                    self.current_favs.remove(game_name)
                    removed += 1
        print(f"{CLR_AMBER}✔ Removed {removed} game(s) from favorites.{CLR_RESET}")

    def auto_seed_hours(self, min_hours: float):
        """Batch-favorite all played games with >= min_hours."""
        added = 0
        for g in self.all_games:
            if g["hours"] >= min_hours:
                if g["name"] not in self.current_favs:
                    self.current_favs.add(g["name"])
                    added += 1
        print(f"{CLR_GREEN}⭐ Auto-selected {added} game(s) with >= {min_hours}h playtime!{CLR_RESET}")

    def save(self):
        """Save changes to drops.db and refresh site docs/."""
        added = self.current_favs - self.initial_favs
        removed = self.initial_favs - self.current_favs

        if not added and not removed:
            print(f"{CLR_CYAN}No changes to save.{CLR_RESET}")
            return

        now = dbm.now_iso()
        # insert new
        for name in added:
            self.conn.execute(
                "INSERT OR IGNORE INTO favorites (game_name, weight, instant_alert, source, created_at) "
                "VALUES (?,10,1,'steam_picker',?)", (name, now))

        # delete removed
        for name in removed:
            self.conn.execute("DELETE FROM favorites WHERE game_name=?", (name,))

        self.conn.commit()
        self.initial_favs = set(self.current_favs)
        print(f"{CLR_GREEN}✔ Database updated! (+{len(added)} / -{len(removed)}){CLR_RESET}")

        # Regenerate site docs and push
        import site_builder as sb
        import main as mn
        import argparse
        try:
            print(f"{CLR_PURPLE}Rebuilding site docs/ with new favorites...{CLR_RESET}")
            sb.cmd_build(argparse.Namespace(db=str(mn.db_path())))
            if self.auto_push:
                mn.commit_on_change(self.conn, dry=False)
            print(f"{CLR_GREEN}✔ Site updated successfully!{CLR_RESET}")
        except Exception as e:
            print(f"{CLR_RED}Site rebuild notice: {e}{CLR_RESET}")

    def print_help(self):
        """Print interactive command help."""
        print(f"""
{CLR_BOLD}Interactive Commands:{CLR_RESET}
  {CLR_CYAN}<numbers>{CLR_RESET}          Toggle items (e.g. {CLR_GOLD}1{CLR_RESET}, {CLR_GOLD}1,3,5{CLR_RESET}, {CLR_GOLD}1-5{CLR_RESET}, {CLR_GOLD}2 4 6{CLR_RESET})
  {CLR_CYAN}+ <numbers>{CLR_RESET}        Add items to favorites (e.g. {CLR_GOLD}+ 1 3 5{CLR_RESET} or {CLR_GOLD}+ 1-10{CLR_RESET})
  {CLR_CYAN}- <numbers>{CLR_RESET}        Remove items from favorites (e.g. {CLR_GOLD}- 2 4{CLR_RESET})
  {CLR_CYAN}auto <hours>{CLR_RESET}       Auto-select all games with >= N hours (e.g. {CLR_GOLD}auto 20{CLR_RESET})
  {CLR_CYAN}clear{CLR_RESET}              Unmark all favorites
  {CLR_CYAN}/ <query>{CLR_RESET}          Search games by name (e.g. {CLR_GOLD}/rust{CLR_RESET} or {CLR_GOLD}/strike{CLR_RESET})
  {CLR_CYAN}reset{CLR_RESET}              Clear search filter and return to full list
  {CLR_CYAN}view favs{CLR_RESET}          Show only currently favorited games
  {CLR_CYAN}view all{CLR_RESET}           Show all played games
  {CLR_CYAN}n{CLR_RESET} / {CLR_CYAN}p{CLR_RESET}              Next / Previous page
  {CLR_CYAN}page <num>{CLR_RESET}        Jump to specific page
  {CLR_CYAN}s{CLR_RESET} / {CLR_CYAN}save{CLR_RESET}           Save changes to DB and publish to website
  {CLR_CYAN}h{CLR_RESET} / {CLR_CYAN}help{CLR_RESET}           Show this help cheatsheet
  {CLR_CYAN}q{CLR_RESET} / {CLR_CYAN}quit{CLR_RESET}           Exit interactive picker
""")

    def run(self):
        """Run the interactive loop."""
        while True:
            self.render()
            try:
                raw = input(f"{CLR_BOLD}drops-pick > {CLR_RESET}").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting...")
                break

            if not raw:
                continue

            cmd_lower = raw.lower()

            if cmd_lower in ("q", "quit", "exit"):
                if self.current_favs != self.initial_favs:
                    ans = input(f"{CLR_AMBER}You have unsaved changes. Save before exiting? (y/n/cancel): {CLR_RESET}").strip().lower()
                    if ans in ("y", "yes"):
                        self.save()
                        break
                    elif ans in ("n", "no"):
                        print("Exiting without saving.")
                        break
                    else:
                        continue
                else:
                    break

            elif cmd_lower in ("h", "help", "?"):
                self.print_help()
                input(f"{CLR_DIM}Press Enter to continue...{CLR_RESET}")

            elif cmd_lower in ("s", "save"):
                self.save()
                input(f"{CLR_DIM}Press Enter to continue...{CLR_RESET}")

            elif cmd_lower in ("n", "next"):
                if self.current_page < self.total_pages:
                    self.current_page += 1
                else:
                    print(f"{CLR_AMBER}Already on last page.{CLR_RESET}")

            elif cmd_lower in ("p", "prev"):
                if self.current_page > 1:
                    self.current_page -= 1
                else:
                    print(f"{CLR_AMBER}Already on first page.{CLR_RESET}")

            elif cmd_lower.startswith("page "):
                try:
                    p = int(cmd_lower.split(" ", 1)[1])
                    if 1 <= p <= self.total_pages:
                        self.current_page = p
                    else:
                        print(f"{CLR_RED}Page must be between 1 and {self.total_pages}.{CLR_RESET}")
                except ValueError:
                    print(f"{CLR_RED}Invalid page number.{CLR_RESET}")

            elif cmd_lower.startswith("/") or cmd_lower.startswith("search ") or cmd_lower.startswith("find "):
                query = re.sub(r"^(/|search\s+|find\s+)", "", raw).strip()
                self.search_filter = query
                self.current_page = 1
                self.apply_filter()

            elif cmd_lower == "reset":
                self.search_filter = ""
                self.view_mode = "played"
                self.current_page = 1
                self.apply_filter()

            elif cmd_lower in ("view favs", "favs"):
                self.view_mode = "favs"
                self.current_page = 1
                self.apply_filter()

            elif cmd_lower in ("view all", "all"):
                self.view_mode = "played"
                self.current_page = 1
                self.apply_filter()

            elif cmd_lower.startswith("auto "):
                try:
                    hrs = float(cmd_lower.split(" ", 1)[1])
                    self.auto_seed_hours(hrs)
                    self.apply_filter()
                except ValueError:
                    print(f"{CLR_RED}Invalid hours. Example: auto 20{CLR_RESET}")

            elif cmd_lower in ("clear", "unmark all"):
                confirm = input(f"{CLR_RED}Are you sure you want to unmark ALL favorites? (y/n): {CLR_RESET}").strip().lower()
                if confirm in ("y", "yes"):
                    self.current_favs.clear()
                    self.apply_filter()
                    print(f"{CLR_AMBER}Cleared all favorites.{CLR_RESET}")

            elif cmd_lower.startswith("+"):
                expr = raw.lstrip("+").strip()
                indices = parse_index_expression(expr, len(self.filtered_games))
                if indices:
                    self.add_indices(indices)
                else:
                    print(f"{CLR_RED}No valid game numbers parsed.{CLR_RESET}")

            elif cmd_lower.startswith("-"):
                expr = raw.lstrip("-").strip()
                indices = parse_index_expression(expr, len(self.filtered_games))
                if indices:
                    self.remove_indices(indices)
                else:
                    print(f"{CLR_RED}No valid game numbers parsed.{CLR_RESET}")

            else:
                # Check if it's a number/range expression (default action is toggle)
                indices = parse_index_expression(raw, len(self.filtered_games))
                if indices:
                    self.toggle_indices(indices)
                else:
                    print(f"{CLR_RED}Unknown command: '{raw}'. Type 'h' for help.{CLR_RESET}")


def main():
    import argparse
    import main as mn
    ap = argparse.ArgumentParser(description="Mick's Steam Favorites Interactive Selector")
    ap.add_argument("--page-size", type=int, default=20, help="Games per page (default: 20)")
    ap.add_argument("--no-push", action="store_true", help="Do not push git commits on save")
    args = ap.parse_args()

    conn = dbm.init_db(mn.db_path())
    picker = SteamFavoritesPicker(conn, page_size=args.page_size, auto_push=not args.no_push)
    picker.run()


if __name__ == "__main__":
    main()
