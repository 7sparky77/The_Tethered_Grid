"""
WORK IN PROGRESS - spine bot (not yet stronger than greedy_bot.py; drones still strand).

Run it from the repo root:  python Match_Runner.py bots/wip/spine_bot.py bots/greedy_bot.py

Greedy baseline bot ("spine" strategy).

Every turn:
  1. Remember what was seen: storms (until they expire), belts, scouted cells.
  2. Pick a target: the best known storm, or a scouting point in the storm band.
  3. Plan a "spine": the fewest relay cells leading from the Mother-Node to the
     target, where consecutive cells see each other (straight line, <= 5 cells,
     no walls). This is a BFS on a graph whose edges are tether links.
  4. Assign the nearest drones to the spine cells (relays). Everyone else is a
     filler: it walks toward free storm cells (or scouting points, or a stranded
     inert friend) and only leaves the network when its battery is full.
  5. Opportunistic sabotage: crush inert enemies against solid cells, push enemies
     out of storms, finish off weakened enemy anchors.

It ignores terminals and never builds anchors: a sparring partner, not a strong bot.
"""
import heapq
import json
import random
import sys
import traceback
from collections import deque

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
        self.max_battery = r["max_battery"]
        self.storm_rows = tuple(r["storm_rows"])
        self.storms = {}                         # centre -> (multiplier, expires_on_turn)
        self.belts = {}
        self.last_seen = {}
        self.rng = random.Random(self.me)
        self.target = None
        self.spine = []                          # relay cells, home side first
        self.spine_maps = []                     # walking distance map toward each relay cell
        self.relay_of = {}                       # drone id -> spine index

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

    def walk_map(self, goals: dict) -> dict:
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

    def plan_spine(self, goal_cells, blockers):
        """Fewest relay cells from the Mother-Node to any goal cell (BFS over tether links)."""
        prev = {}
        queue = deque()
        rows = range(1, self.tether + 1) if self.home == 0 else \
            range(self.n - 1 - self.tether, self.n - 1)
        for y in rows:
            for x in range(self.n):
                c = (x, y)
                if not self.solid(x, y) and c not in blockers and c not in self.belts \
                        and self.mother_link(c, blockers):
                    prev[c] = None
                    queue.append(c)
        while queue:
            c = queue.popleft()
            if c in goal_cells:
                path = []
                while c is not None:
                    path.append(c)
                    c = prev[c]
                return path[::-1]
            for dx, dy in EIGHT:
                for k in range(1, self.tether + 1):
                    nxt = (c[0] + dx * k, c[1] + dy * k)
                    if self.solid(*nxt) or nxt in blockers:
                        break                    # the tether cannot pass this cell
                    if nxt not in prev and nxt not in self.belts:
                        prev[nxt] = c
                        queue.append(nxt)
        return []

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

    def pick_target(self, turn):
        live = {c: v for c, v in self.storms.items() if v[1] - turn > 12}
        if self.target in live:
            return self.target                   # stick with it while it lasts
        if live:
            return max(live, key=lambda c: (live[c][0], -abs(c[1] - self.home)))
        # Nothing known: scout the storm band, least recently seen point first.
        lo, hi = self.storm_rows
        near_row = lo + 3 if self.home == 0 else hi - 3
        points = [(x, near_row) for x in (6, 16, 25, 34, 43)]
        if self.target in points and self.target not in self.last_seen:
            return self.target
        return min(points, key=lambda c: (self.last_seen.get(c, -10 ** 6),
                                          abs(c[0] - self.n // 2)))

    # ---- turn ----------------------------------------------------------------
    def decide(self, s):
        turn = s["turn"]
        mine = s["my_drones"]
        by_id = {d["id"]: d for d in mine}
        enemies = {(e["x"], e["y"]): e for e in s["visible_enemy_drones"]}
        enemy_anchors = s["visible_enemy_anchors"]
        anchor_cells = {(a["x"], a["y"]) for a in s["my_anchors"] + enemy_anchors}
        blockers = set(enemies) | {(a["x"], a["y"]) for a in enemy_anchors}
        los_blockers = self.walls | blockers

        active = [d for d in mine if not d["is_inert"]]
        visible = set()
        for u in active + s["my_anchors"]:
            for dy in range(-self.vision_r, self.vision_r + 1):
                for dx in range(-self.vision_r, self.vision_r + 1):
                    visible.add((u["x"] + dx, u["y"] + dy))
        self.remember(s, visible)

        storm_cells = {}
        for (sx, sy), (mult, _) in self.storms.items():
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if not self.solid(sx + dx, sy + dy):
                        storm_cells[(sx + dx, sy + dy)] = mult

        # --- spine (re)planning ---------------------------------------------
        target = self.pick_target(turn)
        spine_broken = any(c in blockers for c in self.spine)
        if target != self.target or spine_broken or not self.spine:
            self.target = target
            goal = {c for c in storm_cells if max(abs(c[0] - target[0]), abs(c[1] - target[1])) <= 1} \
                or {target}
            self.spine = self.plan_spine(goal, los_blockers)
            self.spine_maps = [self.walk_map({c: 0}) for c in self.spine]
            self.relay_of = {}

        # --- relay assignment (keep old ones, fill holes with nearest drones) --
        self.relay_of = {i: k for i, k in self.relay_of.items() if i in by_id and not by_id[i]["is_inert"]}
        taken = set(self.relay_of.values())
        free = [d for d in active if d["id"] not in self.relay_of]
        for k, cell in enumerate(self.spine):
            if k in taken or not free:
                continue
            best = min(free, key=lambda d: self.spine_maps[k].get((d["x"], d["y"]), 10 ** 6))
            self.relay_of[best["id"]] = k
            free.remove(best)

        # --- filler goals ------------------------------------------------------
        my_cells = {(d["x"], d["y"]) for d in mine}
        goals = {c: (3 - m) * 4 for c, m in storm_cells.items() if c not in my_cells}
        for d in mine:                           # rescue stranded inert friends
            if d["is_inert"]:
                for dx, dy in DIRS.values():
                    c = (d["x"] + dx, d["y"] + dy)
                    if not self.solid(*c):
                        goals[c] = min(goals.get(c, 99), 6)
        lo, hi = self.storm_rows
        for y in range(lo, hi + 1, 3):
            for x in range(1, self.n, 3):
                c = (x, y)
                if turn - self.last_seen.get(c, -10 ** 6) > STALE_AFTER and not self.solid(*c):
                    goals.setdefault(c, EXPLORE_COST)
        filler_map = self.walk_map(goals) if goals else {}

        # --- who is holding whom up (current power tree) -----------------------
        beam_cells = set()
        for a in s["my_anchors"]:
            dx, dy = DIRS[a["direction"]]
            for k in range(1, self.beam + 1):
                c = (a["x"] + dx * k, a["y"] + dy * k)
                if c in los_blockers or not (0 <= c[0] < self.n and 0 <= c[1] < self.n):
                    break
                beam_cells.add(c)
        roots = [d for d in mine if self.mother_link((d["x"], d["y"]), los_blockers)
                 or (d["x"], d["y"]) in beam_cells]
        tethered = {d["id"] for d in roots}
        kids = {d["id"]: [] for d in mine}
        queue = deque(roots)
        while queue:
            cur = queue.popleft()
            for d in mine:
                if d["id"] not in tethered and self.los((cur["x"], cur["y"]), (d["x"], d["y"]), los_blockers):
                    tethered.add(d["id"])
                    kids[cur["id"]].append((d["x"], d["y"]))
                    queue.append(d)

        # --- choose actions -----------------------------------------------------
        occupied = my_cells | set(enemies) | anchor_cells
        committed = []                           # cells of drones already handled and tethered
        commands = []

        def linked(c):
            return (self.mother_link(c, los_blockers) or c in beam_cells
                    or any(self.los(c, o, los_blockers) for o in committed))

        relays = sorted((d for d in active if d["id"] in self.relay_of), key=lambda d: self.relay_of[d["id"]])
        fillers = sorted((d for d in active if d["id"] not in self.relay_of),
                         key=lambda d: (abs(d["y"] - self.home), self.rng.random()))
        for d in relays + fillers:
            here = (d["x"], d["y"])

            action = self.sabotage(d, here, enemies, enemy_anchors, anchor_cells, storm_cells, occupied)
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

            walk = self.spine_maps[self.relay_of[d["id"]]] if d["id"] in self.relay_of else filler_map
            careful = d["battery"] < self.max_battery    # leave the network only on a full battery
            must_keep = kids[d["id"]]

            best, best_key = here, None
            for c, name in options:
                if must_keep and c != here and not all(self.los(c, k, los_blockers) for k in must_keep):
                    continue                     # would drop the drones it powers
                ok = linked(c)
                gap = 0 if ok else min([max(abs(c[0] - o[0]), abs(c[1] - o[1])) for o in committed]
                                       + [abs(c[1] - self.home)])
                steps = walk.get(c, 10 ** 6) + (1 if c in self.belts else 0)
                key = (not ok, gap, steps, name is not None) if careful else (steps, not ok, name is not None)
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
