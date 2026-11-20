"""Headless match controls for installed AIs."""

from __future__ import annotations

import hashlib
import queue
import tempfile
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable

from .discovery import Installation
from .game_sources import GameSourceError, list_game_sources
from .headless import MatchError, MatchProgress, Player, archive_cache_path, run_match, scan_content
from .settings import SettingsError, load_content_cache, load_match_map, save_content_cache, save_match_map


@dataclass(frozen=True)
class ScanResult:
    installation: Installation
    engine: str
    content: dict


@dataclass(frozen=True)
class MatchResult:
    summary: str
    path: Path


@dataclass(frozen=True)
class WorkError:
    message: str


class PlayerRow(ttk.Frame):
    def __init__(self, parent: tk.Misc, ais: dict[str, dict], remove: Callable[[], None]) -> None:
        super().__init__(parent)
        self.ais = ais
        self.ai = tk.StringVar()
        self.profile = tk.StringVar()
        self.profile_choices: dict[str, str] = {}
        self.faction = tk.StringVar(value="Armada")
        self.ai_box = ttk.Combobox(self, textvariable=self.ai, values=tuple(ais), state="readonly", width=28)
        self.ai_box.pack(side="left", fill="x", expand=True)
        self.ai_box.bind("<<ComboboxSelected>>", self._changed)
        self.profile_box = ttk.Combobox(self, textvariable=self.profile, state="readonly", width=24)
        self.profile_box.pack(side="left", padx=5)
        ttk.Combobox(self, textvariable=self.faction, values=("Armada", "Cortex", "Legion"),
                     state="readonly", width=9).pack(side="left")
        ttk.Button(self, text="Remove", command=remove).pack(side="left", padx=(5, 0))
        if ais:
            self.ai.set(next(iter(ais)))
            self._changed()

    def _changed(self, _event: tk.Event | None = None) -> None:
        profiles = self.ais.get(self.ai.get(), {}).get("profiles", [])
        self.profile_choices = {
            f"{p['name']} ({p['key']})" if p["name"] != p["key"] else p["key"]: p["key"]
            for p in profiles
        }
        self.profile_box["values"] = tuple(self.profile_choices)
        self.profile.set(next(iter(self.profile_choices), ""))

    def player(self, ally: int) -> Player:
        ai = self.ais.get(self.ai.get())
        if ai is None:
            raise MatchError("Select an installed AI for every player.")
        profile = self.profile_choices.get(self.profile.get(), "")
        return Player(ai["short_name"], ai["version"], profile, self.faction.get(), ally, ai["name"])

    def destroy(self) -> None:
        super().destroy()
        del self.ai, self.profile, self.faction


