# BAR AI Studio

The local web workshop edits BARbarIAn-style projects and profiles. Run it from
this directory with Python:

```powershell
python -m app
```

The app opens a browser and prints its local URL. It binds only to localhost
and stops automatically when all Studio tabs are closed and the current operation
finishes. Press Ctrl+C to stop it manually. No web service is required.
Unit icons additionally require Pillow:

```powershell
python -m pip install -r requirements.txt
```

The rest of the app works without it. See [BAR AI Studio MCP](docs/mcp.md) to connect a local MCP client.
Other users or processes on the same computer may be able to access the local
server while it is running; close it when finished.
Git must be installed and available on PATH for project history.
On first run, if no installation is saved, Studio suggests
`%LOCALAPPDATA%\Programs\Beyond-All-Reason` when `LOCALAPPDATA` is available.
You can change the suggested folder before inspecting or saving it.
BAR and its game/engine content must be installed separately; the app does not
download them.

1. Use **New AI from BAR** to copy an installed game's hard profile, or select
   an existing AI and use **Create AI from this project** to make an independent
   copy. You can also duplicate one profile in the sidebar.
2. Use **Production** for factory weights, **Opening** for recognized factory
   opener scripts, and the other sections for their configuration. **Undo** and
   **Review changes** can revert edits. **All files**
   provides a visual tree and source editor for custom or unsupported content.
3. Review and save changes. The editor checks for external modifications and
   stages multi-file saves; keep your own backup before substantial edits.
4. Open **AI install / remove** to inspect and save a BAR installation and
   engine, then install or remove the selected AI. Restart BAR to refresh its
   AI list. **Test match** scans installed content and can run a smoke test
   followed by a headless match.

**Managed installations** lists deployed copies across engines under the saved
BAR installation. You can remove copies left behind by deleted projects.

The defense and placement walkthroughs use a BARbarIAn Hard profile.

- [Shield Advanced Fusion Reactors](docs/fusion-shields.md)
- [Build more Pulsars, Bulwarks and Bastions](docs/heavy-defenses.md)
- [Shield groups of heavy defenses](docs/cluster-shields.md)
- [Keep factory routes clear](docs/keeping-chokepoints-open.md)

For air production and interception, see [fighter sweeps](docs/fighter-swarms.md),
[bomber waves](docs/bomber-swarms.md), [fighter quick-reaction forces](docs/fighter-reserve.md),
and [late-game bomber reserves](docs/bomber-qrf.md).

Choose **Queue match** or **Queue N-game series**. The status below the scan
controls reports queued and active games, and the tracker shows game progress.
Running status is checked against the engine process;
an exited engine or an unavailable status connection is reported instead.
Process liveness alone does not prove that the simulation is making progress.
Installed-content scan results are saved per BAR installation and engine and
restored when Studio opens. They can be stale after changing the installation
or updating games, maps or AIs; use **Refresh installed content** to rescan.

Under **Starting conditions**, set the starting metal and energy and toggle
**Starter base** and **Base Builder Turret** independently. **Apply Quickstart
defaults** sets 2,000 metal and 5,000 energy and enables both options. BAR
deducts up to 800 metal and 400 energy to fund the starter base. Test match and
round-robin tournament drafts remember their own starting conditions. Parameter
sweeps also have separate starting conditions. The selected settings apply to
the smoke test and every game in the run. The results show the recorded settings.

Tournaments schedule the same map and factions on both starting sides before
moving to the next pairing. Results show how much of the full schedule ran,
the seed used to resolve random factions, and the exact planned games. A sweep
variant needs at least two clean, decided pairs and a paired score above 50%
to qualify for promotion. Draws count as half a point. Saved results include
engine and installed AI fingerprints and the game and map checksums reported
by the engine when available.

Under **Artifact storage** below Match history, review disk use or select
unprotected runs for cleanup. Pin any run whose artifacts you want to keep.
Studio protects replays automatically and pauses cleanup during active operations.

Studio automatically initializes a local Git repository and baseline for each
project when the workspace loads. **Project history** lists checkpoints and
creates new ones from the saved files in the whole project, including every
profile. Save does not create a checkpoint. All project files are tracked,
even files matched by Git ignore rules; Git metadata itself is excluded.
A cloned AI starts with its own baseline, not the source AI's history.
An existing Git repository not created by Studio is left untouched and reported
as unavailable for Studio history.

