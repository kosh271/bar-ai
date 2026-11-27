"use strict";

window.StudioTips = (() => {
    // [Scale anchor in elmos, reference measurement, reference name].
    // Unit widths use the BAR collision volumes. Ranges and blast diameters use
    // the BAR weapon definitions. Map dimensions are installed map scan values.
    const distanceScale = [
        [1, 24, "the width of a Grunt"],
        [5, 20, "the width of a Tick"],
        [10, 20, "the width of a Tick"],
        [15, 16, "one building-grid step"],
        [20, 24, "the width of a Grunt"],
        [35, 32, "the width of a Sentry"],
        [55, 52, "the width of an Agitator"],
        [90, 97, "the width of a Juggernaut"],
        [145, 145, "an Armada Commander's build range"],
        [235, 215, "a Grunt's weapon range"],
        [380, 430, "a Sentry's weapon range"],
        [615, 620, "an Overwatch's weapon range"],
        [950, 950, "a Juggernaut's rocket range"],
        [1100, 1280, "the diameter of an Armada nuke blast"],
        [1610, 1280, "the diameter of an Armada nuke blast"],
        [2605, 2100, "an Armada Radar Tower's detection range"],
        [4215, 4650, "the range of the Basilica long-range plasma cannon"],
        [6820, 6144, "the height of Comet Catcher Remake"],
        [11035, 12288, "the height of Supreme Isthmus"],
        [17855, 16384, "the height of Pit of Azar"],
    ];
    const quantities = new Intl.NumberFormat("en", {maximumSignificantDigits: 8});
    const multiples = new Intl.NumberFormat("en", {maximumSignificantDigits: 2});
    const liveDescriptions = new WeakMap();

    function compareDistance(elmos, {signed = false} = {}) {
        if (!Number.isFinite(elmos)) return "";
        const distance = Math.abs(elmos);
        if (!distance) return "No distance (0 elmos).";
        const [, reference, name] = distanceScale.find(([anchor], index) => {
            const next = distanceScale[index + 1]?.[0];
            return next === undefined || distance <= (anchor + next) / 2;
        });
        const ratio = distance / reference;
        let comparison;
        if (ratio < 0.2) comparison = `Much smaller than ${name}`;
        else if (ratio < 0.4) comparison = `Less than half of ${name}`;
        else if (ratio <= 0.6) comparison = `About half of ${name}`;
        else if (ratio < 0.82) comparison = `Smaller than ${name}`;
        else if (ratio <= 1.2) comparison = `About ${name}`;
        else if (ratio < 1.8) comparison = `Larger than ${name}`;
        else comparison = `About ${multiples.format(ratio)} times ${name}`;
        const sign = signed && elmos < 0 ? "−" : "";
        return `${comparison} (${sign}${quantities.format(distance)} elmos).`;
    }

    function description(control) {
        return [control.dataset.tooltip, liveDescriptions.get(control)?.()].filter(Boolean).join(" ");
    }

    function refreshDescription(control) {
        const text = description(control);
        if (text) control.setAttribute("aria-description", text);
        else control.removeAttribute("aria-description");
        return text;
    }

    function dynamic(control, readDescription) {
        control.dataset.liveTooltip = "";
        liveDescriptions.set(control, readDescription);
        refreshDescription(control);
        return control;
    }

    function distance(control, factor = 1, {disabledWhenNegative = false, wholeUnits = false} = {}) {
        const group = control.ownerDocument.createElement("span");
        group.className = "distance-control";
        const readout = control.ownerDocument.createElement("span");
        readout.className = "distance-readout";
        // The input announces the readout as its description, including inside implicit labels.
        readout.setAttribute("aria-hidden", "true");
        if (control.parentNode) control.replaceWith(group);
        group.append(control, readout);
        function update() {
            const value = control.valueAsNumber;
            let text = "";
            if (control.validity.valid && Number.isFinite(value)) {
                const elmos = (wholeUnits ? Math.trunc(value) : value) * factor;
                text = disabledWhenNegative && value < 0 ? "Distance limit disabled." : compareDistance(elmos, {signed: true});
            }
            readout.textContent = text;
            readout.hidden = !text;
            const help = [control.dataset.tooltip, text].filter(Boolean).join(" ");
            if (help) control.setAttribute("aria-description", help);
            else control.removeAttribute("aria-description");
        }
        control.addEventListener("input", update);
        control.addEventListener("change", update);
        update();
        return group;
    }

    const workspace = {
        home: "Reload Studio and open Overview. Save any edits you want to keep before leaving this page.",
        project: "Switch to another AI project. Its profiles and files replace the ones shown in the editor. Studio asks before discarding unsaved edits.",
        profile: "Switch to another set of settings for this AI. Studio asks before discarding unsaved edits to the current profile.",
        "new-project": "Create an editable AI from a copy of an installed game's Hard profile. The original game files stay in place.",
        clone: "Copy the saved profile inside this project so you can try changes independently.",
        "manage-project": "Rename, copy, merge, import, export, or delete projects and profiles.",
        reload: "Read the profile files from disk again. Studio asks before discarding unsaved edits.",
        undo: "Reverse the most recent recorded edit in this profile. Save afterward to write the reverted version to disk.",
        review: "See which files have unsaved changes. You can restore individual files to their last saved version.",
        "compare-profiles": "Compare this profile, including unsaved edits, with a saved profile. The comparison leaves both profiles intact.",
        "project-history": "Compare and restore saved project checkpoints. A restore can affect files across multiple profiles.",
        "save-install": "Save this profile and install or update the selected AI in BAR. BAR may need a restart to refresh its AI list.",
        save: "Write the edited profile files to disk after validation. Use Save & install to update the copy BAR runs.",
        "dialog-close": "Close this dialog and return to the workspace."
    };

    const navigation = {
        manage: "Choose which BAR installation and engine Studio uses, and manage the AI copies BAR can run.",
        overview: "See the selected profile, check its settings, or jump to an editor.",
        opening: "Set the first units a new factory tries to build. These queues can delay regular production.",
        production: "Tune which units factories favor as income and battlefield conditions change. Increasing one unit's relative weight lowers the others' shares.",
        economy: "Tune how the AI expands, assigns builders, and reacts to resource shortages.",
        defense: "Set when the AI builds defenses and which structures it requests after finishing other buildings.",
        combat: "Tune how the AI reacts to enemy unit types and chooses which responses to prioritize.",
        units: "Edit how the AI classifies units. Other rules may use these roles and attributes to decide which units qualify.",
        placement: "Control space around buildings and how the AI reads the map. Extra clearance can reduce congestion and leave fewer build sites.",
        advanced: "Open profile files and custom scripts directly. Edits here affect the same files as the visual editors.",
        match: "Run installed AIs without opening the game window. Compare results, inspect logs, or record a replay.",
        experiments: "Compare AI profiles over multiple games or vary one number to test its effect. Results depend on the chosen maps, opponents, and limits."
    };

    const fields = {
        "Configuration layer": "Pick one settings file to edit. The AI may load later files that override values in this one.",
        "Settings file to edit": "Pick one building-placement file to edit. Use the preview to see how later files can replace its values.",
        "Add unit ID": "Enter the game's internal unit ID. Verify the factory can build it before relying on the resulting weights.",
        Situation: "Show the weights for land, water, or a response to enemy aircraft. Edits apply to the selected situation.",
        "Friendly response role": "Choose the type of friendly unit whose counter rules you want to edit.",
        "Enemy role": "Choose the enemy unit category this response reacts to, such as aircraft. The AI must recognize the role name.",
        "Investment ratio": "Multiply the known enemy units' metal cost by this number. The AI compares that result with the adjusted metal cost of the counters it already has. Raising the ratio can justify building more counters.",
        Importance: "Set how strongly this response competes with other responses. This is a relative weight. The investment ratio and production rules also affect the result.",
        "Add role": "Enter a role that other rules can use to select this unit, such as a builder or fighter.",
        "Add attribute": "Enter an extra tag that other rules can use to identify this unit. Use a tag your AI recognizes.",
        "Completion count": "Request the follow-up building after this many qualifying completions. The counting scope determines which buildings contribute.",
        "Nearby search radius (elmos)": "Count standing copies of the chosen building within this distance of the one that just finished. An elmo is a unit of game distance. A larger radius can group separate sites.",
        "Count completions": "Count completions across this AI or within nearby groups. Nearby counting lets separate clusters trigger their own follow-up.",
        "Cluster terrain": "Choose the terrain category whose defense order you want to edit. Other terrain categories have their own lists.",
        "Preventive defenses (first slots)": "Away from the front line, the AI considers only this many entries from the top of the cluster defense order. Reordering changes which defenses can be built there.",
        "Game time (seconds)": "Set when the scheduled defense becomes due in simulated seconds. Builders and resources still determine when it gets built.",
        "Defense building ID": "Enter the game's internal ID for the building requested by this chain.",
        "Offset distance (elmos)": "Set how far from the completed building to look for the new defense, in game units. Greater distance can spread the defenses out.",
        "Chance (%)": "Set the chance to request this defense when the completed building triggers the action. A request still needs a usable site and available builders.",
        Chain: "Append to an existing sequence or start a separate chain for this completed building.",
        "Offset direction": "Choose which direction from the completed building to try placing the new defense.",
        "New completed building ID": "Enter the game's internal ID for the building that should trigger the chain.",
        "New completed building category": "Choose the category where the new trigger belongs in the defense settings.",
        "Completed building category": "Filter the buildings whose completion can trigger defense chains.",
        "Buildings covered by this rule": "Choose the building group whose placement settings you want to edit. Changes can affect every building in that group.",
        "Target count": "Set how many units this rule aims to maintain. If units are lost, the rule can request replacements when its other conditions allow.",
        "Start after (min)": "Set the earliest game minute when the recruitment rule can run. Other requirements still apply.",
        "Cooldown (sec)": "Set the wait between requests in simulated seconds. A longer wait gives other orders more time but slows replenishment.",
        "Milestone buildings (optional IDs)": "List building IDs separated by commas. The rule waits until the AI has had each building at least once. Losing a building after it counts leaves the requirement met.",
        "Enemy role gate (optional)": "Require a known enemy unit category before recruiting. Leave it blank to run without this enemy requirement.",
        "Min enemy metal cost": "Set the minimum total metal cost of known enemy units in the category named in Enemy role gate, such as aircraft. Leave that field blank to ignore this minimum.",
        "Profile file": "Choose a file from this profile. Edits affect this file, which may be shared by multiple rules or included by other scripts.",
        "Show games": "Filter the tracker to all, active and queued, or finished games. This only changes which rows you see.",
        Game: "Choose the installed BAR game version. It determines the units and rules used in the run, so keep it fixed when comparing results.",
        "Primary map": "Choose the map for a single game and the fallback map for a series. Terrain and starting positions can change the result.",
        Project: "Filter match history by project name when you apply the filters.",
        Profile: "Filter match history by profile name when you apply the filters.",
        Map: "Filter match history by map name when you apply the filters.",
        Checkpoint: "Filter match history by the beginning of a saved checkpoint ID.",
        "Target project": "Choose the project that contains the saved profile you want to compare.",
        "Target saved profile": "Choose the saved profile to compare with the one open in Studio.",
        "From checkpoint": "Choose the older saved snapshot as the starting point for the comparison.",
        To: "Compare against another checkpoint or the files currently saved on disk.",
        "Checkpoint description": "Name this snapshot so you can recognize why it was saved when you revisit the project history.",
        "New profile name": "Name the new set of AI settings. Give it a distinct name to tell it apart from its source when testing.",
        "New AI name": "Name the new independent AI project. It has its own profiles and can be installed separately.",
        "AI name": "Name the editable AI project Studio will create from BAR.",
        "Profile name": "Name the settings profile Studio will add to this project.",
        "BAR installation folder": "Enter the main Beyond All Reason folder. Studio uses it to find installed game content and engines.",
        "Merge with": "Choose the second saved profile. The merged result will be a new profile in the current project.",
        "Prefer on conflict": "Choose which profile supplies the value when Studio cannot combine both edits. Review those choices in the preview.",
        "Project display name": "Enter the name shown for this project in Studio. This is a display name for the existing project.",
        "Profile display name": "Enter the name shown for this profile in Studio. This is a display name for the existing profile.",
        "Search installed maps": "Filter the available maps by name. The maps you already selected stay in the pool.",
        "Game time limit (minutes)": "Cap each game in simulated minutes. A short cap can leave slow strategies without enough time to win.",
        "Game limit": "Set the maximum number of games in the experiment. A small budget can leave some matchups or swapped-side pairs unfinished.",
        "Real-time limit (minutes)": "Cap the whole experiment in wall-clock minutes, rounded to the nearest second. Time spent paused still uses this budget.",
        "Literal line": "Enter the source line containing the number you want to test. The preview shows the line from the saved file.",
        "Literal column": "Choose a character in the number shown below. The preview marks your column and highlights the full number that each candidate replaces. Counting starts at 1, including spaces and tabs.",
        "Candidate values (2–16)": "Enter 2 to 16 distinct numbers separated by commas. Studio tests temporary variants of one setting against the same opponent.",
        "Source file": "Choose the saved profile file that contains the number you want to vary. Studio tests copies of that file.",
        "Baseline opponent": "Choose the installed opponent every candidate faces. A good result against this profile may differ against other opponents.",
        "Experiment type": "A tournament plays profiles against each other. A sweep tries several numbers for one setting against a fixed opponent.",
        "Saved experiment": "Open a saved run to review its standings and game results.",
        "Switch threshold lower bound": "Set the shortest random switching threshold in game seconds. The AI also weighs elapsed time and its stored metal before switching.",
        "Switch threshold upper bound": "Set the longest random switching threshold in game seconds. A wider range makes factory switching less predictable.",
        "Switch formula: frame exponent": "Control how quickly elapsed game time increases pressure to switch factories. Higher values make time count more strongly.",
        "Switch formula: stored-metal multiplier": "Control how strongly stored metal contributes to factory switching. Raising it makes the same stockpile count for more.",
        "Simultaneous metal extractor upgrades": "Cap how many metal extractor upgrades the AI can attempt at once. A higher cap can improve income sooner and tie up more builders.",
        "Metal cluster range": "Set the distance in game units used to group metal spots. A longer range can group deposits that the AI would otherwise treat separately.",
        "Target construction time": "Set a target build time in seconds. A shorter target can make the AI assign more building power to finish sooner.",
        "Mobile buildpower ratio": "Set how much mobile construction power the AI wants per unit of metal income. A higher ratio can mean more builders competing for factory time.",
        "Team-size scaling": "Adjust how strongly team size affects economy decisions. Test with the intended team size because solo results can differ.",
        "Metal spot cap": "Limit the share of map metal spots this AI aims to claim. The adjacent ally setting governs whether allied spots count toward the cap.",
        "Minimum generator income": "Set the minimum power output a generator must meet to pass this filter. A higher value can exclude smaller power sources.",
        "Generator cost ratio": "Edit the generator cost ratio stored in this profile. BARb versions can use different generator selection rules, so verify the effect in the version you test.",
        "Metal empty: fraction of storage": "Set how low stored metal must fall before the AI treats it as empty. A larger fraction makes that condition kick in sooner.",
        "Metal full: fraction of storage": "Set how much metal storage must fill before the AI treats it as full. A smaller fraction marks it full earlier.",
        "Early-game energy grace period (minutes)": "Set how long the early energy rules apply before switching to the later rules. The two sets can flag a shortage at different levels.",
        "Early-game energy stall: fraction of storage": "During the early period, flag a stall when energy use exceeds income and storage falls below this fraction. Raising it flags trouble sooner.",
        "Late-game energy empty: fraction of storage": "Set the stored energy level that counts as empty later in the game. Reaching it also counts as an energy stall.",
        "Late-game energy stall: fraction of storage": "Flag a later energy stall when use exceeds income and storage falls below this fraction. The empty-energy threshold can also trigger a stall.",
        "Energy full: fraction of storage": "Set the fraction of energy storage that must fill before the AI treats it as full. A smaller fraction marks it full earlier.",
        "Factory assist required: metal fraction": "Ask for factory construction help when stored metal rises above this share of capacity. Enter 0.5 for half. Lower values can pull helpers off other jobs sooner.",
        "Heavy energy base-build metal cost": "Set the metal cost that qualifies a stationary power building for heavy base-building treatment. Lowering it includes less expensive buildings.",
        "T1/T2 base builder metal cost split": "Set the metal cost dividing basic and advanced base builders. Raising it classifies more builders as basic under this rule.",
        "Defense may start after minutes (or other conditions)": "Set the game minute after which this condition allows defense construction. Economy or threat can allow it earlier.",
        "Defense may start above metal income (or other conditions)": "Set a metal income level that can allow defense construction. Time or enemy threat can also allow it.",
        "Defense may start above enemy mobile threat (or other conditions)": "Set how much mobile enemy threat can allow defense construction. Time or metal income can also allow it.",
        "Air production enemy-AA threat limit (legacy)": "Set how much enemy anti-air threat this older rule tolerates. A higher limit allows air production to continue against stronger anti-air."
    };

    const actions = {
        "Brief guide": "Read the short guide to this section, including what edits affect and how to try them.",
        "In-depth guide": "Read the full guide, including examples, terms, and how the settings interact.",
        "Preview settings from multiple files": "Try a load order and see which placement settings win. This preview leaves the files and the AI's actual startup order intact.",
        "Preview combined layers": "Try a load order and see which file supplies each combined value. Studio only applies this order inside the preview.",
        "Use example order": "Replace the preview order with the example sequence for the conditions you checked.",
        "Add settings file": "Append this file to the preview. Values from later files can override earlier values.",
        "Add file": "Append this file to the preview. Values from later files can override earlier values.",
        "Save, install & test": "Save the profile, update the installed AI, and open Test match so you can choose what to run.",
        "Create AI from this project": "Copy this saved project into an independent AI you can modify and install separately.",
        "AI install & remove": navigation.manage,
        "Test match": navigation.match,
        "Open Test match": navigation.match,
        "Validate profile": "Check the edited profile for recognized configuration problems and missing references. A match still checks how it behaves in the game.",
        "Change unit catalog": "Choose another installed game to read unit names and build options from. This changes the editor's reference data.",
        "Load unit catalog": "Read unit names and build options from an installed game so Studio can offer searchable pickers.",
        "Installation settings": navigation.manage,
        "Read game units": "Scan the selected game for its units and factory build options. This may take a moment.",
        "Cancel catalog scan": "Stop reading game units. You can start another scan later.",
        "Use catalog": "Return to the editor with the scanned catalog available for names and build options.",
        "Add catalog unit": "Add the selected buildable unit to this factory's production choices. Its weights determine how it competes with existing units.",
        "Add unit": "Add this unit ID to the factory's production choices. An invalid ID or unavailable build option can prevent the AI from using it.",
        "Add response": "Add a counter rule for the selected friendly role. Its ratio and importance influence how much production shifts toward this response.",
        "Update completion rule": "Replace this rule. The trigger, count, and grouping determine when the follow-up building is requested.",
        "Add completion rule": "Create a rule that requests a building after the chosen number of completions.",
        "Remove completion rule": "Delete this follow-up rule. Other rules can still request the same building.",
        "Add to order": "Append the defense to this terrain's list. Its position determines when the AI considers it.",
        "Add scheduled build": "Add a defense request for the chosen game time. The request may wait for builders or resources.",
        "Update scheduled build": "Replace this scheduled request with the selected building and game time.",
        "Remove scheduled build": "Delete this timed request. Other defense rules can still request the same building.",
        "Add defense action": "Add a building request to the selected chain. Its offset and chance affect where and how often it is attempted.",
        "Include reserved lanes": "Include the terrain markers that keep factory exits and travel lanes clear. Apply this only where the rule should account for reserved space.",
        "Preset: Fighter reserve (30)": "Add a rule for a 30-fighter Armada reserve starting after two game minutes. Its orders can compete with regular factory production.",
        "Preset: Bomber reserve (50)": "Add a rule for a 50-bomber Armada reserve after its milestone buildings and metal requirement are met. It alternates with regular production.",
        "Update recruitment rule": "Replace this recruitment rule with the values shown. The factory can change what it builds once the rule's conditions are met.",
        "Add recruitment rule": "Add a rule that asks a factory to maintain this group of units when its conditions are met.",
        "Remove recruitment rule": "Remove this special request. The factory can still produce the unit through its normal rules.",
        "Add layer": "Add this file to the end of this startup list. It can override values from files loaded earlier.",
        "Copy replay path": "Copy the replay file's location so you can find it or share the path on this computer.",
        "Copy to BAR demos": "Copy the recorded replay into BAR's demos folder so the replay can appear in BAR.",
        Cancel: "Cancel this game. Other games in the queue can continue.",
        "Apply limit": "Change how many new games Studio starts at once. Running games finish normally, and a limit of zero pauses new starts.",
        "Refresh installed content": "Rescan games, maps, and AIs after an install or update. The scan can take time and refreshes the saved results.",
        "Scan installed content": "Find the games, maps, and AIs this engine can use. The scan result is saved for future Studio sessions.",
        "Cancel queue": "Stop the current queue, including running and waiting games. Finished results remain available.",
        "Cancel operation": "Stop the current Studio operation. You can restart it when ready.",
        "Save & install active AI": "Save your edits and update the AI BAR runs before queuing a test.",
        "＋ Add AI": "Add another AI-controlled player to this team. Larger teams can change the match balance and use more resources.",
        "Apply Quickstart defaults": "Set starting resources to 2,000 metal and 5,000 energy and turn on the starter base and Base Builder Turret.",
        "Apply filters": "Update the history list to show runs matching the entered fields.",
        Clear: "Clear the history filters and show all saved matches.",
        "New AI from BAR": workspace["new-project"],
        "Save with warnings": "Save the files with these warnings acknowledged. The warned-about settings may still behave unexpectedly in BAR.",
        "Discard all unsaved changes": "Return all edited files in the current profile to their last saved contents.",
        "Restore this file": "Return this file to its last saved contents. Edits to other files stay in the editor.",
        Compare: "Compare the open profile with the selected saved profile. You can inspect both values and exact source differences.",
        "Restore this checkpoint": "Restore the project files from this checkpoint. This can change several profiles, so review its difference first.",
        "View diff": "See how the project files differ from this checkpoint.",
        "Create checkpoint": "Record the current saved project as a checkpoint you can compare or restore later.",
        "Duplicate profile": workspace.clone,
        "Create AI": "Create an independent editable project. You can install it separately when it is ready to test.",
        "Create profile": "Save a new editable set of AI settings in the current project. Install the AI update before testing it in BAR.",
        "Find installed game versions": "Look inside the entered BAR installation for games you can copy the Hard profile from.",
        "Reload workspace": "Try reading the projects and selected profile from disk again.",
        "Preview merge": "Review which changes would be combined and where one profile's value would win. Studio saves the result only when you create the merged profile.",
        "Create merged profile": "Save the reviewed merge as a new profile. The two source profiles remain available for comparison.",
        "Rename project": "Save the project's new display name.",
        "Export project ZIP": "Download the whole project and its profiles as a ZIP archive you can share or import.",
        "Delete project": "Delete this editable project and its profiles from the workspace. Use an export if you need a copy to restore later.",
        "New profile from BAR": "Add an editable copy of the Hard profile from the installed game you select.",
        "Make default": "Select the profile the AI uses when no specific profile is chosen.",
        "Delete profile": "Delete this set of settings from the project. The game can use the updated profile list after you install the AI again.",
        "Rename profile": "Save the profile's new display name so it is easier to identify.",
        "Export profile ZIP": "Download this profile as a ZIP archive for sharing or importing into a project.",
        "Merge profiles": "Combine settings from two saved profiles into a new profile, with a preview before anything is written.",
        "Import profile ZIP": "Unpack the selected profile archive into this project under the entered name.",
        "Import ZIP": "Unpack a Studio project archive and add its profiles as an editable project.",
        "Save installation selection": "Use this BAR folder and engine for future installs and tests. Switching either can change which games, maps, and AIs are available.",
        "Change BAR installation": "Open the folder and engine fields. Saving a different selection changes where Studio looks for game content.",
        "Install / update selected AI": "Copy the saved AI into the selected BAR engine. Restart BAR if its AI list is already open.",
        "Remove selected AI from BAR": "Remove the installed AI copy from the selected engine. The editable project stays in Studio.",
        "Start experiment": "Queue the selected tournament or sweep. The game and real-time budgets can stop it before all planned comparisons finish.",
        Resume: "Allow new experiment games to start again where scheduling left off. The wall-clock budget has continued during the pause.",
        "Pause new games": "Let active games finish and hold new starts. The experiment's real-time limit keeps counting down.",
        "Cancel experiment": "Stop this experiment instead of holding it for later. Its results may represent only part of the planned schedule."
    };

    const sections = {
        "Earlier values": "See which earlier files supplied values that a later file replaced.",
        "Show settings not combined": "See where the preview could not combine values because the files use different kinds of settings.",
        "Show ignored values": "See which later values the preview skipped because they could not replace an existing settings group.",
        "Cluster defense budget": "Reveal the settings that limit how much metal the AI can commit to defending a cluster.",
        "Add completed building": "Open the form for a building whose completion should trigger a defense chain.",
        "Team telemetry": "Show each team's recorded resources and combat totals for this game.",
        "Engine report": "Read the engine's end-of-run summary, including diagnostics when available.",
        "Game logs": "Show available logs for this result, including startup checks when recorded.",
        "Startup configuration layers": "Show the files the AI loads at startup. Later files can replace earlier values.",
        "Startup configuration layers (script/init.as)": "See which files the startup script can load and in what order. Later files can replace earlier values.",
        "Script structure (includes & hooks)": "Show scripts this file loads and the entry points the game can call."
    };

    function summary(label) {
        if (!label) return "";
        if (Object.hasOwn(sections, label)) return sections[label];
        if (label.startsWith("Adjustable script constants")) return "Show the script values Studio knows how to edit. Changing a constant affects the rules that read it.";
        if (label.startsWith("Game results")) return "Show individual matchups and outcomes behind the standings, with logs when available.";
        if (/warnings?/.test(label)) return "Expand to read the warnings and see which settings or data they concern.";
        return `Expand to see the details for ${label.split("  ·  ", 1)[0]}.`;
    }

    function set(control, description) {
        if (description) {
            control.dataset.tooltip = description;
            control.setAttribute("aria-description", description);
        }
        return control;
    }

    function addDescription(control, id, parent) {
        const context = parent.ownerDocument.createElement("span");
        context.hidden = true;
        context.id = id + "-help";
        context.textContent = control.dataset.tooltip;
        parent.append(context);
        control.setAttribute("aria-describedby", context.id + " " + id);
        control.removeAttribute("aria-description");
    }

    function attach(root) {
        const tip = root.createElement("div");
        tip.className = "control-tooltip";
        tip.setAttribute("aria-hidden", "true");
        tip.hidden = true;
        let target, timer, closeTimer, ready = false;
        const removal = new MutationObserver(() => { if (!target?.isConnected) hide(); });

        function hide() {
            clearTimeout(timer);
            clearTimeout(closeTimer);
            removal.disconnect();
            target = null;
            ready = false;
            tip.hidden = true;
        }

        function show(control) {
            clearTimeout(closeTimer);
            if (target === control) return;
            hide();
            if (!control) return;
            target = control;
            timer = setTimeout(() => {
                if (!control.isConnected || !control.getClientRects().length) return hide();
                (control.closest(":popover-open, dialog[open]") || root.body).append(tip);
                ready = true;
                render(control);
                removal.observe(root.body, {childList: true, subtree: true});
            }, 450);
        }

        function render(control) {
            tip.textContent = description(control);
            const anchor = control.getBoundingClientRect();
            const vw = root.documentElement.clientWidth, vh = root.documentElement.clientHeight;
            tip.hidden = !tip.textContent || anchor.bottom <= 0 || anchor.top >= vh;
            if (tip.hidden) return;
            const {offsetWidth: width, offsetHeight: height} = tip;
            tip.style.left = Math.max(8, Math.min(anchor.left, vw - width - 8)) + "px";
            const top = anchor.bottom + 5 + height <= vh ? anchor.bottom + 5 : anchor.top - height - 5;
            tip.style.top = Math.max(8, top) + "px";
        }

        for (const name of ["input", "change"]) root.addEventListener(name, event => {
            if (!liveDescriptions.has(event.target)) return;
            refreshDescription(event.target);
            if (event.target === target && ready) render(target);
        });

        const described = node => node?.closest?.("[data-tooltip], [data-live-tooltip]");
        function pointerControl(node) {
            const control = described(node);
            return control || described(node?.closest?.("label")?.control);
        }
        function scheduleHide() {
            clearTimeout(closeTimer);
            closeTimer = setTimeout(hide, 120);
        }
        root.addEventListener("pointerover", event => {
            if (event.pointerType === "touch") return;
            const control = pointerControl(event.target);
            if (event.target === tip) clearTimeout(closeTimer);
            else if (control) show(control);
            else if (target) scheduleHide();
        });
        root.addEventListener("pointerout", event => {
            if (event.target === tip || pointerControl(event.target) !== pointerControl(event.relatedTarget)) scheduleHide();
        });
        root.addEventListener("focusin", event => {
            if (event.target.matches(":focus-visible")) show(described(event.target));
        });
        root.addEventListener("focusout", event => { if (event.target === target) hide(); });
        root.addEventListener("pointerdown", hide, true);
        root.addEventListener("scroll", () => {
            if (target && target === root.activeElement && target.matches(":focus-visible")) {
                if (ready) render(target);
            } else hide();
        }, true);
        root.addEventListener("close", hide, true);
        root.addEventListener("beforetoggle", event => { if (event.newState === "closed") hide(); }, true);
        root.addEventListener("keydown", event => {
            if (event.key !== "Escape" || !target) return;
            if (!tip.hidden) {
                event.preventDefault();
                event.stopPropagation();
            }
            hide();
        }, true);
        root.defaultView.addEventListener("resize", hide);
    }

    function start(root) {
        for (const control of root.querySelectorAll("[data-workspace-tip], #navigation button")) {
            set(control, workspace[control.dataset.workspaceTip || control.id] || navigation[control.dataset.page]);
        }
        attach(root);
    }

    return {set, addDescription, start, compareDistance, distance, dynamic, field: name => Object.hasOwn(fields, name) ? fields[name] : "",
        action: name => Object.hasOwn(actions, name) ? actions[name] : "", summary,
        page: name => navigation[name]};
})();

window.StudioTips.start(document);
