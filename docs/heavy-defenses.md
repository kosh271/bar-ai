# Build more Pulsars, Bulwarks and Bastions

Use **Cluster defense order** to add heavy turrets around the metal clusters BARbarIAn chooses to defend. Use **On building completed** to request a turret near each Advanced Fusion Reactor the AI finishes through its build tasks.

| Faction | Heavy defense          | Advanced Fusion Reactor |
| ------- | ---------------------- | ----------------------- |
| Armada  | Pulsar (`armanni`)     | `armafus`               |
| Cortex  | Bulwark (`cordoom`)    | `corafus`               |
| Legion  | Bastion (`legbastion`) | `legafus`               |

## Increase cluster defenses

1. Use **Duplicate profile** in the sidebar if you want an unchanged profile for comparison. Open **Defense** and choose the file to edit under **Configuration layer**. For Willtest Hard, choose `build_chain_leg.json` if you're using Studio's **Test match**, which enables the Legion Faction game option. If you're launching from the BAR lobby, use `build_chain_leg.json` with Legion Faction on and `build_chain.json` with it off. This choice depends on the game option even when your AI plays Armada or Cortex.
2. Under **Cluster defense order**, set **Cluster terrain** to **Land**. In Willtest hard, the last entry already uses slot `12`. The shared slot maps to Pulsar, Bulwark or Bastion through the relevant faction's unit list. Confirm the names in your own profile because custom unit lists can assign different buildings to `12`.
3. Choose that slot in **Defense to add**, then click **Add to order**. Use **Move up** to put the new copy closer to the start if cheaper defenses already use the budget. Leave the first entry in place if you want to retain Willtest hard's one inexpensive preventive defense. Use **Remove** to remove a trial entry. The **Water** order is independent of the **Land** order.
4. If the heavy slot rarely gets a task, open **Cluster defense budget → Income multiplier**. For a first test in Willtest Hard, try changing `48` and `32` to `50` and `34`. The two multipliers scale the metal budget from income on the reference map sizes. A 10 × 10 map uses the first, a 20 × 20 map uses the second, and the AI interpolates by map area between them. Keep **Map-size reference** unchanged for that test. **Preventive defenses (first slots)** limits how many candidates the AI considers away from the front. Raising it can put more defenses behind the front as well.
5. Click **Save & install** in Studio. If you made a duplicate, choose the original profile for one run and the edited profile for the other. Keep the map, faction, and game options the same. Check where heavy turrets appear and whether earlier defenses still get built.

BARbarIAn considers cluster defenses around groups of metal spots it is developing. It skips unavailable units and anti-air candidates if it has seen too little enemy aircraft investment. It queues a defense only while the cumulative cost is below the budget. Repeating a slot adds another candidate. Builder technology, terrain, access and build execution still determine whether that candidate becomes a finished turret.

The native layout uses a vector toward its estimated enemy position and places candidates in rows across that vector. Long-range attackers such as these three turrets are assigned the middle row. This gives an enemy-oriented cluster layout with preferred sites the builder can adjust. An exact straight line toward the enemy, a chosen sequence of map coordinates, or a minimum distance outside your own base would require a change to the placement rules. The stock source checkout's configuration maps Armada and Cortex. Bastion requires a profile and game that load Legion, such as the sampled Willtest hard setup with Legion enabled.

### Check which file supplies the land order

A layer is a settings file. Willtest Hard loads `build_chain.json` first. With Legion Faction enabled, it also loads `build_chain_leg.json` afterward. Both files define the land order, so the second file replaces the entire list. An extra Pulsar entry saved only in the first file would be replaced in a match with Legion Faction enabled, even for an Armada AI.

**Preview combined layers** is an optional, read-only check. It combines the files you choose, including unsaved edits, and shows where each resulting value came from. The preset buttons fill in the preview's file list using orders Studio can read from the profile's startup script. You supply the match context by choosing a preset. The preview leaves the match options, startup script, and editor file selection as they were.

1. In **Defense**, select `build_chain.json` under **Configuration layer**, then open **Preview combined layers**. The selected file tells Studio which settings presets to show. Starting with a build-chain file brings up the presets for defense orders.
2. Under **Presets from init.as**, click **Legion enabled** for Studio's **Test match** or for a BAR lobby game with Legion Faction on. Click **Standard (Armada / Cortex)** for a game with Legion Faction off. **Preview load order** shows the files Studio will combine for that preview.
3. Under **Combined values**, expand **Combined configuration**, then **porcupine**. That is the settings file's name for cluster defense. Find the row named **land** and read **Source**. With the **Legion enabled** preset in Willtest Hard, it shows `config/build_chain_leg.json`. The two files can show the same list of numbers, so use the source filename to decide where to edit.
4. Close the preview and select that filename under **Configuration layer** before returning to **Cluster defense order**. For custom profiles with no matching preset, use the match's engine log to check the `Load config` lines before assuming which files or order the AI uses.

## Add a heavy turret near an Advanced Fusion Reactor

1. In **Defense**, choose `build_chain.json` for the example Armada or Cortex reactor or `build_chain_leg.json` for Legion under **Configuration layer**. For example, add a Pulsar to the Armada reactor trigger in `build_chain.json` even for Studio's **Test match**. Willtest Hard's Legion file replaces the shared land order and adds a separate Legion reactor trigger. The Armada and Cortex reactor triggers still come from `build_chain.json`. Go to **On building completed**, pick **energy** under **Completed building category**, and pick the reactor ID under **Completed building ID**. If the selected file has no reactor trigger, expand **Add completed building** and choose `energy` and its reactor ID there.
2. Enter the faction's turret ID under **Defense building ID**. Choose an **Offset direction** and set **Offset distance (elmos)**, for example **Front** and `160` as a starting point. An elmo is an in-game distance unit. **Front** follows the reactor's facing rather than the direction of the enemy. The AI adds footprint clearance and can adjust the site.
3. Leave **Chance (%)** at `100` to request the task whenever the completion checks pass. If the **Chain** picker appears, leave it on **As a new chain** to allow the turret to start independently of existing actions. Click **Add defense action**. To request another turret on the same axis, add another action with a different distance. Check the actual spacing in a test before using the pattern.
4. Save and install the profile. In a test match, observe an AI-built reactor and check whether the capable constructor can reach the requested side and finish the turret. Adjust the direction or distance if the footprint competes with another completion action.

A completion action targets newly finished reactors built through the AI's task system. It applies to reactors in the base and at remote locations. The AI can reject a site in an ally's territory. A rule that fires only for reactors outside your own base would require a different placement policy. For the same trigger with a plasma shield, see [Shield Advanced Fusion Reactors](fusion-shields.md).

## Source references

- The sampled profile is `projects/Willtest/profiles/hard`. Its `script/init.as` establishes the conditional layer order, and `config/build_chain.json` and `config/build_chain_leg.json` define the cluster order and reactor triggers.
- The stock source examined is `RecoilEngine-vanilla` at `92efda5e60fb6df54a8f17a87e4f4cdb4aa2de31`. Under `AI/Skirmish/BARb/src/circuit`, `module/MilitaryManager.cpp` reads the slot tables and implements `DefaultMakeDefence` and `UpdateDefenceTasks`. `setup/DefenceData.cpp` creates the defense points. `module/BuilderManager.cpp` parses completion rules, and `task/builder/BuilderTask.cpp` runs `ExecuteChain`.
- BAR's English unit names and faction build menus were checked in `Beyond-All-Reason` at `df5240b19a739b36184cd9e402d21d0d074dabed`, in `language/en/units.json` and the unit definitions for advanced constructors.
