# Late-game nuke battery, air force and fortification (DefWk2-N)

The DefWk2-N profile (a copy of Defensive Wk2) starts a nuclear programme at **12 minutes**: up to 23 launchers, a new one about every two minutes, behind the base, with a growing ring of anti-nukes, plus a swarm of scout aircraft that finds targets for them. Everything is AngelScript and configuration; nothing native was changed.

Code: `script/manager/nuke.as`, `nuke_subs.as`, `anti_nuke.as`, `naval.as`, `outrigger.as`, `air_nanos.as`, `scout_swarm.as`, `air_defence.as`, `air_reserve.as`, `factory.as`, `big_gun_support.as`, `census.as`. Limits: `config/behaviour.json` and `config/behaviour_leg.json`.

## Nuclear programme

| Setting | Value | Where |
| --- | --- | --- |
| Start | 12:00 (`Nuke::startFrame`). Silos are unavailable to the AI before 10:00 (`"since": 600`). | `behaviour*.json`, `nuke.as` |
| Cap | 23 launchers, silos and nuclear submarines together (`Nuke::siloTarget`, `"limit": 23`) | `behaviour*.json`, `nuke.as` |
| First launch | due by 22:00 (`Nuke::firstLaunchBy`): from then even one estimated missile opens a volley | `Nuke::VolleyNeeded` |
| Volley size | all standing silos loaded once: at least 2 (until the 22:00 deadline) and at most 8 estimated missiles | `Nuke::VolleyNeeded` |
| Silo requests | one every 2 minutes, up to 3 builds at once, 20-minute task timeout, once metal income is at least 15 and energy income at least 500, energy is not stalling, the AI has a Tech 2 constructor and an air plant (or it is 17:00). The first silo is top priority, the second high, later ones normal | `Nuke::RequestSilo` |
| Tech 2 nudge | from 8:00, when the AI has no Tech 2 factory, factory selection is nudged toward the advanced lab, every 2 minutes at most | `Nuke::ChooseTechFactory` |
| Park nanos | 3 nano turrets are queued at the park with the first silo (a silo is 178500 build points; a nano turret adds 200 a second). Eight were tried and starved the air plant of builders | `Nuke::RequestSilo` |
| Silo park and pads | a park spot 1000 elmos behind the commander's start position, away from the front (the map centre stands in until there are 4 land units), clamped to the map. Requests cycle through 10 pads along the back edge: 0, +250, -250, +500 ... elmos sideways from the park. A failed request retries the same pad | `Nuke::ChoosePark`, `Nuke::Pad` |
| Silo escort | the park is an escort site of the heaviest tier (3 shields, 5 heavy statics, 5 anti-nukes, 4 T2 SAMs); the two outer pads each get a tier-3 site. Escort tasks run at normal priority | `BigGunSupport::RegisterEscort` |
| Base escort | a tier-3 escort site at the base (2 shields, 3 heavy, 4 anti-nuke, 3 T2 SAMs); a new one every 4 minutes after the last finishes, up to 6 | `Nuke::FortifyHome` |
| T3 labs | none: `limit: 0` on the Armada, Cortex and Legion gantries (land and underwater) | `behaviour*.json` |

The silo is requested as an ordinary defence build, not a big-gun build, so the big-gun placement module does not move it toward the front. Each silo logs where it finished, its distance from the base anchor and from the park, and how far toward the front it stands (negative is behind the base).

The first live game (with the old 45-minute start and 8 silos) showed silos finishing at 52, 74, 79, 84, 90 and 97 minutes. Three of the build tasks timed out at 4 minutes without finishing, and builds ran one at a time. The longer timeout and three builds at once address that; no T3 lab also frees the metal. In the headless games below, escort structures at low priority starved while silo builds held every builder (tasks timing out after about 4.5 minutes), which is why escorts run at normal priority and only the first two silos are high priority.

### How the volley gate works

The AI's superweapon task fires as soon as any silo holds one missile, and scripts cannot read a silo's stockpile. The gate therefore uses an estimate and AI control:

