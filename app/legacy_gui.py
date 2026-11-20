"""Legacy Tkinter editor, retained for existing workflows."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .discovery import DiscoveryError, Installation, default_root, discover, engine_readiness
from .match_editor import MatchEditor
from .project_editor import ProjectEditor
from .settings import Settings, SettingsError, load_settings, save_settings

APP_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = APP_ROOT / "data" / "settings.json"


class Editor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("BAR AI Editor")
        self.minsize(900, 600)
        self.installation: Installation | None = None
        self.root_path = tk.StringVar()
        self.engine = tk.StringVar()
        self.status = tk.StringVar(value="Select a BAR installation to begin.")
        self.details = tk.StringVar()
        self.installation_expanded = True
        self._build_ui()
        self.root_path.trace_add("write", self._root_changed)
        self.protocol("WM_DELETE_WINDOW", self.close)

        try:
            settings = load_settings(SETTINGS_PATH)
        except SettingsError as error:
            self.status.set(str(error))
            return
        initial = Path(settings.bar_root) if settings else default_root()
        if initial is not None:
            self.root_path.set(str(initial))
            self.inspect(settings.engine if settings else "")
        if self.installation is not None and self.engine.get():
            self._set_installation_expanded(False)

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=8)
        container.pack(fill="both", expand=True)
        installation_section = ttk.Frame(container)
        installation_section.pack(fill="x")
        self.installation_toggle = ttk.Button(
            installation_section, text="[-] BAR installation", command=self._toggle_installation
        )
        self.installation_toggle.pack(anchor="w")
        frame = ttk.Frame(installation_section, padding=8)
        frame.pack(fill="x")
        self.installation_frame = frame
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="BAR installation:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.root_path).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(frame, text="Browse...", command=self.browse).grid(row=0, column=2)
        ttk.Button(frame, text="Inspect", command=self.inspect).grid(row=1, column=1, sticky="w", padx=8, pady=10)
        ttk.Label(frame, text="Engine to use:").grid(row=2, column=0, sticky="w")
        self.engine_box = ttk.Combobox(frame, textvariable=self.engine, state="readonly")
        self.engine_box.grid(row=2, column=1, sticky="ew", padx=8)
        self.engine_box.bind("<<ComboboxSelected>>", self._engine_changed)
        ttk.Button(frame, text="Save selection", command=self.save).grid(row=2, column=2)
        ttk.Label(frame, textvariable=self.status, wraplength=600).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(16, 6)
        )
        ttk.Label(frame, textvariable=self.details, wraplength=600, justify="left").grid(
            row=4, column=0, columnspan=3, sticky="w"
        )
        tab_bar = ttk.Frame(container)
        tab_bar.pack(fill="x", pady=(8, 0))
        pages = ttk.Frame(container)
        pages.pack(fill="both", expand=True)
        project_frame = ttk.Frame(pages)
        self.project_editor = ProjectEditor(project_frame, APP_ROOT / "projects", self.engine.get)
        self.project_editor.pack(fill="both", expand=True)
        self.match_editor = MatchEditor(pages, APP_ROOT / "data", lambda: self.installation, self.engine.get)
        self.pages = {"AI projects": project_frame, "Headless match": self.match_editor}
        self.tab_buttons: dict[str, tk.Button] = {}
        for name in self.pages:
            button = tk.Button(
                tab_bar, text=name, command=lambda selected=name: self._select_tab(selected),
                padx=12, pady=5, borderwidth=2, foreground="#202020", activeforeground="#202020",
            )
            button.pack(side="left", padx=(0, 4))
            self.tab_buttons[name] = button
        self._select_tab("AI projects")

    def _select_tab(self, selected: str) -> None:
        for name, page in self.pages.items():
            active = name == selected
            if active:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
            color = "#bed7f4" if active else "#ededed"
            self.tab_buttons[name].configure(
                text=name,
                relief="sunken" if active else "raised",
                background=color, activebackground=color,
            )

    def _set_installation_expanded(self, expanded: bool) -> None:
        self.installation_expanded = expanded
        if expanded:
            self.installation_frame.pack(fill="x")
        else:
            self.installation_frame.pack_forget()
        self._update_installation_toggle()

    def _update_installation_toggle(self) -> None:
        state = "[-]" if self.installation_expanded else "[+]"
        engine = self.engine.get()
        suffix = f" - {engine}" if engine and not self.installation_expanded else ""
        self.installation_toggle.configure(text=f"{state} BAR installation{suffix}")

    def _toggle_installation(self) -> None:
        self._set_installation_expanded(not self.installation_expanded)

    def _root_changed(self, *_args: str) -> None:
        if self.installation is None or self.root_path.get().strip() == str(self.installation.root):
            return
        self.installation = None
        self.engine.set("")
        self.engine_box["values"] = ()
        self.details.set("")
        self.status.set("Inspect the current installation path.")
        self.project_editor.set_installation(None, None)
        self.match_editor.installation_changed()
        self._update_installation_toggle()

    def browse(self) -> None:
        selected = filedialog.askdirectory(parent=self, title="Select Beyond All Reason installation")
        if selected:
            self.root_path.set(selected)
            self.inspect()

    def inspect(self, preferred_engine: str = "") -> None:
        self.installation = None
        self.engine.set("")
        self.engine_box["values"] = ()
        self.details.set("")
        root = self.root_path.get().strip()
        if not root:
            self.status.set("Choose a BAR installation directory.")
            self.project_editor.set_installation(None, None)
            self.match_editor.installation_changed()
            self._update_installation_toggle()
            return
        try:
            installation = discover(Path(root))
        except (DiscoveryError, OSError) as error:
            self.status.set(str(error))
            self.project_editor.set_installation(None, None)
            self.match_editor.installation_changed()
            self._update_installation_toggle()
            return
        self.installation = installation
        self.root_path.set(str(installation.root))
        self.project_editor.set_installation(installation.data, installation.game_directory)
        self.engine_box["values"] = installation.engines
        if preferred_engine in installation.engines:
            self.engine.set(preferred_engine)
        elif preferred_engine:
            self.status.set(f"Saved engine {preferred_engine!r} is not installed. Select an engine.")
            self.match_editor.installation_changed()
            self._update_installation_toggle()
            return
        else:
            self.engine.set(installation.engines[-1])
        self._show_status()
        self.match_editor.installation_changed()
        self._update_installation_toggle()

    def _engine_changed(self, _event: tk.Event) -> None:
        self._show_status()
        self.match_editor.installation_changed()
        self._update_installation_toggle()

    def _show_status(self) -> None:
        installation = self.installation
        if installation is None:
            return
        self.status.set(installation.active_engine_message)
        selected = self.engine.get()
        details = [
            f"Game directory: {installation.game_directory or 'BAR.sdd not found'}",
        ]
        if selected:
            if installation.active_engine and selected != installation.active_engine:
                details.append("Selected engine differs from the launcher-reported engine.")
            missing = engine_readiness(installation, selected)
            if missing:
                details.extend(f"Missing {item}" for item in missing)
            else:
                details.append("Headless engine and BARbarIAn DLL found.")
        else:
            details.append("Select an installed engine before saving.")
        self.details.set("\n".join(details))

    def save(self) -> None:
        installation = self.installation
        if installation is None or self.root_path.get().strip() != str(installation.root):
            messagebox.showerror("BAR AI Editor", "Inspect the current installation path before saving.", parent=self)
            return
        engine = self.engine.get()
        try:
            missing = engine_readiness(installation, engine)
            if missing:
                raise DiscoveryError("Selected engine is missing:\n" + "\n".join(missing))
            save_settings(SETTINGS_PATH, Settings(str(installation.root), engine))
        except (DiscoveryError, SettingsError) as error:
            messagebox.showerror("BAR AI Editor", str(error), parent=self)
            return
        self.status.set(f"Saved settings to {SETTINGS_PATH}")

    def close(self) -> None:
        if self.match_editor.can_close() and self.project_editor.can_close():
            try:
                self.match_editor.cleanup()
            except OSError as error:
                messagebox.showerror("BAR AI Editor", f"Cannot remove the content scan cache: {error}", parent=self)
                return
            self.destroy()


def main() -> None:
    Editor().mainloop()


if __name__ == "__main__":
    main()
