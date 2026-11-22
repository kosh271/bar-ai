# Build more Pulsars, Bulwarks and Bastions

Use **Cluster defense order** to add heavy turrets around the metal clusters BARbarIAn chooses to defend. Use **On building completed** to request a turret near each Advanced Fusion Reactor the AI finishes through its build tasks.

| Faction | Heavy defense | Advanced Fusion Reactor |
| --- | --- | --- |
| Armada | Pulsar (`armanni`) | `armafus` |
| Cortex | Bulwark (`cordoom`) | `corafus` |
| Legion | Bastion (`legbastion`) | `legafus` |

## Increase cluster defenses

1. Open **Defense → Preview combined layers** and choose the detected startup order for your game options. Check the source file shown for `porcupine.land`, the land cluster order. Select that file under **Configuration layer**. In Willtest Hard, use `build_chain_leg.json` for Studio's **Test match**, which enables Legion Faction. In the BAR lobby, use `build_chain_leg.json` if Legion Faction is on or `build_chain.json` if it is off. The Legion layer's land order applies to all factions while that layer is active.
2. Under **Cluster defense order**, set **Cluster terrain** to **Land**. In Willtest hard, the last entry already uses slot `12`. The shared slot maps to Pulsar, Bulwark or Bastion through the relevant faction's unit list. Confirm the names in your own profile because custom unit lists can assign different buildings to `12`.
3. Choose that slot in **Defense to add**, then click **Add to order**. Use **Move up** to put the new copy closer to the start if cheaper defenses already use the budget. Leave the first entry in place if you want to retain Willtest hard's one inexpensive preventive defense. Use **Remove** to remove a trial entry. The **Water** order is independent of the **Land** order.
4. If the heavy slot rarely gets a task, open **Cluster defense budget → Income multiplier**. For a first test, increase both values by the same small amount and leave the **Map-size reference** values alone. The multiplier scales the metal budget from income. The references are map side lengths in BAR map squares, which the AI squares to obtain the reference areas. **Preventive defenses (first slots)** limits how many candidates the AI considers at points it classifies as away from the front. Raising it can put more defenses behind the front as well.
5. Click **Save & install** in Studio. Compare the edited AI with the original on the same map, faction, and game options. Check where heavy turrets appear and whether earlier defenses still get built.

BARbarIAn considers cluster defenses around groups of metal spots it is developing. It skips unavailable units and anti-air candidates if it has seen too little enemy aircraft investment. It queues a defense only while the cumulative cost is below the budget. Repeating a slot adds another candidate. Builder technology, terrain, access and build execution still determine whether that candidate becomes a finished turret.

The native layout uses a vector toward its estimated enemy position and places candidates in rows across that vector. Long-range attackers such as these three turrets are assigned the middle row. This gives an enemy-oriented cluster layout with preferred sites the builder can adjust. An exact straight line toward the enemy, a chosen sequence of map coordinates, or a minimum distance outside your own base would require a change to the placement rules. The stock source checkout's configuration maps Armada and Cortex. Bastion requires a profile and game that load Legion, such as the sampled Willtest hard setup with Legion enabled.

## Add a heavy turret near an Advanced Fusion Reactor

1. In **Defense**, choose `build_chain.json` for the example Armada or Cortex reactor or `build_chain_leg.json` for Legion under **Configuration layer**. Go to **On building completed**. Pick **energy** under **Completed building category**, then pick the reactor ID from the table under **Completed building ID**. If the selected file has no reactor trigger, expand **Add completed building** and choose `energy` and its reactor ID there.
2. Enter the faction's turret ID under **Defense building ID**. Choose an **Offset direction** and set **Offset distance (elmos)**, for example **Front** and `160` as a starting point. An elmo is an in-game distance unit. **Front** follows the reactor's facing rather than the direction of the enemy. The AI adds footprint clearance and can adjust the site.
3. Leave **Chance (%)** at `100` to request the task whenever the completion checks pass. If the **Chain** picker appears, leave it on **As a new chain** to allow the turret to start independently of existing actions. Click **Add defense action**. To request another turret on the same axis, add another action with a different distance. Check the actual spacing in a test before using the pattern.
4. Save and install the profile. In a test match, observe an AI-built reactor and check whether the capable constructor can reach the requested side and finish the turret. Adjust the direction or distance if the footprint competes with another completion action.

A completion action targets newly finished reactors built through the AI's task system. It applies to reactors in the base and at remote locations. The AI can reject a site in an ally's territory. A rule that fires only for reactors outside your own base would require a different placement policy. For the same trigger with a plasma shield, see [Shield Advanced Fusion Reactors](fusion-shields.md).

## Source references

- The sampled profile is `projects/Willtest/profiles/hard`. Its `script/init.as` establishes the conditional layer order, and `config/build_chain.json` and `config/build_chain_leg.json` define the cluster order and reactor triggers.
- The stock source examined is `RecoilEngine-vanilla` at `92efda5e60fb6df54a8f17a87e4f4cdb4aa2de31`. Under `AI/Skirmish/BARb/src/circuit`, `module/MilitaryManager.cpp` reads the slot tables and implements `DefaultMakeDefence` and `UpdateDefenceTasks`. `setup/DefenceData.cpp` creates the defense points. `module/BuilderManager.cpp` parses completion rules, and `task/builder/BuilderTask.cpp` runs `ExecuteChain`.
- BAR's English unit names and faction build menus were checked in `Beyond-All-Reason` at `df5240b19a739b36184cd9e402d21d0d074dabed`, in `language/en/units.json` and the unit definitions for advanced constructors.
