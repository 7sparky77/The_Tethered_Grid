# The Tethered Grid

A turn-based programming game. Two bots each command a swarm of up to 25 drones,
spawned onto a 50 × 50 grid. Drones only work while they are *tethered*: linked by straight lines of
sight, at most 5 cells long, back to their Mother-Node row. Points come from keeping
tethered drones inside the wandering Anima Storms. There is no HP and no attack:
you win fights by cutting the enemy's tethers, dragging them with hacked conveyor
belts, and crushing their dead drones against walls.

**Full rules:** [RULES.md](RULES.md). Its decision log lists every place where the
engine deviates from or fills a gap in the design doc.

## Quick start

Needs Python 3.8+ and nothing else (on Linux / macOS type `python3` where this says `python`).

```bash
# Play a match (≈10 s) and save a replay
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

## Files

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

## Writing a bot

A bot is any program (any language) that talks JSON over stdin/stdout, one message per line.

1. It first receives `{"type": "init", ...}` (player id, walls, rule constants). **Don't reply to it.**
2. On each of its turns it receives `{"type": "turn", ...}` and must print **one line**: a JSON
   array of commands, within 1 second.
3. Never print anything else to stdout. Debug output goes to stderr, which ends up in `logs/`.

```json
[{"drone_id": 3, "action": "MOVE", "direction": "DOWN"},
 {"drone_id": 7, "action": "PUSH", "direction": "LEFT"},
 {"drone_id": 9, "action": "INTERACT", "target_x": 20, "target_y": 14, "belt_dir": "RIGHT"},
 {"drone_id": 4, "action": "BUILD", "direction": "DOWN", "partners": [5, 6]},
 {"drone_id": 11, "action": "SABOTAGE_ANCHOR", "anchor_id": 2},
 {"action": "SPAWN", "x": 12, "y": 4}]
```

A turn payload looks like:

```json
{"type": "turn", "turn": 42, "max_turns": 3000, "player_id": 1, "my_score": 120, "enemy_score": 95,
 "my_drones": [{"id": 3, "x": 10, "y": 7, "battery": 3, "is_powered": true, "is_inert": false}],
 "my_anchors": [{"id": 1, "x": 12, "y": 9, "direction": "DOWN", "hp": 3}], "my_reserve": 14,
 "visible_enemy_drones": [{"id": 31, "x": 11, "y": 9, "is_inert": false}],
 "visible_enemy_anchors": [], "visible_terminals": [{"id": 2, "x": 9, "y": 8, "turns_left": 17}],
 "visible_belts": [{"x": 10, "y": 8, "dir": "LEFT"}],
 "visible_storms": [{"x": 14, "y": 22, "multiplier": 3, "turns_left": 61}],
 "feedback": ["turn 40: drone 5 MOVE: cell occupied by a drone"]}
```

Bots in other languages: pass the command line in quotes, e.g.
`python Match_Runner.py "./mybot" bots/greedy_bot.py`.

## Checkpoint (engine v1) and what's next

Done:
* the complete ruleset from the doc, with a decision log ([RULES.md](RULES.md)),
* a deterministic, seeded engine with mirrored maps,
* a bot runner that survives bad bots,
* replays and a visualizer / sandbox,
* 45 tests.

Open questions for the team:
* **Balance**: storm count, lifetimes and multipliers; tether range vs board size; terminal
  count. Everything is a constant at the top of `Game_Engine.py`.
* **Terminals**: are belt hacks strong enough to matter? Neither sample bot uses them yet, so
  this is untested in real play.
* **Stronger reference bots** (finish `bots/wip/spine_bot.py`), so the balance questions can
  be answered with data.
* **Tournament harness**: round-robin over many seeds, both sides.
