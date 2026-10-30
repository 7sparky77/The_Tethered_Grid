# The Tethered Grid

Read **The Tethered Grid v3** to get the idea of the game here[(https://docs.google.com/document/d/1XgGKd_IMZjwYMCiCfVOiwnnUdR7Z9xiQWOC6-nZncRo/edit?tab=t.hhq2rzkvn9u6#heading=h.tho8ke39w6sf)].

## Quick start

### Files

| File | What it is |
|---|---|
| `Game_Engine.py` | The rules. Pure Python, no I/O, deterministic for a given seed. All tunable numbers are at the top. |
| `Match_Runner.py` | Runs two bot programs against each other: timeouts, crash handling, logs (`logs/`), replays (`replays/`). |
| `Visualizer.html` | Replay viewer + live sandbox (runs the real engine in the browser via Pyodide). |
| `bots/bot_template.py` | Start here when writing a bot. |
| `bots/greedy_bot.py` | Baseline sparring bot: chains drones toward storms, crushes / evicts enemies. |
| `bots/random_bot.py` | Random moves; useful for fuzzing. |
| `bots/wip/spine_bot.py` | Work in progress: plans an explicit relay chain. Not better than greedy yet. |
| `tests/` | Rule tests, a fuzz test (random garbage commands) and runner tests with crashing / hanging / slow bots. |

Needs Python 3.8+ and nothing else (on Linux / macOS type `python3` where this says `python`).

```bash
# Play a match between two bots (≈10 s) and save a replay
python Match_Runner.py bots/greedy_bot.py bots/random_bot.py --seed 7

# Same seed, played twice with sides swapped (use this for real comparisons)
python Match_Runner.py bots/greedy_bot.py bots/random_bot.py --seed 7 --both-sides

# Run the rule tests
python -m unittest discover -s tests
```

Watch a replay: open `Visualizer.html` in a browser and click **Open replay**
(or drag a file from `replays/` onto the board).

To play with the live engine (the **Sandbox** tab: manual play, you vs bot), run

```bash
python start_visualizer.py
```

It opens the visualizer in your browser through a small local server (opening the HTML
file directly does not work for the sandbox: browsers block it from loading `Game_Engine.py`).
Doing the same by hand:

```bash
python -m http.server 8000
# then open http://localhost:8000/Visualizer.html?sandbox=1
# or       http://localhost:8000/Visualizer.html?replay=replays/seed7.json
```

In the sandbox you can play either side by hand. To play **you vs the bot**, tick **Bot is P2**:
the greedy bot then answers every P2 turn automatically (or press **Bot plays turn** to let it move
for whichever side is up). Click an empty cell to SPAWN; the **Drone P1 / P2** tools place drones
anywhere for quick debugging. You can also step the greedy bot, edit the board
(walls, belts, drones, anchors, storms, terminals, drain batteries), undo/redo, and
see the exact JSON payload a bot would receive.

## **1. Start the visualizer**

1. Save all the files in one folder.
2. Open a command prompt in the repository root (the folder that contains `start_visualizer.py`).
3. Run `start_visualizer.py`

   - This starts a tiny local HTTP server and automatically opens your default browser pointing at `Visualizer.html`.

## **2. Basic UI controls (manual)**

**Note:** The **Tool Box** in the left side is strictly for **debugging purposes / editing the grid entities**. Selecting "command" and then the below information to make your moves is actually how the game abide by rules, select any other option of tool box and using it tells how the engine works for special cases. For example, according to rules, we cannot spawn a drone at any point that we want on the grid, but using tool box we can put 2 drones adjacent to a wall and then commanding the drone to push the other against a wall to see if it disappears or not for testing edge cases. If we tried to test this edge case by following the rules then that might take proper network of drones to reach the wall and the perfect position where one drone can push the other. So tool box does this quick.

| Control | What it does |
|---|---|
| **Spawn Drone** | Click an empty grid cell to spawn a drone there (ensure that some other drone's "INTERACT", "BUILD", etc. isn't in effect). You may spawn a maximum of 5 drones in a single turn. Pressing "End Turn" confirms the actions for all your drones for this turn and passes the turn to the opponent. Spawning a drone is itself an action, so you cannot perform actions like "move", "push", etc. for the spawned drone for this turn.|
| **Select Drone** | Click on a drone to select / highlight it; its stats appear in the right-hand panel. Clicking again de-selects it. |
| **MOVE / WAIT** | Click on the drone to select it, then in "selected drone" box choose any direction to move it in that direction if possible according to rules or choose **WAIT** to do nothing for that drone. |
| **PUSH** | Click on the drone to select it, then in "selected drone" box choose "push up", "push down", etc. This pushes an adjacent entity (in the same direction choosen), if there, by 1 unit in the direction choosen. |
| **Interact** | After selecting a drone, click **Interact** and then click a valid cell to hack it. "Select belt direction..." appears if you click an empty visible cell and then choosing direction places a belt with specified direction there. "Existing belt..." appears if you click on a visible existing belt, you may then choose to change its direction or remove it instead.|
| **Build** | Select a drone, click **Build**, choose the desired direction for power beam of the anchor, then click the two drones beside it that lie on the same line to build anchor on the central drone. Ensure that the build operation is commanded through central drone. |
| **Sabotage** | Select a drone, click **Sabotage**, then click any valid enemy anchor to lower its hp by 1, if it becomes 0, anchor disappears (our drone is removed in this process according to rules).  |
| **End Turn** | Commits all actions that we performed for our drones one by one previously, updates battery life, scores, performs the job of conveyor belts and advances the world tick and passes the turn to the opponent if turns are still remaining. |
| **Bot Turn** | Press **Bot Turn** to let the built-in greedy bot play the current turn automatically. |
| **Bot is P2** | Greedy bot now controls all actions of player 2 and we don't need to control player 2 actions manually.  |
| **Undo / Redo** | Self-explanatory. |


## **3. Typical workflow to spawn a drone**

1. **Select** an empty square where you want the drone.
2. Click the **Spawn Drone** button (or simply click the square – the UI treats a click on an empty cell as a spawn request).
3. Press **End Turn** – the engine will deduct battery if the drone is not on a powered tile and will update the score.

## **4. Using other actions**

- **Conveyor belts**: After clicking **Interact**, a small panel appears. Choose **North / East / South / West** to set the belt direction.
- **Removing a belt**: Click the belt cell, choose **Interact**, then pick **Remove** in the direction panel.
- **Terminals / Anchors**: Follow the same pattern – the UI will prompt for the required direction.

## **5. Running a full match from the command line (optional)**

If you want to run a whole game without the UI, use the helper script:

```bash
python Match_Runner.py --bot bots/greedy_bot.py --seed 42
```

This script loads `Game_Engine.py` and the specified bot, runs the match to completion, prints the final score, and writes a replay JSON.

---

**That’s it!** You now have the minimal steps to launch the visualizer, control drones, and optionally run a match from the console.
