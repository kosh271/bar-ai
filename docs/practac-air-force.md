# PracTac air force: fighter QRF and production waves

PracTac keeps a standing fighter reserve and builds aircraft in rotating waves. The profile uses `script/manager/air_force.as` and `script/manager/air_nanos.as`. The optional Willtest Bot Companion game adds direct targeting for Liche, Dragon and Tyrannus strike groups.

Before this change PracTac built an air plant only as a response to enemy aircraft (6 minutes in, at least 150 metal of enemy air seen), and its air plants were limited to fighters and AA. Neither the enemy's anti-air nor the `quota.aa_threat` veto was the cause: PracTac has set `aa_threat` to `1e30` since it was created, so `IsAirValid()` is always true.

## Fighter QRF

| Setting | Value |
| --- | --- |
| Floor | 20 fighters from 10:00, scaled to the economy: `min(20, metal income / 2)`, so the full 20 needs 40 metal income, and nothing is forced below 10 income |
| Ceiling | 60 fighters |
| Below the floor | Fighters are ordered at **high priority** ahead of ordinary wave recruits. Every fourth request that gets past the air-constructor bootstrap goes to stock production. Below half the floor, advanced plants can spend some heavy-wave turns on emergency fighter replacements. |
| Counted | Completed Tech 1 and Tech 2 fighters together (`armfig`/`armhawk`, `corveng`/`corvamp`, `legfig`/`legafigdef`). The script allows up to four tracked replacements beyond the floor while aircraft are still under construction. |

The basic plant makes the Tech 1 fighter and the advanced plant makes the Tech 2 one. Fighters assigned to the reserve use a defend task. They intercept eligible enemies in friendly defended territory and return to base after an engagement. Fighters beyond the reserve use the stock anti-air task and can pursue farther out.

## Waves

Waves run in this order and then repeat: **scouts, fighters, bombers, heavy aircraft**. From 45:00 the rotation alternates a heavy wave with the other aircraft types. While a heavy wave is open, basic plants replace fighters and advanced plants prioritize the heavy wave, with some time for emergency fighter replacements. A wave ends when all its units have been ordered, after 6 minutes, or 20 seconds after no plant can build its unit. A wave that has nothing to do (cap reached, economy too weak) is skipped.

| Wave | Size | Gate | Units |
| --- | --- | --- | --- |
| Scouts | 10 to 30 (10 + metal income / 3) | metal income 15; at most 30 alive | `armpeep`/`armawac`, `corfink`/`corawac`, `legwhisper` (Legion has no Tech 1 scout aircraft) |
| Fighters | 10 more | metal income 12; never past 60 | the QRF fighters above |
| Bombers | 8 to 20 (8 + metal income / 15) | metal income 25; at most 40 alive | `armthund`/`armpnix`, `corshad`/`corhurc`, `legkam`/`legphoenix`. Tech 1 bombers only while there is no advanced plant |
| Liches | Before 45:00, one aircraft, or two when income can pay for one in 45 seconds. From 45:00, 10 to 20 (`income * 300 s / unit cost`), within remaining capacity. | Income pays for one in 90 seconds. Cap of 20 alive. Orders have a 10-minute window. | Armada Liche `armliche`, from the advanced plant |
| Gunships | 10 to 30 (`income * 300 s / unit cost`). Production continues on alternating advanced-plant turns through the other waves. | Income pays for one in 90 seconds. Cap of 30 alive. The fighter reserve retains replacement slots. | Cortex Dragon `corcrwh` or Legion Tyrannus `legfort`, from the advanced plant |

Scouts and bombers wait at base in a defend task that never promotes. When the wave's units are all waiting (or at least 5 are, after 4 minutes), the tasks are aborted and the units are released in the same 20 seconds: scouts take scout tasks, bombers get their stock bomb task and group up. Scouts explore and report what they see; the superweapon and bomb tasks choose targets from that picture. Heavy aircraft are held too and leave as a swarm in a strike (next section).

Wave orders do not check the metal bank (the AI spends metal as it arrives, so a bank check starved every wave in the first test games); the order waits for its metal inside the plant. Waves are therefore limited by income, not by a gate. Every 4th request from an air plant is left to the stock logic, so constructors and the rest of the mix still get built. In `factory.json` and `factory_leg.json` the weights of the scripted units (scouts, Tech 2 radar planes, Liche, Dragon, Tyrannus) are 0 so the stock mix does not add to the waves. Dragon is `corcrwh` (the old `corcrw` is the scavenger unit); it has a `behaviour.json` entry with the `heavy` role.

## Focused strikes with the companion

Select **Willtest Bot Companion air-strike** and the updated PracTac profile. Strike aircraft gather at base on hold fire and seek valuable targets in line of sight, including anti-nukes, strategic launchers and advanced economy.

