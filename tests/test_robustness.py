"""Engine must survive any bot output; the runner must survive any bot behaviour."""
import os
import random
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import Match_Runner
from Game_Engine import GameState

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "broken_bots.py")


def junk(rng):
    return rng.choice([None, True, False, 0, 1, -5, 10 ** 12, 3.7, "", "UP", "up", "NONE", "MOVE",
                       [], [1, 2], [[1]], {}, {"a": 1}, "SABOTAGE_ANCHOR", "BUILD", "INTERACT"])


class EngineFuzz(unittest.TestCase):
    def test_random_garbage_never_crashes_the_engine(self):
        rng = random.Random(0)
        game = GameState(seed=11, max_turns=400)
        actions = ["MOVE", "PUSH", "INTERACT", "BUILD", "SABOTAGE_ANCHOR", "WAIT", "FLY", None, 7]
        while not game.is_over:
            payload = game.begin_turn()
            ids = [d["id"] for d in payload["my_drones"]] + [0, 999, -1]
            commands = []
            for _ in range(rng.randint(0, 40)):
                cmd = {"drone_id": rng.choice(ids + [junk(rng)]), "action": rng.choice(actions)}
                for key in ("direction", "target_x", "target_y", "belt_dir", "partners",
                            "sacrifice_ids", "anchor_id"):
                    if rng.random() < 0.5:
                        cmd[key] = junk(rng) if rng.random() < 0.5 else rng.choice(
                            ["UP", "DOWN", "LEFT", "RIGHT", rng.randint(0, 49), ids[:2], 1])
                commands.append(cmd if rng.random() < 0.9 else junk(rng))
            game.apply_commands(commands if rng.random() < 0.95 else junk(rng))
            game.end_turn()
        self.assertEqual(game.turn, 400)
        # Occupancy index must still match the units.
        cells = {(d.x, d.y) for d in game.drones.values()} | {(a.x, a.y) for a in game.anchors.values()}
        self.assertEqual(cells, set(game.occupant))

    def test_command_cap(self):
        game = GameState(seed=1, max_turns=2)
        game.begin_turn()
        rejected = game.apply_commands([{"drone_id": 1, "action": "WAIT"}] * 5000)
        self.assertTrue(any("only the first" in r for r in rejected))


class RunnerSurvivesBadBots(unittest.TestCase):
    def run_against(self, mode, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            return Match_Runner.play_match(
                f"{sys.executable} -u {FIXTURE} {mode}",
                "bots/random_bot.py", seed=4, turns=20, log_dir=tmp, quiet=True, **kw)

    def test_crashing_bot(self):
        result = self.run_against("crash")
        self.assertIn("exited", result["bots"]["1"]["crashed"])
        self.assertEqual(result["turns_played"], 20)

    def test_hanging_bot_is_killed(self):
        result = self.run_against("hang", soft_limit=0.3, hard_limit=1.0)
        self.assertIn("no answer", result["bots"]["1"]["crashed"])
        self.assertEqual(result["turns_played"], 20)

    def test_slow_answer_is_discarded(self):
        result = self.run_against("slow", soft_limit=0.3, hard_limit=2.0)
        self.assertEqual(result["bots"]["1"]["timeouts"], 1)
        self.assertIsNone(result["bots"]["1"]["crashed"])

    def test_garbage_output_is_reported(self):
        game_result = self.run_against("garbage")
        self.assertIsNone(game_result["bots"]["1"]["crashed"])


if __name__ == "__main__":
    unittest.main()
