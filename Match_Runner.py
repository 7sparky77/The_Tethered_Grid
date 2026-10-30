"""
Runs a Tethered Grid match between two bot programs and saves a replay.

    python Match_Runner.py bots/greedy_bot.py bots/random_bot.py
    python Match_Runner.py bots/greedy_bot.py "./my_cpp_bot" --seed 7 --both-sides

A bot is any program that reads one JSON object per line on stdin and, for
every message with "type": "turn", prints one JSON array of commands on a
single stdout line. Anything a bot prints to stderr goes to logs/.
See README.md for the protocol.
"""

import argparse
import gzip
import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import time

from Game_Engine import GameState, MAX_TURNS

ROOT = os.path.dirname(os.path.abspath(__file__))


def bot_command(spec: str) -> list:
    """'bots/x.py [args]' runs with this Python; anything else is run as a shell-style command."""
    tokens = shlex.split(spec)
    if tokens[0].endswith(".py"):
        return [sys.executable, "-u", *tokens]
    return tokens


class BotProcess:
    def __init__(self, player_id: int, spec: str, log_path: str):
        self.player_id = player_id
        scripts = [t for t in shlex.split(spec) if t.endswith(".py")]
        self.name = os.path.basename(scripts[0] if scripts else shlex.split(spec)[0])
        self.alive = True
        self.timeouts = 0
        self.total_time = 0.0
        self.death_reason = ""
        self.log = open(log_path, "w")
        self.proc = subprocess.Popen(bot_command(spec), stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=self.log,
                                     text=True, bufsize=1, cwd=ROOT)
        self.lines: "queue.Queue[str | None]" = queue.Queue()
        threading.Thread(target=self._pump_stdout, daemon=True).start()

    def _pump_stdout(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)                                    # EOF: the bot exited

    def send(self, message: dict) -> bool:
        if not self.alive:
            return False
        try:
            self.proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError):
            self.kill("stdin closed (bot crashed?)")
            return False

    def ask(self, payload: dict, soft_limit: float, hard_limit: float):
        """Sends a turn and returns the bot's command list ([] if late / broken)."""
        if not self.send(payload):
            return []
        start = time.perf_counter()
        try:
            line = self.lines.get(timeout=hard_limit)
        except queue.Empty:
            self.kill(f"no answer within {hard_limit:.1f}s on turn {payload['turn']}")
            return []
        elapsed = time.perf_counter() - start
        self.total_time += elapsed
        if line is None:
            self.kill(f"exited on turn {payload['turn']} (see its log)")
            return []
        if elapsed > soft_limit:
            self.timeouts += 1                                   # answer arrived late: discard
            return []
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return "invalid JSON from bot: " + line.strip()[:120]

    def kill(self, reason: str):
        if self.alive:
            self.alive = False
            self.death_reason = reason
            print(f"  [P{self.player_id} {self.name}] {reason}", file=sys.stderr)
        if self.proc.poll() is None:
            self.proc.kill()

    def close(self):
        try:
            self.proc.stdin.close()                             # bots exit when stdin closes
        except OSError:
            pass
        try:
            self.proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.proc.stdout.close()
        self.log.close()