- Liches can launch once at least two are ready. The group apportions its payload across targets in one area based on their remaining health. Each Liche turns for home after releasing its payload.
- Dragons and Tyrannuses launch with 10 to 30 healthy aircraft of one model. They concentrate their fire on one target and can attack another valuable unit in the same area. If scouts lose sight of a target during the approach, the wing can move briefly toward its last known position to regain sight. Damaged aircraft returning to base can receive repair orders.
- Each wing waits at least three minutes between launches after an engagement. A gunship wing can retry sooner if it returns without making contact.

The profile uses the stock heavy-air behavior below in the base game. The earlier **Willtest Bot Companion liche-strike** release continues to provide focused Liche strikes.

## Stock heavy strikes

The stock bomb task (`BombTask::FindTarget`) picks the enemy with the **least health** it can reach, so left alone a Liche is spent on the cheapest thing in range. The script cannot see single enemies or give a unit a target, so a strike picks one of two stock mechanisms by what the AI has seen:

1. Heavy aircraft wait at base in a defend task that never promotes. That task attacks enemies that come near the base. Gunships launch when their economy-scaled group is ready, with a minimum of ten. After eight minutes staging, any ready group of 10 to 30 can launch. Liches use their wave size or launch with at least two after six minutes.
2. A strike needs expensive enemies to be known, and starts at least 3 minutes after the last one:
   - **Heavy mode** requires at least one known enemy heavy unit and 2500 metal total in known enemy heavy units and commanders. The stock **anti-heavy task** (`TaskF::Common(AH)`) looks for both types and ends if the known heavy cost falls to zero. The `anti_heavy` attribute grants the capability to `armliche`, `corcrwh` and `legfort`. Their primary roles still control regular assignments.
   - **Mask mode** requires at least 8000 metal of known enemy static units. For **12 seconds**, enemy ground types under 1000 metal (`strikeMinCost`) are marked ignored, with exemptions for heavy, super and commander types. Liches receive a bomb task. Dragons and Tyrannuses receive an attack task. The mask affects target selection during the window. After it expires, regular targeting resumes and the aircraft can engage cheaper units.
3. Aircraft must still be assigned to the heavy holding group when a release begins. The selected Dragons or Tyrannuses share one squad task. Aircraft already assigned continue after the 12-second window. When their tasks end, they rejoin the reserve for a future strike.

During mask mode, the bot's other attack, defend and raid tasks also skip the masked ground types when selecting targets.

Heavy mode only helps against heavy units and commanders. Expensive buildings (fusions, silos) are reached in mask mode only.

## Plants and nano turrets

- **Plants.** Armada and Cortex queue the basic plant from 8:00 at 30 metal income and the first advanced plant from 14:00 at 50. Legion queues its basic plant from 7:00 at 25 and prioritizes its first advanced plant from 12:00 at 42, using the basic plant as a site reference. The second and third advanced plants are available from 25:00 at 100 income and 45:00 at 150. **An air constructor builds the advanced plant** (`armca`/`armaca`, `corca`/`coraca`, `legca`/`legaca`). When needed, the basic plant receives a high-priority construction-aircraft order. Legion asks the advanced plant for a Whisper radar plane as soon as it can. Waves start at 9:00.
- **Nano turrets.** `air_nanos.as` gives each basic plant 3 nano turrets and each advanced plant 4, rising to 6 from 30:00 (turrets of any origin within 450 elmos count), at metal income 30 and no energy stall. The native logic hands nanos out newest factory first, so an air plant built after a land lab got none.

## Transports

**Not possible with BARb and AngelScript.** BARb has no transport task: the `transport` role is only a label. The script interface has no load, unload or move order for a single unit, so a transport cannot be told to pick up units, fly to a place, or drop them. Building transports without that would only waste metal, so PracTac builds none. The nuke-and-drop idea needs native work: a transport task that loads a group of units, flies to a point the script chooses (for example a missile impact), and unloads them. See [circuitai-angelscript-limits.md](circuitai-angelscript-limits.md).

## Throughput limits

A Liche is 2200 metal and 46000 energy and takes about 75 seconds in an advanced plant with 4 nano turrets. Ten take over 12 minutes in one plant, before any fighter replacement. In the early test games PracTac made 75 to 90 metal a second at 45 to 60 minutes. Ten Liches cost 22000 metal, around 5 minutes of the whole income. Factory throughput, aircraft losses and the release timeout determine how many are ready together.

## Tuning

Tune the constants at the top of `air_force.as`, the wave sizes in `PlanWave`, and the plant thresholds in `EnsurePlants`. Look for `Air force:` in the log. The two-minute status reports include the team number and separate finished fighters, fighters including construction, and the number assigned to defend. A strike reports how many aircraft were selected and how many actually received a task before the window closed. Weapon fire and damage require separate game telemetry.

## Mediterraneum replay and follow-up