1. A finished silo is taken out of AI control. It keeps stockpiling, because the military manager keeps the stockpile queue topped up whatever the unit's task is.
2. Each silo banks one second of progress per game second, none while energy is stalling. A missile counts every 150 s of progress (the weapon takes 120 s; the extra 25% is a margin for shortfalls). A silo holds at most 10.
3. When the estimate reaches the needed number, every silo is handed back to the AI, which picks targets and fires. The window is long enough for each silo to fire its estimated stock at one missile per 10 s, at least 30 s.
4. When the window is over, every enemy unit type is marked "ignored" for 20 s. The superweapon task then finds no target and orders its silo to stop, which clears the silo's order queue. After that the silos are held again, the ignore flags are restored and the estimate restarts from zero. Log lines: `volley window ending, targets masked`, then `volley window closed, silos held`.

Why step 4 exists: the first version only held the silos when the window ended, and a launch happened while the window was closed. The superweapon task gives a silo an attack order that stays live for 60 s, and handing the silo to script control does not cancel it, so a missile that completed in that tail fired alone. The script interface has no stop order, so the stop is induced by removing every valid target.

The estimate can still be wrong. A silo that was part-way through a missile when it was re-held keeps that progress. A window with no valid target leaves missiles unspent that the estimate has forgotten, which only delays the next volley. Check the engine log for `Nuke:` lines to compare the estimate with what you see in a replay.

## Scout swarm

From 12:00 the AI's aircraft plants also build radar aircraft until 30 are alive in total: Peepers (`armpeep`) and Finks (`corfink`) from the Tech 1 plants, advanced radar planes (`armawac`, `corawac`, `legwhisper`) from advanced plants. Legion has no Tech 1 scout aircraft. If the AI has no Tech 1 aircraft plant, the swarm also nudges factory selection toward one (every 2 minutes at most).

Scouts that finish after 12:00 wait at the base in a defend task that never promotes. When 20 are waiting (10 after 6 minutes of waiting), the hold tasks are aborted and the scouts take scout tasks together within 20 seconds. After that each scout flies the stock scout task, which explores and reports what it sees. The superweapon task chooses its targets from that picture by cost, so finding the enemy base is what lets the silos hit something valuable.

## Air force and fortification

