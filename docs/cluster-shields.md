# Shields near heavy defenses

BARbarIAn's built-in configuration can request a shield after a particular defense completes, or include shields in the land-defense sequence around metal clusters. Both place shields near defenses in common layouts. A rule based on the number of completed towers needs the opt-in **Defense → Construction completion rules** script.

## Pick the heavy defense and its shield

| Faction | Heavy defense          | Land shield              | Configuration layer                                                   |
| ------- | ---------------------- | ------------------------ | --------------------------------------------------------------------- |
| Armada  | Pulsar (`armanni`)     | Keeper (`armgate`)       | `build_chain.json`                                                    |
| Cortex  | Bulwark (`cordoom`)    | Overseer (`corgate`)     | `build_chain.json`                                                    |
| Legion  | Bastion (`legbastion`) | Soteria (`legdeflector`) | `build_chain_leg.json` when the Legion Faction game option is enabled |

The BAR definitions inspected for all three shields give a 550-elmo radius. Elmos are units of in-game distance. Coverage depends on the shield's eventual site and the distance to each tower.

## Request a shield after one heavy defense finishes

1. Select your project and profile. Open **Defense** and choose the file in the table under **Configuration layer**. In the example Hard profile these files supply different tower triggers, so an Armada tower still uses `build_chain.json` when Legion Faction is enabled. The land order below is a single shared list and has different override behavior.
2. In the completed-building editor, select **Completed building category** `defence` and **Completed building ID** for the tower. Use **Add completed building** if the trigger is absent. These towers are `defence` tasks when built by the stock land-defense sequence.
3. Enter the shield ID under **Defense building ID**. If you see the **Chain** picker, choose **As a new chain**. Set **Offset direction** to **Back** and **Offset distance (elmos)** to `160`. Leave **Chance (%)** at `100` for an attempt on every eligible completion or set it to `80` for an 80% chance. Click **Add defense action**.
4. Use **Save & install** in the header, then select the updated AI and profile in **Test match**. Watch a replay or run a match in BAR to see which heavy defenses finish and where the shields are built.

The runtime evaluates availability and chance when the tower's construction task finishes. **Back** follows the tower's build orientation, and the runtime adds half the shield footprint along that axis. The resulting position is a starting point for its search. A new chain can start independently even if an earlier chain has other tasks to finish.

This rule can request a shield beside an isolated tower and can request more than one in an overlapping group. It applies to future completions of a matching task type and unit ID. Shields use the same kind of task as other build-chain defenses, so avoid cycles such as shield → heavy tower → shield.

## Include shields in land-defense sequences

The default military manager treats these as defenses of metal clusters. It places structures without attack weapons in the back row of a defense point. In the inspected hard profile, land-defense index `12` resolves to the heavy tower and index `28` resolves to the land shield for each listed faction. Those numbers depend on the active profile's `porcupine.unit` lists.

1. In **Defense**, choose `build_chain.json` under **Configuration layer**. For the example Hard profile with **Legion Faction** enabled, use `build_chain_leg.json` because that layer supplies the land order for all factions in that match.
2. In **Cluster defense order**, choose **Land** under **Cluster terrain**. In **Defense to add**, choose the slot that lists the shield, then click **Add to order**. In the example Hard profile, slot 12 lists the heavy towers and slot 28 lists the ordinary shields. Leave the shield after the heavy tower if you want the AI to consider the tower first.
3. Save, install, and test the active profile on a land map. To measure this method by itself, leave the one-tower completion rule out of that profile.

The land sequence selects candidates in order. The AI tries fewer candidates away from the front, and available builders and income limit the tasks it creates. It can skip a heavy tower and still choose a shield. The sequence targets the metal cluster's defense point without measuring shield coverage or counting finished towers.

## Require a minimum number of nearby completed towers

1. Select your profile and open **Defense → Construction completion rules**.
2. Pick the faction's heavy turret under **Completed building** and its shield under **Requested building**.
3. Set **Count completions** to **Within nearby groups**, **Completion count** to **2**, and **Nearby search radius (elmos)** to **400**. Click **Add completion rule**.
4. Click **Save & install**. Adding a rule enables it for that profile. In **Test match**, select the AI under its project name and select the edited profile. Turn on **Record replay** to check the tower and shield positions afterward. The engine log line `Studio construction request` with both building IDs confirms that the rule queued a shield. Watch the replay to see whether it finished and whether the visible shield bubble covers both towers.

With these values, the second tower completes the group if the first is still present and within 400 elmos. The rule requests a shield near the second tower and consumes both towers for that rule. Another two towers can form a new group even if the first shield covers the same area. Unused towers are dropped if destroyed. The search radius groups towers and does not bound the final shield site. A queued request still needs a capable constructor and a usable site. For a clean comparison, test the grouped rule on its own before adding either of the other methods above.

If Studio reports that the profile already defines custom construction callbacks, use a fresh copy of the stock Hard profile for this recipe. To make the AI consider more towers for the trial, see [Build more Pulsars, Bulwarks and Bastions](heavy-defenses.md).

The built-in `build_chain` conditions cover availability and individual checks such as air threat, economy, chance, and nearby radar or sonar. They have no predicate for the number of completed towers, current shield coverage, or pending shield requests. The native military planner's metal clusters also use map resource spots, so its defense points need not coincide with the groups you want to shield.

## Source references

- Recoil's BARb development example is at `AI/Skirmish/BARb/data/config/dev/build_chain.json` under `build_chain.defence`. The stock development actions also include a jammer. The inspected Studio hard copy has the shields in its unit lists and leaves them out of its land sequence.
- `src/circuit/module/BuilderManager.cpp`, `src/circuit/task/builder/BuildChain.cpp`, and `src/circuit/task/builder/BuilderTask.cpp` in BARb parse and execute completion chains. `src/circuit/module/MilitaryManager.cpp` resolves the faction indices and schedules metal-cluster defenses.
- The inspected BAR game definitions are `units/ArmBuildings/LandUtil/armgate.lua`, `units/CorBuildings/LandUtil/corgate.lua`, and `units/Legion/Utilities/legdeflector.lua`. Installed engine and game versions can differ from these sources.
