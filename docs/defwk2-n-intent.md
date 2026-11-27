# DefWk2-N: design intent

What the DefWk2-N AI profile (project Willtest, copied from Defensive Wk2) is supposed to do, and why. This is the target to judge changes against. How it is implemented, the settings and the test results are in [nuke-programme-and-scout-swarm.md](nuke-programme-and-scout-swarm.md).

Last updated 2026-11-26.

## The idea

A defensive opponent that gets dangerous late. It digs in, spreads its economy, and then wins through nuclear weapons, a large air force and layered defences.

- **It does not need to beat BARb.** BARb is heavily optimised for the early game and will usually break a weak defensive player. Results against BARb are not the measure.
- **It does not need to rush the first nuke.** A strong, well-protected programme matters more than an early launch.

## What it should do

### Nuclear programme

- Build many launchers, adding roughly one every two minutes once under way, up to a hard cap of **23** (silos and nuclear submarines together).
- Keep growing through the late game. Two silos at 90 minutes is a failure.
- Put silos at the **back** of the base, away from the enemy, and defend them.
- Fire in **volleys** large enough to saturate anti-nuke cover. Nothing should launch outside a volley. The first request was for at least eight missiles ready before the first launch.

### Finding targets

- While the missiles build, send **scout aircraft swarms of 15 to 30** that find the enemy's main base and the valuable things in it.
- A swarm arrives together along one axis: spread out enough that one flak burst does not kill several, close enough to saturate the air defences.

### Staying alive

- **Anti-nukes:** many of them, spread over everything worth protecting, growing with the programme. Never just one or two.
- **Navy:** where there is water, shipyards build anti-nuke ships, and nuclear submarines (extra units pack) join the launcher force under the same volley control.
- **Defences:** plenty of heavy statics, shields and Tech 2 anti-air.
- **Walls:** heavy statics get walls around them. Always **fortification walls**, never Dragon's Teeth: anywhere a heavy static can be built, a Tech 2 constructor exists.

### Air force

- Many more fighters, bombers and scouts than the stock AI builds.
- Air plants and advanced air plants have **construction turrets** around them.

### Tactical and EMP missiles

- Build the launchers (`armemp`, `cortron`, `legperdition`) and let them fire.
- Place them **toward the enemy but never in front of the other defences**.
- PracTac gets this too.

### Outrigger bases

Separate resource clusters, away from both the front and the main base, so they end up **off to the side**.

- Each starts with **3 fusion reactors and 3 energy converters** (spare power for shields and weapons), under a shield, with extra defences facing the enemy.
- When a cluster is complete, it grows: another cluster on each side, or doubled contents in each.
- A reasonable cluster by **60 minutes**: about 10 fusions, 15 converters, 10 to 20 heavy statics, 3 anti-nukes and two shields.

### What it does not do

- With the extra units disabled, the metal budget goes into the defenses and nuclear programme above. With Extra Units Pack enabled, a strong late economy can support one gantry for the pack's siege and assault units.

## Rules for placing anything

- Only ground the building can actually stand on: no water, no cliffs.
- A spot that fails is not asked for again while another is available.
- Strategic launchers and other irreplaceable things go to the rear. Short-range weapons go forward, behind the line of defences.

## Constraints

- Everything is script and configuration for now. Native engine or AI changes may come later.
- The optional synced stockpile gadget provides real ammo counts. The plain-game fallback estimates them. Direct launch acknowledgement and scout flight in formation need capabilities beyond the profile's existing scripts.

## Where it falls short today

| Intent | Today |
| --- | --- |
| A launcher about every two minutes | Much slower in test games (roughly one every 8 to 10 minutes). Economy and builder time are the limit |
| Volleys of eight or more | The profile normally waits for eight ready missiles and four ready launchers, then uses the fewest rounds per launcher that reach eight planned shots. Eight loaded launchers can use a 20-second window. After five minutes above the missile threshold, a smaller group can release its stockpile. The optional gadget reads real ammo and the fallback estimates it. Actual launches depend on targeting and reload |
| Rear silo placement | The profile requests rear sites. The native builder can move a construction site forward. A shoreline fixture produced two rear silos and one forward silo, so strict rear placement still needs native support |
| Nothing launches outside a volley | The controlled witness observed shots inside the release window. An earlier native attack order can still fire during the following stop grace period |
| Scout swarm formation and axis | Scouts gather and leave together, then explore on the stock task |
| Outrigger size by 60 minutes | Not reached in test games: the first stage finished at about 41 to 54 minutes |
| Navy, nuclear submarines | Never exercised in a test. Submarines also need the extra units option switched on |
| Tactical and EMP fire | Launchers get built; no test has shown one firing |

## How to judge a change

Run several headless games (five at a time) on a land map **and** a water map, from more than one start position, and tune after each batch. Test games use an opponent that cannot attack, so they show what gets built, not how well it survives: that needs a real game.