- **Fighters.** From 10 minutes a standing fighter force is kept whether or not enemy air has been seen: 6 + minutes/4 Tech 1 fighters (3 + minutes/6 advanced fighters), plus one per three fighter-costs of enemy air observed; capped at 45 and 30.
- **Plant rotation.** Fighters, scouts and bombers all want the same air plants, and the fighter screen always wants more. The factory hook now rotates who is asked first (scout swarm, bomber reserve, fighter screen), so scouts and bombers are no longer starved.
- **Bombers.** The bomber reserve (cap 50, one order every 5 s once a silo and an anti-nuke exist) only builds from the advanced air plant. If the AI has none, factory selection is nudged toward one every 2 minutes (`ChooseBomberPlant`). Advanced air plants may now be built twice (`"limit": 2`).
- **T2 SAMs, shields, heavy defences, anti-nukes.** These come from escort sites: the silo park, its outer pads and the base itself (a tier-3 site around the commander's start position, renewed every 4 minutes after the last one finishes, up to 6). Quotas per site are in `big_gun_support.as`.
- **T3 labs.** Blocked with `"limit": 0`; the Legion, Armada and Cortex gantries never become available.
- **Census.** `census.as` logs a `Census Nm:` line every 5 minutes from 10 minutes: counts of T3 labs and units, silos, anti-nukes, T2 and T1 SAMs, shields, heavy defences, fighters, bombers, scouts, Tech 1 and advanced air plants, labs, and metal and energy income.

## Anti-nukes

- **Base cover.** One anti-nuke at the base (`AntiNuke::Coverage`, the original rule: only while there are none).
- **Ring.** From 18:00 (and once a silo stands, or from 26:00) a second rule keeps adding batteries: wanted = 2 + one per two silos (standing or being built) + one every 4 minutes after 18:00, at most 24. They go in turn to the base, the silo pads and the outrigger clusters, two tasks at a time, one every 15 s (`AntiNuke::RequestRing`).
- **Escort sites** add more at the park, the outer pads, the base and the outriggers (see below).
- **Navy.** The shipyard hook (`Naval::TryAntiNuke`) wanted anti-nuke ships only in proportion to flagships, and flagships are limited to one on open water and none on mixed maps, so it never wanted more than one. It now also wants 2 from 14:00 plus one every 4 minutes, at most 8 (`Naval::TimedTarget`): Armada `armantiship`, Cortex `corantiship`, Legion `leganavyantinukecarrier`. This is the "one or two launchers" cap: the base rule and the fleet rule each topped out at one.

## Nuclear submarines

`armseadragon` (Armada) and `cordesolator` (Cortex) are in the advanced shipyard only when the game's "experimental extra units" option is on; Legion has none. `nuke_subs.as` orders up to 2 of them from 25:00 (metal income 60, energy income 4000, one order per 3 minutes) and they count against the 23-launcher cap.

**How they fire.** Like the Thor's EMP missile, the ICBM is a manual-fire stockpile weapon, and the native code fires those itself. A unit with a manual-fire weapon gets a `CDGunAction` whenever it is assigned to a fighter task (`FighterTask.cpp`); every few updates it takes the most powerful visible enemy within the weapon's range and orders the shot (`DGunAction.cpp`), if a missile is stockpiled and the weapon's energy cost per second of stockpile time is affordable. The ICBM is a `StarburstLauncher`, which `CircuitWDef.cpp` counts as high trajectory, so the action's line-of-fire check over the terrain is skipped, and its 72000 range covers the map. The military manager keeps the stockpile going. The role in `behaviour_extra_units.json` is `super`, as for `armthor`.

**How the volley gate treats them.** A submarine is registered in `Nuke::silos` like a silo and counts toward the volley estimate (140 s per missile, the same 150 s margin). Outside a window it is taken out of AI control, which clears its fighter task and with it the manual-fire action. When a window opens it is handed back and the native action picks targets. When the window ends it is held again at once, before the 20 s target masking, because the manual-fire action does not look at the ignore flags the mask uses. Differences from a silo: the target is the most powerful visible enemy unit, not the largest group, and a submarine that has to surface or move to see targets may fire later than a silo does.

**Not verified.** The test map has no water, so none of this ran. Set `maxSubs` to 0 in `nuke_subs.as` if 8000 metal and 85000 energy each is not worth it.

## EMP and tactical missiles

- **Firing: yes.** Static launchers with the role `super` get the same superweapon task as silos, which picks the largest enemy group in range and orders an attack on the ground. `behaviour.json` and `behaviour_leg.json` already give `armemp`, `cortron` and `legperdition` that role (the comment on `armemp` says it does not shoot without it); `cortron` and `legperdition` are limited to 1. Mobile launchers fire through the manual-fire action described above: the Thor (`armthor`) is a gantry unit and the gantries are blocked in this profile.
- **Building: yes** (`tactical.as`, also in PracTac). One launcher of the side's own kind (`armemp`, `cortron` or `legperdition`) at a time, one request every 3 minutes, at most 6 (PracTac 4), from 24:00 (PracTac 18:00), once metal income is 40 (PracTac 30), energy income 1500 (PracTac 1000), energy is not stalling and a Tech 2 constructor exists. Tasks run at high priority: at normal priority the first request in each of five test games timed out unbuilt, because the site is far from the base.
- **Where.** Toward the enemy but never in front of the other defences. The module keeps the position of every finished immobile unit with a weapon range of 300 or more (not the launchers or silos) measured along the base-to-front line, ignoring anything more than 1500 elmos to the side of that line (the outrigger clusters). A launcher stands 350 elmos behind the most forward of those, but never more than 1600 elmos from the base, with a sideways offset of 0, +350, -350, +700 ... for the 1st, 2nd, 3rd ... launcher. The missiles reach 2250 to 2300 elmos (EMP 3650), so standing as far forward as the defences allow is what brings the enemy into range. In DefWk2-N every third launcher also gets a small escort site: a shield, an anti-nuke and a T2 SAM.
- **Limits.** `armemp`, `cortron` and `legperdition` were limited to 1 (or none) in `behaviour*.json`; now 6 (PracTac 4).

## Outriggers

Up to two energy clusters well away from the base, at right angles to the base-to-front line (2200 elmos sideways, 300 to the rear), on the side with more room; the second goes on the other side (`outrigger.as`). Each cluster grows in three stages. A stage starts when the one before is complete (its structures and its escort site) and its start time has come:

| Stage | Starts | Fusions | Converters | Shields | Heavy statics | Anti-nukes | T2 SAMs |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 18:00, after an air plant (28:00 at the latest) | 3 | 3 | 2 | 4 | 3 | 2 |
| 2 | 38:00 | 6 | 9 | 3 | 10 | 3 | 3 |
| 3 | 48:00 | 10 | 15 | 4 | 16 | 3 | 3 |

(Totals per cluster.) The second cluster starts at 35:00 once the first one has finished stage 1. Fusions make 1000 energy a second each and advanced converters use 600, so stage 1 leaves 1200 spare, and stage 3 about 1000 spare with the converters at full draw; the surplus runs shields and weapons. Structure tasks run at high priority (at normal priority they starved: far from the base they lost every builder to the nearer silo, anti-nuke and defence tasks and timed out), nothing is requested until the AI has a Tech 2 constructor, and each cluster also gets 2, 4 and 6 nano turrets by the end of stages 1, 2 and 3. Heavy statics stand between the cluster and the front and spread out as more are added. If 6 tasks fail in a row after waiting at least a minute, with nothing standing, the cluster moves to the other side once, then is dropped. The silo chance hubs on fusion reactors were removed from `build_chain*.json`, so a fusion no longer starts a silo of its own.

## Walls

`walls.as` puts a ring of fortification wall segments (`armfort`, `corfort`, `legforti`: 40 metal, 8900 health) around every finished Annihilator, Doomsday Machine and Bastion: 16 segments on a circle of radius 88, started on the side facing the front and alternating outward. Segments are queued at low priority, 4 tasks at a time, once metal income is 30. Heavy statics need a Tech 2 constructor and so does the fortification wall, so Dragon's Teeth are never used. A failed segment is skipped; a destroyed heavy static loses its remaining pieces.

## Air plants

- **Nano turrets.** `air_nanos.as` gives every air plant 2 nano turrets and every advanced air plant 4, counting nano turrets of any origin within 450 elmos. The native logic hands nanos out per factory starting with the newest, and a land lab with a large weight took all of them.
- **Plant fallback.** The factory-selection nudge ("selected corap for the defensive response") was seen repeating for half an hour without a plant ever being built, in roughly one game in four. `AirDefence::EnsurePlant` queues the plant directly at the base from 14:00 when there is none (every 3 minutes, 5 tries). The plant also sits ahead of the Tech 2 lab nudge in factory selection.

## What needs native support

Neither of these is reachable from AngelScript. The profile approximates them as above.

- **A minimum stockpile in the superweapon task.** `SuperTask::Update` fires whenever `GetStockpile() > 0`. A native minimum (sum of stockpiles across launchers, read with `GetStockpile()`) would replace the estimate and the control hand-off.
- **A coordinated swarm task.** The script interface has no move or attack order for a single unit, so these cannot be scripted:
  - a shared approach axis from a staging point toward the enemy base;
  - lateral spacing wide enough that one flak burst does not hit several scouts, tight enough to saturate the defences' fire;
  - timing so the whole swarm arrives together (the existing scout-saturation code staggers arrivals instead);
  - the goal order "find the main base, then the highest-value buildings".

  The native tree already has deterministic planners for formation slots and synchronized multi-group arrival (`FormationSlots.h`, `MultiGroupManeuver.h`) that a swarm task could build on.

## What has been verified

Headless games on Koom Valley (engine 2026.09.03, one profile under test per game, Armada and Cortex alternating). Script compile errors: none. The scripts for repeating this are in the session scratch folder, not in the repository.

**Against BARb Hard** (5 games): the profile was eliminated in 4 of them between 44 and 54 game-minutes, so this setup is far harsher than the live game and could not test the late game.

**Against an opponent with its factories disabled** (so it cannot attack, and the game is set never to end): eight batches of 5 games to 50 game-minutes while the nuclear strategy was tuned (p6 to p13; Cortex in three of five). The last batch (p13), with the final settings:

| Measure | Result |
| --- | --- |
| Script compile errors, engine crashes | 0 |
| First silo standing | 25.6, 26.5, 26.7, 29.1 and 31.5 minutes. The batch before it, without the air plant fallback, gave 22.1 to 23.8 in all five games: the air force takes builders and metal from the first silo |
| First volley | 32.9, 36.1, 41.8, 43.2 and 48.3 minutes (1 to 3 missiles); the goal was 18 to 22 minutes |
| Silos at 45 minutes | 4, 4, 4, 5 and 2 (the batch before had 6 in four games); the goal was one about every two minutes |
| Silo placement | 665 to 1487 elmos behind the base along the front axis in every game (the old 600 gave -600 to -1200) |
| Anti-nukes at 45 minutes | 7, 5, 10, 2 and 11 (batches p9 to p12: 10 to 15). Ring batteries queued 3 to 8 per game; many timed out waiting for a builder |
| Air plants | present by 20 minutes in all five games (three needed the fallback); without the fallback 2 of 5 games had none at 45 minutes |
| Outriggers | in the batch before the air-plant gate all started and completed: first fusion at 32 to 35 minutes, second cluster at 37 to 46; shield, anti-nukes, SAMs and 4 heavy statics queued at each |

**Later batches (p14 to p16, 5 games each to 66 minutes).** Walls and outrigger stages:

| Measure | Result |
| --- | --- |
| Fortification walls | 10 to 73 standing by 65 minutes (the count is the AI's, including any it built itself) |
| Outrigger, normal priority (p14) | stage 1 unfinished at 65 minutes in 4 of 5 games; in 2 the cluster was moved after 6 failed tasks (tasks with no constructor able to take them fail within seconds and were counted) |
| Outrigger, high priority with nano turrets, waiting for a Tech 2 constructor (p16) | no failures or moves; the first fusion at 31 to 44 minutes, 2 fusions at 41 to 54, the second cluster started at 47 to 60, stage 2 started at 58.7 and 64.9 minutes in two games and not at all in the other three |
| Fusions / converters at 65 minutes (p16, whole base) | 17/24, 8/3, 12/7, 15/9, 17/16; energy income 9700 to 33800 |
| Silos at 65 minutes (p16) | 11, 8, 9, 9, 10 |
| Volleys at 55 to 65 minutes (p16) | 6 to 10 missiles on 6 to 11 silos in the games with at least 9 silos |

So the 60-minute picture (10 fusions, 15 converters, 10 to 20 heavy statics per outrigger) is not reached in the test games: stage 1 finishes at 41 to 54 minutes, and stage 2 needs the economy to carry two more clusters' worth of builders. The heavy statics at 65 minutes are 13 to 50 across the whole base, outriggers included.

Why the first launch is late: a silo is 8100 metal and 90000 energy, and each missile 1000 metal and 125000 energy. At 20 to 30 minutes the test economy makes 80 to 300 metal and 1300 to 4500 energy a second, which also feeds the labs, the air plant, the outriggers and the ring. More silos sooner would need a larger economy (the outriggers help, but start late) or starving the rest, and starving the air plant is what the 8-nano trial did. The first volley follows the first silo by 4 to 20 minutes because the missile estimate does not advance while energy stalls.

## Not verified

- **Tactical and EMP missiles in a fight.** In the test games (opponent with factories disabled) 1 or 2 launchers were standing by 33 to 37 minutes in 4 of 7 games, 1500 to 1700 elmos toward the front and behind the nearest defences; in the others the first task timed out and a second was queued. Nothing shows whether they stockpile and fire: the opponent had nothing in range.
- **Missiles actually held back.** Each window close shows the silos under script control and the target mask running, but the log cannot show a launch. The leak seen in the live game (a launch while the window was closed) has a mechanism that the mask addresses, but the headless games cannot confirm that no missile fired outside a window.
- **Volleys that hit something.** The opponent in the long games had no army and, quickly, nothing worth a missile.
- **Fighters, bombers and scouts under real attack.** Counts depend on what survives. The plant rotation and the advanced plant nudge raise production; they cannot make aircraft survive enemy air defence.
- **Cortex escorts.** Roughly 40% of escort tasks in the Cortex games timed out waiting for a builder; the structures still arrive, later. I did not find why Cortex has fewer builders available.
- **The existing big-gun relocation code** (`big_gun.as`) aborts and re-enqueues tasks from inside the builder hook for real big-gun tasks. That path has not crashed in these games; the "spread" variant did, and was removed.
- **Nuclear submarines and anti-nuke ships.** The test map has no water, so neither the submarine orders and firing nor the navy changes (`naval.as`) ran; they compile and are reasoned from the source only.
- **Whether the walls help.** They get built; the test opponent never attacked them, so what they do under fire is unknown.
- **Nano turrets at the park.** Whether they speed up the silos was not isolated: the first silo came at 22 to 24 minutes in the batch that added them, against 25 to 52 before, but the Tech 2 nudge and the top priority were added in the same batch.
