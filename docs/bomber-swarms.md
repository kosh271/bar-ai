# Send occasional bomber waves in BAR

Give one bomber type enough production weight to appear regularly while leaving other aircraft in the mix. BARbarIAn can group nearby bombers of the same type, then attack when the group finds a target it can reach and engage. A production batch helps create a wave. The stock settings have no launch interval or order to wait for a fixed squad size.

## Tune a bomber type you can observe

1. In the sidebar, select the Willtest project and Hard profile for this example, then click **Duplicate profile**. Open **Production**, select the matching **Configuration layer**, and choose the plant under **Factory**. For a first trial, try the Armada Tech 1 example. Check an unedited replay to confirm the AI builds `armap`, the basic Aircraft Plant, in that matchup. If it uses another plant, tune the plant the replay shows.

   | Faction | Configuration layer | Example factory and bomber |
   | --- | --- | --- |
   | Armada | `factory.json` | `armap` → Stormbringer (`armthund`) or `armaap` → Blizzard (`armpnix`) |
   | Cortex | `factory.json` | `corap` → Whirlwind (`corshad`) or `coraap` → Hailstorm (`corhurc`) |
   | Legion | `factory_leg.json` | `legaap` → Phoenix (`legphoenix`) |

   For Legion, Studio's **Test match** enables **Legion Faction**. Enable it in the BAR lobby as well if you launch there. Use the Phoenix for the Legion example because its primary role is `bomber` in Willtest Hard. [Stormbringer unit reference](https://www.beyondallreason.info/unit/armthund) · [Blizzard unit reference](https://www.beyondallreason.info/unit/armpnix) · [Whirlwind unit reference](https://www.beyondallreason.info/unit/corshad)

2. Set **Situation** to **Land**. In a Willtest Hard Armada trial, change the Stormbringer's **Tier 1** and **Tier 2** weights from `0.20` to `0.35`. These are income bands within the Tech 1 aircraft plant's table. Leave **Tier 0** at `0.20` to keep the lowest-income mix. At **Tier 3** its weight is zero, so set a positive weight there too if you want the Tech 1 plant to keep considering it late in the match. A weight of `0.35` is relative to the other aircraft in the column.
3. Keep interceptor weights positive. Look at **Enemy air** separately because BARbarIAn uses that table when known enemy aircraft outweigh its estimate of friendly anti-air. You can leave the bomber weights there unchanged to concentrate the experiment on the Land table. If you switch to the Tech 2 Blizzard example, its Land weights start at zero in Willtest Hard. Try `0.20` in **Tier 0** and **Tier 1**, with the other aircraft weights left in place.
4. If bombers come out in ones and twos, try raising **Combat & response → All settings → quota → num batch** from `3` to `5` in a separate run. Choose `behaviour_leg.json` under **Configuration layer** for Willtest Hard in Studio's **Test match**, or `behaviour.json` in a BAR lobby game with Legion Faction off. This lengthens runs of the same production choice across air, ground, and naval factories. Aircraft still depart according to their squad's orders.

## Increase bomber production in response to buildings

Try this separately from the production-weight change. Open **Combat & response → Configuration layer → response.json**. Under **Friendly response role**, select `bomber`. In the `static` row, increase **Response importance** from `10` to `15` for a first trial. This boosts the production preference when the AI knows about enemy static units and the investment limits still allow a response. The bomber still needs a positive weight in the active production tier. This setting affects recruitment. The bomber squad picks its own target in the match.

## Verify a wave in a replay

Run the original profile in **Test match** with **Record replay** enabled. After making one change, click **Save & install** and run the duplicate with the same map, faction, opponent, and time limit. At the same game time in both replays, note the first active aircraft plant, the number of bombers finished, and the largest group that reached an attack together. More aircraft on the roster can still produce small, separate runs if they find different targets or get separated. If many bombers are built and none attacks, check enemy visibility and anti-air around possible targets before raising production further.

The inspected BARb implementation chooses production in `module/FactoryManager.cpp`. Its `task/fighter/BombTask.cpp` groups nearby units of the same type and evaluates targets against visibility, targeting rules, and estimated threat. Synchronized strikes with a set minimum wing size would require a mission policy beyond these controls.
