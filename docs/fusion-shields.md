# Shield Advanced Fusion Reactors

Use **Defense → Construction completion rules** to request an ordinary shield at each fifth Advanced Fusion Reactor completion and an advanced shield at each fifteenth. At completion 15, both intervals are due, so the AI can request both shields near that reactor. The count starts fresh when the AI script loads and includes earlier completions even if those reactors are later destroyed.

| Faction | Advanced Fusion Reactor | Ordinary shield | Advanced shield |
| --- | --- | --- | --- |
| Armada | `armafus` | Keeper (`armgate`) | Asylum (`armgatet3`) |
| Cortex | `corafus` | Overseer (`corgate`) | Sanctuary (`corgatet3`) |
| Legion | `legafus` | Soteria (`legdeflector`) | Elysium (`leggatet3`) |

These IDs are present in the Studio catalog for **Beyond All Reason test-32018-ad135b9**. Enable **Extra Units Pack** in **Test match** or in the BAR lobby's game options. It gives the AI's advanced aircraft, bot, and vehicle constructors the advanced shield build option. Studio's Test match already enables **Legion Faction**. Enable it in the BAR lobby when using Legion there. The Studio catalog scans with optional units enabled even when a match leaves them off.

## Request a shield every fifth and fifteenth completion

1. Open the AI project, select the profile you will play, and open **Defense → Construction completion rules**.
2. Use **Completed building** to pick your faction's Advanced Fusion Reactor. The three factions use the same display name, so match the ID in the table. Use **Requested building** to pick the ordinary shield.
3. Under **Count completions**, select **Across this AI**. Set **Completion count** to **5** and click **Add completion rule**.
4. Add another rule for the same reactor and the advanced shield. Select **Across this AI** and set **Completion count** to **15**.
5. Click **Save & install** in the Studio header. In **Test match**, refresh installed content if the AI is missing, then select your installed AI and the edited profile on the desired side. Enable **Extra Units Pack** and leave **Skip smoke test** clear. For a shorter trial, duplicate the profile and set the intervals to 1 and 2 before installing the project again.

Adding the rule enables it in the selected profile's script. Each interval requests a construction task near the reactor that triggers it if the shield is available. In the match engine log, look for `Studio construction request` with the reactor and shield IDs. That line means the request reached the build queue. Construction still needs a suitable site, a constructor with the shield in its build options, and sufficient resources. Check the eventual position in BAR or in a recorded replay. The count tracks completion events, so a lost shield or a failed build can leave fewer shields than the target ratio.

## Use an independent chance on each completion instead

The **On building completed** editor writes native `build_chain` rules. Its JSON conditions can roll a chance on each completion. They do not track a count or calculate a ratio. A **20%** chance averages one request per five eligible completions over many rolls. A **6.6667%** chance approximates one per fifteen. A particular match can produce several in a row or none. For a given shield, choose either the interval rule or the chance rule to avoid two independent requests.

1. In **Defense**, use **Configuration layer** to select `build_chain.json` for Armada or Cortex, or `build_chain_leg.json` for Legion. A configuration layer is one file the AI loads over its earlier settings. These are the matching reactor files in the example Hard profile. For a custom profile, use **Preview combined layers** and its detected startup order to check which file supplies the reactor's existing actions. In the example profile, the reactor is under **Completed building category** → `energy`.
2. Select the faction's Advanced Fusion Reactor in **Completed building ID**. If the reactor has no entry in the active file, expand **Add completed building** and enter the category `energy` and the reactor's ID.
3. Enter the ordinary **Defense building ID**, choose **As a new chain** if the **Chain** picker appears, and set **Offset direction** to **Front**, **Offset distance (elmos)** to **100**, and **Chance (%)** to **20**. Click **Add defense action**.
4. Repeat with the advanced shield ID, **Back**, **160** elmos, and **6.6667** percent. Keep the two shields in independent chains so the advanced shield can be attempted when the ordinary shield roll fails. The two rolls can also both succeed.

The offsets use the completed reactor's facing. Elmos are units of in-game distance, and the runtime adds clearance for half the shield footprint. The values above give the AI starting points for finding a build site. Each chain runs for a reactor completed through an AI construction task with the matching category.

## Add one timed shield to scheduled base defenses

Scheduled defenses are independent of reactor completions. Use this for a separate timed attempt near the base, even if the AI has built no reactors.

1. In **Defense**, open the layer that supplies the active schedule. In the inspected Hard profile, `build_chain.json` supplies it. `build_chain_leg.json` adds the Legion unit list and inherits the schedule.
2. Under **Scheduled base defenses**, use **Defense building** in the add form to choose the entry that lists Keeper, Overseer, or Soteria. In the example Hard files all three occupy slot 28 in their faction lists, so a single scheduled build covers the active faction.
3. Enter a **Game time (seconds)** when a constructor should be available, such as **1800** for 30 minutes. Click **Add scheduled build** once. If that shield already has a row, update the existing row instead of adding another.

Each schedule row supplies one attempt per base schedule the AI starts. If the AI establishes a new base after losing all its factories, the schedule can start again. When a row becomes due, the AI drops it even if the unit is unavailable. Check the selected faction, constructor, and time before relying on it.