Restoring requires confirmation and a clean project: save or discard browser
edits and checkpoint any changed, staged, untracked or ignored project files
first. Restore changes the working files without deleting history; create a
checkpoint afterward to record the restoration. Other open editors or processes
can have unsaved changes Studio cannot see. Project Git repositories are local
history, not backups; keep a separate backup.

Production weights are relative, not guaranteed percentages. Configuration
sections show the selected source file, **not** a calculated merged view:
conditional Legion and other files can override base values. Runtime compatibility
is not automatically verified; the installed engine's stock DLL may differ
from the C++ sources in this repository. A smoke test checks loading and script
compilation, not playing strength.
Named variants such as `factory_swarm.json` and `behaviour_swarm.json` are
available in their respective configuration sections. File names do not prove
which layers load or their order; consult the profile's startup script.

The bundled English unit-name reference displays friendly names next to unit
IDs where available. **Load unit catalog** reads unit names and factory options
from a selected installed game using the selected engine; its names take
precedence over the bundled reference.
Unit icons are read on demand from the selected installation's unpacked
`data/games/BAR.sdd/unitpics` directory and converted in memory for display.
They are not included with the app. If that directory is absent, icons are
unavailable; packaged games are not searched. Unit icons are by IceXuick and
Floris for Beyond All Reason, licensed CC BY-NC-ND.
The most recent completed catalog is restored when the workshop opens; existing
scan files are also used if no newer saved catalog exists. A successful new
scan replaces it. Names appear alongside IDs throughout the visual editors;
IDs in the files remain unchanged. The catalog belongs to its scanned game,
which may differ from your current installation or match. Opening-script
`Factory::` symbols are name matches, not verified aliases.
The scan executes that game's definition scripts locally in a simulated,
empty-match context with optional units enabled. Randomized definitions use a
deterministic sample, not the match's random choices. Parsing can be incomplete;
warnings are shown, and missing entries do not prove a unit is unavailable.
Suggestions do not verify actual match options, buildability or compatibility
with the AI DLL. Manual unit IDs remain available. Unpacked game sources
cannot be selected for this catalog.

**Preview combined layers** lets you add and reorder configuration files to
inspect a read-only hypothetical merge, value sources and overridden values,
including unsaved edits. It follows the inspected C++ loader's object merge behavior,
including cases where an object cannot be replaced by a non-object. It does not
interpret `init.as` or discover the files, game options, fallbacks or runtime
DLL used by a match; verify the chosen order and test in the engine.
The quick example assembles selected files from one family; its order is not
a detection of the profile's actual loading logic.

The opening editor supports recognized `GetOpenInfo()` factory queues. The
production, economy and defense pages also expose a few uniquely recognized
script thresholds. Unrecognized scripts and comments are preserved instead of
silently translating them; use the source view for unsupported constructs.
Structural list edits are limited where comments or mismatched arrays make
preservation unsafe.
When a supported script is only a forwarding include, the editor follows it to
the included profile file and identifies that source. Editing it can also affect
other scripts that include it; this does not establish which code runs in a match.
The economy, defense, combat, unit and placement pages include a structured
configuration tree; not every setting has a specialized visual editor.
Configuration editing accepts comments and trailing commas while preserving
the surrounding source.

The previous Tkinter editor remains available as `python -m app.legacy_gui`.
The following legacy instructions describe that interface.

## Legacy editor

Choose the BAR installation directory and inspect it. The editor selects the
latest installed engine by folder version; you can choose a different engine.
The launcher-reported active engine may differ or be unknown. Settings are
saved only when requested, in `data/settings.json`.
The **BAR installation** button expands or collapses the installation details;
they start collapsed when an installation and engine have been selected.
Use the **AI projects** and **Headless match** buttons to switch views.

Use **AI projects** in the same window to create an AI from the installed
BARbarIAn hard profile. Select an AI and profile to edit its files, or create a
profile from hard or a copy of an existing profile. Projects are kept in
`projects/`, separately from the game. The hard source shows the last game in
the engine log when it matches an installed package; use **Change** to select
another version. The suggestion is not proof of the currently active game. An
unpacked `BAR.sdd` can also be selected, but its version may differ from the
installed package.

