# What you can set up with AngelScript in CircuitAI

**If you’re editing a BAR AI Studio profile, you can set up rules that notice something and ask BARbarIAn to do an existing job.** For example, “Once my silo and anti-nuke are finished, start ordering bombers and replace losses.” You can decide when to start, what to order, and how many to aim for. BARbarIAn still flies the bombers, picks their targets, and plans their routes.

This applies to **BARbarIAn (BARb)**, the AI binary Studio normally installs with your profile. The separate **CircuitAI/stable** package shipped with the inspected Recoil 2026.09.03 engine has the older startup scripting interface. Its scripts pick config files and set up categories and roles. The in-match recipes below require BARb’s callbacks. [BARbarIAn Hard profile scripts](https://github.com/beyond-all-reason/Beyond-All-Reason/tree/master/luarules/configs/BARb/stable/script/hard)

## Can I set this up?

| What you want the AI to do | What you can set up |
| --- | --- |
| Give each new factory a specific opener | **Yes.** When the factory comes online, request a sequence of units it can build. For the Hard example, find the opener lists in `script/misc/commander.as` and the code that queues them in `Factory::AiUnitAdded` in `script/manager/factory.as`. |
| Start bomber production after my own silo and anti-nuke finish | **Yes.** Remember each completion and start requesting bombers on that factory’s production turns once both have happened. You can keep that rule active even if a building is later lost. [Bomber reserve recipe](bomber-qrf.md) |
| Maintain a reserve of around 30 fighters | **Yes, as a recruitment target.** Ask for replacements when the count falls. The AI’s count includes fighters under construction, and orders still waiting in the queue need separate accounting. The factory needs capacity and resources. [Fighter replacement recipe](fighter-reserve.md) |
| Ask for a shield near every fifth completed Advanced Fusion Reactor | **Yes.** Count completions and create a build request near the reactor. Studio’s **Defense → Construction completion rules** can set this up for you. The request still needs a suitable builder and a valid site. [Reactor shield recipe](fusion-shields.md) |
| Wait until five minutes or a stronger economy before planning defenses | **Yes, for the normal defense-planner call covered by the hook.** Change the condition in `Military::AiMakeDefence`. A separate recipe can still request defenses early. For example, a reactor completion rule sends its shield request straight to the builder manager. |
| Hold aircraft at a spot, scramble them against a particular raider, then recall them along waypoints | **Only in part.** Raising the reserve is straightforward. BARb exposes a guard task anchored to a unit and a defend task that starts from the AI’s base. These are existing behaviors you can try through a task-selection rule. A recruitment rule alone leaves fighters on normal anti-air duty, which can take them away from base. The stock script interface has no general move-to-coordinate, attack-this-unit, or waypoint queue for aircraft. Exact dispatch and recall would need native support. |
| Keep a specific bridge clear of all buildings or change how squads route around it | **Requires native support for that exact rule.** Existing placement settings can protect routes the AI already reserves and adjust spacing. A script can choose where to request certain builds. A map-wide exclusion that all placement decisions obey, or a new routing algorithm, belongs in the native AI. [Existing placement controls](keeping-chokepoints-open.md) |
| Give a unit longer weapon range or more damage in the actual game | **Use game data or game Lua.** Some scripts can adjust the range BARb uses in its own calculations. The game controls how far the weapon fires and how much damage it deals. |

## How to tell whether your idea fits

Write the idea as **when this happens, check these facts, then ask the AI to do this job**. All three pieces need to be available in the profile.

1. **When can the rule run?** BARb offers entry points for startup, periodic updates, completed or destroyed units, and selected manager decisions such as choosing a factory’s next task. A timer can compare game time during one of those calls. Adding a function named `OnEnemySpotted` does not give it a new event.
2. **What can it check?** Common inputs include game time, metal and energy readings, the AI’s own unit types and counts, and some enemy threat summaries. The rule can remember what it observed, such as whether that silo ever finished. If it needs information the AI library does not expose, the script needs another available signal or a new native binding.
3. **What can it ask for?** It can request exposed jobs such as build, recruit, or guard, choose among existing tasks, and change supported settings such as fire stance. If the job is new, or you need to control each step of how a job runs, check what the existing task actually does before committing to a script-only approach.

Use **configuration** when the change is a value the AI already reads, such as raising Falcon production weights or setting a per-model cap. Use **AngelScript** when the decision needs a condition or memory, such as replenishing Falcons below a target. Use **C++** when the needed event, data, command, or task is missing from the DLL.

## Where to make the change

For a Studio BARb profile, look in **All files → script**. Use `main.as` for match-wide events such as unit completion, `manager/factory.as` for production choices, and `manager/military.as` for decisions such as when to call the defense planner. A script entry point is a function the AI already knows to call, with an exact name and arguments. Start from the example in the relevant recipe and keep the default behavior for cases your rule does not handle.

In a test match, first confirm that the intended script loaded. Then trigger your rule and check the result. For a shield rule, a logged build request shows that the script asked. The finished structure shows that the AI found a way to carry it out.
