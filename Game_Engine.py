"""
The Tethered Grid - Game Engine (pure rules, no process / file I/O).

This file only knows the rules. Running real bot processes lives in
Match_Runner.py, so this file can also be loaded inside the browser
(Visualizer.html runs it through Pyodide).

Coordinates: x = column (0..49, left -> right), y = row (0..49, top -> bottom).
UP means y - 1. Player 1's Mother-Node is row 0, Player 2's is row 49.

One "turn" = one player's move. Turns alternate P1, P2, P1, ...
A full turn for the active player runs in this order (see RULES.md):
    begin_turn()     -> conveyor belts move that player's drones, power is
                        recomputed, the fog-of-war payload is built
    apply_commands() -> the player's commands run one by one, in array order
    end_turn()       -> power recomputed, batteries tick, the player scores,
                        terminal / storm timers tick, a replay frame is saved
"""

import random
from collections import deque
from enum import Enum
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Tunable constants (every number the rules mention lives here)
# ---------------------------------------------------------------------------
GRID_SIZE = 50
DRONES_PER_PLAYER = 25          # each player's total supply of drones (no respawn)
STARTING_DRONES = 0             # placed on row 1 / 48 at the start; the rest wait in reserve
SPAWNS_PER_TURN = 5             # SPAWN commands allowed per turn
CRUSH_ACTIVE_DRONES = True      # PUSH into a solid cell destroys ANY drone (False: inert only)
MAX_TURNS = 3000                # total turns (P1 and P2 get 1500 each)
MAX_BATTERY = 5                 # drains 1 on EVERY turn (both players') while cut off
TETHER_RANGE = 5                # max Chebyshev distance of a power link
VISION_RADIUS = 2               # 5x5 vision around every active unit
ANCHOR_HP = 3
ANCHOR_BEAM_RANGE = 5
SABOTAGE_RADIUS = 2             # 5x5 defense region around an anchor
TERMINAL_PAIRS = 3              # 6 terminals on the map (3 mirrored pairs)
TERMINAL_LIFESPAN = (40, 50)    # turns before a terminal pair relocates (random in range 40..50)
STORM_PAIRS = 4                 # 8 Anima Storms (4 mirrored pairs)
STORM_LIFESPAN = (40, 50)       # turns a storm pair lives (random in range 40..50)
STORM_ROWS = (10, 39)           # every storm cell lies in these rows (public knowledge)
WALL_SEGMENT_PAIRS = 10
WALL_FREE_ROWS = (15, 34)       # no walls in the middle of the board
BELT_STRIPE_PAIRS = 5           # 10 belt stripes (5 mirrored pairs)
BELT_STRIPE_LIFESPAN = (20, 30) # turns a belt stripe pair lives (random in range 20..30)
BELT_STRIPE_LENGTH = (4, 6)     # length of each stripe (random in range 4..6)
BELT_DRONE_INTERVAL = 6         # random 1-cell belt under each player's drone every 6 turns
MAX_FEEDBACK = 50               # rejected-command messages sent back per turn
MAX_COMMANDS = 200              # commands read per turn; the rest are ignored

MOTHER_ROW = {1: 0, 2: GRID_SIZE - 1}


class Direction(Enum):
    UP = (0, -1)
    DOWN = (0, 1)
    LEFT = (-1, 0)
    RIGHT = (1, 0)

    @property
    def dx(self) -> int:
        return self.value[0]

    @property
    def dy(self) -> int:
        return self.value[1]

    def mirrored(self) -> "Direction":
        """Direction after the 180-degree map rotation used for fairness."""
        return {Direction.UP: Direction.DOWN, Direction.DOWN: Direction.UP,
                Direction.LEFT: Direction.RIGHT, Direction.RIGHT: Direction.LEFT}[self]


def parse_direction(value) -> Optional[Direction]:
    if isinstance(value, Direction):
        return value
    if isinstance(value, str) and value.upper() in Direction.__members__:
        return Direction[value.upper()]
    return None


def mirror(x: int, y: int) -> Tuple[int, int]:
    """Point reflection through the centre: P1's view of the map == P2's."""
    return GRID_SIZE - 1 - x, GRID_SIZE - 1 - y


def is_int(value) -> bool:
    """JSON integers only (bool is a subclass of int in Python, so exclude it)."""
    return isinstance(value, int) and not isinstance(value, bool)


def chebyshev(x1: int, y1: int, x2: int, y2: int) -> int:
    return max(abs(x1 - x2), abs(y1 - y2))


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------
class Drone:
    def __init__(self, drone_id: int, player_id: int, x: int, y: int):
        self.drone_id = drone_id
        self.player_id = player_id
        self.x = x
        self.y = y
        self.battery = MAX_BATTERY
        self.is_powered = True                  # connected to the tether network
        self.power_source: Optional[Tuple[int, int]] = None   # cell it draws power from

    @property
    def is_inert(self) -> bool:
        """Cut off from the network AND out of battery: cannot act, does not see."""
        return not self.is_powered and self.battery <= 0

    @property
    def is_active(self) -> bool:
        return not self.is_inert


class Anchor:
    def __init__(self, anchor_id: int, player_id: int, x: int, y: int, direction: Direction):
        self.anchor_id = anchor_id
        self.player_id = player_id
        self.x = x
        self.y = y
        self.direction = direction              # the one orthogonal direction it beams power
        self.hp = ANCHOR_HP


class Terminal:
    def __init__(self, terminal_id: int, x: int, y: int, timer: int):
        self.terminal_id = terminal_id
        self.x = x
        self.y = y
        self.timer = timer                      # turns left before it relocates


class BeltStripePair:
    def __init__(self, pair_id: int, orientation: str):
        self.pair_id = pair_id
        self.orientation = orientation          # 'H' or 'V'
        self.timer = 0                          # turns left before it relocates
        self.cells: List[Tuple[int, int]] = []
        self.mirror_cells: List[Tuple[int, int]] = []