The November 25 match included two Armada PracTac bots. Each reached one advanced air plant despite qualifying for more. Together they launched ten scout groups and eleven bomber groups. Their largest heavy release was eight aircraft, and both remained below the 20-fighter floor for most sampled intervals. The replay's heavy release logs announced the selected mode. The profile's anti-heavy entry did not grant the aircraft the capability to take that task. The revised profile applies the correct attribute and records successful assignments separately.

In a two-minute headless smoke on TitanDuel_V3 with the same engine and game build, both Armada and Legion loaded the revised profile without AngelScript errors. An instrumented Mediterraneum scenario seeded fighters and targets and boosted early income. It showed 22 completed fighters with 20 assigned to defend, and ten Liches each received one anti-heavy assignment in the first release. The three advanced plants were built through the AI's ordinary task path and completed at about 15:41, 25:16 and 45:26, after their respective 14:00, 25:00 and 45:00 gates.

## Earlier headless tests

Headless games on TitanDuel_V3 (engine 2026.09.03, Armada, quickstart start: 2000 metal, 5000 energy, starter base and base builder turret), PracTac against the Hard profile, one game per row. Script compile errors: none.

| Run | Result |
| --- | --- |
| Air force off (baseline), to 60 min | PracTac wiped out at 23:46; made 33k metal |
| Air force on, bank check on the waves | wiped out at 47:54; made 90k metal. Waves starved (6 of 20 scouts, no bombers), which is why the bank check was removed |
| Air force on, bank check removed | wiped out at 52:27; made 183k metal. Air plant at 4:00, advanced plant at 19:30, 20 fighters reached repeatedly, Liche built twice, scouts 9 and 1 of 20 and 27, bombers 2 of 10 and 5 of 13 |
| Air force on against NullAI (does not fight), to 12:11 | all 18 scouts ordered, 12 launched together at 9:24, the herd reached 20, the 10-fighter wave completed and the bomber wave began; the game ended when PracTac killed the opponent's commander |

| Heavy swarm and strike code added (late switch moved to 20:00 for the test) | Compiled and loaded with no script errors in 4 games. Liches were built and held at base ("1 waiting"). The advanced plant was built after the air-constructor fix, in the games where PracTac lived long enough. The strike itself never fired: PracTac was overrun at 34:49 twice and at 13:38 once, or never had enough heavies waiting |

| Supreme Isthmus v2.1, air force off | PracTac won at 51:50 with 578k metal |
| Supreme Isthmus v2.1, air force on, strike test (late switch 14:00, strike on one waiting heavy, gate off) | PracTac eliminated the opponent at about 29 minutes (68k metal against 28k). Income 117 to 181 at 25 to 29 minutes, 6 heavy aircraft built, 2 anti-heavy strikes released with no script errors, heavy count kept rising afterwards so the swarm came back |
| Supreme Isthmus v2.1, air force on, real thresholds (late switch 14:00) | PracTac lost at 22:05 with 17.6k metal: income was 8 to 18 for the first 20 minutes and no heavy aircraft was built. The economy was already behind the baseline from about 4 to 6 minutes, before the air force spent much; its only early action was the 4:00 high-priority plant described under Plants |
| Supreme Isthmus v2.1, air force on, plants and spending delayed to 8:00+ | PracTac won at 11:05 (22k metal against Hard's 11k, 18.5k produced by 10:00 against the baseline's 9.1k). The game ended before any air force wave mattered |

Each row is one game and the opponent varies (Hard made 48k to 86k metal in the 25-minute games), so treat the comparison as indicative only.

**Open questions from those tests**
- **What a strike hits.** Two anti-heavy strikes were released and no script error followed, but the logs cannot show which enemy the swarm attacked or whether it killed it; the team statistics do not separate air kills. Mask mode (a rich enemy base but no heavy units) never ran at all.
- **Home-base response.** Held aircraft sit in a defend task that attacks enemies near the base (read from `DefendTask::FindTarget`); no game showed it happening.
- **Flats and Forests, Project SD-129, Cortex and Legion**: not run.
- **Swarms of 10 to 20.** The most heavy aircraft alive at once in any game was 2. PracTac made 30 to 90 metal a second in the test games and dies to Hard between 14 and 60 minutes on this map. A swarm of 10 Liches is 22000 metal and 460000 energy.
- Bombers launching together and bombing, and the Liche in a fight: against Hard, PracTac lost every unit it built (679 of 679 in one game), so scouts and bombers died waiting at base. The NullAI game ended before the bomber wave finished.
- Cortex and Legion: only Armada was run. Dragon (`corcrwh`), Tyrannus (`legfort`), the Cortex and Legion plants and Legion's missing Tech 1 scout are untested.
- Fighter losses: the earlier production-only herd repeatedly fell from 20 to 3–6 against Hard. The revised profile gives a portion of the fleet a home-defense task.
- `armamsub` in `factory.json` has 6 weights for 5 units (the Studio validator reports 6 errors). That was already there and was not touched.
