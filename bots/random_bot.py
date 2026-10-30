"""Random bot: every active drone does a random MOVE / PUSH / WAIT. Good for fuzzing the engine."""
import json
import random
import sys

DIRECTIONS = ["UP", "DOWN", "LEFT", "RIGHT"]


def main():
    rng = random.Random()
    for line in sys.stdin:
        message = json.loads(line)
        if message["type"] == "init":
            rng.seed(message["player_id"] * 7919)
            continue
        commands = []
        home = 1 if message["player_id"] == 1 else 48        # row next to our Mother-Node
        for _ in range(min(message["my_reserve"], 2)):
            commands.append({"action": "SPAWN", "x": rng.randrange(50), "y": home})
        for drone in message["my_drones"]:
            if drone["is_inert"]:
                continue
            action = rng.choices(["MOVE", "PUSH", "WAIT"], weights=[6, 2, 1])[0]
            commands.append({"drone_id": drone["id"], "action": action,
                             "direction": rng.choice(DIRECTIONS)})
        print(json.dumps(commands), flush=True)


if __name__ == "__main__":
    main()