The last selected AI, profile, file, and hard source are saved automatically in
`data/editor-state.json` and reopened on startup when the source is available.
Unsaved file edits are not stored in this state.

Select a project and use **Install / update AI** to install all its saved
profiles into the selected engine, using that engine's stock BARbarIAn DLL.
Close BAR first and restart it afterward to refresh the AI list. Unsaved editor
changes must be saved before installing. **Remove installed AI** removes the
selected engine's installed copy, not the editable project.

The editor adds names to the shared lobby AI-name file without replacing its
other entries. It asks before extending an existing unmanaged file, and stops if
the file cannot be safely extended. It also asks before replacing unmanaged or
modified AI files; removal does not restore files that were replaced. If an
interrupted update leaves a `.bar-ai-` directory, the editor stops and reports
its location for recovery. Actual in-game loading has not yet been verified.

In **Headless match**, refresh installed content once, then choose a game and map
and add installed AIs to both sides. Each AI can use a different profile and
faction. Install saved project changes before testing. The required game-time
limit defaults to 120 minutes; reaching it is not a win. Use **Stop / cancel**
to stop a match or its smoke test. The summary shows the smoke result, the
skirmish outcome and the real time taken by each phase. **Show log** expands
the detailed, scrollable status; select its text or use **Copy status** to copy
it.
The scan indicator also has a cancel button; the first scan
can take several minutes. Matches use the map's own start positions in team order,
which may not suit every team map. Matches run in local-only mode and do not
accept network players. The last selected map is saved automatically
in `data/match-state.json` and restored when available, including after a
content refresh. If it is unavailable, the editor reports that and selects
the first available map instead.

The scan is cached separately for each installation and engine and restored
when the editor reopens. Refresh it after updating the engine or installing or
removing games, maps or AIs. Map dimensions appear beside each map name in
BAR's 512-elmo map squares; maps without dimensions are identified as such.

Legion is enabled in the match options. Matches request 20× simulation speed,
but actual speed depends on the computer. The elapsed timer measures real time
since starting the match, including loading; it is not an in-game clock.

By default, before each match, a separate two-game-minute smoke run uses the
selected game, map, AIs, profiles and factions. **Skip smoke test** starts the
match without verifying AI loading or compilation. Like the original project's
smoke test, the smoke run checks the engine exit, missing AIs, AngelScript
compilation errors and script load messages. Each selected AI and profile must
have a verifiable load message; AIs without one cannot currently pass. Other
engine errors and warnings are reported separately and retained in the logs,
rather than treated as script
compilation failures. Diagnostics appearing in both the engine log and
standard output are counted once; repeated events and output-only diagnostics
are retained. Known messages about the missing AI interface, deprecated AdvSky,
test-projectile model, groundFX atlas, PBR, picture-in-picture and two Lua
shaders, the sensor-range jammer widget shutdown, and missing `allpinesb_ad0`
features are omitted from diagnostic summaries and result JSON, but remain in
the original logs. Smoke runs time out after ten real minutes, including
loading. A successful smoke test does not guarantee that the longer match
will work or that the game reached its time limit.

The smoke runner uses a local copy of the original project's headless
configuration. Engine archive caches are retained separately per installation
and engine and reused by later scans and smoke tests. The match receives a
copy from its smoke test, or from the shared cache if smoke testing is skipped;
each run keeps its own write directory and logs.

Runs write their start script, logs and result under `data/matches/`; they do
not install a game-side gadget. Winner reporting uses the engine's autohost
interface and the time limit uses the selected game's scheduled debug command.
After a game-over event, the runner allows 30 seconds for the game's own
autoquit to shut down the engine; otherwise it attempts shutdown and reports
if termination was required. After the engine reports that the game started,
a game that does not confirm the scheduled command within two minutes is
stopped and reported as an error. An unconfirmed engine exit is reported as
unknown, not a win. Engine diagnostics are reported alongside the result.

## Testing

Run the Python tests from this directory:

```powershell
python -m unittest discover -s tests
```

Run the Studio JavaScript tests with Node.js 22 or later:

```powershell
node --test 'tests/*.test.js'
```

The optional Chromium suite loads Studio with fixture data for keyboard
interaction, the live status poll, and profile saves:

```powershell
npm install --no-save --no-package-lock playwright@1.61.1
npx playwright install chromium
node --test tests/studio.browser.cjs
```
