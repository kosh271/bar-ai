# Extra Units Pack in Willtest

PracTac and DefWk2-N use the faction build options unlocked by **Extra Units Pack**. The policy uses the build menus from BAR `test-32071-e1cbffe`. That build has 43 distinct units unlocked through ordinary faction producers, including the Atoll shared by Cortex and Legion. Epic Pulsar additionally needs an evolved Armada commander at level 8 or above. Lootbox and Scavenger production have separate game options.

Each profile has its own [Armada](../projects/Willtest/profiles/practac/script/manager/extra_armada.as), [Cortex](../projects/Willtest/profiles/practac/script/manager/extra_cortex.as), and [Legion](../projects/Willtest/profiles/practac/script/manager/extra_legion.as) policies. The DefWk2-N files sit in the corresponding profile folder. The additional orders have per-unit caps and start, metal-income, and energy-income requirements. The bots use completed producers and wait out an energy stall. Native construction also uses eligible economy and build-power options.

## Armada

| Unit | Job |
| --- | --- |
| Gun Platform (`armgplat`) | Plasma cover around working shipyards. |
| Scumbag (`armfrock`) | Floating missile AA when the bot has seen enemy aircraft. |
| Zapper (`armzapper`) | EMP support for a ground attack group. |
| Firefly (`armfify`) | Flying wreck recovery and repair. |
| Shockwave (`armshockwave`) | Armed extractor for exposed metal deposits, placed by the native metal-spot planner. |
| Advanced Wind Turbine (`armwint2`) | Supplemental base power on maps with useful wind. |
| Advanced Construction Turret (`armnanotct2`) | Build power for working advanced land factories. |
| Dragon's Fury (`armlwall`) | Short-range pop-up turret on the defensive line. |
| Asylum (`armgatet3`) | High-capacity shield for a cluster of finished heavy defenses. |
| Aurora (`armfgate`) | Floating plasma cover for an advanced shipyard. |
| Floating Advanced Construction Turret (`armnanotc2plat`) | Build power around advanced naval production. |
| Excalibur (`armexcalibur`) | Coastal bombardment support from a submarine. |
| Seadragon (`armseadragon`) | Strategic missile submarine. DefWk2-N assigns it to the existing volley programme. |
| Meatball (`armmeatball`) | Amphibious ranged fire support with anti-air reach. |
| Assimilator (`armassimilator`) | Faster amphibious skirmisher and escort. |
| Epic Pulsar (`armannit3`) | Extreme-range defense behind an existing heavy emplacement. |

## Cortex

| Unit | Job |
| --- | --- |
| Gun Platform (`corgplat`) | Plasma picket near a working shipyard. |
| Janitor (`corfrock`) | Floating missile AA when enemy aircraft have been seen. |
| Advanced Wind Turbine (`corwint2`) | Supplemental power on maps with useful wind. |
| Advanced Construction Turret (`cornanotct2`) | Build power around working advanced land production. |
| Dragon's Rage (`cormwall`) | Pop-up rocket turret on the defensive line. |
| Sanctuary (`corgatet3`) | High-capacity shield for clustered heavy defenses. |
| Atoll (`corfgate`) | Floating plasma cover for an advanced shipyard. |
| Floating Advanced Construction Turret (`cornanotc2plat`) | Build power for advanced shipyards. |
| Deadeye (`cordeadeye`) | Standoff fire with its burst laser. |
| Printer (`corvac`) | Field construction, repairs, and recovery with its attached worker. |
| Phantom (`corphantom`) | Amphibious radar and sonar scouting. |
| Siegebreaker (`corsiegebreaker`) | Long-range laser fire against emplacements. |
| Forge (`corforge`) | Armed engineering and recovery support. |
| Torch (`cortorch`) | Quick raiding for PracTac and close-range defense for DefWk2-N. |
| Adjudicator (`coresuppt3`) | Late fleet anchor with heavy beam weapons for open-water maps. |
| Onager (`coronager`) | Submerged coastal fire support. |
| Desolator (`cordesolator`) | Strategic missile submarine. DefWk2-N assigns it to the existing volley programme. |
| Black Prince (`corprince`) | Long-range shore bombardment. |
| Vesuvius (`corves`) | Expensive siege centerpiece, limited to one. |

## Legion

| Unit | Job |
| --- | --- |
| Overcharged Metal Extractor (`legmext15`) | Powered upgrade on reserved metal spots, with a cap to leave room for T2 extractors. |
| Advanced Wind Turbine (`legwint2`) | Supplemental power on maps with useful wind. |
| Advanced Construction Turret (`legnanotct2`) | Build power around working advanced land factories. |
| Dragon's Constitution (`legrwall`) | Armed wall turret on an existing defensive line. |
| Elysium (`leggatet3`) | High-capacity shield for clustered heavy defenses. |
| Atoll (`corfgate`) | Floating plasma cover built by Legion's advanced construction submarine. |
| Floating Advanced Construction Turret (`legnanotct2plat`) | Build power around advanced shipyards. |
| Pilum (`legbunk`) | Fast railgun skirmisher. |
| Apollyon (`legapollyon`) | Late heavy assault centerpiece with anti-air weapons. |

## Shield and SAM placement

Asylum, Sanctuary, and Elysium need a funded economy, a charged energy reserve, and at least two nearby completed heavy defenses worth a combined 4,500 metal. The bot looks for a site behind the cluster and avoids adding another advanced shield in the same area. DefWk2-N can also open one late gantry for the pack's assault and siege units.

DefWk2-N counts finished long-range T2 SAMs around each advanced fusion site. An isolated reactor calls for two, a cluster for up to four, and observed heavy air pressure can add one. New placements aim for at least 680 elmos of clearance from the reactors, beyond the stock 640-elmo blast radius. Lost SAMs reopen the deficit. This coverage is active with either Extra Units Pack setting.
