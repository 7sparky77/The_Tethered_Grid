"""
Starter bot for The Tethered Grid - copy this file and fill in decide().

Protocol (one JSON object per line):
  * The engine first sends {"type": "init", ...}: your player id, the board size,
    the full wall list and the rule constants. Do NOT answer it.
  * Then, on each of your turns, it sends {"type": "turn", ...} with what your
    units can see. Answer with ONE line: a JSON array of commands, e.g.
        [{"drone_id": 3, "action": "MOVE", "direction": "DOWN"},
         {"drone_id": 7, "action": "PUSH", "direction": "LEFT"}]
  * Never print anything else to stdout. Debug output goes to stderr
    (Match_Runner.py saves it in logs/).
  * Answer within the time limit (1 s by default) or the turn is skipped.

Commands (one per drone per turn; the array runs in order, first come first served):
  MOVE            direction: UP | DOWN | LEFT | RIGHT
  PUSH            direction                       (shove the drone next to you 1 cell)
  INTERACT        target_x, target_y, belt_dir: UP | DOWN | LEFT | RIGHT | NONE
                  (drone must stand on a terminal; target must be inside your vision)
  BUILD           direction (beam), partners: [id, id]  (2 adjacent drones of yours)
  SABOTAGE_ANCHOR anchor_id                       (enemy anchor within 2 cells)
  WAIT
  SPAWN           x, y   (no drone_id: bring a reserve drone onto an empty cell linked to
                          your powered network; up to rules["spawns_per_turn"] per turn)
"""
import json
import sys
import traceback

DIRS = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}


class Bot:
    def __init__(self, init: dict):
        self.me = init["player_id"]
        self.size = init["grid_size"]
        self.home_row = init["my_mother_row"]
        self.walls = {tuple(w) for w in init["walls"]}
        self.rules = init["rules"]

    def decide(self, state: dict) -> list:
        commands = []
        if state["turn"] < 2:
            # Example: spawn one drone right next to our Mother-Node (always linked).
            row = 1 if self.home_row == 0 else self.size - 2
            commands.append({"action": "SPAWN", "x": self.size // 2, "y": row})
        for drone in state["my_drones"]:
            if drone["is_inert"]:
                continue                       # inert drones cannot act
            commands.append({"drone_id": drone["id"], "action": "WAIT"})
        return commands


def main():
    bot = None
    for line in sys.stdin:
        message = json.loads(line)
        if message["type"] == "init":
            bot = Bot(message)
            continue
        try:
            commands = bot.decide(message)
        except Exception:
            traceback.print_exc(file=sys.stderr)  # a crash in decide() just skips this turn
            commands = []
        print(json.dumps(commands), flush=True)


if __name__ == "__main__":
    main()
