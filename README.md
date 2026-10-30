# The Tethered Grid

Read **[The Tethered Grid v3](https://docs.google.com/document/d/1XgGKd_IMZjwYMCiCfVOiwnnUdR7Z9xiQWOC6-nZncRo/edit?tab=t.hhq2rzkvn9u6#heading=h.tho8ke39w6sf)** to understand the concept of the game.

## Quick Start

### Files

Save all the files in a single folder.

| File | What it is |
|---|---|
| `Game_Engine.py` | The rules. Written in pure Python, with no I/O, and deterministic for a given seed. All tunable numbers are at the top. |
| `Match_Runner.py` | Runs two bot programs against each other. Handles timeouts and crashes, and generates logs (`logs/`) and replays (`replays/`). |
| `Visualizer.html` | Replay viewer and live sandbox (runs the real engine in the browser via Pyodide). |
| `bots/bot_template.py` | Start here when writing a bot. |
| `bots/greedy_bot.py` | Baseline sparring bot: chains Drones towards Storms and crushes / evicts enemies. |
| `bots/random_bot.py` | Makes random moves; useful for fuzzing. |
| `bots/wip/spine_bot.py` | Work in progress: plans an explicit relay chain. Not better than Greedy yet. |
| `tests/` | Contains rule tests, a fuzz test (random garbage commands), and runner tests with crashing, hanging, and slow bots. |

Requires Python 3.8+ and nothing else. (On Linux / macOS, use `python3` instead of `python` in the commands below.)

```bash
# Play a match between two bots with seed 7 (≈10 s) and save a replay
python Match_Runner.py bots/greedy_bot.py bots/random_bot.py --seed 7

# Same seed, played twice with sides swapped (use this for real comparisons)
python Match_Runner.py bots/greedy_bot.py bots/random_bot.py --seed 7 --both-sides

# Run the rule tests
python -m unittest discover -s tests
```

### Watching a Replay

Open `Visualizer.html` in a browser, click **Open Replay**, and upload a file from `replays/` onto the board.

### Playing with the Live Engine

To play with the live engine using the **Sandbox** tab (manual play: you vs. a bot or manual vs manual), make sure you are in the correct directory (saved folder directory) and run:

```bash
python start_visualizer.py
```

This opens the visualizer in your browser through a small local server. Opening the HTML file directly does not work for the sandbox because browsers block it from loading `Game_Engine.py`.

Alternatively, you can start the server manually:

```bash
python -m http.server 8000
# Then open http://localhost:8000/Visualizer.html?sandbox=1
# Or       http://localhost:8000/Visualizer.html?replay=replays/seed7.json
```

## Basic UI Controls (Manual)

**Note:** The **Tool Box** on the left side is strictly for debugging purposes and editing grid entities. To make moves according to the game rules, select **Command** and use the controls described below. Selecting any other Tool Box option lets you see how the engine works in special cases.
For example, according to the rules, we cannot spawn a Drone just anywhere on the grid. However, using the Tool Box, we can place two Drones adjacent to a wall and then command one Drone to push the other against the wall to see whether it disappears. This is useful for testing edge cases. Testing this scenario by following the rules would require a proper network of Drones to reach the wall and the perfect position for one Drone to push the other. The Tool Box makes this process quick.

| Control | What it does |
|---|---|
| **Spawn Drone** | Click an empty grid cell to spawn a Drone there (ensure that no other Drone's `INTERACT`, `BUILD`, etc. action is in effect). You may spawn a maximum of 5 Drones in a single turn. Pressing **End Turn** confirms the actions for all your Drones during that turn and passes the turn to the opponent. Spawning a Drone is itself an action, so you cannot perform actions like `MOVE`, `PUSH`, etc. with the newly spawned Drone during that turn. |
| **Select Drone** | Click on a Drone to select / highlight it; its stats appear in the right-hand panel. Click again to deselect it. |
| **MOVE / WAIT** | Click on a Drone to select it, then, in the **Selected Drone** box, choose a direction to move it in, if possible according to the rules, or choose **WAIT** to do nothing with that Drone. |
| **PUSH** | Click on a Drone to select it, then, in the **Selected Drone** box, choose **Push Up**, **Push Down**, etc. This pushes an adjacent entity (if one is present) by one unit in the chosen direction. |
| **Interact** | After selecting a Drone, click **Interact** and then click a valid visible cell to hack it. The box **"Select belt direction..."** appears when you click an empty, visible cell. Choosing a direction then places a belt with the specified direction on that cell. The box **"Existing belt..."** appears when you click a visible cell containing an existing belt. You can then choose to change its direction or remove it. |
| **Build** | Select a Drone, click **Build**, choose the desired direction for the Anchor's power beam, and then click the two Drones beside it that lie on the same line to build an Anchor on the central Drone. Ensure that the build operation is initiated through the central Drone. |
| **Sabotage** | Select a Drone, click **Sabotage**, and then click a valid enemy Anchor to reduce its HP by 1. If its HP reaches 0, the Anchor disappears, and regardless our Drone is removed in the process if hp lowers, according to the rules. |
| **End Turn** | Commits all the actions performed by our Drones during the turn, updates battery levels and scores, processes Conveyor Belts, advances the world tick, and passes the turn to the opponent if any turns remain. |
| **Bot Turn** | Press **Bot Turn** to let the built-in Greedy bot play the current turn automatically. |
| **Bot is P2** | The Greedy bot now controls all actions for Player 2, so we do not need to control Player 2's actions manually. |
| **Undo / Redo** | As the names suggest, these options allow you to undo or redo actions. |

---

## View Box

The **View Box** helps us visualize:
- The **Fog of War**, showing the cells visible to a particular player.
- The tether lines connecting Powered Drones.
- The direction of an Anchor's power beam.

## Current Mechanics of Anima Storms, Conveyor Belts, and Terminals

### 1. Map Generation and Fairness

To ensure **fairness**, all randomly generated map elements (Walls, Conveyor Belts, Terminals, and Anima Storms) are generated in pairs that are 180° rotations of each other.

This ensures that P1's view of the map is exactly the same as P2's view, with the two sides being rotationally symmetric.

### 2. Anima Storms

- There are **8 Anima Storms (4 pairs)** on the map.
- Each pair randomly relocates every **40–50 turns**.

### 3. Terminals

- There are **6 Terminals (3 pairs)** on the map.
- Each pair randomly relocates every **40–50 turns**.

### 4. Conveyor Belts

Conveyor Belts exist in three types:

**Type 1: Striped Conveyor Belts**
- A stripe of random length (**4–6 cells**) appears, either horizontally or vertically placed.
- There are **5 pairs**, each appearing randomly every **20–30 turns**.
- The difference between the number of horizontal and vertical stripes is always exactly 1:

  `|Number of Horizontal Stripes - Number of Vertical Stripes| = 1`

**Type 2: Single-Cell Conveyor Belts**
- A single-cell Conveyor Belt appears directly underneath an Active Drone of each player.
- Afterward, they relocate randomly every **6 turns**, underneath a randomly selected Active Drone of each player.

**Type 3: Terminal-Created Conveyor Belts**
- Conveyor Belts created using Terminals remain active for the current turn and the next turn.
- They disappear after that.
- These belts do not appear randomly on their own; they are created through Terminal interactions and only disappear automatically after their duration expires.
