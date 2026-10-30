"""
Greedy baseline bot - a sparring partner for testing, not a strong bot.

Every turn:
  1. Remember what was seen: storms (until they expire), belts, scouted cells.
  2. Build one distance map toward "goals": free cells of known storms (better
     storms are cheaper), cells next to stranded inert friends, and, more
     weakly, cells of the storm band not scouted lately.
  3. Handle drones from the one nearest home outwards. Each drone takes the step
     that gets closest to a goal while staying tethered (to the Mother-Node, an
     anchor beam, a drone already handled this turn, or a tethered drone further
     ahead). A drone that powers others only moves to cells that keep them in sight.
  4. Opportunistic sabotage: crush inert enemies against solid cells, push enemies
     out of storms, finish off weakened enemy anchors.

It ignores terminals and never builds anchors.
"""
import heapq
import json
import random
import sys
import traceback

DIRS = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}
EIGHT = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
EXPLORE_COST = 14         # how much a stale scouting cell is "worth" compared with a storm
STALE_AFTER = 60          # turns before a scouted cell is worth checking again


class GreedyBot:
    def __init__(self, init: dict):
        self.me = init["player_id"]
        self.n = init["grid_size"]
        self.home = init["my_mother_row"]
        self.walls = {tuple(w) for w in init["walls"]}
        r = init["rules"]
        self.tether = r["tether_range"]
        self.vision_r = r["vision_radius"]
        self.beam = r["anchor_beam_range"]
        self.sabotage_r = r["sabotage_radius"]
        self.storm_rows = tuple(r["storm_rows"])
        self.spawns_per_turn = r.get("spawns_per_turn", 0)
        self.storms = {}                         # centre -> (multiplier, expires_on_turn)
        self.belts = {}
        self.last_seen = {}
        # Tie-breaks between equally good moves. An optional command-line seed
        # (python greedy_bot.py 7) changes only these coin flips.
        self.rng = random.Random(self.me if len(sys.argv) < 2 else f"{self.me}-{sys.argv[1]}")

    # ---- geometry ------------------------------------------------------------
    def solid(self, x, y):
        return not (0 <= x < self.n and 0 < y < self.n - 1) or (x, y) in self.walls

    def los(self, a, b, blockers):
        dx, dy = b[0] - a[0], b[1] - a[1]
        dist = max(abs(dx), abs(dy))
        if dist == 0 or dist > self.tether or (dx and dy and abs(dx) != abs(dy)):
            return False
        sx, sy = (dx > 0) - (dx < 0), (dy > 0) - (dy < 0)
        return all((a[0] + sx * k, a[1] + sy * k) not in blockers for k in range(1, dist))

    def mother_link(self, c, blockers):
        gap = abs(self.home - c[1])
        if gap == 0 or gap > self.tether:
            return False
        return any(0 <= tx < self.n and self.los(c, (tx, self.home), blockers)
                   for tx in (c[0], c[0] - gap, c[0] + gap))

    def distance_map(self, goals):
        """Dijkstra over open cells from {cell: starting cost}."""
        dist = {}
        heap = [(cost, c) for c, cost in goals.items()]
        heapq.heapify(heap)
        while heap:
            d, c = heapq.heappop(heap)
            if c in dist:
                continue
            dist[c] = d
            for dx, dy in DIRS.values():
                nxt = (c[0] + dx, c[1] + dy)
                if nxt not in dist and not self.solid(*nxt):
                    heapq.heappush(heap, (d + 1, nxt))
        return dist

    # ---- memory --------------------------------------------------------------
    def remember(self, s, visible):
        turn = s["turn"]
        for c in visible:
            self.last_seen[c] = turn
            self.belts.pop(c, None)
        for b in s["visible_belts"]:
            self.belts[(b["x"], b["y"])] = b["dir"]
        seen_now = {(st["x"], st["y"]) for st in s["visible_storms"]}
        for c in list(self.storms):
            if self.storms[c][1] <= turn or (c in visible and c not in seen_now):
                del self.storms[c]
        for st in s["visible_storms"]:
            self.storms[(st["x"], st["y"])] = (st["multiplier"], turn + st["turns_left"])

    # ---- turn ----------------------------------------------------------------
    def decide(self, s):
        turn = s["turn"]
        mine = s["my_drones"]
        enemies = {(e["x"], e["y"]): e for e in s["visible_enemy_drones"]}
        enemy_anchors = s["visible_enemy_anchors"]
        anchor_cells = {(a["x"], a["y"]) for a in s["my_anchors"] + enemy_anchors}
        blockers = self.walls | set(enemies) | {(a["x"], a["y"]) for a in enemy_anchors}

        visible = set()
        for u in [d for d in mine if not d["is_inert"]] + s["my_anchors"]:
            for dy in range(-self.vision_r, self.vision_r + 1):
                for dx in range(-self.vision_r, self.vision_r + 1):
                    visible.add((u["x"] + dx, u["y"] + dy))
        self.remember(s, visible)

        my_cells = {(d["x"], d["y"]) for d in mine}
        storm_cells = {}
        for (sx, sy), (mult, _) in self.storms.items():
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    storm_cells[(sx + dx, sy + dy)] = mult
        goals = {c: (3 - m) * 4 for c, m in storm_cells.items()
                 if not self.solid(*c) and c not in my_cells}
        for d in mine:                           # rescue inert drones: stand next to them
            if d["is_inert"]:
                for dx, dy in DIRS.values():
                    c = (d["x"] + dx, d["y"] + dy)
                    if not self.solid(*c):
                        goals[c] = min(goals.get(c, 99), 6)
        band_lo, band_hi = self.storm_rows       # storms only appear in this band
        for y in range(band_lo, band_hi + 1, 3):
            for x in range(1, self.n, 3):
                c = (x, y) if self.home == 0 else (self.n - 1 - x, self.n - 1 - y)
                if turn - self.last_seen.get(c, -10 ** 6) > STALE_AFTER and not self.solid(*c):
                    goals.setdefault(c, EXPLORE_COST)
        dist = self.distance_map(goals) if goals else {}

        # Power sources this turn: Mother-Node row and our anchor beams.
        beam_cells = set()
        for a in s["my_anchors"]:
            dx, dy = DIRS[a["direction"]]
            for k in range(1, self.beam + 1):
                c = (a["x"] + dx * k, a["y"] + dy * k)
                if c in blockers or not (0 <= c[0] < self.n and 0 <= c[1] < self.n):
                    break
                beam_cells.add(c)

        # Current power tree, to know which drones are holding others up.
        children = {d["id"]: [] for d in mine}
        parent = {}
        powered = [d for d in mine if self.mother_link((d["x"], d["y"]), blockers)
                   or (d["x"], d["y"]) in beam_cells]
        tethered = {d["id"] for d in powered}
        queue = list(powered)
        while queue:
            cur = queue.pop(0)
            for d in mine:
                if d["id"] not in tethered and self.los((cur["x"], cur["y"]), (d["x"], d["y"]), blockers):
                    tethered.add(d["id"])
                    children[cur["id"]].append((d["x"], d["y"]))
                    parent[d["id"]] = cur["id"]
                    queue.append(d)
        by_id = {d["id"]: d for d in mine}

        def descendants(drone_id):
            out, stack = set(), [drone_id]
            while stack:
                cur = stack.pop()
                for kid, p in parent.items():
                    if p == cur and kid not in out:
                        out.add(kid)
                        stack.append(kid)
            return out

        occupied = set(my_cells) | set(enemies) | anchor_cells
        committed = []                           # cells of drones already handled and tethered
        processed = set()
        commands = []
        active = [d for d in mine if not d["is_inert"]]
        active.sort(key=lambda d: (abs(d["y"] - self.home), self.rng.random()))

        def linked(c, extra=()):
            return (self.mother_link(c, blockers) or c in beam_cells
                    or any(self.los(c, a, blockers) for a in committed)
                    or any(self.los(c, a, blockers) for a in extra))

        # Spawn reserve drones on the free linked cell closest to a goal. Each new
        # drone is powered at once, so the next spawn can extend the chain from it.
        sources = [(by_id[i]["x"], by_id[i]["y"]) for i in tethered]
        home_rows = range(1, self.tether + 1) if self.home == 0 else \
            range(self.n - 1 - self.tether, self.n - 1)
        home_cells = [(x, y) for y in home_rows for x in range(self.n)
                      if not self.solid(x, y) and self.mother_link((x, y), blockers)]
        for _ in range(min(s.get("my_reserve", 0), self.spawns_per_turn)):
            candidates = set(home_cells) | beam_cells
            for sx, sy in sources:
                for dx, dy in EIGHT:
                    for k in range(1, self.tether + 1):
                        c = (sx + dx * k, sy + dy * k)
                        if self.solid(*c) or c in blockers:
                            break
                        candidates.add(c)
            candidates = [c for c in candidates if c not in occupied and c in dist]
            if not candidates:
                break
            best = min(candidates, key=lambda c: (dist[c], self.rng.random()))
            commands.append({"action": "SPAWN", "x": best[0], "y": best[1]})
            occupied.add(best)
            sources.append(best)
            committed.append(best)

        for d in active:
            here = (d["x"], d["y"])
            processed.add(d["id"])
            # Tethered drones not handled yet (and not hanging off this one) will
            # probably still be there after they move: good enough to link to.
            below = descendants(d["id"])
            ahead = [(by_id[i]["x"], by_id[i]["y"]) for i in tethered
                     if i not in processed and i not in below]

            action = self.sabotage(d, here, enemies, enemy_anchors, anchor_cells,
                                   storm_cells, occupied)
            if action:
                commands.append(action)
                if linked(here):
                    committed.append(here)
                continue

            options = [(here, None)]
            for name, (dx, dy) in DIRS.items():
                c = (here[0] + dx, here[1] + dy)
                if not self.solid(*c) and c not in occupied:
                    options.append((c, name))
            kids = children.get(d["id"], [])
            best, best_key = here, None
            for c, name in options:
                if kids and c != here and not all(self.los(c, k, blockers) for k in kids):
                    continue                     # would drop the drones it powers
                ok = linked(c, ahead)
                gap = 0 if ok else min([max(abs(c[0] - a[0]), abs(c[1] - a[1])) for a in committed]
                                       + [abs(c[1] - self.home)])
                steps = dist.get(c, 10 ** 6) + (1 if c in self.belts else 0)
                key = (not ok, gap, steps, name is not None)
                if best_key is None or key < best_key:
                    best, best_key = c, key
            if best != here:
                name = next(n for c, n in options if c == best)
                commands.append({"drone_id": d["id"], "action": "MOVE", "direction": name})
                occupied.discard(here)
                occupied.add(best)
            if linked(best):
                committed.append(best)
        return commands

    def sabotage(self, d, here, enemies, enemy_anchors, anchor_cells, storm_cells, occupied):
        for a in enemy_anchors:
            if a["hp"] <= 1 and max(abs(a["x"] - here[0]), abs(a["y"] - here[1])) <= self.sabotage_r:
                return {"drone_id": d["id"], "action": "SABOTAGE_ANCHOR", "anchor_id": a["id"]}
        for name, (dx, dy) in DIRS.items():
            target = (here[0] + dx, here[1] + dy)
            enemy = enemies.get(target)
            if enemy is None:
                continue
            behind = (target[0] + dx, target[1] + dy)
            if enemy["is_inert"] and (self.solid(*behind) or behind in anchor_cells):
                return {"drone_id": d["id"], "action": "PUSH", "direction": name}   # crush it
            if not enemy["is_inert"] and target in storm_cells and not self.solid(*behind) \
                    and behind not in occupied:
                return {"drone_id": d["id"], "action": "PUSH", "direction": name}   # evict it
        return None


def main():
    bot = None
    for line in sys.stdin:
        message = json.loads(line)
        if message["type"] == "init":
            bot = GreedyBot(message)
            continue
        try:
            commands = bot.decide(message)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            commands = []
        print(json.dumps(commands), flush=True)


if __name__ == "__main__":
    main()
