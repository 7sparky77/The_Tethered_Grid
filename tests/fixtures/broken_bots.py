"""Misbehaving bots for the runner tests.  Usage: python broken_bots.py <crash|hang|slow|garbage>"""
import json
import sys
import time

mode = sys.argv[1]
turns = 0
for line in sys.stdin:
    msg = json.loads(line)
    if msg["type"] != "turn":
        continue
    turns += 1
    if mode == "crash" and turns == 3:
        raise SystemExit("crash bot: exiting on purpose")
    if mode == "hang" and turns == 3:
        time.sleep(60)
    if mode == "slow" and turns == 2:
        time.sleep(0.6)
    if mode == "garbage" and turns % 2 == 0:
        print("this is not json", flush=True)
        continue
    drones = [d for d in msg["my_drones"] if not d["is_inert"]]
    print(json.dumps([{"drone_id": d["id"], "action": "MOVE", "direction": "DOWN" if msg["player_id"] == 1 else "UP"}
                      for d in drones]), flush=True)
