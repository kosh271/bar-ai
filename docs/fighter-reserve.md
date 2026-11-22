# Maintain a fighter quick-reaction force in BAR

Build up about 30 Tech 1 fighters before a large bomber or gunship raid and replace losses as the match goes on. BARbarIAn's normal anti-air squads can pursue targets away from base. This version of the quick-reaction force (QRF) relies on replenishment to stay useful through repeated raids. BAR's Falcon is a Tech 1 air-to-air fighter. [Falcon unit reference](https://www.beyondallreason.info/unit/armfig)

## What stock BARbarIAn can do

Studio can increase fighter production before an air raid. Fighters assigned the `anti_air` role hunt eligible targets and seek safe positions when they have none. The production weights affect the AI's choices at any count. The workbench has no count-based replacement control.

For a production-only approximation, duplicate your profile. In Willtest Hard as Armada, open **Production**, set **Configuration layer** to `factory.json`, and choose `armap` under **Factory**. Try `0.60` for Falcon (`armfig`) in Tiers 0–3 under both **Land** and **Enemy air**. Leave its first role as `anti_air` in **Unit behavior**. The **Land** weights encourage production before enemy air triggers a response. A per-model `limit` of `30` would cap further production selections once the AI's tracked count reaches 30. It would supply neither a replenishment target nor a way to hold those fighters near base. [Fighter and aircraft plant IDs](fighter-swarms.md#increase-fighter-batches)

## Give the profile a replacement target

Because pursuit is fine, the profile only needs to change recruitment. Use the stock factory script hook to request an eligible Tech 1 fighter when its tracked count is below 30. The stock anti-air task continues to handle target selection and pursuit.

1. Select **Willtest → Hard** in the sidebar and click **Duplicate profile**. In the duplicate, open **All files → script → manager → factory.as**.
2. Inside `namespace Factory`, replace the existing `AiMakeTask` function with this block. The example starts at two game minutes to leave time for the factory opener and requests one fighter at a time. Earlier or later starts change how soon replacements can take priority.

   ```angelscript
   const int fighterTarget = 30;
   IUnitTask@ pendingFighter;

   IUnitTask@ AiMakeTask(CCircuitUnit@ unit)
   {
       if (ai.frame >= 2 * MINUTE && pendingFighter is null &&
           unit.circuitDef.GetName() == "armap")
       {
           CCircuitDef@ fighter = ai.GetCircuitDef("armfig");
           if (fighter !is null && fighter.count < fighterTarget &&
               fighter.IsAvailable(ai.frame))
           {
               @pendingFighter = aiFactoryMgr.Enqueue(TaskS::Recruit(
                   Task::RecruitType::FIREPOWER, Task::Priority::NORMAL,
                   fighter, unit.GetPos(ai.frame), 64.f));
               AiLog("Fighter reserve: queued Falcon, count " + fighter.count);
               return pendingFighter;
           }
       }
       return aiFactoryMgr.DefaultMakeTask(unit);
   }
   ```

3. Add this check inside the existing `AiTaskRemoved` function. In Willtest Hard that function is empty. In a custom profile, preserve any statements already in it.

   ```angelscript
   if (task is pendingFighter)
       @pendingFighter = null;
   ```

4. Click **Save & install**. Select the duplicate in **Test match** and leave **Skip smoke test** clear. In the engine log, look for `Fighter reserve: queued Falcon`. Test on a setup where the AI builds and keeps a Tech 1 aircraft plant. Use `corap` and `corveng` for Cortex or `legap` and `legfig` for Legion. Update the log message if you change factions.

The rule runs when the matching plant asks for work. Below the target, its direct request can take priority over other orders and bypass the stock factory's usual production checks. At or above 30, regular factory selection resumes and may still build fighters. BARbarIAn's count includes fighters already under construction, and the rule keeps at most one of its own fighter orders outstanding across all plants. The target depends on an operational plant and enough resources to finish the fighters.

## How to judge the result

Record a match where the opponent sends bomber or gunship waves. Before the first raid, note how many interceptors the AI has built. Check how many engage before the bombers drop or the gunships reach their targets. After losses, look for new replacement requests in the engine log and confirm fighters finish in the replay. Measure how much of the fighting force is restored by the next raid. Fighters pursuing away from base are acceptable if the AI rebuilds its losses and can respond again.

In a controlled Recoil 2026.09.03 run with a supplied aircraft plant and economy, the same script with a target of two built two Falcons, then queued and finished a replacement after a forced loss. This checks the replacement loop. The 30-fighter target and interception timing still need a full match trial.

In the inspected BARb source, production and per-model limits are in `module/FactoryManager.cpp`, anti-air target and fallback behavior is in `task/fighter/AntiAirTask.cpp`, and the script interfaces are registered in `script/InitScript.cpp`, `script/MilitaryScript.cpp`, and `script/FactoryScript.cpp`.