class AnimaStorm:
    def __init__(self, storm_id: int, x: int, y: int, multiplier: int, timer: int):
        self.storm_id = storm_id
        self.x = x                              # centre of the 3x3 zone
        self.y = y
        self.multiplier = multiplier
        self.timer = timer                      # turns left before it fades

    def contains(self, x: int, y: int) -> bool:
        return abs(x - self.x) <= 1 and abs(y - self.y) <= 1

    def cells(self) -> List[Tuple[int, int]]:
        return [(self.x + dx, self.y + dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]


def storm_multiplier(center_y: int) -> int:
    """Storms closer to the middle row are worth more, to pull both swarms together."""
    distance = abs(center_y - (GRID_SIZE - 1) / 2)
    if distance <= 1.5:
        return 3
    if distance <= 3.5:
        return 2
    return 1


# ---------------------------------------------------------------------------
# Game state
# ---------------------------------------------------------------------------
class GameState:
    def __init__(self, seed: Optional[int] = None, generate_map: bool = True,
                 spawn_drones: bool = True, max_turns: int = MAX_TURNS):
        self.seed = seed if seed is not None else random.randrange(1, 10 ** 9)
        self.rng = random.Random(self.seed)
        self.max_turns = max_turns
        self.turn = 0                           # 0-based; even = P1, odd = P2
        self.scores = {1: 0, 2: 0}
        self.reserve = {1: DRONES_PER_PLAYER, 2: DRONES_PER_PLAYER}   # drones not yet spawned
        self.spawned_this_turn = 0

        self.walls: set = set()
        self.belts: Dict[Tuple[int, int], Direction] = {}
        self.belt_stripes: List[BeltStripePair] = []
        self.belt_drone_timer = BELT_DRONE_INTERVAL
        self.drone_belt_cells: List[Tuple[int, int]] = []
        self.terminal_belts: Dict[Tuple[int, int], int] = {}
        self.drones: Dict[int, Drone] = {}
        self.anchors: Dict[int, Anchor] = {}
        self.terminals: List[Terminal] = []     # stored in mirrored pairs: [0,1], [2,3], ...
        self.storms: List[AnimaStorm] = []      # same pairing as terminals
        self.occupant: Dict[Tuple[int, int], object] = {}   # cell -> Drone / Anchor

        self.next_drone_id = 1
        self.next_anchor_id = 1
        self.next_storm_id = 1

        self.stats = {p: {"crushed_enemies": 0, "drones_lost": 0, "anchors_built": 0,
                          "anchors_destroyed": 0, "hacks": 0} for p in (1, 2)}
        self.feedback: Dict[int, List[str]] = {1: [], 2: []}
        self.events: List[dict] = []            # what happened during the current turn

        # Per-turn bookkeeping filled by begin_turn()
        self.turn_started = False
        self.can_act: set = set()
        self.acted: set = set()
        self.turn_vision: set = set()

        self.frames: List[dict] = []
        self._last_belts = None

        if generate_map:
            self._generate_map()
        if spawn_drones:
            self._spawn_starting_drones()
        self.recompute_power()
        self.record_frame()

    # -----------------------------------------------------------------------
    # Basic queries
    # -----------------------------------------------------------------------
    @property
    def current_player(self) -> int:
        return 1 if self.turn % 2 == 0 else 2

    @property
    def is_over(self) -> bool:
        return self.turn >= self.max_turns

    @staticmethod
    def in_bounds(x: int, y: int) -> bool:
        return 0 <= x < GRID_SIZE and 0 <= y < GRID_SIZE

    @staticmethod
    def is_mother_row(y: int) -> bool:
        return y == 0 or y == GRID_SIZE - 1

    def is_solid(self, x: int, y: int) -> bool:
        """Cells that never move: map edge, Mother-Node rows, walls, anchors."""
        if not self.in_bounds(x, y) or self.is_mother_row(y) or (x, y) in self.walls:
            return True
        return isinstance(self.occupant.get((x, y)), Anchor)

    def is_free(self, x: int, y: int) -> bool:
        return not self.is_solid(x, y) and (x, y) not in self.occupant

    def get_entity_at(self, x: int, y: int):
        return self.occupant.get((x, y))

    def terminal_at(self, x: int, y: int) -> Optional[Terminal]:
        for t in self.terminals:
            if t.x == x and t.y == y:
                return t
        return None

    def player_drones(self, player_id: int) -> List[Drone]:
        return [d for d in self.drones.values() if d.player_id == player_id]

    # -----------------------------------------------------------------------
    # Unit placement helpers (keep the occupancy index in sync)
    # -----------------------------------------------------------------------
    def add_drone(self, player_id: int, x: int, y: int) -> Optional[Drone]:
        if not self.is_free(x, y):
            return None
        drone = Drone(self.next_drone_id, player_id, x, y)
        self.next_drone_id += 1
        self.drones[drone.drone_id] = drone
        self.occupant[(x, y)] = drone
        return drone

    def add_anchor(self, player_id: int, x: int, y: int, direction: Direction) -> Optional[Anchor]:
        if not self.is_free(x, y):
            return None
        anchor = Anchor(self.next_anchor_id, player_id, x, y, direction)
        self.next_anchor_id += 1
        self.anchors[anchor.anchor_id] = anchor
        self.occupant[(x, y)] = anchor
        return anchor

    def _move_drone(self, drone: Drone, x: int, y: int):
        del self.occupant[(drone.x, drone.y)]
        drone.x, drone.y = x, y
        self.occupant[(x, y)] = drone

    def _remove_drone(self, drone: Drone):
        self.occupant.pop((drone.x, drone.y), None)
        self.drones.pop(drone.drone_id, None)

    def _destroy_drone(self, drone: Drone, cause: str, by_player: Optional[int] = None):
        self._remove_drone(drone)
        self.stats[drone.player_id]["drones_lost"] += 1
        if by_player is not None and by_player != drone.player_id:
            self.stats[by_player]["crushed_enemies"] += 1
        self.events.append({"type": "crushed", "drone": drone.drone_id, "player": drone.player_id,
                            "x": drone.x, "y": drone.y, "cause": cause})

    # -----------------------------------------------------------------------
    # Line of sight & the power network
    # -----------------------------------------------------------------------
    def blocks_los(self, x: int, y: int, player_id: int) -> bool:
        """Walls block everyone. Enemy units (drones in any state, anchors) block you."""
        if (x, y) in self.walls:
            return True
        unit = self.occupant.get((x, y))
        return unit is not None and unit.player_id != player_id

    def has_los(self, x1: int, y1: int, x2: int, y2: int, player_id: int) -> bool:
        """Straight (orthogonal / diagonal) unobstructed line of length 1..TETHER_RANGE."""
        dx, dy = x2 - x1, y2 - y1
        dist = max(abs(dx), abs(dy))
        if dist == 0 or dist > TETHER_RANGE:
            return False
        if dx != 0 and dy != 0 and abs(dx) != abs(dy):
            return False
        sx = (dx > 0) - (dx < 0)
        sy = (dy > 0) - (dy < 0)
        for step in range(1, dist):
            if self.blocks_los(x1 + sx * step, y1 + sy * step, player_id):
                return False
        return True

    def mother_link(self, x: int, y: int, player_id: int) -> Optional[Tuple[int, int]]:
        """Mother-Node cell a unit at (x, y) can draw power from, if any."""
        row = MOTHER_ROW[player_id]
        gap = abs(row - y)
        if gap == 0 or gap > TETHER_RANGE:
            return None
        for tx in (x, x - gap, x + gap):        # straight down/up, or the two diagonals
            if 0 <= tx < GRID_SIZE and self.has_los(x, y, tx, row, player_id):
                return (tx, row)
        return None

    def beam_cells(self, anchor: Anchor) -> List[Tuple[int, int]]:
        """Cells lit by an anchor's beam; the beam stops at walls and enemy units."""
        cells = []
        for step in range(1, ANCHOR_BEAM_RANGE + 1):
            cx = anchor.x + anchor.direction.dx * step
            cy = anchor.y + anchor.direction.dy * step
            if not self.in_bounds(cx, cy) or self.blocks_los(cx, cy, anchor.player_id):
                break
            cells.append((cx, cy))
        return cells

    def recompute_power(self):
        """BFS over each player's tether network. Sets is_powered and power_source."""
        for player_id in (1, 2):
            drones = self.player_drones(player_id)
            powered = set()
            queue = deque()

            def power(drone: Drone, source: Tuple[int, int]):
                drone.power_source = source
                powered.add(drone.drone_id)
                queue.append(drone)

            # Roots: the Mother-Node row and friendly anchor beams
            for drone in drones:
                link = self.mother_link(drone.x, drone.y, player_id)
                if link is not None:
                    power(drone, link)
            for anchor in self.anchors.values():
                if anchor.player_id != player_id:
                    continue
                for cell in self.beam_cells(anchor):
                    unit = self.occupant.get(cell)
                    if isinstance(unit, Drone) and unit.player_id == player_id \
                            and unit.drone_id not in powered:
                        power(unit, (anchor.x, anchor.y))

            # Spread: any connected drone powers every friendly drone it can see
            while queue:
                current = queue.popleft()
                for other in drones:
                    if other.drone_id in powered:
                        continue
                    if self.has_los(current.x, current.y, other.x, other.y, player_id):
                        power(other, (current.x, current.y))

            for drone in drones:
                drone.is_powered = drone.drone_id in powered
                if not drone.is_powered:
                    drone.power_source = None

    def _tick_batteries(self):
        for drone in self.drones.values():
            if drone.is_powered:
                drone.battery = min(MAX_BATTERY, drone.battery + 1)
            else:
                drone.battery = max(0, drone.battery - 1)

    # -----------------------------------------------------------------------
    # Vision / fog of war
    # -----------------------------------------------------------------------
    def vision(self, player_id: int) -> set:
        """5x5 around every active drone and every anchor of the player."""
        centres = [(d.x, d.y) for d in self.drones.values()
                   if d.player_id == player_id and d.is_active]
        centres += [(a.x, a.y) for a in self.anchors.values() if a.player_id == player_id]
        seen = set()
        r = VISION_RADIUS
        for cx, cy in centres:
            for y in range(max(0, cy - r), min(GRID_SIZE, cy + r + 1)):
                for x in range(max(0, cx - r), min(GRID_SIZE, cx + r + 1)):
                    seen.add((x, y))
        return seen

    def generate_player_payload(self, player_id: int, visible: Optional[set] = None) -> dict:
        if visible is None:
            visible = self.vision(player_id)
        enemy = 3 - player_id

        my_drones = [{"id": d.drone_id, "x": d.x, "y": d.y, "battery": d.battery,
                      "is_powered": d.is_powered, "is_inert": d.is_inert}
                     for d in sorted(self.player_drones(player_id), key=lambda d: d.drone_id)]
        my_anchors = [{"id": a.anchor_id, "x": a.x, "y": a.y, "direction": a.direction.name,
                       "hp": a.hp}
                      for a in self.anchors.values() if a.player_id == player_id]
        enemy_drones = [{"id": d.drone_id, "x": d.x, "y": d.y, "is_inert": d.is_inert}
                        for d in self.drones.values()
                        if d.player_id == enemy and (d.x, d.y) in visible]
        enemy_anchors = [{"id": a.anchor_id, "x": a.x, "y": a.y, "direction": a.direction.name,
                          "hp": a.hp}
                         for a in self.anchors.values()
                         if a.player_id == enemy and (a.x, a.y) in visible]
        terminals = [{"id": t.terminal_id, "x": t.x, "y": t.y, "turns_left": t.timer}
                     for t in self.terminals if (t.x, t.y) in visible]
        belts = [{"x": x, "y": y, "dir": d.name} for (x, y), d in sorted(self.belts.items())
                 if (x, y) in visible]
        storms = [{"x": s.x, "y": s.y, "multiplier": s.multiplier, "turns_left": s.timer}
                  for s in self.storms if any(c in visible for c in s.cells())]

        return {
            "type": "turn",
            "turn": self.turn,
            "max_turns": self.max_turns,
            "player_id": player_id,
            "my_score": self.scores[player_id],
            "enemy_score": self.scores[enemy],
            "my_drones": my_drones,
            "my_anchors": my_anchors,
            "my_reserve": self.reserve[player_id],
            "visible_enemy_drones": enemy_drones,
            "visible_enemy_anchors": enemy_anchors,
            "visible_terminals": terminals,
            "visible_belts": belts,
            "visible_storms": storms,
            "feedback": list(self.feedback[player_id]),
        }

    def init_message(self, player_id: int) -> dict:
        """Sent once before the first turn: everything static about the match."""
        return {
            "type": "init",
            "player_id": player_id,
            "grid_size": GRID_SIZE,
            "max_turns": self.max_turns,
            "my_mother_row": MOTHER_ROW[player_id],
            "enemy_mother_row": MOTHER_ROW[3 - player_id],
            "walls": [[x, y] for (x, y) in sorted(self.walls)],
            "rules": {
                "max_battery": MAX_BATTERY, "tether_range": TETHER_RANGE,
                "vision_radius": VISION_RADIUS, "anchor_hp": ANCHOR_HP,
                "anchor_beam_range": ANCHOR_BEAM_RANGE, "sabotage_radius": SABOTAGE_RADIUS,
                "terminal_lifespan": list(TERMINAL_LIFESPAN), "drones_per_player": DRONES_PER_PLAYER,
                "storm_rows": list(STORM_ROWS), "spawns_per_turn": SPAWNS_PER_TURN,
                "crush_active_drones": CRUSH_ACTIVE_DRONES,
            },
        }

    # -----------------------------------------------------------------------
    # Turn pipeline
    # -----------------------------------------------------------------------
    def begin_turn(self) -> dict:
        """Active player power and vision computed, then that player gets its payload."""
        if self.turn_started:
            raise RuntimeError("begin_turn() called twice without end_turn()")
        player_id = self.current_player
        self.events = []
        self.recompute_power()
        self.turn_vision = self.vision(player_id)
        self.can_act = {d.drone_id for d in self.player_drones(player_id) if d.is_active}
        self.acted = set()
        self.spawned_this_turn = 0
        self.turn_started = True
        payload = self.generate_player_payload(player_id, self.turn_vision)
        self.feedback[player_id] = []
        return payload

    def apply_commands(self, commands) -> List[str]:
        """Runs a whole command array (in order). Returns the rejection messages."""
        player_id = self.current_player
        if isinstance(commands, dict) and isinstance(commands.get("commands"), list):
            commands = commands["commands"]
        if not isinstance(commands, list):
            self._reject(player_id, "response must be a JSON array of commands")
            return list(self.feedback[player_id])
        if len(commands) > MAX_COMMANDS:
            self._reject(player_id, f"only the first {MAX_COMMANDS} commands are read")
        for cmd in commands[:MAX_COMMANDS]:
            self.execute_command(cmd)
        return list(self.feedback[player_id])

    def end_turn(self):
        """Conveyor belts push standing drones, power/batteries/scores/world tick, frame recorded."""
        if not self.turn_started:
            raise RuntimeError("end_turn() called before begin_turn()")
        self._belt_phase()
        self.recompute_power()
        self._tick_batteries()                     # every turn, both players' drones
        self._score(1)                              # both players score every turn
        self._score(2)
        self._tick_world()
        self.record_frame()
        self.turn += 1
        self.turn_started = False

    def play_turn(self, commands) -> List[str]:
        """Convenience for tests / scripts that already know their commands."""
        self.begin_turn()
        rejected = self.apply_commands(commands)
        self.end_turn()
        return rejected

    # -----------------------------------------------------------------------
    # Commands
    # -----------------------------------------------------------------------
    def _reject(self, player_id: int, message: str):
        if len(self.feedback[player_id]) < MAX_FEEDBACK:
            self.feedback[player_id].append(f"turn {self.turn}: {message}")

    def execute_command(self, cmd) -> Tuple[bool, str]:
        """Executes one command for the active player. Returns (accepted, message)."""
        player_id = self.current_player
        if not self.turn_started:
            return False, "no turn in progress"

        def reject(message: str) -> Tuple[bool, str]:
            self._reject(player_id, message)
            return False, message

        if not isinstance(cmd, dict):
            return reject(f"command {cmd!r} is not an object")
        if str(cmd.get("action", "")).upper() == "SPAWN":
            message = self._do_spawn(player_id, cmd)
            return (True, "spawn") if message is None else reject(f"SPAWN: {message}")
        drone_id = cmd.get("drone_id")
        drone = self.drones.get(drone_id) if is_int(drone_id) else None
        if drone is None or drone.player_id != player_id:
            return reject(f"drone {drone_id!r} is not one of your drones")
        if drone_id in self.acted:
            return reject(f"drone {drone_id} already acted this turn")
        if drone_id not in self.can_act:
            return reject(f"drone {drone_id} is inert and cannot act")
        self.acted.add(drone_id)

        action = str(cmd.get("action", "")).upper()
        if action == "WAIT":
            return True, "wait"
        if action == "MOVE":
            message = self._do_move(drone, cmd)
        elif action == "PUSH":
            message = self._do_push(drone, cmd)
        elif action == "INTERACT":
            message = self._do_interact(drone, cmd)
        elif action == "BUILD":
            message = self._do_build(drone, cmd)
        elif action == "SABOTAGE_ANCHOR":
            message = self._do_sabotage(drone, cmd)
        else:
            message = f"unknown action {cmd.get('action')!r}"
            return reject(f"drone {drone_id}: {message}")

        if message is None:
            return True, action.lower()
        return reject(f"drone {drone_id} {action}: {message}")

    # Each _do_* returns None on success or a short reason on failure.
    def spawn_link(self, x: int, y: int, player_id: int) -> Optional[Tuple[int, int]]:
        """Power source a new drone at (x, y) would draw from right now, if any."""
        link = self.mother_link(x, y, player_id)
        if link is not None:
            return link
        for anchor in self.anchors.values():
            if anchor.player_id == player_id and (x, y) in self.beam_cells(anchor):
                return (anchor.x, anchor.y)
        for d in self.drones.values():
            if d.player_id == player_id and d.is_powered and self.has_los(d.x, d.y, x, y, player_id):
                return (d.x, d.y)
        return None

    def _do_spawn(self, player_id: int, cmd: dict) -> Optional[str]:
        x, y = cmd.get("x"), cmd.get("y")
        if not is_int(x) or not is_int(y) or not self.in_bounds(x, y):
            return "x / y must be on the board"
        if self.reserve[player_id] <= 0:
            return "no drones left in reserve"
        if self.spawned_this_turn >= SPAWNS_PER_TURN:
            return f"only {SPAWNS_PER_TURN} spawns per turn"
        if not self.is_free(x, y):
            return f"cell ({x},{y}) is not empty"
        source = self.spawn_link(x, y, player_id)
        if source is None:
            return f"cell ({x},{y}) is not linked to your powered network"
        drone = self.add_drone(player_id, x, y)
        drone.is_powered, drone.power_source = True, source   # can relay later spawns this turn
        self.reserve[player_id] -= 1
        self.spawned_this_turn += 1
        self.acted.add(drone.drone_id)                        # it acts from next turn on
        self.events.append({"type": "spawn", "player": player_id, "drone": drone.drone_id,
                            "x": x, "y": y})
        return None

    def _do_move(self, drone: Drone, cmd: dict) -> Optional[str]:
        direction = parse_direction(cmd.get("direction"))
        if direction is None:
            return f"bad direction {cmd.get('direction')!r}"
        nx, ny = drone.x + direction.dx, drone.y + direction.dy
        if self.is_solid(nx, ny):
            return "blocked by wall / anchor / edge"
        if (nx, ny) in self.occupant:
            return "cell occupied by a drone"
        self._move_drone(drone, nx, ny)
        return None

    def _do_push(self, drone: Drone, cmd: dict) -> Optional[str]:
        direction = parse_direction(cmd.get("direction"))
        if direction is None:
            return f"bad direction {cmd.get('direction')!r}"
        tx, ty = drone.x + direction.dx, drone.y + direction.dy
        target = self.occupant.get((tx, ty))
        if target is None:
            return "nothing to push"
        if isinstance(target, Anchor):
            return "anchors cannot be pushed"
        dest_x, dest_y = tx + direction.dx, ty + direction.dy
        if self.is_solid(dest_x, dest_y):
            if target.is_inert or CRUSH_ACTIVE_DRONES:
                self._destroy_drone(target, "push", by_player=drone.player_id)
                return None
            return "target is active and the cell behind it is solid"
        if (dest_x, dest_y) in self.occupant:
            return "cell behind the target is occupied"
        self._move_drone(target, dest_x, dest_y)
        return None

    def _do_interact(self, drone: Drone, cmd: dict) -> Optional[str]:
        if self.terminal_at(drone.x, drone.y) is None:
            return "drone is not standing on a terminal"
        tx, ty = cmd.get("target_x"), cmd.get("target_y")
        if not is_int(tx) or not is_int(ty) or not self.in_bounds(tx, ty):
            return "target_x / target_y must be on the board"
        if (tx, ty) not in self.turn_vision:
            return "target tile is not inside your vision"
        if self.is_mother_row(ty) or (tx, ty) in self.walls:
            return "cannot place belts on walls or Mother-Node rows"
        if isinstance(self.occupant.get((tx, ty)), Anchor):
            return "cannot place belts under anchors"
        raw = cmd.get("belt_dir", "NONE")
        if raw is None or (isinstance(raw, str) and raw.upper() == "NONE"):
            self.belts.pop((tx, ty), None)
            self.terminal_belts.pop((tx, ty), None)
            new_dir = "NONE"
        else:
            direction = parse_direction(raw)
            if direction is None:
                return f"bad belt_dir {raw!r}"
            self.belts[(tx, ty)] = direction
            self.terminal_belts[(tx, ty)] = 2
            new_dir = direction.name
        self.stats[drone.player_id]["hacks"] += 1
        self.events.append({"type": "hack", "player": drone.player_id, "drone": drone.drone_id,
                            "x": tx, "y": ty, "dir": new_dir})
        return None

    def _do_build(self, drone: Drone, cmd: dict) -> Optional[str]:
        direction = parse_direction(cmd.get("direction"))
        if direction is None:
            return f"bad beam direction {cmd.get('direction')!r}"
        partner_ids = cmd.get("partners", cmd.get("sacrifice_ids"))
        if not isinstance(partner_ids, list) or len(partner_ids) != 2 \
                or not all(is_int(p) for p in partner_ids) \
                or len(set(partner_ids)) != 2 or drone.drone_id in partner_ids:
            return "partners must list 2 other drone ids"
        partners = []
        for pid in partner_ids:
            partner = self.drones.get(pid)
            if partner is None or partner.player_id != drone.player_id:
                return f"partner {pid!r} is not your drone"
            if pid not in self.can_act:
                return f"partner {pid} is inert"
            if pid in self.acted:
                return f"partner {pid} already acted this turn"
            if chebyshev(partner.x, partner.y, drone.x, drone.y) > 5:
                return f"partner {pid} is more than 5 cells away from the builder"
            partners.append(partner)
        # Collinearity check: the two partners and the builder drone must lie on the same
        # orthogonal or diagonal line through the builder.  That is, the unit direction
        # vectors from drone → partner[0] and drone → partner[1] must be exact opposites.
        def unit_dir(ax, ay, bx, by):
            dx, dy = bx - ax, by - ay
            if dx == 0 and dy == 0:
                return (0, 0)
            g = abs(dx) if dy == 0 else (abs(dy) if dx == 0 else min(abs(dx), abs(dy)))
            # Only orthogonal / diagonal lines are valid (|dx|==|dy| or one is 0)
            if dx != 0 and dy != 0 and abs(dx) != abs(dy):
                return None  # not on a clean diagonal
            return (dx // g, dy // g)
        d1, d2 = partners
        v1 = unit_dir(drone.x, drone.y, d1.x, d1.y)
        v2 = unit_dir(drone.x, drone.y, d2.x, d2.y)
        if v1 is None or v2 is None:
            return "partners must be collinear with the builder on an orthogonal or diagonal line"
        if v1 != (-v2[0], -v2[1]):
            return "partners must be on opposite sides of the builder along the same line"
        if self.terminal_at(drone.x, drone.y) is not None:
            return "cannot build an anchor on a terminal"

        x, y, player_id = drone.x, drone.y, drone.player_id
        for unit in [drone] + partners:
            self.acted.add(unit.drone_id)
            self._remove_drone(unit)
        anchor = self.add_anchor(player_id, x, y, direction)
        self.stats[player_id]["anchors_built"] += 1
        self.events.append({"type": "build", "player": player_id, "anchor": anchor.anchor_id,
                            "x": x, "y": y, "dir": direction.name,
                            "sacrificed": [drone.drone_id] + partner_ids})
        return None

    def _do_sabotage(self, drone: Drone, cmd: dict) -> Optional[str]:
        anchor_id = cmd.get("anchor_id")
        anchor = self.anchors.get(anchor_id) if is_int(anchor_id) else None
        if anchor is None or anchor.player_id == drone.player_id:
            return f"anchor {anchor_id!r} is not an enemy anchor"
        if chebyshev(drone.x, drone.y, anchor.x, anchor.y) > SABOTAGE_RADIUS:
            return "drone is outside the anchor's 5x5 defense region"
        self._remove_drone(drone)
        self.stats[drone.player_id]["drones_lost"] += 1
        anchor.hp -= 1
        self.events.append({"type": "sabotage", "player": drone.player_id, "drone": drone.drone_id,
                            "anchor": anchor.anchor_id, "hp_left": anchor.hp})
        if anchor.hp <= 0:
            del self.occupant[(anchor.x, anchor.y)]
            del self.anchors[anchor.anchor_id]
            self.stats[drone.player_id]["anchors_destroyed"] += 1
            self.events.append({"type": "anchor_destroyed", "anchor": anchor.anchor_id,
                                "player": anchor.player_id, "x": anchor.x, "y": anchor.y})
        return None

    # -----------------------------------------------------------------------
    # Conveyor belts
    # -----------------------------------------------------------------------
    def _belt_phase(self):
        """
        Every drone on the board standing on a belt slides 1 cell.
        All slides are simultaneous: a drone may follow one that moves out of
        the way, two drones sliding into the same cell both stay put, and an
        INERT drone dragged into a solid cell is crushed (active ones just stop).
        """
        pending = {d.drone_id: self.belts[(d.x, d.y)] for d in self.drones.values()
                   if (d.x, d.y) in self.belts}
        while pending:
            progress = False
            wanted: Dict[Tuple[int, int], List[int]] = {}
            for drone_id, direction in pending.items():
                drone = self.drones[drone_id]
                wanted.setdefault((drone.x + direction.dx, drone.y + direction.dy), []).append(drone_id)
            for cell, drone_ids in wanted.items():
                if len(drone_ids) > 1:                     # head-on contest: nobody moves
                    for drone_id in drone_ids:
                        del pending[drone_id]
                    progress = True
                    continue
                drone = self.drones[drone_ids[0]]
                if self.is_solid(*cell):
                    if drone.is_inert:
                        self._destroy_drone(drone, "belt")
                    del pending[drone.drone_id]
                    progress = True
                elif cell in self.occupant:
                    blocker = self.occupant[cell]
                    if isinstance(blocker, Drone) and blocker.drone_id in pending:
                        continue                           # wait: it may slide away first
                    del pending[drone.drone_id]
                    progress = True
                else:
                    self._move_drone(drone, *cell)
                    del pending[drone.drone_id]
                    progress = True
            if not progress:                               # a closed loop of drones: freeze
                break

    # -----------------------------------------------------------------------
    # Scoring and world timers
    # -----------------------------------------------------------------------
    def _score(self, player_id: int):
        gained = 0
        for storm in self.storms:
            count = sum(1 for d in self.player_drones(player_id)
                        if d.is_active and storm.contains(d.x, d.y))
            gained += count * storm.multiplier
        self.scores[player_id] += gained

    def _tick_world(self):
        # Terminals (3 mirrored pairs)
        for i in range(0, len(self.terminals), 2):
            pair = self.terminals[i:i + 2]
            for t in pair:
                t.timer -= 1
            if pair[0].timer <= 0:
                self._relocate_terminal_pair(i)

        # Storms (4 mirrored pairs)
        for i in range(0, len(self.storms), 2):
            pair = self.storms[i:i + 2]
            for s in pair:
                s.timer -= 1
            if pair[0].timer <= 0:
                self._respawn_storm_pair(i)

        # Belt stripes (5 mirrored pairs, 10 stripes)
        for stripe in self.belt_stripes:
            stripe.timer -= 1
            if stripe.timer <= 0:
                self._relocate_belt_stripe_pair(stripe.pair_id)

        # Terminal-created single-length belts stay for 2 turns and then disappear
        expired_terminal = []
        for cell, turns in list(self.terminal_belts.items()):
            if turns <= 1:
                expired_terminal.append(cell)
            else:
                self.terminal_belts[cell] = turns - 1
        if expired_terminal:
            stripe_cells = {c for s in self.belt_stripes for c in s.cells + s.mirror_cells}
            for cell in expired_terminal:
                del self.terminal_belts[cell]
                if cell not in stripe_cells and cell not in self.drone_belt_cells:
                    self.belts.pop(cell, None)

        # 1-cell conveyor belt under each player's drone every 6 turns (reallocated, old ones disappear)
        self.belt_drone_timer -= 1
        if self.belt_drone_timer <= 0:
            self.belt_drone_timer = BELT_DRONE_INTERVAL
            stripe_cells = {c for s in self.belt_stripes for c in s.cells + s.mirror_cells}
            for cell in self.drone_belt_cells:
                if cell not in stripe_cells and cell not in self.terminal_belts:
                    self.belts.pop(cell, None)
            self.drone_belt_cells = []

            for p in (1, 2):
                p_drones = [d for d in self.player_drones(p)
                            if d.is_active and not self.is_mother_row(d.y)
                            and self.terminal_at(d.x, d.y) is None]
                if p_drones:
                    chosen = self.rng.choice(p_drones)
                    bdir = self.rng.choice((Direction.UP, Direction.DOWN, Direction.LEFT, Direction.RIGHT))
                    self.belts[(chosen.x, chosen.y)] = bdir
                    self.drone_belt_cells.append((chosen.x, chosen.y))

    # -----------------------------------------------------------------------
    # Map generation (everything is placed in mirrored pairs for fairness)
    # -----------------------------------------------------------------------
    def _generate_map(self):
        for _ in range(200):
            walls = set()
            for _ in range(WALL_SEGMENT_PAIRS):
                for cell in self._random_wall_segment():
                    walls.add(cell)
                    walls.add(mirror(*cell))
            if self._is_connected(walls):
                break
        self.walls = walls
        self._place_belt_stripes()
        for i in range(TERMINAL_PAIRS):
            self.terminals += [Terminal(2 * i, 0, 0, 0), Terminal(2 * i + 1, 0, 0, 0)]
            self._relocate_terminal_pair(2 * i, announce=False)
        for i in range(STORM_PAIRS):
            self.storms += [AnimaStorm(0, 0, 0, 1, 0), AnimaStorm(0, 0, 0, 1, 0)]
            self._respawn_storm_pair(2 * i, announce=False)

    def _random_wall_segment(self) -> List[Tuple[int, int]]:
        rng = self.rng
        length = rng.randint(2, 6)
        x0 = rng.randint(0, GRID_SIZE - 1)
        y0 = rng.randint(5, GRID_SIZE // 2 - 1)
        horizontal = rng.random() < 0.5
        cells = [(x0 + i, y0) if horizontal else (x0, y0 + i) for i in range(length)]
        if rng.random() < 0.3:                             # L-shaped corner piece
            ex, ey = cells[-1]
            arm = rng.randint(1, 3) * rng.choice((-1, 1))
            step = 1 if arm > 0 else -1
            cells += [(ex, ey + k) if horizontal else (ex + k, ey)
                      for k in range(step, arm + step, step)]
        return [(x, y) for (x, y) in cells if 0 <= x < GRID_SIZE and 4 <= y <= GRID_SIZE - 5
                and not WALL_FREE_ROWS[0] <= y <= WALL_FREE_ROWS[1]]

    @staticmethod
    def _is_connected(walls: set) -> bool:
        """All playable (non-wall, non-Mother-Node) cells must be reachable."""
        open_cells = {(x, y) for y in range(1, GRID_SIZE - 1) for x in range(GRID_SIZE)} - walls
        start = next(iter(open_cells))
        seen = {start}
        queue = deque([start])
        while queue:
            x, y = queue.popleft()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = (x + dx, y + dy)
                if nxt in open_cells and nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return len(seen) == len(open_cells)

    def _place_belt_stripes(self):
        """Places 5 pairs (10 stripes); H and V counts differ by 1 pair (3H/2V or 2H/3V)."""
        h_count = 3 if self.rng.random() < 0.5 else 2
        v_count = BELT_STRIPE_PAIRS - h_count
        orientations = ['H'] * h_count + ['V'] * v_count
        self.rng.shuffle(orientations)
        self.belt_stripes = []
        for i in range(BELT_STRIPE_PAIRS):
            stripe = BeltStripePair(i, orientations[i])
            self.belt_stripes.append(stripe)
            self._relocate_belt_stripe_pair(i, announce=False)

    def _relocate_belt_stripe_pair(self, index: int, announce: bool = True):
        """Relocates belt_stripes[index] to fresh symmetric non-overlapping cells in rows 10..39."""
        stripe = self.belt_stripes[index]
        # Remove old cells of this stripe
        for cell in stripe.cells:
            self.belts.pop(cell, None)
        for cell in stripe.mirror_cells:
            self.belts.pop(cell, None)
        stripe.cells = []
        stripe.mirror_cells = []

        length = self.rng.randint(*BELT_STRIPE_LENGTH)
        SECTORS = [
            (1, 8, 10, 24),
            (10, 18, 10, 24),
            (20, 28, 10, 24),
            (30, 38, 10, 24),
            (40, 47, 10, 24),
        ]
        sx_min, sx_max, sy_min, sy_max = SECTORS[index % 5]
        other_stripe_cells = {c for j, s in enumerate(self.belt_stripes) if j != index
                              for c in s.cells + s.mirror_cells}
        terminal_cells = {(t.x, t.y) for t in self.terminals}

        chosen_cells, chosen_mcells, chosen_dir = None, None, None

        for attempt in range(1000):
            if stripe.orientation == 'H':
                if attempt < 500:
                    x0 = self.rng.randint(max(0, sx_min), min(GRID_SIZE - length, sx_max))
                    y0 = self.rng.randint(sy_min, sy_max)
                else:
                    x0 = self.rng.randint(0, GRID_SIZE - length)
                    y0 = self.rng.randint(10, 24)
                cells = [(x0 + k, y0) for k in range(length)]
                direction = self.rng.choice((Direction.LEFT, Direction.RIGHT))
            else:
                max_y0 = max(10, 24 - length + 1)
                if attempt < 500:
                    x0 = self.rng.randint(max(0, sx_min), min(GRID_SIZE - 1, sx_max))
                    y0 = self.rng.randint(sy_min, min(sy_max, max_y0))
                else:
                    x0 = self.rng.randint(0, GRID_SIZE - 1)
                    y0 = self.rng.randint(10, max_y0)
                cells = [(x0, y0 + k) for k in range(length)]
                direction = self.rng.choice((Direction.UP, Direction.DOWN))

            mcells = [mirror(x, y) for (x, y) in cells]
            all_cells = cells + mcells

            if set(cells) & set(mcells):
                continue
            if any(c in self.walls or c in other_stripe_cells or c in terminal_cells
                   or self.is_mother_row(c[1]) or not (10 <= c[1] <= 39)
                   for c in all_cells):
                continue

            chosen_cells = cells
            chosen_mcells = mcells
            chosen_dir = direction
            break

        if chosen_cells:
            stripe.cells = chosen_cells
            stripe.mirror_cells = chosen_mcells
            stripe.timer = self.rng.randint(*BELT_STRIPE_LIFESPAN)
            mdir = chosen_dir.mirrored()
            for c in chosen_cells:
                self.belts[c] = chosen_dir
            for c in chosen_mcells:
                self.belts[c] = mdir
            if announce:
                self.events.append({"type": "belts_relocated", "pair": index})

    def _relocate_terminal_pair(self, index: int, announce: bool = True):
        """Relocates terminals[index:index+2] using the same sector and symmetry structure as anima storms."""
        others = [t for j, t in enumerate(self.terminals) if j not in (index, index + 1) and (t.x, t.y) != (0, 0)]
        pair_idx = (index // 2) % 3
        # 3 sectors across columns (left, center, right); rows 11..23 so mirrors (26..38) avoid rows 0..9 and 40..49
        SECTORS = [
            (2, 14, 11, 23),    # Pair 0: left (mirror: right)
            (18, 31, 11, 23),   # Pair 1: center (mirror: center)
            (35, 47, 11, 23),   # Pair 2: right (mirror: left)
        ]
        sx_min, sx_max, sy_min, sy_max = SECTORS[pair_idx]

        for attempt in range(1000):
            if attempt < 500:
                x = self.rng.randint(sx_min, sx_max)
                y = self.rng.randint(sy_min, sy_max)
            else:
                x = self.rng.randint(1, GRID_SIZE - 2)
                y = self.rng.randint(10, GRID_SIZE // 2 - 2)
            mx, my = mirror(x, y)
            if abs(x - mx) <= 1 and abs(y - my) <= 1:      # pair would overlap itself
                continue
            limit = 3 if attempt < 300 else 2
            if any(abs(cx - t.x) <= limit and abs(cy - t.y) <= limit
                   for t in others for cx, cy in ((x, y), (mx, my))):
                continue
            if any((cx, cy) in self.walls or (cx, cy) in self.belts
                   or (cx, cy) in self.occupant
                   for (cx, cy) in ((x, y), (mx, my))):
                continue
            break
        life = self.rng.randint(*TERMINAL_LIFESPAN)
        pair = self.terminals[index:index + 2]
        for terminal, (tx, ty) in zip(pair, ((x, y), (mx, my))):
            terminal.x, terminal.y, terminal.timer = tx, ty, life
        if announce:
            self.events.append({"type": "terminals_moved", "ids": [t.terminal_id for t in pair]})

    def _respawn_storm_pair(self, index: int, announce: bool = True):
        others = [s for j, s in enumerate(self.storms) if j not in (index, index + 1) and (s.x, s.y) != (0, 0)]
        pair_idx = (index // 2) % 4
        # Uniform distribution sectors across columns (extreme left, mid left, center, extreme right)
        # and rows (center y in 11..23 so all storm cells remain strictly in rows 10..39, never 0..9 or 40..49).
        SECTORS = [
            (1, 10, 11, 17),    # Pair 0: extreme left (mirror: extreme right)
            (13, 22, 14, 21),   # Pair 1: mid left (mirror: mid right)
            (22, 27, 19, 23),   # Pair 2: center (mirror: center)
            (39, 48, 11, 18),   # Pair 3: extreme right (mirror: extreme left)
        ]
        sx_min, sx_max, sy_min, sy_max = SECTORS[pair_idx]

        for attempt in range(1000):
            if attempt < 500:
                x = self.rng.randint(sx_min, sx_max)
                y = self.rng.randint(sy_min, sy_max)
            else:
                x = self.rng.randint(1, GRID_SIZE - 2)
                y = self.rng.randint(STORM_ROWS[0] + 1, GRID_SIZE // 2 - 2)
            mx, my = mirror(x, y)
            if abs(x - mx) <= 2 and abs(y - my) <= 2:      # the pair would overlap itself
                continue
            limit = 3 if attempt < 300 else 2
            if any(abs(cx - s.x) <= limit and abs(cy - s.y) <= limit
                   for s in others for cx, cy in ((x, y), (mx, my))):
                continue
            if any((cx + dx, cy + dy) in self.walls
                   for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                   for (cx, cy) in ((x, y), (mx, my))):
                continue
            break
        life = self.rng.randint(*STORM_LIFESPAN)
        multiplier = storm_multiplier(y)
        for storm, (sx, sy) in zip(self.storms[index:index + 2], ((x, y), (mx, my))):
            storm.storm_id = self.next_storm_id
            self.next_storm_id += 1
            storm.x, storm.y, storm.multiplier, storm.timer = sx, sy, multiplier, life
        if announce:
            self.events.append({"type": "storms_moved"})

    def _spawn_starting_drones(self):
        """STARTING_DRONES per player: P1 on row 1 at even columns, P2 the exact mirror on row 48."""
        for i in range(min(STARTING_DRONES, DRONES_PER_PLAYER)):
            self.add_drone(1, 2 * i, 1)
            self.add_drone(2, *mirror(2 * i, 1))
            self.reserve[1] -= 1
            self.reserve[2] -= 1

    # -----------------------------------------------------------------------
    # Results & replays
    # -----------------------------------------------------------------------
    def result(self) -> dict:
        """Winner by score, then surviving drones, then anchors; otherwise a draw."""
        def key(p):
            return (self.scores[p], len(self.player_drones(p)),
                    sum(1 for a in self.anchors.values() if a.player_id == p))
        k1, k2 = key(1), key(2)
        winner = 1 if k1 > k2 else 2 if k2 > k1 else 0
        return {
            "winner": winner,
            "scores": {"1": self.scores[1], "2": self.scores[2]},
            "drones_left": {"1": k1[1], "2": k2[1]},
            "anchors": {"1": k1[2], "2": k2[2]},
            "stats": {"1": self.stats[1], "2": self.stats[2]},
            "turns_played": self.turn,
        }

    def _frame(self) -> dict:
        return {
            "turn": self.turn,
            "player": self.current_player if self.turn_started else 0,
            "scores": [self.scores[1], self.scores[2]],
            "reserve": [self.reserve[1], self.reserve[2]],
            # [id, player, x, y, battery, powered, source_x, source_y]
            "drones": [[d.drone_id, d.player_id, d.x, d.y, d.battery, int(d.is_powered),
                        *(d.power_source or (-1, -1))]
                       for d in sorted(self.drones.values(), key=lambda d: d.drone_id)],
            "anchors": [[a.anchor_id, a.player_id, a.x, a.y, a.direction.name[0], a.hp]
                        for a in self.anchors.values()],
            "terminals": [[t.terminal_id, t.x, t.y, t.timer] for t in self.terminals],
            "storms": [[s.storm_id, s.x, s.y, s.multiplier, s.timer] for s in self.storms],
            "events": list(self.events),
        }

    def _belt_list(self) -> list:
        return [[x, y, d.name[0]] for (x, y), d in sorted(self.belts.items())]

    def record_frame(self):
        """Compact snapshot of the board after a turn (frame 0 = starting position)."""
        frame = self._frame()
        belts = self._belt_list()
        if belts != self._last_belts:                      # belts only stored when they change
            frame["belts"] = belts
            self._last_belts = belts
        self.frames.append(frame)

    def replay(self, meta: Optional[dict] = None) -> dict:
        return {
            "format": "tethered-grid-replay-v1",
            "grid_size": GRID_SIZE,
            "seed": self.seed,
            "max_turns": self.max_turns,
            "walls": [[x, y] for (x, y) in sorted(self.walls)],
            "meta": meta or {},
            "result": self.result(),
            "frames": self.frames,
        }

    def snapshot(self) -> dict:
        """Full current state (used by the in-browser sandbox)."""
        frame = self._frame()
        frame["belts"] = self._belt_list()
        frame["walls"] = [[x, y] for (x, y) in sorted(self.walls)]
        frame["player"] = self.current_player
        frame["turn_started"] = self.turn_started
        frame["acted"] = sorted(self.acted)
        frame["can_act"] = sorted(self.can_act)
        return frame