class MatchEditor(ttk.Frame):
    def __init__(
        self, parent: tk.Misc, data: Path,
        installation: Callable[[], Installation | None], engine: Callable[[], str],
    ) -> None:
        super().__init__(parent, padding=12)
        self.local_data = data
        self.installation = installation
        self.engine = engine
        self.events: queue.Queue[ScanResult | MatchResult | MatchProgress | WorkError] = queue.Queue()
        self.stop_event = threading.Event()
        self.busy = False
        self.running = False
        self.match_started_at: float | None = None
        self.scan_key: tuple[Path, str] | None = None
        self.scanning_key: tuple[Path, str] | None = None
        self.scan_cache: tempfile.TemporaryDirectory | None = None
        self.ais: dict[str, dict] = {}
        self.map_choices: dict[str, str] = {}
        self.match_state_path = data / "match-state.json"
        self.remembered_map: str | None = None
        self.rows: list[list[PlayerRow]] = [[], []]
        self.map = tk.StringVar()
        self.game = tk.StringVar()
        self.minutes = tk.StringVar(value="120")
        self.skip_smoke = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Refresh installed content to configure a match.")
        self.status_summary = tk.StringVar()
        self.smoke_status = tk.StringVar(value="Smoke test: Not run")
        self.match_status = tk.StringVar(value="Skirmish: Not run")
        self.scan_notice = tk.StringVar(value="Refresh to load the dropdowns.")
        self.elapsed = tk.StringVar(value="Elapsed real time: --:--:--")
        self._build_ui()
        try:
            self.remembered_map = load_match_map(self.match_state_path)
        except SettingsError as error:
            self._error(str(error))
        self.after(200, self._poll)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        header = ttk.Frame(self)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        header.columnconfigure(1, weight=1)
        self.refresh_button = ttk.Button(header, text="Refresh installed content", command=self.refresh)
        self.refresh_button.grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.scan_notice, wraplength=400).grid(row=0, column=1, sticky="w", padx=8)
        self.scan_progress = ttk.Progressbar(header, mode="indeterminate", length=100)
        self.scan_progress.grid(row=0, column=2, padx=(0, 8))
        self.scan_progress.grid_remove()
        self.scan_cancel = ttk.Button(header, text="Cancel scan", command=self.stop)
        self.scan_cancel.grid(row=0, column=3)
        self.scan_cancel.grid_remove()
        canvas = tk.Canvas(self, highlightthickness=0)
        canvas.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)
        content = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=content, anchor="nw")

        def resize_content(_event: tk.Event | None = None) -> None:
            canvas.itemconfigure(window, width=canvas.winfo_width(),
                                 height=max(canvas.winfo_height(), content.winfo_reqheight()))
            canvas.configure(scrollregion=canvas.bbox("all"))

        self._resize_content = resize_content
        content.bind("<Configure>", resize_content)
        canvas.bind("<Configure>", resize_content)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(7, weight=1)
        ttk.Label(content, text="Install saved AI changes before testing.").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(content, text="Game:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.game_box = ttk.Combobox(content, textvariable=self.game, state="readonly")
        self.game_box.grid(row=1, column=1, sticky="ew", pady=(8, 0))
        ttk.Label(content, text="Map:").grid(row=2, column=0, sticky="w", pady=6)
        self.map_box = ttk.Combobox(content, textvariable=self.map, state="readonly")
        self.map_box.grid(row=2, column=1, sticky="ew", pady=6)
        self.map_box.bind("<<ComboboxSelected>>", self._remember_map)
        ttk.Label(content, text="Game-time limit (minutes):").grid(row=3, column=0, sticky="w")
        ttk.Entry(content, textvariable=self.minutes, width=8).grid(row=3, column=1, sticky="w")
        teams = ttk.Frame(content)
        teams.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=10)
        teams.columnconfigure(0, weight=1)
        teams.bind("<Configure>", resize_content)
        self.team_frames = []
        for side in range(2):
            frame = ttk.LabelFrame(teams, text=f"Side {side + 1}", padding=5)
            frame.grid(row=side, column=0, sticky="nsew", padx=4, pady=4)
            self.team_frames.append(frame)
            ttk.Button(frame, text="Add AI", command=lambda s=side: self.add_row(s)).pack(anchor="w")
        buttons = ttk.Frame(content)
        buttons.grid(row=5, column=0, columnspan=2, sticky="w")
        ttk.Button(buttons, text="Start match", command=self.start).pack(side="left")
        ttk.Button(buttons, text="Stop / cancel", command=self.stop).pack(side="left", padx=6)
        self.skip_smoke_button = ttk.Checkbutton(buttons, text="Skip smoke test", variable=self.skip_smoke)
        self.skip_smoke_button.pack(side="left", padx=6)
        ttk.Label(content, textvariable=self.elapsed).grid(row=6, column=0, columnspan=2, sticky="w", pady=(5, 0))
        status_frame = ttk.Frame(content)
        status_frame.grid(row=7, column=0, columnspan=2, sticky="nsew", pady=8)
        status_frame.columnconfigure(0, weight=1)
        status_frame.rowconfigure(4, weight=1)
        ttk.Label(status_frame, textvariable=self.status_summary, wraplength=700).grid(row=0, column=0, sticky="w")
        ttk.Label(status_frame, textvariable=self.smoke_status).grid(row=1, column=0, sticky="w")
        ttk.Label(status_frame, textvariable=self.match_status, wraplength=700).grid(row=2, column=0, sticky="w")
        self.log_toggle = ttk.Button(status_frame, text="Show log", command=self._toggle_log)
        self.log_toggle.grid(row=3, column=0, sticky="w", pady=(4, 0))
        self.log_frame = ttk.Frame(status_frame)
        self.log_frame.grid(row=4, column=0, sticky="nsew", pady=(4, 0))
        self.log_frame.grid_remove()
        self.log_frame.columnconfigure(0, weight=1)
        self.log_frame.rowconfigure(0, weight=1)
        self.status_text = tk.Text(self.log_frame, height=3, wrap="word", font="TkDefaultFont", state="disabled")
        self.status_text.grid(row=0, column=0, sticky="nsew")
        status_scrollbar = ttk.Scrollbar(self.log_frame, orient="vertical", command=self.status_text.yview)
        status_scrollbar.grid(row=0, column=1, sticky="ns")
        self.status_text.configure(yscrollcommand=status_scrollbar.set)
        self.copy_status_button = ttk.Button(self.log_frame, text="Copy status", command=self.copy_status)
        self.copy_status_button.grid(row=1, column=0, sticky="e", pady=(4, 0))
        self.status.trace_add("write", self._update_status_text)
        self._update_status_text()

    def _update_status_text(self, *_args: str) -> None:
        self.status_summary.set(self.status.get().split("\n", 1)[0])
        self.status_text.configure(state="normal")
        self.status_text.delete("1.0", "end")
        self.status_text.insert("1.0", self.status.get())
        self.status_text.configure(state="disabled")

    def _toggle_log(self) -> None:
        if self.log_frame.winfo_ismapped():
            self.log_frame.grid_remove()
            self.log_toggle.configure(text="Show log")
        else:
            self.log_frame.grid()
            self.log_toggle.configure(text="Hide log")
        self.after_idle(self._resize_content)

    @staticmethod
    def _duration(seconds: float) -> str:
        hours, remainder = divmod(int(seconds), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}"

    def copy_status(self) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(self.status.get())
        except tk.TclError as error:
            messagebox.showerror("Copy status", f"Cannot copy status: {error}", parent=self.winfo_toplevel())
            return
        self.copy_status_button.configure(text="Copied")
        self.after(1500, lambda: self.copy_status_button.configure(text="Copy status"))

    def _remember_map(self, _event: tk.Event | None = None) -> None:
        map_name = self.map_choices.get(self.map.get())
        if map_name is None:
            return
        self.remembered_map = map_name
        try:
            save_match_map(self.match_state_path, map_name)
        except SettingsError as error:
            self._error(str(error))

    def _selected(self) -> tuple[Installation, str]:
        installation = self.installation()
        engine = self.engine()
        if installation is None or engine not in installation.engines:
            raise MatchError("Inspect a BAR installation and select an installed engine first.")
        return installation, engine

    def _work(self, function: Callable[[], ScanResult | MatchResult]) -> None:
        def worker() -> None:
            try:
                self.events.put(function())
            except (MatchError, OSError, ValueError) as error:
                self.events.put(WorkError(str(error)))
        threading.Thread(target=worker, daemon=True).start()

    def refresh(self) -> None:
        if self.busy:
            return
        try:
            installation, engine = self._selected()
        except MatchError as error:
            self._error(str(error))
            return
        try:
            if self.scan_cache is None:
                self.local_data.mkdir(parents=True, exist_ok=True)
                self.scan_cache = tempfile.TemporaryDirectory(prefix=".scan-", dir=self.local_data)
        except OSError as error:
            self._error(f"Cannot create content scan directory: {error}")
            return
        cache = Path(self.scan_cache.name).resolve()
        self.stop_event.clear()
        self.busy = True
        self.scanning_key = (installation.root, engine)
        self.status.set("Scanning installed maps, games and AIs; the first scan may take several minutes...")
        self.scan_notice.set("Scanning... The first scan may take several minutes.")
        self.refresh_button.state(["disabled"])
        self.scan_cancel.state(["!disabled"])
        self.scan_progress.grid()
        self.scan_cancel.grid()
        self.scan_progress.start()
        engine_dir = installation.data / "engine" / engine
        self._work(lambda: ScanResult(
            installation, engine, scan_content(
                engine_dir, installation.data, cache, self.stop_event,
                archive_cache=archive_cache_path(self.local_data, engine_dir, installation.data),
            )
        ))

    def _apply_scan(self, result: ScanResult) -> None:
        installation, engine, content = result.installation, result.engine, result.content
        if self.installation() != installation or self.engine() != engine:
            self.status.set("Installation or engine changed; refresh installed content.")
            return
        self.scan_key = (installation.root, engine)
        self.ais = {
            f"{ai['name']} ({ai['short_name']}:{ai['version']})": ai for ai in content["ais"]
        }
        self.map_choices = {}
        for item in content["maps"]:
            name, width, height = item["name"], item["width"], item["height"]
            size = f"{width / 512:g} x {height / 512:g}" if width and height else "size unavailable"
            self.map_choices[f"{name} ({size})"] = name
        self.map_box["values"] = tuple(self.map_choices)
        self.game_box["values"] = content["games"]
        selected_map = next(
            (label for label, name in self.map_choices.items() if name == self.remembered_map),
            next(iter(self.map_choices), ""),
        )
        self.map.set(selected_map)
        warning = ""
        if self.remembered_map and self.remembered_map not in self.map_choices.values():
            warning = f"\nLast selected map is unavailable: {self.remembered_map}"
        try:
            sources, suggested = list_game_sources(installation.data, installation.game_directory)
        except (GameSourceError, OSError) as error:
            warning += f"\nGame suggestion failed: {error}"
            sources, suggested = [], None
        preferred = suggested.game_name if suggested else next((s.game_name for s in sources if s.tag == "byar:test"), "")
        self.game.set(preferred if preferred in content["games"] else "")
        for side in range(2):
            for row in self.rows[side]:
                row.destroy()
            self.rows[side] = []
            self.add_row(side)
        if content["warnings"]:
            warning += "\n" + "\n".join(content["warnings"][:5])
            if len(content["warnings"]) > 5:
                warning += f"\nAnd {len(content['warnings']) - 5} more map-size errors."
        self.status.set("Select a game, map and installed AIs. Map start positions are used in team order." + warning)

    def _content_cache_path(self, installation: Installation, engine: str) -> Path:
        identity = f"{str(installation.data.resolve()).casefold()}\0{engine.casefold()}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return self.local_data / "content-cache" / f"{digest}.json"

    def installation_changed(self) -> None:
        installation = self.installation()
        engine = self.engine()
        if installation is not None and (installation.root, engine) == self.scan_key:
            return
        self.scan_key = None
        self.ais = {}
        self.map_choices = {}
        self.map.set("")
        self.game.set("")
        self.map_box["values"] = ()
        self.game_box["values"] = ()
        for side in range(2):
            for row in self.rows[side]:
                row.destroy()
            self.rows[side] = []
        if self.busy:
            return
        if installation is None or engine not in installation.engines:
            self.scan_notice.set("Inspect an installation and select an engine.")
            return
        try:
            content = load_content_cache(self._content_cache_path(installation, engine), installation.data, engine)
        except SettingsError as error:
            self.scan_notice.set("Cached content could not be loaded; refresh.")
            self._error(str(error))
            return
        if content is None:
            self.scan_notice.set("Refresh to load the dropdowns.")
            return
        self._apply_scan(ScanResult(installation, engine, content))
        if content["warnings"]:
            self.scan_notice.set("Using cached content; some map sizes could not be read.")
        else:
            self.scan_notice.set("Using cached content; refresh after installation changes.")

    def add_row(self, side: int) -> None:
        row = PlayerRow(self.team_frames[side], self.ais, lambda: self.remove_row(side, row))
        self.rows[side].append(row)
        row.pack(fill="x", pady=(5, 0))

    def remove_row(self, side: int, row: PlayerRow) -> None:
        self.rows[side].remove(row)
        row.destroy()

    def start(self) -> None:
        if self.busy:
            return
        try:
            installation, engine = self._selected()
            if self.scan_key != (installation.root, engine):
                raise MatchError("Refresh installed content for the selected engine first.")
            if self.map.get() not in self.map_choices or self.game.get() not in self.game_box["values"]:
                raise MatchError("Select an installed game and map.")
            minutes = int(self.minutes.get())
            players = [row.player(side) for side in range(2) for row in self.rows[side]]
            if not 0 < minutes <= 2147483647 // 1800 or any(not rows for rows in self.rows):
                raise MatchError("Enter a valid positive time limit and add at least one AI to each side.")
        except (MatchError, ValueError) as error:
            self._error(str(error) or "Enter a whole number of game minutes.")
            return
        self._remember_map()
        self.stop_event.clear()
        self.busy = self.running = True
        skip_smoke = self.skip_smoke.get()
        self.skip_smoke_button.state(["disabled"])
        self.match_started_at = time.monotonic()
        self.elapsed.set("Elapsed real time: 00:00:00")
        self.smoke_status.set("Smoke test: Skipped" if skip_smoke else "Smoke test: Running")
        self.match_status.set("Skirmish: Starting" if skip_smoke else "Skirmish: Waiting for smoke test")
        self.status.set("Starting headless match..." if skip_smoke else "Starting two-minute smoke test...")
        engine_dir = installation.data / "engine" / engine
        map_name, game = self.map_choices[self.map.get()], self.game.get()

        def run() -> MatchResult:
            summary, path = run_match(
                engine_dir, installation.data, self.local_data, map_name, game, players, minutes, self.stop_event,
                self.events.put, skip_smoke=skip_smoke,
            )
            return MatchResult(summary, path)

        self._work(run)

    def stop(self) -> None:
        if self.busy:
            self.stop_event.set()
            self.status.set("Stopping..." if self.running else "Cancelling content scan...")
            if not self.running:
                self.scan_notice.set("Cancelling scan...")
                self.scan_cancel.state(["disabled"])

    def _error(self, text: str) -> None:
        self.status.set(text)
        messagebox.showerror("Headless match", text, parent=self.winfo_toplevel())

    def _poll(self) -> None:
        if self.running and self.match_started_at is not None:
            self.elapsed.set(f"Elapsed real time: {self._duration(time.monotonic() - self.match_started_at)}")
        try:
            while True:
                result = self.events.get_nowait()
                if isinstance(result, MatchProgress):
                    label = "Smoke test" if result.phase == "smoke" else "Skirmish"
                    duration = f" ({self._duration(result.seconds)} real time)" if result.seconds is not None else ""
                    target = self.smoke_status if result.phase == "smoke" else self.match_status
                    target.set(f"{label}: {result.summary}{duration}")
                    if result.phase == "smoke" and result.summary in ("Failed", "Stopped"):
                        self.match_status.set("Skirmish: Not started")
                    if result.message:
                        self.status.set(result.message)
                    continue
                was_scanning = self.busy and not self.running
                self.busy = self.running = False
                if not was_scanning:
                    self.skip_smoke_button.state(["!disabled"])
                if was_scanning:
                    installation = self.installation()
                    current_key = (installation.root, self.engine()) if installation is not None else None
                    selection_changed = self.scanning_key != current_key
                    self.scanning_key = None
                    self.scan_progress.stop()
                    self.scan_progress.grid_remove()
                    self.scan_cancel.grid_remove()
                    self.refresh_button.state(["!disabled"])
                if isinstance(result, WorkError):
                    if was_scanning:
                        self.scan_notice.set("Scan cancelled." if result.message == "Content scan cancelled." else "Scan failed.")
                        if selection_changed:
                            self.installation_changed()
                    elif self.scan_key is None:
                        self.installation_changed()
                    if was_scanning and result.message == "Content scan cancelled.":
                        self.status.set(result.message)
                    elif not was_scanning:
                        if result.message not in self.status.get():
                            self.status.set(f"{self.status.get()}\n{result.message}")
                        stopped = self.stop_event.is_set() and result.message.startswith("Stopped")
                        outcome = "Stopped" if stopped else "Failed"
                        duration = ""
                        if self.match_started_at is not None:
                            duration = f" ({self._duration(time.monotonic() - self.match_started_at)} real time)"
                        if self.smoke_status.get() == "Smoke test: Running":
                            self.smoke_status.set(f"Smoke test: {outcome}{duration}")
                            self.match_status.set("Skirmish: Not started")
                        elif self.match_status.get() in ("Skirmish: Starting", "Skirmish: Running"):
                            self.match_status.set(f"Skirmish: {outcome}{duration}")
                        if not stopped:
                            messagebox.showerror("Headless match", result.message, parent=self.winfo_toplevel())
                    else:
                        self._error(result.message)
                elif isinstance(result, ScanResult):
                    cache_error = None
                    try:
                        save_content_cache(
                            self._content_cache_path(result.installation, result.engine),
                            result.installation.data, result.engine, result.content,
                        )
                    except SettingsError as error:
                        cache_error = error
                    self._apply_scan(result)
                    if self.installation() != result.installation or self.engine() != result.engine:
                        self.installation_changed()
                    else:
                        if cache_error is not None:
                            self.scan_notice.set("Scan complete; cache not saved.")
                        elif result.content["warnings"]:
                            self.scan_notice.set("Scan complete; some map sizes could not be read.")
                        else:
                            self.scan_notice.set("Scan complete.")
                    if cache_error is not None:
                        self._error(str(cache_error))
                else:
                    if self.scan_key is None:
                        self.installation_changed()
                    self.status.set(f"{result.summary}\nLogs and start script: {result.path}")
        except queue.Empty:
            pass
        self.after(200, self._poll)

    def can_close(self) -> bool:
        if self.busy:
            messagebox.showwarning("Headless match", "Stop or cancel the current operation before closing.",
                                   parent=self.winfo_toplevel())
            return False
        return True

    def cleanup(self) -> None:
        if self.scan_cache is not None:
            self.scan_cache.cleanup()
