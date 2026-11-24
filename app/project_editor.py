"""GUI for creating AI projects and editing their profile files."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Callable

from . import profile_text
from .control_help import control_help
from .game_sources import GameSource, GameSourceError, list_game_sources, stock_directory
from .installation import InstallationError, install, list_managed, remove, remove_installed
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


class _ChoiceDialog(simpledialog.Dialog):
    dialog_title: str
    choice_label: str
    choice_help: str
    choice_error: str
    def __init__(self, parent: tk.Misc, choices: tuple[str, ...], current: str) -> None:
        self.choices = choices
        self.current = current
        self.selected: str | None = None
        super().__init__(parent, title=self.dialog_title)

    def body(self, master: tk.Misc) -> ttk.Combobox:
        ttk.Label(master, text=self.choice_label).pack(anchor="w")
        self.choice = tk.StringVar(value=self.current)
        combo = control_help(
            ttk.Combobox(master, textvariable=self.choice, values=self.choices, state="readonly", width=65),
            self.choice_help,
        )
        combo.pack(fill="x", pady=(6, 0))
        return combo

    def validate(self) -> bool:
        if self.choice.get() not in self.choices:
            messagebox.showerror(self.dialog_title, self.choice_error, parent=self)
            return False
        return True

    def apply(self) -> None:
        self.selected = self.choice.get()


class SourceDialog(_ChoiceDialog):
    dialog_title = "Change hard-profile source"
    choice_label = "Installed game version:"
    choice_help = (
        "Choose the installed release that supplies the Hard profile. "
        "This affects which files new projects and profiles start with."
    )
    choice_error = "Select an installed game version."


class ManagedInstallDialog(_ChoiceDialog):
    dialog_title = "Managed BAR installations"
    choice_label = "Installed AI and engine:"
    choice_help = "Choose which installed AI copy to remove, including copies from deleted projects."
    choice_error = "Select an installed AI and engine."


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
        self.file_bytes = b""
        self.files: list[Path] = []
        self.status = tk.StringVar(value="Create or select an AI project.")
        self.workspace_issues = tk.StringVar()
        self.project_issues: list[dict[str, str]] = []
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
        self.project_box = control_help(
            ttk.Combobox(controls, textvariable=self.project_label, state="readonly"),
            "Choose the AI project to edit. Its profiles replace the ones currently listed.",
        )
        self.project_box.grid(row=0, column=1, sticky="ew")
        self.project_box.bind("<<ComboboxSelected>>", self._project_selected)
        control_help(
            ttk.Button(controls, text="New AI", command=self.new_project),
            "Create an editable AI project from the Hard profile in the selected game version.",
        ).grid(row=0, column=2, padx=5)
        control_help(
            ttk.Button(controls, text="Delete AI", command=self.delete_project),
            "Permanently delete this editable project and all of its profiles after confirmation.",
        ).grid(row=0, column=3, padx=(0, 5))
        ttk.Label(controls, text="Profile:").grid(row=0, column=4, sticky="e", padx=4)
        self.profile_box = control_help(
            ttk.Combobox(controls, textvariable=self.profile_label, state="readonly"),
            "Choose the set of AI settings to edit. Save any file changes you want to keep before switching.",
        )
        self.profile_box.grid(row=0, column=5, sticky="ew")
        self.profile_box.bind("<<ComboboxSelected>>", self._profile_selected)
        control_help(
            ttk.Button(controls, text="New from hard", command=self.new_hard_profile),
            "Add a fresh copy of the selected game's Hard profile to this project.",
        ).grid(row=0, column=6, padx=5)
        control_help(
            ttk.Button(controls, text="Clone profile", command=self.new_clone),
            "Copy the saved profile so you can try changes separately.",
        ).grid(row=0, column=7)
        control_help(
            ttk.Button(controls, text="Delete profile", command=self.delete_profile),
            "Permanently delete this editable profile after confirmation. Install the project again to update BAR's profile list.",
        ).grid(row=0, column=8)
        control_help(
            ttk.Button(controls, text="Install / update AI", command=self.install_ai),
            "Copy this saved AI to the selected engine. Restart BAR afterward to refresh its AI list.",
        ).grid(
            row=1, column=1, sticky="w", pady=(8, 0)
        )
        control_help(
            ttk.Button(controls, text="Remove installed AI", command=self.remove_ai),
            "Remove the installed AI from the selected engine. The editable project stays in the editor.",
        ).grid(
            row=1, column=4, sticky="w", pady=(8, 0)
        )
        ttk.Label(controls, text="Close BAR first.").grid(row=1, column=5, columnspan=2, pady=(8, 0))
        control_help(
            ttk.Button(controls, text="Managed installs…", command=self.manage_installed_ais),
            "Find editor-managed AI copies in every engine, even if the editable project has been deleted.",
        ).grid(row=1, column=7, columnspan=2, sticky="e", pady=(8, 0))

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
        control_help(
            ttk.Button(source_row, text="Change...", command=self.change_source),
            "Choose the installed game used as the source for new copies of the Hard profile.",
        ).grid(row=0, column=2, padx=(6, 0))
        ttk.Label(source_row, textvariable=self.source_status, wraplength=800).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 0)
        )
        ttk.Label(frame, text="Profile files").grid(row=2, column=0, sticky="w", pady=(12, 4))
        file_frame = ttk.Frame(frame)
        file_frame.grid(row=3, column=0, sticky="nsew", padx=(0, 8))
        file_frame.rowconfigure(0, weight=1)
        file_frame.columnconfigure(0, weight=1)
        self.file_list = control_help(
            tk.Listbox(file_frame, width=34, exportselection=False),
            "Choose a settings or script file from the current profile. The editor displays one file at a time.",
        )
        self.file_list.grid(row=0, column=0, sticky="nsew")
        file_scrollbar = control_help(
            ttk.Scrollbar(file_frame, orient="vertical", command=self.file_list.yview),
            "Scroll the profile file list.",
        )
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
        self.text = control_help(
            tk.Text(editor_frame, undo=True, wrap="none", state="disabled", height=16),
            "Edit the selected file directly. Save this file before switching files or installing the AI.",
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        vertical = control_help(
            ttk.Scrollbar(editor_frame, orient="vertical", command=self.text.yview),
            "Scroll up or down through the source file.",
        )
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = control_help(
            ttk.Scrollbar(editor_frame, orient="horizontal", command=self.text.xview),
            "Scroll sideways to read long source lines.",
        )
        horizontal.grid(row=1, column=0, sticky="ew")
        self.text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        control_help(
            ttk.Button(frame, text="Save file", command=self.save_file),
            "Write this file to the editable profile. Install the AI afterward if you want BAR to run the change.",
        ).grid(row=4, column=1, sticky="e", pady=(8, 0))
        ttk.Label(frame, textvariable=self.status, wraplength=800).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        ttk.Label(frame, textvariable=self.workspace_issues, wraplength=800).grid(
            row=6, column=0, columnspan=2, sticky="w"
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
        self.file_bytes = b""
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.edit_modified(False)
        self.text.configure(state="disabled")

    def _refresh_projects(self, select: str = "") -> None:
        issues: list[dict[str, str]] = []
        try:
            projects = list_projects(self.projects_root, issues=issues)
        except (ProjectError, OSError) as error:
            self._show_error(error)
            return
        self.project_issues = issues
        self._show_workspace_issues(issues)
        self.project_choices = {f"{name} ({key})": key for key, name in projects}
        self.project_box["values"] = tuple(self.project_choices)
        for label, key in self.project_choices.items():
            if key == select:
                self.project_label.set(label)
                self._load_project(key)
                break

    def _show_workspace_issues(self, issues: list[dict[str, str]]) -> None:
        self.workspace_issues.set("\n".join(
            f"{issue['project']}/{issue['profile']}: {issue['message']}"
            if "profile" in issue else f"{issue['project']}: {issue['message']}" for issue in issues
        ))

    def _load_project(self, key: str) -> bool:
        project = self.projects_root / key
        issues = self.project_issues.copy()
        try:
            profiles = list_profiles(project, issues=issues)
        except (ProjectError, OSError) as error:
            self._show_error(error)
            return False
        self._show_workspace_issues(issues)
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
            raw = profile_text.read(path)
            text = profile_text.normalized_newlines(raw.decode("utf-8"))
        except (OSError, UnicodeError, ProjectError) as error:
            self._show_error(error)
            self.file_list.selection_clear(0, "end")
            if self.current_file is not None:
                index = self.files.index(self.current_file.relative_to(self.profile))
                self.file_list.selection_set(index)
            return False
        self.current_file = path
        self.file_bytes = raw
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
        if self.current_file is None or self.profile is None:
            self._show_error(ProjectError("Select a file first."))
            return
        try:
            edited = self.text.get("1.0", "end-1c")
            encoded = profile_text.restore_newlines(self.file_bytes.decode("utf-8"), edited).encode("utf-8")
            profile_text.replace(self.profile, [(self.current_file, profile_text.digest(self.file_bytes), encoded)])
        except (OSError, UnicodeError, ProjectError) as error:
            self._show_error(error)
            return
        self.file_bytes = encoded
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

    def manage_installed_ais(self) -> None:
        if self.data is None:
            self._show_error(InstallationError("Inspect a BAR installation first."))
            return
        try:
            engines = tuple(sorted(path.name for path in (self.data / "engine").iterdir() if path.is_dir()))
            installations, issues = list_managed(self.data, engines)
            if issues:
                messagebox.showwarning(ManagedInstallDialog.dialog_title, "\n".join(issues),
                                       parent=self.winfo_toplevel())
            if not installations:
                self.status.set("There are no editor-managed installations in this BAR folder.")
                return
            choices = {f"{entry['name']} ({entry['project']}) · {entry['engine']}": entry
                       for entry in installations}
            dialog = ManagedInstallDialog(self.winfo_toplevel(), tuple(choices), next(iter(choices)))
            selected = choices.get(dialog.selected)
            if selected is None or not self._confirm_deployment(
                f"Remove {selected['name']} from {selected['engine']}?\n\n{selected['path']}"
            ):
                return
            target = remove_installed(selected["project"], self.data, selected["engine"], self._confirm_deployment)
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
