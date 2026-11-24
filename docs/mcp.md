# BAR AI Studio MCP

The Model Context Protocol (MCP) server lets a local MCP client work with the same projects, profiles, installed game content, and match queue as the Studio browser UI.

## Start Studio

From the repository root, install the optional MCP dependency and start Studio on a fixed loopback port.

```powershell
python -m pip install -r requirements-mcp.txt
python -m app --port 8765
```

Select and save a BAR installation in Studio before using the content scan, match, or experiment tools. For a headless Studio session with an installation already saved, add `--no-browser` to the Studio command. Use the same port for Studio and MCP. The MCP process registers as a Studio client, which keeps Studio available if you close the browser tab.

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

## Work with Studio

| Goal | Tools |
| --- | --- |
| Find a project and inspect the profile it contains | `list_projects`, `list_profile_files`, `read_profile_file` |
| Edit a saved workspace file | `save_profile_file` uses the SHA-256 from the read to guard against concurrent edits |
| Find installed game names, map names, and AI identities | `get_installed_content`, `scan_installed_content` |
| Look up a unit by name or ID | `build_unit_catalog`, `find_units` |
| Start or inspect a test match | `start_test_match`, `get_run_status`, `list_matches` |
| Run a round-robin tournament or numeric parameter sweep | `start_experiment`, `get_run_status`, `list_experiments`, `get_experiment` |
| Control an active run | `set_experiment_paused`, `cancel_run` |

Start tools return an operation ID. Use `get_run_status` to see progress and any launch or preparation error. When an experiment finishes, use its ID with `get_experiment` for standings and the per-game results. For a parameter sweep, specify the workspace project/profile as the first player using the project key as `short_name`. Use an installed AI as the second player. The sweep's line and column are one-based, and its candidate values replace the selected numeric literal. Pass the optional `sha256` from `read_profile_file` in the sweep object to guard against concurrent source edits.

Saving through MCP updates the workspace file. Use Studio's installation workflow to make those changes available for ordinary matches with an installed AI. A parameter sweep builds its candidates from the saved workspace profile.
