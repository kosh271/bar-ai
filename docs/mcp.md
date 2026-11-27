# BAR AI Studio MCP

The Model Context Protocol (MCP) server lets a local MCP client work with the same projects, profiles, installed game content, and match queue as the Studio browser UI.

## Start Studio

Use Python 3.10 or newer. From the repository root, install the optional MCP dependency and start Studio on a fixed loopback port.

```powershell
python -m pip install -r requirements-mcp.txt
python -m app --port 8765
```

Select and save a BAR installation and engine in Studio before scanning content, installing an AI, or starting a game. For a headless Studio session with an installation already saved, add `--no-browser` to the Studio command. Use the same port for Studio and MCP. Once MCP registers with Studio, you can close the browser tab and continue through the client. After updating the checkout, restart Studio and reconnect your MCP client using the same checkout.

## Connect an MCP client

Configure a local **stdio** server using the same Python environment where you installed the MCP dependency. Clients that accept an `mcpServers` JSON configuration can use the following entry. Replace the script path with the absolute path on your machine and use an absolute path to `python.exe` if the client cannot find the right Python on its PATH.

```json
{
  "mcpServers": {
    "bar-ai-studio": {
      "command": "python",
      "args": ["D:/code/BAR/mike-bench/app/studio_mcp.py", "--port", "8765"]
    }
  }
}
```

When the client supports setting the working directory, set it to the repository root and use `python -m app.studio_mcp --port 8765` as the command. The client starts the MCP process and exchanges messages with it over stdio.

Call `list_projects` to confirm the connection. If the tools appear but calls report that Studio is unavailable, check that Studio is running on the port in your MCP configuration.

## Work with Studio

| Goal | Tools |
| --- | --- |
| Find a project and inspect the profile it contains | `list_projects`, `list_profile_files`, `read_profile_file` |
| Edit a workspace file or update its installed copy | `save_profile_file`, `get_project_install_status`, `install_project` |
| Find installed game names, map names, and AI identities | `get_installed_content`, `scan_installed_content` |
| Look up a unit by name or ID | `build_unit_catalog`, `find_units` |
| Submit a test match, track queued games, or read match logs | `start_test_match`, `get_run_status`, `list_queue_games`, `list_matches`, `read_match_log` |
| Run a round-robin tournament or numeric parameter sweep | `start_experiment`, `preview_sweep_literal`, `get_run_status`, `list_experiments`, `get_experiment` |
| Create a profile from a qualifying sweep candidate | `promote_sweep_candidate` |
| Control an active run | `set_parallel_games`, `set_experiment_paused`, `cancel_match_game`, `cancel_run` |

### Read, edit, and install profiles

`list_profile_files` lists editable paths and revision hashes. Pass `next_offset` as `offset` to read the next page. `read_profile_file` returns a page of text and the full file's SHA-256 revision. If it returns a continuation, pass both `next_line` and `next_column` as `start_line` and `start_column` in the next call. Coordinates start at 1. Each page defaults to at most 100 lines and 12,000 characters. If the revision changes between pages, read again from the start for a consistent copy.

Pass the entire edited text and the last-read revision to `save_profile_file`. Studio rejects a stale revision and returns the committed revision on success. Call `get_project_install_status` before using `install_project` to deploy the saved workspace to the configured BAR engine. If installation returns a `confirmation`, ask the user to decide and retry only if they approve. Pass the exact accepted prompt in `confirmations` alongside any prompts they already approved for this installation.

### Start and track games

Call `get_installed_content` for the exact game, map, AI version, and profile names. If a scan is needed, call `scan_installed_content`, poll `get_run_status` with the returned ID, and read the content again after completion. For units, start `build_unit_catalog`, poll its ID, and search the completed catalog with `find_units`.

`start_test_match` submits a game to Studio's shared queue. To queue a series in one submission, pass `series` with the number of `games` and optional `maps`, `swap_sides`, and `faction_pairs`. A successful submission includes `submission.queue_id`, `submission.batch_id`, `submission.first_game_index`, and `submission.game_count`. Each submission gets its own batch ID, even when it joins an existing queue. Use the queue ID with `get_run_status` and `list_queue_games` to read current progress. To cancel one game, pass the queue ID and the game's `game_index` to `cancel_match_game`. `cancel_run` cancels the entire operation identified by the run ID.

For saved results, `list_matches` supports filters for `project`, `profile`, `map_name`, and `checkpoint`. To read `infolog.txt`, `stdout.txt`, or `result.json`, use the game's artifact ID from `logs.run_id` in a queue row or `run_dir` in match history. Pass `next_offset` as `offset` to read the next page of a log or results list.

`start_test_match` and `start_experiment` accept `parallel_games` and `start_options`. The start options specify `starting_metal`, `starting_energy`, `starter_base`, and `build_turret`. Setting parallel capacity to zero stops dispatch of additional games while active games finish. Use `set_parallel_games` with a positive capacity to resume dispatch.

### Run experiments

For `start_experiment`, `game_minutes` is simulated time per game and `real_time_seconds` is the real-time budget for the whole experiment. Poll `get_run_status` with the returned ID to see progress and preparation errors. Use the same ID with `get_experiment` for standings, per-game results, and the planned schedule. Pass `next_game_offset` as `game_offset` to continue. The offset applies separately to the results and schedule, so a page can include planned games beyond the number of recorded results. Use `set_experiment_paused` to pause after in-flight games or resume.

For a parameter sweep, specify the workspace project and profile as the first player using the project key as `short_name`. Use an installed AI as the second player. A sweep builds its candidates from the saved workspace profile. Pass a one-based line and Unicode character column and the revision from `read_profile_file` to `preview_sweep_literal` to verify the number's starting column and length. Include the revision in the sweep parameters to guard against concurrent source edits.

Use `promote_sweep_candidate` with the experiment ID, candidate ID, and new profile name. The sweep must be completed and the candidate must earn a majority from at least two clean mirrored pairs. Promotion creates a workspace profile. Use `install_project` to deploy it for ordinary matches.
