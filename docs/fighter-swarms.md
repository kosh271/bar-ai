# Send fighter sweeps in BAR

Increase fighter production in batches and let BARbarIAn's anti-air squads choose when to engage. This produces opportunities for several fighters to go out together as enemy aircraft appear. Fighters such as the Falcon are air-to-air units. For attacks on buildings, use the [bomber swarm walkthrough](bomber-swarms.md). [Falcon unit reference](https://www.beyondallreason.info/unit/armfig)

## Increase fighter batches

1. In the sidebar, select the Willtest project and Hard profile for this example, then click **Duplicate profile**. Edit the duplicate and choose **Armada** for it when setting up **Test match**. In **Production → Configuration layer**, select `factory.json` for Armada or Cortex, or `factory_leg.json` for Legion. Studio's **Test match** enables **Legion Faction**. Enable that option in the BAR lobby for a Legion game. The Legion file adds its own factories and leaves the Armada and Cortex factory rows in `factory.json`.
2. Under **Factory**, select the plant the AI actually uses. Start with a Tech 1 plant so you can see the effect earlier. The table gives the factory and fighter IDs to search for.

   | Faction | Tech 1 factory and fighter | Tech 2 factory and fighter |
   | --- | --- | --- |
   | Armada | `armap` → Falcon (`armfig`) | `armaap` → Highwind (`armhawk`) |
   | Cortex | `corap` → Valiant (`corveng`) | `coraap` → Nighthawk (`corvamp`) |
   | Legion | `legap` → Noctua (`legfig`) | `legaap` → Ajax (`legafigdef`) |

3. Set **Situation** to **Land**. In the **Production preferences** table, find the Falcon row and replace the editable number in its **Tier 0** column from `0.10` to `0.20`. Replace both **Tier 1** and **Tier 2** weights from `0.01` to `0.15`. If the aircraft plant stays active into **Tier 3**, set that cell to `0.10` instead of its original zero. These columns are income bands for the same factory. Leave the other weights in place. The percentage beside each editable number shows its share of that column's weights. Availability and response priorities can change what gets built.
4. Switch **Situation** to **Enemy air** and give the same fighter a positive weight in each tier you expect to reach. For example, try `0.35` in the Falcon's first three Enemy air tiers and `0.20` in Tier 3. BARbarIAn switches to this table when the enemy aircraft it knows about cost more than its estimate of friendly anti-air. It uses **Land** on a land map outside that situation, so changing only **Enemy air** leaves little fighter production before the enemy builds aircraft.
5. If you want longer runs of the same plane, open **Combat & response → Configuration layer**. For a Willtest Hard **Test match**, choose `behaviour_leg.json`. With Legion Faction off, use `behaviour.json`. Under **All settings → quota**, try changing **num batch** from `3` to `5`. This lets a factory keep its selected combat unit for more production decisions while it remains eligible. It also changes batching for ground and naval factories. Start with the weight changes alone if you want to measure the aircraft effect separately.

## Check whether the fighters sweep together

Start with the original profile in **Test match** and turn on **Record replay**. In the replay, check that your side constructed the aircraft plant you plan to edit and that the opposing side actually produced planes. Reuse a matchup that did both. Click **Save & install** for the duplicate, then run it on the same map, faction, and opponent. Pick one comparison time, such as 20 minutes if both sides have air by then. Record how many fighters have finished, how many travel together, and the time of the first interception or whether none occurred by the cutoff. If the edited AI skips that plant, the run gives no evidence about its weights.

Nearby fighters of the same type can join or merge into an anti-air squad. That squad chooses targets using enemy visibility, weapon compatibility, and threat. A longer factory batch may put more planes in the air together. The batch setting controls consecutive production choices. It gives no departure time, minimum flight size, or order to clear a lane for bombers.

The inspected BARb implementation reads `quota.num_batch` and the factory weights in `module/FactoryManager.cpp`. It assigns the `anti_air` role to its anti-air task in `module/MilitaryManager.cpp`. Grouping and target selection are in `task/fighter/AntiAirTask.cpp`.
