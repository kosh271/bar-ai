# Build a late-game bomber reserve in BAR

Add regular Tech 2 bombers to a normal BARbarIAn profile once the AI finishes its own nuclear silo and static anti-nuke. An operating advanced aircraft plant builds and replenishes them, up to **50 existing or under-construction bombers at a time**. The stock bomber task can send them against slow incoming Tech 3 units or choose other targets. This reserve builds up aircraft for the late game while leaving dispatch to the stock AI.

## Decide when to activate it

This recipe counts the nuclear phase as having begun after **the AI has finished one nuclear silo and one static anti-nuke of its own** in that match. Either can finish first. It stays enabled after the milestone, even if a structure is later lost. It uses completed buildings as the signal, regardless of missile stockpile. Enemy or allied nuclear construction alone does not activate it.

| Faction | Advanced aircraft plant | Regular Tech 2 bomber | Nuclear silo | Static anti-nuke |
| --- | --- | --- | --- | --- |
| Armada | `armaap` | Blizzard (`armpnix`) | Armageddon (`armsilo`) | Citadel (`armamd`) |
| Cortex | `coraap` | Hailstorm (`corhurc`) | Apocalypse (`corsilo`) | Prevailer (`corfmd`) |
| Legion | `legaap` | Phoenix (`legphoenix`) | Supernova (`legsilo`) | Aegis (`legabm`) |

Use one regular bomber type per profile so the cap covers the whole reserve. Leave special-purpose aircraft such as torpedo, EMP, and atomic bombers to your normal production rules. [Blizzard](https://www.beyondallreason.info/unit/armpnix) · [Phoenix](https://www.beyondallreason.info/unit/legphoenix)

## Set the cap and leave the normal mix in place

1. In the Studio sidebar, select **Willtest → Hard** and click **Duplicate profile**. Use the duplicate for the example below. It assumes the profile retains the normal rules for constructing factories, nuclear silos, and anti-nukes.
2. In **Production**, choose `factory.json` for Armada or Cortex or `factory_leg.json` for Legion. Select the advanced aircraft plant from the table. Set the reserve bomber's weights to `0` in every tier of **Land**, **Enemy air**, and **Water** if the file supplies that table. In Willtest Hard, Blizzard already has zero weights. Leave the other rows alone. For a custom profile, remove the reserve model from any separate factory opener or scripted queue as well. This lets the reserve rule control when that bomber enters production and prevents competing orders for the same model.
3. In **All files → config**, open `behaviour.json` for Armada or Cortex or `behaviour_leg.json` for Legion. Find the bomber's existing object under `behaviour` and add `"limit": 50` to it, using a comma to separate properties. Leave its first role as `bomber`. This per-model cap also applies to BARbarIAn's ordinary production choices.

## Add the late-game recruitment rule

1. In **All files → script → manager → factory.as**, paste the following inside `namespace Factory` before the existing `AiMakeTask` function. This is the Armada example. To adapt it, replace the four unit IDs with the matching IDs in the table.

   ```angelscript
   const int bomberCap = 50;
   const int bomberOrderInterval = 15 * SECOND;
   bool bomberSiloFinished = false;
   bool bomberAntiNukeFinished = false;
   int nextBomberOrder = 0;
   bool yieldToNormalOrder = false;
   IUnitTask@ pendingBomber;

   void NoteBomberMilestone(CCircuitUnit@ unit)
   {
       const string name = unit.circuitDef.GetName();
       if (name == "armsilo")
           bomberSiloFinished = true;
       if (name == "armamd")
           bomberAntiNukeFinished = true;
   }

   IUnitTask@ TryBomberReserve(CCircuitUnit@ unit)
   {
       if (!bomberSiloFinished || !bomberAntiNukeFinished ||
           pendingBomber !is null || ai.frame < nextBomberOrder ||
           unit.circuitDef.GetName() != "armaap")
           return null;

       CCircuitDef@ bomber = ai.GetCircuitDef("armpnix");
       if (bomber is null || bomber.count >= bomberCap ||
           !bomber.IsAvailable(ai.frame) || aiEconomyMgr.metal.current < bomber.costM)
           return null;

       if (yieldToNormalOrder)
       {
           yieldToNormalOrder = false;
           return null;
       }

       @pendingBomber = aiFactoryMgr.Enqueue(TaskS::Recruit(
           Task::RecruitType::FIREPOWER, Task::Priority::NORMAL,
           bomber, unit.GetPos(ai.frame), 64.f));
       nextBomberOrder = ai.frame + bomberOrderInterval;
       yieldToNormalOrder = true;
       AiLog("Bomber reserve: queued " + bomber.GetName() + ", count " + bomber.count);
       return pendingBomber;
   }
   ```

2. Add these lines at the start of the existing `Factory::AiMakeTask` function. Keep its current body after the new lines. If you also applied the [fighter replenishment recipe](fighter-reserve.md), this lets its Tech 1 rule continue to work.

   ```angelscript
   IUnitTask@ bomberTask = TryBomberReserve(unit);
   if (bomberTask !is null)
       return bomberTask;
   ```

3. In the same file, add this check inside the existing `AiTaskRemoved`. Keep any checks already there.

   ```angelscript
   if (task is pendingBomber)
       @pendingBomber = null;
   ```

4. In **All files → script → main.as**, add this function inside `namespace Main`. If the profile already has a user-written `AiUnitFinished`, add the call inside it. If the function appears in a block marked `// BAR AI Studio construction rules`, start with **New AI from BAR** or duplicate a stock profile without the block. Editing the marked block disables visual editing of its managed rules.

   ```angelscript
   void AiUnitFinished(CCircuitUnit@ unit)
   {
       Factory::NoteBomberMilestone(unit);
   }
   ```

The rule prioritizes a bomber when the matching plant asks for work, the AI has enough stored metal to cover the plane's metal cost, and it is below the cap. It keeps at most one reserve order outstanding, allows at least 15 game seconds between reserve orders, and gives the stock factory logic a turn before ordering another bomber. The plant and economy determine how quickly planes finish. The count includes bombers under construction. At 50, the reserve returns to normal factory selection. When losses reduce the count, the rule can resume recruiting. The cap applies to the AI's own production of that model during a fresh match. Unit transfers and game debug tools can change the count outside recruitment.

## Check whether the bombers help against heavy units

1. Click **Save & install** and select the duplicate and its faction in **Test match**. Leave **Skip smoke test** clear and turn on **Record replay**. Run a match in which the AI can finish both nuclear buildings and keep an advanced aircraft plant operating. For a smaller trial, use the same lower cap in both the script and the behavior file.
2. Confirm that `Bomber reserve: queued` appears in the engine log only after both structures finish. A line ending in `count 49` means the AI had 49 of that model when it requested the next plane. In the replay, count finished and under-construction bombers on that team and check that losses free a slot for replacements. If the log has no requests, check the two buildings, the plant and metal storage before changing combat settings.
3. In the replay, follow the bombers when a slow incoming Tech 3 unit approaches. See whether they engage it, are already attacking something else, or pass over the opportunity. BARbarIAn checks visibility, target speed, weapon compatibility and estimated threat. The replay shows where the bombers went and what they attacked. It cannot tell you which targeting check ruled out a unit.

A controlled Armada run on Recoil 2026.09.03 used the same script with a cap of two and supplied the plant, economy and nuclear buildings. The AI waited for both buildings and enough metal, stopped at two Blizzards, and finished a replacement after a forced loss. A full 50-plane buildup and a response to an incoming Tech 3 wave still need match testing.
