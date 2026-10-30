"""Rule tests for Game_Engine.py.  Run from the repo root:  python -m unittest -v"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import Game_Engine as G
from Game_Engine import AnimaStorm, Direction, GameState, Terminal, mirror


def empty_game() -> GameState:
    """A bare 50x50 board: no walls, belts, storms, terminals or drones."""
    return GameState(seed=1, generate_map=False, spawn_drones=False)


def cmd(drone, action, **kw):
    return dict(drone_id=drone.drone_id, action=action, **kw)


def pass_turn(game):
    game.play_turn([])


class LineOfSight(unittest.TestCase):
    def test_straight_lines_only_and_range(self):
        g = empty_game()
        self.assertTrue(g.has_los(10, 10, 15, 10, 1))      # horizontal, distance 5
        self.assertTrue(g.has_los(10, 10, 15, 15, 1))      # diagonal
        self.assertFalse(g.has_los(10, 10, 16, 10, 1))     # distance 6
        self.assertFalse(g.has_los(10, 10, 12, 11, 1))     # knight-ish angle

    def test_walls_and_enemies_block_friends_do_not(self):
        g = empty_game()
        g.walls.add((12, 10))
        self.assertFalse(g.has_los(10, 10, 14, 10, 1))
        g.add_drone(2, 10, 12)
        self.assertFalse(g.has_los(10, 10, 10, 14, 1))     # enemy drone in the way
        g.add_drone(1, 12, 12)
        self.assertTrue(g.has_los(10, 10, 14, 14, 1))      # own drone does not block


class PowerNetwork(unittest.TestCase):
    def test_mother_row_range(self):
        g = empty_game()
        near = g.add_drone(1, 10, 5)
        far = g.add_drone(1, 30, 6)
        g.recompute_power()
        self.assertTrue(near.is_powered)
        self.assertFalse(far.is_powered)

    def test_diagonal_link_to_mother_row(self):
        g = empty_game()
        g.walls.add((10, 4))                               # block the straight-up line
        d = g.add_drone(1, 10, 5)
        g.recompute_power()
        self.assertTrue(d.is_powered)                      # still sees (5,0) / (15,0) diagonally

    def test_chain_and_body_block(self):
        g = empty_game()
        a = g.add_drone(1, 10, 4)
        b = g.add_drone(1, 10, 9)
        c = g.add_drone(1, 10, 14)
        g.recompute_power()
        self.assertTrue(all(d.is_powered for d in (a, b, c)))
        self.assertEqual(c.power_source, (10, 9))
        g.add_drone(2, 10, 11)                             # enemy steps between b and c
        g.recompute_power()
        self.assertTrue(b.is_powered)
        self.assertFalse(c.is_powered)

    def test_battery_drains_every_turn_then_inert_then_revives(self):
        g = empty_game()
        lone = g.add_drone(1, 25, 25)
        g.recompute_power()
        self.assertFalse(lone.is_powered)
        for expected in range(G.MAX_BATTERY - 1, -1, -1):   # drains on P1 AND P2 turns
            pass_turn(g)
            self.assertEqual(lone.battery, expected)
        self.assertTrue(lone.is_inert)
        if g.current_player == 2:
            pass_turn(g)
        g.begin_turn()
        ok, _ = g.execute_command(cmd(lone, "MOVE", direction="UP"))
        self.assertFalse(ok)                                # inert drones cannot act
        g.end_turn()
        pass_turn(g)
        # A friendly chain reaches it again: it can act right away.
        for y in (5, 10, 15, 20):
            g.add_drone(1, 25, y)
        g.begin_turn()
        self.assertTrue(lone.is_powered)
        ok, _ = g.execute_command(cmd(lone, "WAIT"))
        self.assertTrue(ok)
        g.end_turn()
        self.assertEqual(lone.battery, 1)

    def test_recharge_is_one_per_turn(self):
        g = empty_game()
        d = g.add_drone(1, 5, 3)
        d.battery = 0
        g.play_turn([])
        self.assertEqual(d.battery, 1)
        pass_turn(g)                                        # P2's turn recharges it too
        self.assertEqual(d.battery, 2)


class Movement(unittest.TestCase):
    def test_move_rules(self):
        g = empty_game()
        d = g.add_drone(1, 5, 1)
        g.add_drone(2, 6, 2)
        g.begin_turn()
        self.assertFalse(g.execute_command(cmd(d, "MOVE", direction="UP"))[0])  # Mother-Node row
        self.assertFalse(g.execute_command(cmd(d, "MOVE", direction="DOWN"))[0])  # already acted
        g.end_turn()

    def test_one_command_per_drone_and_ownership(self):
        g = empty_game()
        mine = g.add_drone(1, 5, 3)
        theirs = g.add_drone(2, 9, 46)
        rejected = g.play_turn([cmd(mine, "MOVE", direction="DOWN"),
                                cmd(mine, "MOVE", direction="DOWN"),
                                cmd(theirs, "MOVE", direction="UP")])
        self.assertEqual((mine.x, mine.y), (5, 4))
        self.assertEqual((theirs.x, theirs.y), (9, 46))
        self.assertEqual(len(rejected), 2)
        self.assertEqual(len(g.feedback[1]), 2)             # delivered with the next payload
        payload = (pass_turn(g), g.begin_turn())[1]
        self.assertEqual(len(payload["feedback"]), 2)

    def test_array_order_decides_conflicts(self):
        g = empty_game()
        a = g.add_drone(1, 10, 3)
        b = g.add_drone(1, 12, 3)
        g.play_turn([cmd(b, "MOVE", direction="LEFT"), cmd(a, "MOVE", direction="RIGHT")])
        self.assertEqual((b.x, a.x), (11, 10))


class Pushing(unittest.TestCase):
    def setUp(self):
        self.g = empty_game()
        self.pusher = self.g.add_drone(1, 10, 3)

    def test_push_moves_target_one_cell(self):
        target = self.g.add_drone(2, 11, 3)
        self.g.play_turn([cmd(self.pusher, "PUSH", direction="RIGHT")])
        self.assertEqual((target.x, target.y), (12, 3))
        self.assertEqual((self.pusher.x, self.pusher.y), (10, 3))

    def test_active_drone_is_crushed_too(self):
        self.g.walls.add((12, 3))
        target = self.g.add_drone(2, 11, 3)
        self.assertTrue(target.is_active)
        rejected = self.g.play_turn([cmd(self.pusher, "PUSH", direction="RIGHT")])
        self.assertEqual(rejected, [])
        self.assertNotIn(target.drone_id, self.g.drones)

    def test_active_drone_survives_when_rule_is_off(self):
        G.CRUSH_ACTIVE_DRONES = False
        try:
            self.g.walls.add((12, 3))
            target = self.g.add_drone(2, 11, 3)
            self.g.play_turn([cmd(self.pusher, "PUSH", direction="RIGHT")])
            self.assertIn(target.drone_id, self.g.drones)
        finally:
            G.CRUSH_ACTIVE_DRONES = True

    def test_inert_drone_is_crushed_against_wall_edge_or_mother_row(self):
        g = self.g
        g.walls.add((12, 3))
        edge_pusher = g.add_drone(1, 1, 10)
        row_pusher = g.add_drone(1, 30, 2)
        cases = [(self.pusher, (11, 3), "RIGHT"),           # into a wall
                 (edge_pusher, (0, 10), "LEFT"),             # off the map edge
                 (row_pusher, (30, 1), "UP")]                # into a Mother-Node row
        for pusher, (tx, ty), direction in cases:
            victim = g.add_drone(2, tx, ty)
            victim.is_powered, victim.battery = False, 0
            pusher.battery = 3
            g.begin_turn()
            ok, msg = g.execute_command(cmd(pusher, "PUSH", direction=direction))
            self.assertTrue(ok, msg)
            self.assertNotIn(victim.drone_id, g.drones)
            g.end_turn()
            pass_turn(g)
        self.assertEqual(g.stats[1]["crushed_enemies"], 3)
        self.assertEqual(g.stats[2]["drones_lost"], 3)

    def test_cannot_push_into_occupied_cell_or_push_anchor(self):
        g = self.g
        target = g.add_drone(2, 11, 3)
        g.add_drone(2, 12, 3)
        g.add_anchor(2, 10, 4, Direction.DOWN)
        rejected = g.play_turn([cmd(self.pusher, "PUSH", direction="RIGHT")])
        self.assertEqual((target.x, target.y), (11, 3))
        pass_turn(g)
        rejected = g.play_turn([cmd(self.pusher, "PUSH", direction="DOWN")])
        self.assertIn("anchors cannot be pushed", rejected[0])


class Belts(unittest.TestCase):
    def test_belts_move_standing_drones_every_turn(self):
        g = empty_game()
        g.belts[(10, 3)] = Direction.RIGHT
        g.belts[(30, 46)] = Direction.LEFT
        mine = g.add_drone(1, 10, 3)
        theirs = g.add_drone(2, 30, 46)
        g.begin_turn()                                      # P1's turn starts
        self.assertEqual(mine.x, 10)
        self.assertEqual(theirs.x, 30)
        g.end_turn()                                        # P1 ends turn -> belt moves both drones on belts
        self.assertEqual(mine.x, 11)
        self.assertEqual(theirs.x, 29)
        g.begin_turn()                                      # P2's turn starts
        g.end_turn()                                        # P2 ends turn -> both move again
        self.assertEqual(mine.x, 11)                        # mine is off belt at (11,3)
        self.assertEqual(theirs.x, 29)                      # theirs is off belt at (29,46)

    def test_train_of_drones_moves_together(self):
        g = empty_game()
        for x in (10, 11, 12):
            g.belts[(x, 5)] = Direction.RIGHT
        drones = [g.add_drone(1, x, 5) for x in (10, 11, 12)]
        g.begin_turn()
        g.end_turn()
        self.assertEqual([d.x for d in drones], [11, 12, 13])

    def test_head_on_contest_and_crushing_inert(self):
        g = empty_game()
        g.belts[(10, 5)] = Direction.RIGHT
        g.belts[(12, 5)] = Direction.LEFT
        a = g.add_drone(1, 10, 5)
        b = g.add_drone(1, 12, 5)
        g.walls.add((20, 6))
        g.belts[(19, 6)] = Direction.RIGHT
        inert = g.add_drone(1, 19, 6)
        g.belts[(30, 6)] = Direction.UP
        active = g.add_drone(1, 30, 6)
        g.walls.add((30, 5))
        g.recompute_power()
        inert.is_powered, inert.battery = False, 0
        g.begin_turn()
        g.end_turn()
        self.assertEqual((a.x, b.x), (10, 12))              # both wanted (11,5)
        self.assertNotIn(inert.drone_id, g.drones)          # dragged into the wall
        self.assertIn(active.drone_id, g.drones)            # active drones just stop
        self.assertEqual((active.x, active.y), (30, 6))

    def test_drone_actions_execute_before_belt_moves(self):
        g = empty_game()
        g.belts[(10, 4)] = Direction.DOWN
        d = g.add_drone(1, 10, 3)
        g.begin_turn()
        # Move drone onto belt
        g.execute_command(cmd(d, "MOVE", direction="DOWN"))
        self.assertEqual(d.y, 4)
        # End turn -> belt pushes drone
        g.end_turn()
        self.assertEqual(d.y, 5)

    def test_drone_moving_off_belt_is_not_pushed(self):
        g = empty_game()
        g.belts[(10, 3)] = Direction.DOWN
        d = g.add_drone(1, 10, 3)
        g.begin_turn()
        # Move drone off belt onto empty square (10, 2)
        g.execute_command(cmd(d, "MOVE", direction="UP"))
        self.assertEqual(d.y, 2)
        # End turn -> drone is no longer on belt, stays at (10, 2)
        g.end_turn()
        self.assertEqual(d.y, 2)

    def test_terminal_belts_expire_after_2_turns(self):
        g = empty_game()
        g.terminals.append(Terminal(0, 10, 3, 50))
        hacker = g.add_drone(1, 10, 3)
        g.begin_turn()
        g.execute_command(cmd(hacker, "INTERACT", target_x=11, target_y=3, belt_dir="RIGHT"))
        self.assertEqual(g.belts[(11, 3)], Direction.RIGHT)
        g.end_turn()                                        # 1 turn passed
        self.assertIn((11, 3), g.belts)
        g.begin_turn()
        g.end_turn()                                        # 2 turns passed -> belt expires
        self.assertNotIn((11, 3), g.belts)

    def test_6_turn_drone_belts_reallocate_and_old_disappear(self):
        g = empty_game()
        d1 = g.add_drone(1, 10, 10)
        d2 = g.add_drone(2, 30, 30)
        g.belt_drone_timer = 1
        g.begin_turn()
        g.end_turn()                                        # timer reaches 0, drone belts spawn
        self.assertEqual(len(g.drone_belt_cells), 2)
        old_cells = list(g.drone_belt_cells)
        for cell in old_cells:
            self.assertIn(cell, g.belts)
        # Fast forward 6 turns to trigger reallocation
        for _ in range(6):
            g.begin_turn()
            g.end_turn()
        # Old cells should have disappeared (reallocated to new cells)
        for cell in old_cells:
            if cell not in g.drone_belt_cells:
                self.assertNotIn(cell, g.belts)


class Terminals(unittest.TestCase):
    def setUp(self):
        self.g = empty_game()
        self.g.terminals.append(Terminal(0, 10, 3, 50))
        self.hacker = self.g.add_drone(1, 10, 3)

    def test_interact_places_and_removes_belts_in_vision(self):
        g = self.g
        g.play_turn([cmd(self.hacker, "INTERACT", target_x=11, target_y=4, belt_dir="LEFT")])
        self.assertEqual(g.belts[(11, 4)], Direction.LEFT)
        g.play_turn([cmd(self.hacker, "INTERACT", target_x=11, target_y=4, belt_dir="NONE")])
        self.assertNotIn((11, 4), g.belts)

    def test_interact_rules(self):
        g = self.g
        walker = g.add_drone(1, 20, 3)
        rejected = g.play_turn([
            cmd(self.hacker, "INTERACT", target_x=40, target_y=40, belt_dir="UP"),   # not visible
            cmd(walker, "INTERACT", target_x=20, target_y=4, belt_dir="UP"),         # no terminal
        ])
        self.assertEqual(len(rejected), 2)
        self.assertEqual(g.belts, {})

    def test_terminal_pairs_relocate(self):
        g = GameState(seed=5)
        before = [(t.x, t.y) for t in g.terminals[:2]]
        first_timer = g.terminals[0].timer
        for _ in range(first_timer):
            pass_turn(g)
        after = [(t.x, t.y) for t in g.terminals[:2]]
        self.assertNotEqual(before, after)
        self.assertEqual(after[1], mirror(*after[0]))
        self.assertTrue(G.TERMINAL_LIFESPAN[0] <= g.terminals[0].timer <= G.TERMINAL_LIFESPAN[1])


class Anchors(unittest.TestCase):
    def build_anchor(self, g, direction="DOWN"):
        b = g.add_drone(1, 20, 3)
        p1 = g.add_drone(1, 21, 3)
        p2 = g.add_drone(1, 20, 4)
        g.play_turn([cmd(b, "BUILD", direction=direction, partners=[p1.drone_id, p2.drone_id])])
        return next(iter(g.anchors.values()))

    def test_build_consumes_three_drones(self):
        g = empty_game()
        anchor = self.build_anchor(g)
        self.assertEqual((anchor.x, anchor.y, anchor.direction), (20, 3, Direction.DOWN))
        self.assertEqual(len(g.drones), 0)
        self.assertEqual(g.stats[1]["anchors_built"], 1)

    def test_beam_powers_drones_far_from_home(self):
        g = empty_game()
        anchor = g.add_anchor(1, 25, 25, Direction.RIGHT)
        in_beam = g.add_drone(1, 30, 25)
        off_beam = g.add_drone(1, 25, 30)                  # 5 away but not in the beam
        relay = g.add_drone(1, 30, 30)                      # powered through in_beam
        g.recompute_power()
        self.assertTrue(in_beam.is_powered)
        self.assertEqual(in_beam.power_source, (25, 25))
        self.assertTrue(relay.is_powered)
        self.assertTrue(off_beam.is_powered)                # via relay (5 cells left of it)
        g.add_drone(2, 27, 25)                               # enemy cuts the beam
        g.recompute_power()
        self.assertFalse(in_beam.is_powered)
        self.assertFalse(off_beam.is_powered)

    def test_build_rules(self):
        g = empty_game()
        b = g.add_drone(1, 20, 3)
        p1 = g.add_drone(1, 21, 3)
        far = g.add_drone(1, 25, 3)
        rejected = g.play_turn([cmd(b, "BUILD", direction="DOWN",
                                    partners=[p1.drone_id, far.drone_id])])
        self.assertIn("not adjacent", rejected[0])
        pass_turn(g)
        p2 = g.add_drone(1, 19, 4)
        rejected = g.play_turn([cmd(p1, "WAIT"),
                                cmd(b, "BUILD", direction="DOWN",
                                    partners=[p1.drone_id, p2.drone_id])])
        self.assertIn("already acted", rejected[0])
        self.assertEqual(len(g.anchors), 0)

    def test_sabotage_takes_three_drones(self):
        g = empty_game()
        anchor = g.add_anchor(2, 25, 25, Direction.UP)
        raiders = [g.add_drone(1, x, 23) for x in (24, 25, 26)]
        far = g.add_drone(1, 25, 20)
        g.recompute_power()
        for r in raiders + [far]:                            # keep them active for the test
            r.battery = 3
        rejected = g.play_turn([cmd(r, "SABOTAGE_ANCHOR", anchor_id=anchor.anchor_id)
                                for r in [far] + raiders])
        self.assertEqual(len(rejected), 1)                   # `far` is outside the 5x5 region
        self.assertNotIn(anchor.anchor_id, g.anchors)
        self.assertNotIn((25, 25), g.occupant)
        self.assertEqual(len(g.player_drones(1)), 1)
        self.assertEqual(g.stats[1]["anchors_destroyed"], 1)

    def test_anchor_ignores_belts_and_blocks_enemy_los(self):
        g = empty_game()
        g.belts[(25, 25)] = Direction.UP
        g.add_anchor(2, 25, 25, Direction.UP)
        g.begin_turn()
        g.end_turn()
        self.assertIn((25, 25), g.occupant)
        self.assertFalse(g.has_los(25, 22, 25, 27, 1))


class ScoringAndVision(unittest.TestCase):
    def test_active_drones_score_every_turn_inert_ones_do_not(self):
        g = empty_game()
        g.storms.append(AnimaStorm(1, 25, 25, 2, 999))
        tethered = g.add_drone(1, 24, 24)
        g.add_anchor(1, 24, 20, Direction.DOWN)              # beam lights (24,21)..(24,25)
        on_battery = g.add_drone(1, 26, 26)                  # cut off by the enemy below
        g.add_drone(2, 25, 25)                               # P2 drone inside the storm, on battery
        dead = g.add_drone(1, 26, 25)
        dead.battery = 0                                     # inert: no straight line to a powered unit
        g.play_turn([])
        self.assertTrue(tethered.is_powered)
        self.assertFalse(on_battery.is_powered)
        self.assertTrue(dead.is_inert)
        self.assertEqual(g.scores[1], 2 * 2)                 # tethered + on battery, x2
        self.assertEqual(g.scores[2], 2)                     # P2 scores on P1's turn too
        g.play_turn([])
        self.assertEqual(g.scores[1], 8)

    def test_scoring_example(self):
        g = empty_game()
        g.storms.append(AnimaStorm(1, 10, 4, 3, 999))
        for x in (9, 10, 11):
            g.add_drone(1, x, 4)
        g.play_turn([])
        self.assertEqual(g.scores[1], 9)                     # 3 drones x multiplier 3
        g.play_turn([])                                      # P2's turn: P1 scores again
        self.assertEqual(g.scores[1], 18)

    def test_fog_of_war(self):
        g = empty_game()
        g.add_drone(1, 10, 10)
        g.add_drone(2, 12, 12)                               # inside 5x5
        g.add_drone(2, 13, 10)                               # outside
        g.storms.append(AnimaStorm(1, 30, 30, 1, 999))       # far away
        payload = g.generate_player_payload(1)
        self.assertEqual([(e["x"], e["y"]) for e in payload["visible_enemy_drones"]], [(12, 12)])
        self.assertEqual(payload["visible_storms"], [])
        self.assertNotIn("battery", payload["visible_enemy_drones"][0])

    def test_inert_drones_give_no_vision(self):
        g = empty_game()
        d = g.add_drone(1, 25, 25)
        d.is_powered, d.battery = False, 0
        self.assertEqual(g.vision(1), set())


class MapGeneration(unittest.TestCase):
    def test_map_is_symmetric_and_deterministic(self):
        g = GameState(seed=42)
        for (x, y) in g.walls:
            self.assertIn(mirror(x, y), g.walls)
        for (x, y), d in g.belts.items():
            self.assertEqual(g.belts[mirror(x, y)], d.mirrored())
        for i in range(0, len(g.storms), 2):
            a, b = g.storms[i], g.storms[i + 1]
            self.assertEqual((b.x, b.y), mirror(a.x, a.y))
            self.assertEqual(a.multiplier, b.multiplier)
        for d in g.player_drones(1):
            twin = g.occupant[mirror(d.x, d.y)]
            self.assertEqual(twin.player_id, 2)
        h = GameState(seed=42)
        self.assertEqual(g.walls, h.walls)
        self.assertEqual(g.frames[0], h.frames[0])

    def test_all_open_cells_reachable_for_many_seeds(self):
        for seed in range(30):
            g = GameState(seed=seed)
            self.assertTrue(GameState._is_connected(g.walls), seed)
            for s in g.storms:
                self.assertFalse(any(c in g.walls for c in s.cells()))

    def test_starting_position(self):
        g = GameState(seed=3)
        self.assertEqual(len(g.player_drones(1)), G.STARTING_DRONES)
        self.assertEqual(g.reserve[1], G.DRONES_PER_PLAYER - G.STARTING_DRONES)
        self.assertTrue(all(d.is_powered for d in g.drones.values()))

    def test_centre_is_wall_free(self):
        for seed in range(20):
            g = GameState(seed=seed)
            self.assertFalse(any(G.WALL_FREE_ROWS[0] <= y <= G.WALL_FREE_ROWS[1] for _, y in g.walls))


class Spawning(unittest.TestCase):
    def spawn(self, x, y):
        return {"action": "SPAWN", "x": x, "y": y}

    def test_build_a_chain_in_one_turn(self):
        g = empty_game()
        rejected = g.play_turn([self.spawn(10, 5), self.spawn(10, 10), self.spawn(10, 15)])
        self.assertEqual(rejected, [])
        self.assertEqual(g.reserve[1], G.DRONES_PER_PLAYER - 3)
        self.assertTrue(all(d.is_powered for d in g.player_drones(1)))

    def test_spawn_rules(self):
        g = empty_game()
        g.begin_turn()
        self.assertFalse(g.execute_command(self.spawn(10, 6))[0])     # too far from Mother-Node
        self.assertFalse(g.execute_command(self.spawn(10, 0))[0])     # on the Mother-Node row
        ok, _ = g.execute_command(self.spawn(10, 5))
        self.assertTrue(ok)
        self.assertFalse(g.execute_command(self.spawn(10, 5))[0])     # occupied
        new = g.occupant[(10, 5)]
        ok, msg = g.execute_command(cmd(new, "MOVE", direction="DOWN"))
        self.assertFalse(ok, "a drone cannot act on the turn it spawns")
        for x in range(G.SPAWNS_PER_TURN - 1):
            self.assertTrue(g.execute_command(self.spawn(20 + x, 3))[0])
        self.assertFalse(g.execute_command(self.spawn(40, 3))[0])     # per-turn limit
        g.end_turn()

    def test_enemy_blocks_spawn_link_and_reserve_runs_out(self):
        g = empty_game()
        g.add_drone(2, 10, 3)
        g.begin_turn()
        self.assertTrue(g.execute_command(self.spawn(10, 4))[0])      # diagonal to (6,0)/(14,0)
        g.add_drone(2, 10, 6)
        ok, msg = g.execute_command(self.spawn(10, 8))                # only line is via (10,4): blocked
        self.assertFalse(ok)
        self.assertIn("not linked", msg)
        g.end_turn()
        g.reserve[1] = 0
        pass_turn(g)
        rejected = g.play_turn([self.spawn(30, 2)])
        self.assertIn("reserve", rejected[0])


class FullMatch(unittest.TestCase):
    def test_match_runs_to_the_end_and_replays_identically(self):
        def run(seed):
            g = GameState(seed=seed, max_turns=300)
            import random
            rng = random.Random(seed)
            while not g.is_over:
                payload = g.begin_turn()
                cmds = [{"drone_id": d["id"], "action": rng.choice(["MOVE", "PUSH", "WAIT"]),
                         "direction": rng.choice(["UP", "DOWN", "LEFT", "RIGHT"])}
                        for d in payload["my_drones"] if not d["is_inert"]]
                g.apply_commands(cmds)
                g.end_turn()
            return g
        a, b = run(9), run(9)
        self.assertEqual(a.result(), b.result())
        self.assertEqual(len(a.frames), 301)
        self.assertEqual(a.replay()["frames"][-1], b.replay()["frames"][-1])


if __name__ == "__main__":
    unittest.main()
