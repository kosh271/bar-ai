"""GUI for creating AI projects and editing their profile files."""

from __future__ import annotations

import os
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Callable

from .game_sources import GameSource, GameSourceError, list_game_sources, stock_directory
from .installation import InstallationError, install, remove
from .projects import (
    ProjectError,
    clone_profile,
    create_profile_from_hard,
    create_project,
    delete_profile,
    delete_project,
    list_profiles,
    list_projects,
    profile_files,
)
from .settings import EditorState, SettingsError, load_editor_state, save_editor_state


class SourceDialog(simpledialog.Dialog):
    def __init__(self, parent: tk.Misc, choices: tuple[str, ...], current: str) -> None:
        self.choices = choices
        self.current = current
        self.selected: str | None = None
        super().__init__(parent, title="Change hard-profile source")

    def body(self, master: tk.Misc) -> ttk.Combobox:
        ttk.Label(master, text="Installed game version:").pack(anchor="w")
        self.choice = tk.StringVar(value=self.current)
        combo = ttk.Combobox(master, textvariable=self.choice, values=self.choices, state="readonly", width=65)
        combo.pack(fill="x", pady=(6, 0))
        return combo

    def validate(self) -> bool:
        if self.choice.get() not in self.choices:
            messagebox.showerror("Hard-profile source", "Select an installed game version.", parent=self)
            return False
        return True

    def apply(self) -> None:
        self.selected = self.choice.get()


