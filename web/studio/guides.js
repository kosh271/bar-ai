"use strict";

window.StudioGuides = {
    overview: {
        intro: "Overview is the starting point for building and testing a BARbarIAn strategy in Beyond All Reason (BAR). The AI project is your editable copy, and its profiles are alternate strategies you can select when starting a match. Check the project and profile shown here before editing. Use the cards to find the settings you need, then validate, save, install, and test your changes.",
        brief: [
            "Choose an AI project and profile in the sidebar. Use New AI from BAR to start from the installed game's BARbarIAn Hard profile.",
            "Duplicate profile to try a variant inside the same AI, or use Create AI from this project to make a separate project.",
            "Choose a section based on what you want to observe. Opening controls first factory queues, Production changes later unit preferences, and Defense changes how the AI requests defensive structures.",
            "Return to Overview for Validate profile and use Review changes in the header. Close BAR, then use Save, install & test to write and deploy the files and open Test match. Set up the roster there and choose Queue match."
        ],
        detail: [
            ["Where changes live", "The project contains all of its profiles. Selecting one loads its files into the browser. Edits are pending until you save, and Undo works on pending edits. Compare profiles shows differences between profiles. A local Project history checkpoint captures the saved files of the whole project, including other profiles. Restoring requires a clean project and confirmation, changes the working files, and leaves previous checkpoints available."],
            ["How the pieces meet", "Configuration files supply values and AngelScript files supply executable rules. The profile's script/init.as tells the AI which configuration layers to load and in what order. Game options can change that list. On a configuration page, open Preview combined layers, choose the files or a preset matching the game's options, then find the setting and read its Source. In Test match, open View logs and look for lines beginning with Load config to confirm which files the AI loaded, if that build reports them."],
            ["From edit to evidence", "Validation checks file structure and known references with the information Studio has. Save writes the editable project. Install deploys its saved profiles for one selected engine. Then Test match launches the installed copy. The smoke test checks startup and script loading. To see whether a behavior actually changed, look at the in-game situation that should trigger it or compare replays from the same map, faction, and game options."],
            ["A first comparison", "Duplicate Hard and edit one opening step for the Armada Bot Lab (armlab), for example. Keep Hard as your comparison. Use Save, install & test on Overview. In Test match, put your edited profile in the first slot as Armada and Hard in the opposing slot. Enable Record replay and run a second game with Hard in the first slot as well. Keep the map, starting position, faction, and opponent the same between runs. Compare the first slot's initial units if it builds that Bot Lab in both games."]
        ],
        glossary: [
            ["Project", "The editable copy of one AI, containing one or more profiles."],
            ["Profile", "A named set of configuration and script files for one strategy."],
            ["Layer", "One configuration file loaded as part of a profile, potentially overriding earlier values."],
            ["Checkpoint", "A local Git snapshot of the saved files across the project."],
            ["Smoke test", "A short engine run that checks startup and script loading."]
        ]
    },
    opening: {
        intro: "Opening controls what a factory tries to build first, including factories the AI adds later in a match. Each supported factory can have several alternative queues. A relative weight helps choose one queue, and each ordered step requests a role, such as builder or raider, and a count. Use this page to experiment with scouting, expansion, or pressure. Use Production to change the preferences the AI uses for normal factory decisions.",
        brief: [
            "Select the factory. Read each queue from top to bottom. A queue of builder ×1, raider ×2 asks for a builder before the two raiders.",
            "Change a step's Role or Count, move it, or add a step. Adjust queue weights to favor one opening over another. Two queues weighted 3 and 1 show shares of 75% and 25%.",
            "Check the source path at the bottom. If Studio follows a forwarding include, the included file receives the edit.",
            "Use Save & install, then enable Record replay in Test match. Watch for the first units from that specific factory. Repeat the same setup if you want to observe alternative queues."
        ],
        detail: [
            ["Choosing the opening", "Studio recognizes the GetOpenInfo() table in script/misc/commander.as. In the sampled Hard profile, the factory manager calls GetOpener each time a factory is registered, including later factories. It looks up that factory's internal ID and rolls against the listed queue weights. The script uses its default queue when there is no matching factory or the roll yields no choice. The visual editor preserves that default queue in the script. The displayed shares describe the choice among listed queues. A small sample can produce very different frequencies."],
            ["Turning roles into orders", "Once a queue is chosen, the factory script considers its roles in order and resolves them through that factory's build choices. The role and count express what the strategy is asking for. The exact unit and number actually finished depend on build options, task eligibility, and whether the factory can execute its orders. Changing the normal production weight for a unit is a separate edit."],
            ["Supported source forms", "Studio can follow a file that only forwards to one include. The destination can also be used by other scripts. The editor recognizes a narrow layout of factory entries, queues, and role steps. Comments can prevent safe moves or removals, and custom script constructs may require All files. A factory symbol matched to a friendly unit name still needs to resolve correctly in the installed game."]
        ],
        glossary: [
            ["Opener", "The initial build instructions the factory script selects."],
            ["Queue", "One possible ordered list of opener steps."],
            ["Relative weight", "A value compared against the other weights in the same choice."],
            ["Role", "A task label such as builder or raider. The script resolves it to a unit the factory can build."],
            ["Include", "A script reference that brings another source file into the program."],
            ["Factory ID", "The internal name the game uses for a factory unit definition."]
        ]
    },
    production: {
        intro: "Production shapes the units a factory favors during normal play. Choose a settings file, a factory, and a situation, then edit weights for different levels of income. The percentages describe the weights on the screen. Actual production also depends on which units are available, enemy activity, the economy, and the settings the AI loads at match start. Use Opening to change a factory's initial queue.",
        brief: [
            "Open Preview combined layers, use the order that matches your test's game options, and find the file named under Source for the tier you want. Close the preview, select that file under Configuration layer, then choose Factory and Situation.",
            "Find the unit you want and adjust an existing tier weight. Compare the percentage before and after. A newly added row starts with zero weights, so give it a positive weight in the situations and tiers you want to test.",
            "Use the catalog to look for build options and check the selected game's actual factory menu. Removing a unit removes its row across every tier and situation in this file.",
            "Use Unit reserve & recruitment rules below the factory weights when you want a factory to build toward a target unit count or react to completed milestone buildings or enemy forces.",
            "Save and install. Observe the factory after its initial queue and compare the mix during the income range and situation you changed."
        ],
        detail: [
            ["How the table is stored", "Each entry under factory in factory.json or a named variant has a unit list. The weight for the first unit is the first value in each tier array, and so on. Studio edits these aligned arrays together when adding or removing a unit. It may decline structural edits when comments or inconsistent lengths prevent a safe rewrite. The displayed share divides each positive weight by the total positive weight in that column. Weights of 2, 1, and 0 display about 67%, 33%, and 0%."],
            ["Which weights the AI uses", "Income thresholds above the weight table are the boundaries between tier0, tier1, and higher bands. The inspected source uses the lower of average metal and energy income rates, scaled by an economy factor. It uses Enemy air when known enemy aircraft investment exceeds its measure of allied anti-air investment. Otherwise it selects Land or Water according to its map classification. The loader discards units the game does not recognize or the factory cannot build. Timing, terrain, energy, build limits, factory status, response demand, and repeated orders can all change what comes out."],
            ["Related choices", "Factory switching edits recognizable bounds in script/manager/factory.as separately from the layer selector. In the sampled Hard script the random bounds are multiplied by SECOND and compared through a threshold that depends on elapsed frames, metal income, and stored metal. They are part of a switching calculation, so interpreting them as the scheduled time of the next factory would be misleading. Inspect the script before using those controls."],
            ["When an edit seems ignored", "Follow the profile's startup layer order. A later file can replace a whole tier array. Check the installed game's factory build options and the match's faction and options. Studio shows configured thresholds. If your AI build logs factory selection with the tier number, use that to establish the band it reached. In a replay, displayed income and the units produced give context, while the runtime may use different averaging or scaling. Use Load unit catalog for a reference from a selected installed game and engine."]
        ],
        glossary: [
            ["Tier", "An income band indexing a list of unit weights. This use is separate from basic or advanced factory technology."],
            ["Weight", "A preference compared with the other eligible weights."],
            ["Situation", "The land, water, or enemy-air configuration group selected for editing."],
            ["Catalog", "A scanned reference of game units, names, and factory build options."],
            ["Factory switching", "How the AI decides when to move its production emphasis to another factory."]
        ]
    },
    economy: {
        intro: "Economy covers how the AI expands its metal supply, adds power, and allocates construction work. The page shows common settings from the selected file, plus a tree for the remaining values. Some profiles also expose script thresholds for calling storage empty or full. Start with the bottleneck you observed. Slow construction, shortages, and lack of expansion can come from different decisions.",
        brief: [
            "Use Preview combined layers with the order matching your game's options. Find the Source file for the value you want, then choose it under Configuration layer. For expansion, look at Metal spot cap, Metal cluster range, and Simultaneous metal extractor upgrades.",
            "For construction delays, inspect Target construction time and Mobile buildpower ratio. Check whether the AI lacks builders, metal, or energy before changing both settings.",
            "If available, use Economy script thresholds to adjust the fraction of storage used for the empty or full flags. A fraction of 0.2 means 20% of capacity.",
            "Save, install, and compare the timing of extractors, generators, and construction in similar matches. Open Explore settings or All files for values without a dedicated control."
        ],
        detail: [
            ["Getting metal", "A metal extractor collects metal at a map resource spot. In the inspected source, a metal spot cap of 0.25 limits expansion to roughly one quarter of the map's spots, and 1 disables the cap. Under Explore settings, expand economy and mex_max to find the second value. True counts the allied total and false counts this AI's extractors. Cluster range groups spots by in-game distance. Simultaneous metal extractor upgrades limits upgrade attempts, subject to spot access and builder technology."],
            ["Turning resources into projects", "Mobile buildpower is construction capability provided by moving builders. The ratio sets a target relative to income. Raising it raises the buildpower target the inspected factory manager compares against available buildpower when considering more builders. Target construction time is measured in game seconds and used when assigning builders. Team-size scaling affects some team economy decisions. Generator settings include a minimum output used in filtering and a cost ratio whose effect varies by version."],
            ["Other economy controls", "The settings tree can also show faction-specific generators and construction assistants, construction delays, and spending tradeoffs. For example, the sampled Hard file describes ms_pull as balancing metal demand for mobile construction and factory work as the AI claims metal spots. Read the source comments and match options when using a file with custom settings."],
            ["Flow versus stored resources", "Income is a rate. Storage is capacity. The current amount is the stock on hand. The sampled economy script compares the current stock with storage multiplied by a fraction and sets flags such as isMetalEmpty or isEnergyFull. Other code uses those flags to decide what to do. Energy stalling can involve both demand and stored energy, and the sampled script has separate early and later game logic."],
            ["Where edits land", "Common economy controls edit the selected economy file. Economy script thresholds edit uniquely recognized expressions in script/manager/economy.as or its forwarding target. Selecting a different JSON layer leaves those script controls pointed at the same script. Check game options and the startup script when a later economy file can override a value."]
        ],
        glossary: [
            ["Mex", "Shorthand for metal extractor."],
            ["Income", "Resources coming in per unit of game time."],
            ["Storage fraction", "Current resource stock divided by total storage capacity."],
            ["Stall", "A shortage that can slow production or construction as spending exceeds the resources available."],
            ["Buildpower", "The rate at which builders or construction assistants perform work."],
            ["Elmo", "One unit of in-game distance."]
        ]
    },
    defense: {
        intro: "Defense gives the AI several ways to request protective buildings. It can defend groups of metal spots, attempt a base defense at a certain game time, or react when construction finishes. Pick the control that matches the event you care about, then check which settings file the match loads. Requested defenses still need enough resources, a suitable site, and a constructor capable of building them.",
        brief: [
            "Use Cluster defense order to change candidates near metal spots. Choose Land or Water, add or move a slot, and check which building your faction uses for that slot.",
            "Use Scheduled base defenses for a timed attempt. For a shield after each reactor built as an energy task, use On building completed and choose the energy category and reactor ID.",
            "For a shield after two nearby towers, use Construction completion rules. Choose the tower and shield, select Within nearby groups, enter a count of 2 and a radius of 400 elmos, then add the rule. Once counted, those towers stay on the map and cannot count again for this rule.",
            "For file-based rules, use Preview combined layers to find the Source file, then select it under Configuration layer. Use Save & install and enable Record replay in Test match. Compare construction requests in the logs with structures that finished."
        ],
        detail: [
            ["Defending metal clusters", "Under porcupine in the build-chain file, the land and water arrays list slot numbers in the order the AI tries them. Each faction maps the same slot number to its own building using the unit lists. Repeating a slot gives the AI another candidate. Preventive defenses limits the portion considered away from the front. Threat, budget, terrain, and eligible constructors can cause entries to be skipped. The budget uses an income multiplier that can vary by map size, so a position in the list alone seldom explains what finished."],
            ["Scheduling a base build", "Scheduled base defenses stores pairs of a defense slot and a time in game seconds. For example, 300 is five simulated minutes. At the scheduled time the AI needs an eligible constructor and site. A later layer that supplies a base array can replace the whole schedule. The same slot can resolve to different buildings by faction."],
            ["Chaining follow-up tasks", "On building completed uses the building ID and the category of the task that built it. For example, a reactor normally built as an energy task can request a nearby shield. Existing chains run eligible actions in order, and a new chain may start independently. Chance is tested for each eligible completion. The offset uses the completed building's facing and suggests a starting location for the site search. Follow-up rules that point back at each other can repeatedly trigger further work."],
            ["Counting completions in a script", "Construction completion rules uses the profile's main script and is separate from the selected configuration layer. Across this AI counts matching completions for that AI. Within nearby groups looks for enough matching, unused buildings still present within the radius of the latest completion. When a group is counted, its buildings stay on the map and cease to count for that rule, even if the target cannot be queued. The radius measures grouping distance. Counts start fresh when the match starts or reloads. Existing custom callbacks can block adding these rules."],
            ["Finding the effective defense file", "In the sampled Hard profile, enabling Legion loads build_chain_leg.json after build_chain.json. In Preview combined layers, use Legion enabled and open porcupine to check the Source of land. The later land list applies even to Armada in that match, while separate per-building trigger entries can survive an object merge. Check the Land and Water arrays independently."],
            ["Commander behavior and defense gates", "The Defense settings tree also shows commander.json and its variants. In the sampled Hard files these define commander selection, upgrades, hiding conditions and factory assistance. The recognized military script controls edit time or metal-income expressions separately. Read the surrounding condition because the script can permit a defense under several different circumstances."]
        ],
        glossary: [
            ["Metal cluster", "A group of map resource spots the AI can plan to develop and defend."],
            ["Defense slot", "A numbered position resolved through a faction's list of defensive buildings."],
            ["Porcupine", "The configuration key for the cluster orders, unit lists, budget, and base schedule."],
            ["Build chain", "An ordered set of follow-up requests tied to completion of a matching AI construction task."],
            ["Trigger category", "The AI task's classification for the completed structure, such as energy or defence."],
            ["Completion radius", "The in-game distance used to look for other completed buildings in a counted group."]
        ]
    },
    combat: {
        intro: "Combat & response helps you tune which friendly roles the AI favors when it observes a particular kind of enemy force. For example, a response can make anti-air units more attractive when the opponent invests in aircraft. Response rules depend on friendly and enemy unit classifications, the factory's available units, and its current production preferences. Use Defense for fixed protective structures and construction triggers.",
        brief: [
            "Choose response.json and select the Friendly response role you want the AI to build, such as anti_air. Then find or add the enemy role the rule responds to, such as air.",
            "Raise Investment ratio to let the same enemy force justify a response even with more friendly units already built. Raise Response importance to strengthen the weight of a response that already qualifies.",
            "Check Unit behavior for the classification of both sides' units. In Production, find a factory that can build the intended response and check for a positive weight in the relevant situation and tier.",
            "Save and install. Test against an opponent that actually fields enough of the triggering role, then compare the units built and check the logs."
        ],
        detail: [
            ["Reading one row", "The selected Friendly response role is what your AI may build. The Enemy role is what it tracks on the other side. A row aligns three source arrays named vs, ratio, and importance. Editing a row changes the mapping at that position. The visual response table requires aligned lists in response.json. Other matching files and general behavior settings can be inspected in Explore settings or All files."],
            ["How demand and weight fit together", "In the inspected CircuitAI source, the military manager tracks observed enemy metal cost by role and friendly investment. For each enemy-role row, enemy metal multiplied by the ratio is compared with friendly role cost scaled by a role factor. An army-share cap can also block the candidate. To score an eligible response, the AI adds one to friendly role cost, divides enemy role cost by that sum, then multiplies by importance. The highest eligible score for that friendly role is used."],
            ["A worked response", "Suppose the AI knows about 300 metal of enemy air, already has 200 metal of anti-air, and the role's scaling factor is 1. A ratio of 0.5 counts 150 toward the 200 needed for eligibility. A ratio of 1 counts 300 and clears that check. The prospective unit must also fit under the army-share cap. Once eligible, increasing importance increases the score. The factory still checks availability and positive base weight. It multiplies the score by the sum of the unit's tier weight and the global _weight_ setting from response.json."],
            ["Other combat settings", "Explore settings can expose per-role army-share limits, team-size scaling, and global response multipliers. The behaviour files can also define group power thresholds, threat modifiers, and retreat rules. Those affect decisions such as whether a group can engage, alongside the response settings that shape which units the AI tries to build."]
        ],
        glossary: [
            ["Friendly response role", "The main role of a unit your AI may choose in response."],
            ["Enemy role", "A classification used to count observed hostile investment."],
            ["Investment", "The accumulated metal cost of units associated with a role."],
            ["Investment ratio", "A multiplier on enemy investment used in a response eligibility check."],
            ["Importance", "A relative multiplier on an eligible response's score."],
            ["Army-share cap", "A limit on how much of the friendly army's cost a response role can occupy."]
        ]
    },
    units: {
        intro: "Unit behavior shows how the AI classifies individual game units and which extra rules it associates with them. Select a settings file and a unit to inspect its roles, attributes, and existing fields. These settings can affect which units count toward a combat response or qualify for a task. Use the unit's internal ID to identify it when display names differ between game versions.",
        brief: [
            "Choose a behaviour file and select the Unit ID. A loaded catalog can help you recognize the unit by display name.",
            "Check the first entry under Roles before editing. The inspected loader uses it as the primary role and classifies enemies of that unit type by all entries in the list.",
            "Inspect Attributes and Selected unit settings for flags and limits already present. Use role and attribute names recognized by the installed AI, and check later settings files for overrides.",
            "Save and install. Run a match that gives the unit a chance to appear and exercise the rule you changed."
        ],
        detail: [
            ["Role changes have consumers", "A unit entry is keyed by its internal ID under behaviour. In the inspected loader, a list of bomber followed by air gives the friendly unit a main role of bomber. An enemy of that unit type can contribute to counts for both bomber and air. That distinction matters when Combat & response reacts to air but builds a different friendly role."],
            ["Attributes and other fields", "The inspected loader can read attribute entries as special flags or as additional friendly roles. For example, the factory logic distinguishes rare units when evaluating an inactive factory. Other fields can change estimates of enemy threat and friendly power, retreat thresholds, or when a unit is eligible to be built. Use names from the profile or its scripts as a starting point. Runtime log messages can expose unknown names."],
            ["Testing a single behavior", "If a unit has an existing retreat setting, change it in a duplicate and run a matchup in which both versions of the unit take damage and can fall back. Compare the health and position at the start of retreat. Keep the rest of the profile fixed. Unit settings and group decisions can both affect what you see."],
            ["Separating classification from availability", "A role describes the unit to the AI. A factory still needs the unit in its actual game build menu and appropriate production settings. Changing a role can shift which units the AI counts or favors without changing their weapons or movement. A conditional behaviour layer can replace a role or attribute array as a whole. Compare layers using the exact unit ID when changes appear to vanish."]
        ],
        glossary: [
            ["Unit ID", "The stable internal name used to connect a game unit to configuration."],
            ["Primary role", "The first configured role, used for major classification decisions such as friendly response selection."],
            ["Enemy classification", "The roles the AI uses to categorize an opposing unit it observes."],
            ["Attribute", "A named flag that can enable special handling in AI code."],
            ["Per-unit limit", "A setting on a specific unit type, such as a restriction on availability by time or count."]
        ]
    },
    placement: {
        intro: "Building placement controls where the AI looks for room to build. Choose a building group to inspect its spacing and the types of structures it checks for nearby. You can use these rules to leave more clearance around a factory, include its reserved exit lane in spacing checks, or adjust special spacing for large structures. The preview illustrates the rule, and a replay shows what happened on an actual map.",
        brief: [
            "Choose Settings file to edit, then choose the building group. Check the unit IDs listed for that group before changing it.",
            "In Land building spacing, adjust the shape, size, or extra clearance. Each step of size, clearance, or numeric radius represents 16 units of in-game distance.",
            "Review the building types included in spacing checks. If the page offers Include reserved lanes, it adds factory lanes to the types the rule must keep clear.",
            "Use More placement settings for water rules and terrain analysis. Save and install, then inspect placement and whether units can leave the factory in a replay."
        ],
        detail: [
            ["Which buildings and maps use a rule", "The building.instance lists associate internal unit IDs with rule groups. The editor exposes land groups directly, and the full tree shows class_land, class_water, and terrain data when present. In the inspected source, an applicable water-map group can take precedence over the matching land group. Terrain settings help classify connected map regions and distinguish lakes and seas. They are separate from the clearance around an individual structure."],
            ["Reading the shape controls", "The first type value selects a rectangle or circle, and the second identifies a category used by spacing checks. Size defines width and depth when supplied. Yard adds to the total width and depth of the rectangular area. For example, a size of 8 by 6 and a yard of 4 by 2 give a rectangle of 12 by 8 steps, or 192 by 128 elmos. Offset moves that area relative to the building. A circle uses a radius, which can be numeric or a preset tied to the structure's explosion or the number of allies."],
            ["Spacing and factory exits", "An ignore list excludes the listed categories from spacing checks. A not_ignore list restricts checks to listed categories where that form applies. The special category terra can include reserved factory lanes. Include reserved lanes adds terra to the applicable keep-apart list. If a rule explicitly ignores those lanes, inspect the ignore setting. The preview shows the shape and dimensions only. Placement checks apply locally, so a map passage can still become blocked somewhere outside a reserved lane."],
            ["Diagnosing a failed placement", "Wider clearance gives the AI fewer possible sites. Observe whether a building was attempted, where it was placed, and whether the resulting route remains usable. Check the active file and whether the map uses the land or water group before attributing a result to an edit."]
        ],
        glossary: [
            ["Footprint", "The width and depth occupied by the building itself."],
            ["Yard", "Extra clearance added to the checked rectangular area."],
            ["Elmo", "One unit of in-game distance. One spacing step is 16 elmos."],
            ["Reserved lane", "An area the AI can mark for movement out of a factory."],
            ["Category", "A label spacing rules use to decide which nearby objects to check."],
            ["Water map", "A map the AI classifies as water-dominated for rules that distinguish land and water."]
        ]
    },
    advanced: {
        intro: "All files gives you direct access to the selected profile's settings and scripts. Use it to inspect startup logic, check a setting without a dedicated control, or edit a custom rule. The visual tree lets you browse configuration values, and the Source box shows the text Studio saves. Changes here affect the same files used by the other editors, so review the diff before testing.",
        brief: [
            "Use Profile file to find a configuration file or script. Start at script/init.as to edit Startup configuration layers visually, or inspect its startup logic.",
            "Expand Visual configuration tree for supported JSON files, or use Adjustable script constants and Script structure on .as files before editing the Source box. Use the unit catalog as a reference when checking IDs.",
            "Use Review changes to inspect what you edited and Undo to back out a pending change. Save changes after checking the path and diff.",
            "Save and install before running an engine smoke test. If a script fails to load, use View logs to locate the first relevant error."
        ],
        detail: [
            ["Configuration and code", "The config folder contains settings. Studio understands comments and trailing commas in its configuration editor. The script folder contains AngelScript code. Startup logic in script/init.as selects configuration parts and may branch on game options. A script can include other script files, and changing a shared include can affect several callers in the same profile."],
            ["Layer order and preview", "File names alone cannot tell you which files load. In the inspected loader, configuration objects can merge by key, while a later array can replace an earlier one as a whole. A later scalar cannot replace an existing object under the inspected merge rule. The combined preview on the specialized pages shows the result for the selected files and order, including pending edits. Presets, when available, fill in recognized orders from the startup script. Check the actual match context and its log."],
            ["Editing and checking", "Changes in the visual editors and Source box share the same pending file text. Malformed configuration may prevent a visual tree from opening while Source remains available for repairs. Studio checks configuration syntax and known structures on save, and checks for external modifications to files it is saving. Use Review changes to see which files changed. Use Project history for saved checkpoints."],
            ["Running the result", "AngelScript logic is compiled by the installed AI at startup. Install saved files for the selected engine before testing. A smoke test can reveal load and compilation errors, and a longer test is needed to see a custom behavior execute. A value that parses can still be ignored, overridden, or interpreted differently by the selected runtime."]
        ],
        glossary: [
            ["Source", "Here, the editable text of the selected file. In a combined preview, Source identifies the file supplying a value."],
            ["Configuration object", "A group of named settings enclosed in braces."],
            ["Array", "An ordered list enclosed in brackets. The position can carry meaning."],
            ["AngelScript", "The scripting language used for executable profile rules."],
            ["Include", "A reference that makes another script's contents available to the current script."],
            ["Diff", "A view of added, removed, or modified text."]
        ]
    },
    manage: {
        intro: "AI install & remove connects your Studio projects to an installed copy of Beyond All Reason. Choose the BAR folder and engine that will run your AI, then install the selected project's saved profiles. Install again after editing if you want BAR to use the new files. This page can also remove a copy previously installed by Studio. Close BAR before installing or removing and reopen it to refresh the AI list.",
        brief: [
            "Open Change BAR installation if needed. Enter the BAR installation folder, choose an engine, and click Save installation selection.",
            "Choose your project in the sidebar and save any edits you want to deploy. Confirm the target project and engine shown here.",
            "Close BAR and click Install / update selected AI. Reopen BAR to refresh the lobby list, or refresh installed content in Test match.",
            "To remove the deployed copy, select the same project and engine, close BAR, and click Remove selected AI from BAR."
        ],
        detail: [
            ["Choosing the target", "The installation folder can contain several engine versions. Studio inspects it and offers the versions it finds. Under Change BAR installation, compare the Engine picker with the Launcher reports active engine message when available. Save installation selection records your target for deployment and headless tests. The engine BAR actually launches needs to match the deployment if you want to see that AI in the lobby."],
            ["What gets installed", "Install uses the saved files of every profile in the selected project. Studio stages the project for that engine under its project AI identity and uses that engine's stock BARbarIAn AI library. It also registers the AI name for the lobby. Saving edits writes the workspace and does not update deployed files. Save & install combines the two operations."],
            ["Replacing and removing a deployment", "Studio checks whether the target is managed and can flag pre-existing or externally modified files before a replacement. Read the target and conflict information if asked to confirm. Removal operates on the managed deployment for the selected engine. The editable project and its history remain in Studio."],
            ["Finding the copy in a test", "A content scan lists AIs available to the selected engine. Refresh installed content after deployment if the list is stale. Select the project AI and its intended profile. A successful install establishes that deployment finished. The smoke test checks whether that engine and game can load the deployed scripts."]
        ],
        glossary: [
            ["Installation root", "The folder containing BAR's engine and game data directories."],
            ["Engine", "The executable and support files that run a particular version of a match."],
            ["AI library", "The engine's compiled AI program, a DLL on Windows, which reads the profile's files."],
            ["Deployment", "The saved project profiles copied into a selected engine for use in matches."],
            ["Managed copy", "A deployment Studio recorded and can update or remove."]
        ]
    },
    match: {
        intro: "Test match runs installed AIs against one another without opening the game interface. Choose a game, map, and roster, then start one match or a series. Studio can run a short startup check first and report the match outcome, timing, and logs afterward. Use a replay when you need to see a particular event, such as which factory units came out first or where a defense was built.",
        brief: [
            "Save and install the project you want to test. Choose the same BAR installation and engine, then scan or refresh installed content.",
            "Choose the Game and Primary map. Put an AI on both sides and confirm each slot's profile and faction. Set the game-time limit. Turn on Record replay if you plan to watch the run afterward.",
            "Start the match with the smoke test enabled to check loading first. For repeated games, set up the series and optional side, map, or faction changes.",
            "Read the result and open logs to diagnose a failure. If the completed run shows Copy to BAR demos, use it to make the recorded replay available in BAR. Use Match history to find earlier runs."
        ],
        detail: [
            ["Where the roster comes from", "A scan reads packages and installed AIs for the selected installation and engine. Studio may restore a cached list. Refresh it after changing installed content. Select the deployed AI and profile for each roster slot. The roster's faction controls which side's units that AI plays, while options such as Extra Units Pack can alter which game definitions and profile layers are available."],
            ["Smoke phase and match phase", "The default smoke test is a short separate engine run that checks whether the selected AIs and profiles load and whether scripts compile. Some AIs cannot supply the load messages Studio expects. Skip smoke test bypasses that check. A successful smoke run confirms startup evidence. The full match can still hit a game-time limit, fail, or end with a winner or draw."],
            ["Timing and repeated games", "Game-time minutes are measured in the simulation. The elapsed indicator measures real time, including loading. A live engine process can still be stalled. A series runs multiple games with the selected rosters and can swap sides or rotate maps and faction matchups. Read which setup was used for each game when interpreting a scoreboard. Reaching a time limit does not award a win."],
            ["Reading evidence", "Use View logs to trace startup and runtime messages. Results can include telemetry, which summarizes events the engine or AI reported. For a recorded replay, use Copy to BAR demos in the completed run when available, then open the replay in BAR. This lets you see which units or structures appeared and where. Match history retains run records with filters such as map, profile, or checkpoint where that information is available."]
        ],
        glossary: [
            ["Headless", "Running the engine without the interactive game window."],
            ["Roster", "The set of AI slots on both teams, with each slot's profile and faction."],
            ["Smoke test", "A short separate run to check AI startup and script loading."],
            ["Game time", "Elapsed simulated time inside the match."],
            ["Real time", "Elapsed clock time on your computer."],
            ["Telemetry", "Match measurements and events recorded for inspection."],
            ["Replay", "An engine recording you can open in BAR to watch the match."]
        ]
    },
    experiments: {
        intro: "Tournaments & sweeps runs batches of headless games. A round-robin tournament compares installed profiles against one another. A parameter sweep tests several values for one number in a saved profile against an installed baseline opponent. Choose a map pool and time and game limits before starting. The results show which games actually ran, and a qualifying sweep candidate can become a new editable profile.",
        brief: [
            "Set up a BAR installation and scan installed content in Test match. Choose a game, maps, and an experiment type here.",
            "For a tournament, select 2–16 installed profiles. For a sweep, save the source profile, choose a script or JSON file, point the line and column inside one number, and enter 2–16 distinct values plus a baseline opponent. Choose a setting the Armada candidate can exercise. The opponent plays Cortex.",
            "Set the game-time, game-count, and real-time limits. Each sweep value needs two games per map to cover both starting positions. Three values on two maps need 12 games and enough real time to run them.",
            "Start the experiment, then inspect Game results and logs alongside the leaderboard. If a sweep offers promotion, give the candidate a new profile name."
        ],
        detail: [
            ["What gets scheduled", "A mirrored pair runs the same matchup and factions twice, swapping starting positions. Round-robin mode compares the selected installed profiles across the map pool. Sweep mode compares each value against the same opponent. The schedule seed reproduces scheduling choices and faction draws when the inputs and their order are unchanged. The game budget can limit how many mirrored pairs are scheduled, and the real-time limit or cancellation can stop a scheduled pair before it finishes."],
            ["What makes a sweep", "A sweep changes a single written decimal number in a saved JSON or AngelScript file. Line and column start at 1 and must point into that number, outside a string or comment. For example, find goal_exec in the active economy file in All files. In a saved line beginning with four spaces followed by \"goal_exec\": 50.0, the 5 is at column 18. Test values such as 40 and 60 to vary the target construction time in seconds. Check that the game loads the file and that a later layer leaves the value in effect. Studio snapshots the source and runs variants in an isolated engine workspace."],
            ["How to read the table", "Win rate divides wins by wins, losses, and draws combined. Failures and timeouts appear separately. Compare games played, map, and starting side before comparing percentages, particularly on a partial schedule. The results can show script errors, including some that cannot be assigned to a side. Use the logs if an outcome is surprising."],
            ["How promotion works", "A sweep candidate needs at least two clean mirrored pairs with decided results and a paired win rate above 50%. Draws count as half wins for this rate. The leaderboard also shows the overall win rate, which can include decided games from unfinished pairs. If Studio offers Promote, it reconstructs the variant from the saved snapshot and creates an editable profile."]
        ],
        glossary: [
            ["Round-robin", "A schedule in which every selected profile is paired against every other profile."],
            ["Parameter sweep", "A comparison of several values for one setting."],
            ["Numeric literal", "A number written directly into the source file, such as 0.5."],
            ["Baseline opponent", "The installed profile every sweep candidate plays against."],
            ["Starting side", "Which team position on the map a profile starts in. Each profile keeps its assigned faction when it changes starting side. In a sweep, the candidate plays Armada and the baseline plays Cortex."],
            ["Decided pair", "A sweep matchup on one map with a result on both starting sides, counting draws."],
            ["Promotion", "Creating an editable profile from the recorded variant Studio offers."]
        ]
    }
};