def play_match(p1_spec: str, p2_spec: str, seed=None, turns: int = MAX_TURNS,
               soft_limit: float = 1.0, hard_limit: float = 5.0, replay_path=None,
               log_dir: str = os.path.join(ROOT, "logs"), quiet: bool = False,
               note: str = "") -> dict:
    game = GameState(seed=seed, max_turns=turns)
    os.makedirs(log_dir, exist_ok=True)
    bots = {
        1: BotProcess(1, p1_spec, os.path.join(log_dir, f"seed{game.seed}_p1.log")),
        2: BotProcess(2, p2_spec, os.path.join(log_dir, f"seed{game.seed}_p2.log")),
    }
    try:
        for pid, bot in bots.items():
            bot.send(game.init_message(pid))

        while not game.is_over:
            player = game.current_player
            payload = game.begin_turn()
            # The first answer also pays for interpreter start-up.
            soft = soft_limit * 3 if game.turn < 2 else soft_limit
            commands = bots[player].ask(payload, soft, hard_limit)
            if isinstance(commands, str):                        # bad JSON: tell the bot next turn
                game._reject(player, commands)
                commands = []
            game.apply_commands(commands)
            game.end_turn()
            if not quiet and game.turn % 500 == 0:
                print(f"  turn {game.turn:5d}/{turns}   P1 {game.scores[1]:6d}  P2 {game.scores[2]:6d}")
    finally:
        for bot in bots.values():
            bot.close()

    result = game.result()
    result["bots"] = {str(p): {"name": b.name, "spec": spec, "timeouts": b.timeouts,
                               "avg_ms": round(1000 * b.total_time / max(1, turns // 2), 2),
                               "crashed": b.death_reason or None}
                      for (p, b), spec in zip(bots.items(), (p1_spec, p2_spec))}
    if replay_path:
        os.makedirs(os.path.dirname(os.path.abspath(replay_path)), exist_ok=True)
        meta = {"p1": bots[1].name, "p2": bots[2].name, "p1_cmd": p1_spec, "p2_cmd": p2_spec,
                "note": note}
        opener = gzip.open if replay_path.endswith(".gz") else open   # .json.gz = ~10x smaller
        with opener(replay_path, "wt") as f:
            json.dump(game.replay(meta), f, separators=(",", ":"))
        result["replay"] = replay_path
    return result


def describe(result: dict) -> str:
    names = {p: result["bots"][p]["name"] for p in ("1", "2")}
    s = result["scores"]
    w = result["winner"]
    head = f"P1 {names['1']}: {s['1']}   vs   P2 {names['2']}: {s['2']}   ->   " + \
        (f"winner P{w} ({names[str(w)]})" if w else "draw")
    lines = [head]
    for p in ("1", "2"):
        b, st = result["bots"][p], result["stats"][p]
        lines.append(f"  P{p}: drones left {result['drones_left'][p]:2d}, anchors {result['anchors'][p]}, "
                     f"crushed {st['crushed_enemies']}, lost {st['drones_lost']}, hacks {st['hacks']}, "
                     f"avg {b['avg_ms']} ms/turn, late answers {b['timeouts']}"
                     + (f", CRASHED: {b['crashed']}" if b["crashed"] else ""))
    if "replay" in result:
        lines.append(f"  replay: {result['replay']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Run a Tethered Grid match between two bots.")
    ap.add_argument("p1", help="bot for Player 1 (a .py file or a command)")
    ap.add_argument("p2", help="bot for Player 2")
    ap.add_argument("--seed", type=int, default=None, help="map / RNG seed (random if omitted)")
    ap.add_argument("--turns", type=int, default=MAX_TURNS, help=f"total turns (default {MAX_TURNS})")
    ap.add_argument("--time-limit", type=float, default=1.0, help="seconds per turn (default 1.0)")
    ap.add_argument("--replay", default=None,
                    help="replay file (default replays/seed<N>.json; end in .json.gz to compress)")
    ap.add_argument("--no-replay", action="store_true")
    ap.add_argument("--both-sides", action="store_true",
                    help="play a second game on the same map with sides swapped")
    ap.add_argument("--note", default="", help="text stored in the replay and shown by the viewer")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    seed = args.seed if args.seed is not None else GameState().seed
    games = [(args.p1, args.p2, "")]
    if args.both_sides:
        games = [(args.p1, args.p2, "_a"), (args.p2, args.p1, "_b")]

    totals = {}
    for p1, p2, suffix in games:
        replay = None if args.no_replay else \
            (args.replay or os.path.join(ROOT, "replays", f"seed{seed}{suffix}.json"))
        if args.both_sides and args.replay:
            replay = args.replay.replace(".json", f"{suffix}.json")
        print(f"Seed {seed}: {p1} (P1) vs {p2} (P2)")
        result = play_match(p1, p2, seed=seed, turns=args.turns, soft_limit=args.time_limit,
                            replay_path=replay, quiet=args.quiet, note=args.note)
        print(describe(result))
        for p, spec in (("1", p1), ("2", p2)):
            totals[spec] = totals.get(spec, 0) + result["scores"][p]
    if args.both_sides:
        print("Total over both sides: " + "   ".join(f"{k}: {v}" for k, v in totals.items()))


if __name__ == "__main__":
    main()