class ProjectEditor(ttk.Frame):
    def __init__(self, parent: tk.Misc, root: Path, selected_engine: Callable[[], str]) -> None:
        super().__init__(parent)
        self.projects_root = root
        self.state_path = root.parent / "data" / "editor-state.json"
        self.selected_engine = selected_engine
        self.data: Path | None = None
        self.unpacked: Path | None = None
        self.source_choices: dict[str, GameSource] = {}
        self.source_suggested = False
        self.source_label = tk.StringVar()
        self.source_display = tk.StringVar(value="No source selected")
        self.source_display_style = "MissingSource.TLabel"
        self.remembered_source = ""
        self.remembered_source_suggested = False
        self.source_status = tk.StringVar(value="Inspect a BAR installation to find game sources.")
        self.project_choices: dict[str, str] = {}
        self.profile_choices: dict[str, str] = {}
        self.project_label = tk.StringVar()
        self.profile_label = tk.StringVar()
        self.project: Path | None = None
        self.profile: Path | None = None
        self.current_file: Path | None = None
        self.files: list[Path] = []
        self.status = tk.StringVar(value="Create or select an AI project.")
        self._build_ui()
        self._refresh_projects()
        self._restore_selection()

    def _build_ui(self) -> None:
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(3, weight=1)
        controls = ttk.Frame(frame)
        controls.grid(row=0, column=0, columnspan=2, sticky="ew")
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(5, weight=1)
        ttk.Label(controls, text="AI:").grid(row=0, column=0, padx=(0, 4))
        self.project_box = ttk.Combobox(controls, textvariable=self.project_label, state="readonly")
        self.project_box.grid(row=0, column=1, sticky="ew")
        self.project_box.bind("<<ComboboxSelected>>", self._project_selected)
        ttk.Button(controls, text="New AI", command=self.new_project).grid(row=0, column=2, padx=5)
        ttk.Button(controls, text="Delete AI", command=self.delete_project).grid(row=0, column=3, padx=(0, 5))
        ttk.Label(controls, text="Profile:").grid(row=0, column=4, sticky="e", padx=4)
        self.profile_box = ttk.Combobox(controls, textvariable=self.profile_label, state="readonly")
        self.profile_box.grid(row=0, column=5, sticky="ew")
        self.profile_box.bind("<<ComboboxSelected>>", self._profile_selected)
        ttk.Button(controls, text="New from hard", command=self.new_hard_profile).grid(row=0, column=6, padx=5)
        ttk.Button(controls, text="Clone profile", command=self.new_clone).grid(row=0, column=7)
        ttk.Button(controls, text="Delete profile", command=self.delete_profile).grid(row=0, column=8)
        ttk.Button(controls, text="Install / update AI", command=self.install_ai).grid(
            row=1, column=1, sticky="w", pady=(8, 0)
        )
        ttk.Button(controls, text="Remove installed AI", command=self.remove_ai).grid(
            row=1, column=4, sticky="w", pady=(8, 0)
        )
        ttk.Label(controls, text="Close BAR first.").grid(row=1, column=5, columnspan=2, pady=(8, 0))

        source_row = ttk.Frame(frame)
        source_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        source_row.columnconfigure(1, weight=1)
        ttk.Label(source_row, text="Hard source:").grid(row=0, column=0, padx=(0, 6))
        ttk.Style(self).configure(
            self.source_display_style, foreground="#b00020", font=("TkDefaultFont", 9, "bold")
        )
        self.source_display_label = ttk.Label(
            source_row, textvariable=self.source_display, wraplength=600, style=self.source_display_style
        )
        self.source_display_label.grid(row=0, column=1, sticky="w")
        ttk.Button(source_row, text="Change...", command=self.change_source).grid(row=0, column=2, padx=(6, 0))
        ttk.Label(source_row, textvariable=self.source_status, wraplength=800).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 0)
        )
        ttk.Label(frame, text="Profile files").grid(row=2, column=0, sticky="w", pady=(12, 4))
        file_frame = ttk.Frame(frame)
        file_frame.grid(row=3, column=0, sticky="nsew", padx=(0, 8))
        file_frame.rowconfigure(0, weight=1)
        file_frame.columnconfigure(0, weight=1)
        self.file_list = tk.Listbox(file_frame, width=34, exportselection=False)
        self.file_list.grid(row=0, column=0, sticky="nsew")
        file_scrollbar = ttk.Scrollbar(file_frame, orient="vertical", command=self.file_list.yview)
        file_scrollbar.grid(row=0, column=1, sticky="ns")
        self.file_list.configure(yscrollcommand=file_scrollbar.set)
        self.file_list.bind(
            "<Map>", lambda _event: self.file_list.after_idle(lambda: file_scrollbar.set(*self.file_list.yview()))
        )
        self.file_list.bind("<<ListboxSelect>>", self._file_selected)
        editor_frame = ttk.Frame(frame)
        editor_frame.grid(row=3, column=1, sticky="nsew")
        editor_frame.rowconfigure(0, weight=1)
        editor_frame.columnconfigure(0, weight=1)
        self.text = tk.Text(editor_frame, undo=True, wrap="none", state="disabled", height=16)
        self.text.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(editor_frame, orient="vertical", command=self.text.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(editor_frame, orient="horizontal", command=self.text.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        ttk.Button(frame, text="Save file", command=self.save_file).grid(row=4, column=1, sticky="e", pady=(8, 0))
        ttk.Label(frame, textvariable=self.status, wraplength=800).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )

    def set_installation(self, data: Path | None, unpacked: Path | None) -> None:
        previous = self.source_label.get() if data is not None and data == self.data else ""
        was_suggested = self.source_suggested
        self.data = data
        self.unpacked = unpacked
        self.source_choices = {}
        self.source_label.set("")
        self.source_display.set("No source selected")
        self.source_display_label.configure(style=self.source_display_style)
        self.source_suggested = False
        if self.data is None:
            self.source_status.set("Inspect a BAR installation to find game sources.")
            return
        try:
            sources, suggested = list_game_sources(self.data, self.unpacked)
        except (GameSourceError, OSError) as error:
            self.source_status.set(str(error))
            return
        self.source_choices = {source.label: source for source in sources}
        if previous in self.source_choices:
            self._select_source(self.source_choices[previous], suggested=was_suggested)
        elif self.remembered_source in self.source_choices:
            self._select_source(
                self.source_choices[self.remembered_source], suggested=self.remembered_source_suggested
            )
        elif suggested is not None:
            self._select_source(suggested, suggested=True)
        elif sources:
            self.source_status.set("Last-used version could not be identified; choose a source with Change.")
        else:
            self.source_status.set("No installed game packages or unpacked BAR.sdd were found.")

    def _select_source(self, source: GameSource, *, suggested: bool = False) -> None:
        self.source_label.set(source.label)
        self.source_display_label.configure(style="TLabel")
        self.source_suggested = suggested
        if source.unpacked is not None:
            self.source_display.set("BARbarIAn Hard - unpacked BAR.sdd")
            self.source_status.set("The unpacked game may not match the current version.")
        else:
            self.source_display.set(f"BARbarIAn Hard - {source.game_name}")
            self.source_status.set(
                "Suggested from the last game log; it may differ from the current game."
                if suggested else "Using the selected installed game version."
            )

    def change_source(self) -> None:
        if not self.source_choices:
            self._show_error(ProjectError("No installed game sources are available."))
            return
        dialog = SourceDialog(self.winfo_toplevel(), tuple(self.source_choices), self.source_label.get())
        if dialog.selected is not None:
            self._select_source(self.source_choices[dialog.selected])
            self._remember_selection()

    def _show_error(self, error: Exception) -> None:
        messagebox.showerror("AI Projects", str(error), parent=self.winfo_toplevel())

    def _remember_selection(self) -> None:
        project = self.project.name if self.project else ""
        profile = self.profile.name if self.profile else ""
        file = self.current_file.relative_to(self.profile).as_posix() if self.current_file and self.profile else ""
        try:
            save_editor_state(self.state_path, EditorState(
                project, profile, file, self.source_label.get(), self.source_suggested
            ))
        except SettingsError as error:
            self._show_error(error)

    def _restore_selection(self) -> None:
        try:
            state = load_editor_state(self.state_path)
        except SettingsError as error:
            self._show_error(error)
            return
        if state is None:
            return
        self.remembered_source = state.source
        self.remembered_source_suggested = state.source_suggested
        if not state.project:
            return
        project_label = next((label for label, key in self.project_choices.items() if key == state.project), None)
        if project_label is None:
            self.status.set(f"Last AI is no longer available: {state.project}")
            return
        if not self._load_project(state.project):
            return
        self.project_label.set(project_label)
        if not state.profile:
            return
        profile_label = next((label for label, key in self.profile_choices.items() if key == state.profile), None)
        if profile_label is None:
            self.status.set(f"Last profile is no longer available: {state.profile}")
            return
        if not self._load_profile(state.profile):
            return
        self.profile_label.set(profile_label)
        if not state.file:
            return
        index = next((i for i, path in enumerate(self.files) if path.as_posix() == state.file), None)
        if index is None:
            self.status.set(f"Last file is no longer available: {state.file}")
            return
        self.file_list.selection_set(index)
        self.file_list.activate(index)
        self.file_list.see(index)
        self._open_file(self.files[index])

    def _discard_changes(self) -> bool:
        return not self.text.edit_modified() or messagebox.askyesno(
            "Unsaved changes", "Discard unsaved changes to this file?", parent=self.winfo_toplevel()
        )

    def _clear_file(self) -> None:
        self.current_file = None
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.edit_modified(False)
        self.text.configure(state="disabled")

    def _refresh_projects(self, select: str = "") -> None:
        try:
            projects = list_projects(self.projects_root)
        except (ProjectError, OSError) as error:
            self._show_error(error)
            return
        self.project_choices = {f"{name} ({key})": key for key, name in projects}
        self.project_box["values"] = tuple(self.project_choices)
        for label, key in self.project_choices.items():
            if key == select:
                self.project_label.set(label)
                self._load_project(key)
                break

    def _load_project(self, key: str) -> bool:
        project = self.projects_root / key
        try:
            profiles = list_profiles(project)
        except (ProjectError, OSError) as error:
            self._show_error(error)
            return False
        self.project = project
        self.profile = None
        self.profile_label.set("")
        self.profile_choices = {f"{name} ({profile_key})": profile_key for profile_key, name in profiles}
        self.profile_box["values"] = tuple(self.profile_choices)
        self.file_list.delete(0, "end")
        self.files = []
        self._clear_file()
        self.status.set("Select a profile.")
        return True

    def _project_selected(self, _event: tk.Event) -> None:
        label = self.project_label.get()
        key = self.project_choices.get(label)
        if key is None:
            return
        if not self._discard_changes():
            self.project_label.set(next((label for label, item in self.project_choices.items()
                                         if self.project and item == self.project.name), ""))
            return
        if not self._load_project(key):
            self.project_label.set(next((label for label, item in self.project_choices.items()
                                         if self.project and item == self.project.name), ""))
        else:
            self._remember_selection()

    def _profile_selected(self, _event: tk.Event) -> None:
        label = self.profile_label.get()
        key = self.profile_choices.get(label)
        if key is None or self.project is None:
            return
        if not self._discard_changes():
            self.profile_label.set(next((label for label, item in self.profile_choices.items()
                                         if self.profile and item == self.profile.name), ""))
            return
        if not self._load_profile(key):
            self.profile_label.set(next((label for label, item in self.profile_choices.items()
                                         if self.profile and item == self.profile.name), ""))
        else:
            self._remember_selection()

    def _load_profile(self, key: str) -> bool:
        if self.project is None:
            return False
        profile = self.project / "profiles" / key
        try:
            files = profile_files(profile)
        except (ProjectError, OSError) as error:
            self._show_error(error)
            return False
        self.profile = profile
        self.files = files
        self.file_list.delete(0, "end")
        for path in files:
            self.file_list.insert("end", str(path))
        self._clear_file()
        self.status.set(f"Editing profile: {key}")
        return True

    def _file_selected(self, _event: tk.Event) -> None:
        selected = self.file_list.curselection()
        if not selected or self.profile is None:
            return
        relative = self.files[selected[0]]
        if self._open_file(relative):
            self._remember_selection()

    def _open_file(self, relative: Path) -> bool:
        if self.profile is None:
            return False
        path = self.profile / relative
        if path == self.current_file:
            return True
        if not self._discard_changes():
            self.file_list.selection_clear(0, "end")
            if self.current_file is not None:
                index = self.files.index(self.current_file.relative_to(self.profile))
                self.file_list.selection_set(index)
            return False
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            self._show_error(error)
            self.file_list.selection_clear(0, "end")
            if self.current_file is not None:
                index = self.files.index(self.current_file.relative_to(self.profile))
                self.file_list.selection_set(index)
            return False
        self.current_file = path
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.text.edit_reset()
        self.text.edit_modified(False)
        self.status.set(str(relative))
        return True

    def _source(self) -> GameSource:
        source = self.source_choices.get(self.source_label.get())
        if source is None:
            raise ProjectError("Select an installed game source before copying the hard profile.")
        return source

    def new_project(self) -> None:
        if not self._discard_changes():
            return
        name = simpledialog.askstring("New AI", "AI name:", parent=self.winfo_toplevel())
        if name is None:
            return
        try:
            source = self._source()
            with stock_directory(source, self.projects_root) as stock:
                created = create_project(self.projects_root, name, stock, source_label=source.label)
        except (GameSourceError, ProjectError, OSError, UnicodeError) as error:
            self._show_error(error)
            return
        self._refresh_projects(created.name)
        if self.project == created:
            self._remember_selection()
        self.status.set(f"Created AI {name.strip()} with a hard profile.")

    def new_hard_profile(self) -> None:
        self._new_profile(from_hard=True)

    def new_clone(self) -> None:
        self._new_profile(from_hard=False)

    def _new_profile(self, *, from_hard: bool) -> None:
        if self.project is None:
            self._show_error(ProjectError("Select an AI project first."))
            return
        if not from_hard and self.profile is None:
            self._show_error(ProjectError("Select the profile to clone."))
            return
        if not self._discard_changes():
            return
        name = simpledialog.askstring("New profile", "Profile name:", parent=self.winfo_toplevel())
        if name is None:
            return
        try:
            if from_hard:
                source = self._source()
                with stock_directory(source, self.projects_root) as stock:
                    created = create_profile_from_hard(self.project, name, stock, source_label=source.label)
            else:
                assert self.profile is not None
                created = clone_profile(self.project, name, self.profile.name)
        except (GameSourceError, ProjectError, OSError, UnicodeError) as error:
            self._show_error(error)
            return
        if not self._load_project(self.project.name):
            return
        for label, key in self.profile_choices.items():
            if key == created.name:
                self.profile_label.set(label)
                if self._load_profile(key):
                    self._remember_selection()
                break
        self.status.set(f"Created profile {name.strip()}.")

    def save_file(self) -> None:
        if self.current_file is None:
            self._show_error(ProjectError("Select a file first."))
            return
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="", dir=self.current_file.parent,
                delete=False, suffix=".tmp",
            ) as output:
                temporary = output.name
                output.write(self.text.get("1.0", "end-1c"))
            os.replace(temporary, self.current_file)
            temporary = None
        except (OSError, UnicodeError) as error:
            self._show_error(error)
            return
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
        self.text.edit_modified(False)
        self.status.set(f"Saved {self.current_file.name}")

    def _deployment_selection(self) -> tuple[Path, Path, str] | None:
        if self.project is None or self.data is None or not self.selected_engine():
            self._show_error(ProjectError("Select an AI project and an inspected BAR engine first."))
            return None
        return self.project, self.data, self.selected_engine()

    def _confirm_deployment(self, text: str) -> bool:
        return messagebox.askyesno("AI installation", text, parent=self.winfo_toplevel())

    def install_ai(self) -> None:
        selection = self._deployment_selection()
        if selection is None:
            return
        if self.text.edit_modified():
            self._show_error(ProjectError("Save the current file before installing the AI."))
            return
        try:
            result = install(*selection, self._confirm_deployment)
        except (InstallationError, OSError, UnicodeError) as error:
            self._show_error(error)
            return
        if result.changed:
            self.status.set(f"Installed {result.path}. Restart BAR to refresh the AI list.")
        else:
            self.status.set(f"{result.path} is already up to date; no changes were needed.")

    def remove_ai(self) -> None:
        selection = self._deployment_selection()
        if selection is None:
            return
        if not self._confirm_deployment(
            "Remove the installed AI from the selected engine? Your editable project will be kept."
        ):
            return
        try:
            target = remove(*selection, self._confirm_deployment)
        except (InstallationError, OSError, UnicodeError) as error:
            self._show_error(error)
            return
        self.status.set(f"Removed {target}. Restart BAR to refresh the AI list.")

    def delete_project(self) -> None:
        if self.project is None:
            self._show_error(ProjectError("Select an AI project to delete."))
            return
        if not self._discard_changes():
            return
        project_name = self.project_label.get()
        if not messagebox.askyesno(
            "Delete AI",
            f"Delete the AI project: {project_name}?\n\nThis will permanently delete the project "
            "and all its profiles. This action cannot be undone.",
            parent=self.winfo_toplevel()
        ):
            return
        try:
            delete_project(self.projects_root, self.project.name)
        except (ProjectError, OSError) as error:
            self._show_error(error)
            return
        self.project = None
        self.profile = None
        self.project_label.set("")
        self.profile_label.set("")
        self.profile_box["values"] = ()
        self.file_list.delete(0, "end")
        self.files = []
        self._clear_file()
        self._refresh_projects()
        self.status.set(f"Deleted AI project: {project_name}")

    def delete_profile(self) -> None:
        if self.project is None or self.profile is None:
            self._show_error(ProjectError("Select a profile to delete."))
            return
        if not self._discard_changes():
            return
        profile_name = self.profile_label.get()
        if not messagebox.askyesno(
            "Delete profile",
            f"Delete the profile: {profile_name}?\n\nThis will permanently delete the profile. "
            "This action cannot be undone.",
            parent=self.winfo_toplevel()
        ):
            return
        try:
            delete_profile(self.project, self.profile.name)
        except (ProjectError, OSError) as error:
            self._show_error(error)
            return
        self.profile = None
        self.profile_label.set("")
        self.file_list.delete(0, "end")
        self.files = []
        self._clear_file()
        if not self._load_project(self.project.name):
            self._refresh_projects()
        self.status.set(f"Deleted profile: {profile_name}")

    def can_close(self) -> bool:
        return self._discard_changes()
