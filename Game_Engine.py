import random
import os
import sys
import subprocess
import json
from enum import Enum
from typing import List, Dict, Optional, Tuple

# Constants
GRID_SIZE = 50
MAX_DRONE_BATTERY = 3
TERMINAL_LIFESPAN = 50

class Direction(Enum):
    UP = (0, -1)
    DOWN = (0, 1)
    LEFT = (-1, 0)
    RIGHT = (1, 0)
    NONE = (0, 0)

class Drone:
    def __init__(self, drone_id: int, player_id: int, x: int, y: int):
        self.drone_id = drone_id
        self.player_id = player_id
        self.x = x
        self.y = y
        self.battery = MAX_DRONE_BATTERY
        self.is_powered = True  # True if connected to grid
        self.is_inert = False   # True if battery reaches 0
    
    def tick_battery(self):
        if not self.is_powered and not self.is_inert:
            self.battery -= 1
            if self.battery <= 0:
                self.is_inert = True
    
    def recharge(self):
        if self.is_powered:
            self.is_inert = False
            self.battery = min(MAX_DRONE_BATTERY, self.battery + 1)

class Anchor:
    def __init__(self, anchor_id: int, player_id: int, x: int, y: int, direction: Direction):
        self.anchor_id = anchor_id
        self.player_id = player_id
        self.x = x
        self.y = y
        self.direction = direction # The single orthogonal direction it beams power
        self.hp = 3

class Terminal:
    def __init__(self, terminal_id: int, x: int, y: int):
        self.terminal_id = terminal_id
        self.x = x
        self.y = y
        self.timer = TERMINAL_LIFESPAN

class AnimaStorm:
    def __init__(self, x: int, y: int, multiplier: int):
        self.x = x  # Top-left corner of the 3x3 storm
        self.y = y
        self.multiplier = multiplier

class GameState:
    def __init__(self):
        self.turn = 0
        self.round = 0
        self.current_player = 1
        
        # Player 1 Mother-Node is row 0. Player 2 Mother-Node is row 49 (GRID_SIZE - 1).
        self.scores = {1: 0, 2: 0}
        
        # Entity Storage
        self.drones: Dict[int, Drone] = {}
        self.anchors: Dict[int, Anchor] = {}
        self.terminals: List[Terminal] = []
        
        # 50x50 Grid matrices to store environmental data
        # 0 = Empty, 1 = Normal Wall (#)
        self.walls_grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        
        # Stores Direction enums for conveyor belts. NONE = no belt.
        self.conveyor_grid = [[None for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        self.storms = self._place_storms()

        # Replay Data Storage
        self.replay_data = {
            "grid_size": GRID_SIZE,
            "walls": [],
            "frames": []
        }

    def _place_storms(self, count: int = 10) -> list:
        """
        Generates 'count' non-overlapping AnimaStorms spread across the grid interior.

        Constraints (on 50x50 grid):
          - Storm center y in [11, 38] so the 3x3 footprint (center ± 1) never
            touches rows 0-9 (P1 Mother-Node area) or rows 40-49 (P2 Mother-Node area).
          - Storm center x in [2, 47] so the 3x3 footprint stays inside the grid.
          - Two storms are considered overlapping when BOTH |dx| < 3 AND |dy| < 3
            (i.e. their 3x3 areas share at least one cell). Placement rejects these.
          - The valid area is split into a 5-column × 2-row sub-grid (10 cells) so
            one storm is seeded per region, preventing clustering at the centre.
        """
        storms = []
        x_min, x_max = 2, 47   # keeps 3x3 footprint inside columns 1..48
        y_min, y_max = 11, 38  # keeps 3x3 footprint inside rows 10..39

        # ── Phase 1: one candidate per sub-region for even distribution ──────
        cols_n, rows_n = 5, 2
        x_step = (x_max - x_min + 1) // cols_n   # ≈9
        y_step = (y_max - y_min + 1) // rows_n   # ≈14

        candidates = []
        for row in range(rows_n):
            for col in range(cols_n):
                rx_min = x_min + col * x_step
                rx_max = min(x_max, rx_min + x_step - 1)
                ry_min = y_min + row * y_step
                ry_max = min(y_max, ry_min + y_step - 1)
                candidates.append((random.randint(rx_min, rx_max),
                                   random.randint(ry_min, ry_max)))

        random.shuffle(candidates)
        for cx, cy in candidates:
            if len(storms) >= count:
                break
            if any(abs(cx - s.x) < 3 and abs(cy - s.y) < 3 for s in storms):
                continue  # overlaps an existing storm
            storms.append(AnimaStorm(x=cx, y=cy, multiplier=random.randint(1, 3)))

        # ── Phase 2: rejection-sample any remaining slots ─────────────────────
        attempts = 0
        while len(storms) < count and attempts < 2000:
            attempts += 1
            cx = random.randint(x_min, x_max)
            cy = random.randint(y_min, y_max)
            if not any(abs(cx - s.x) < 3 and abs(cy - s.y) < 3 for s in storms):
                storms.append(AnimaStorm(x=cx, y=cy, multiplier=random.randint(1, 3)))

        return storms

    def get_entity_at(self, x: int, y: int):
        # Helper to check if a tile is occupied by a Drone or Anchor
        for drone in self.drones.values():
            if drone.x == x and drone.y == y: return drone
        for anchor in self.anchors.values():
            if anchor.x == x and anchor.y == y: return anchor
        return None

    def is_cell_blocked_for_los(self, x: int, y: int, player_id: int) -> bool:
        """Returns True if a cell contains a Wall or an ENEMY unit (active or inert)."""
        if self.walls_grid[y][x] == 1:
            return True
        
        entity = self.get_entity_at(x, y)
        if entity is not None and entity.player_id != player_id:
            return True
            
        return False

    def has_los(self, x1: int, y1: int, x2: int, y2: int, player_id: int) -> bool:
        """Calculates if there is a clear orthogonal/diagonal path between two points (Max 5 cells)."""
        # Calculate Chebyshev distance (max of x-diff or y-diff)
        dist = max(abs(x1 - x2), abs(y1 - y2))
        
        # Rule check: Must be within 5 cells, and cannot be the exact same cell
        if dist > 5 or dist == 0:
            return False
            
        # Rule check: Must be a perfectly straight line (Orthogonal or Diagonal)
        dx = x2 - x1
        dy = y2 - y1
        if dx != 0 and dy != 0 and abs(dx) != abs(dy):
            return False 
            
        # Determine the step direction (-1, 0, or 1) for the raycast
        step_x = 0 if dx == 0 else (1 if dx > 0 else -1)
        step_y = 0 if dy == 0 else (1 if dy > 0 else -1)
        
        # Raycast: Step through each cell between point 1 and point 2
        cx, cy = x1 + step_x, y1 + step_y
        while (cx, cy) != (x2, y2):
            if self.is_cell_blocked_for_los(cx, cy, player_id):
                return False
            cx += step_x
            cy += step_y
            
        return True

    def check_mother_node_connection(self, drone: Drone) -> bool:
        """Checks if a Drone has direct LoS to its player's 1-row Mother-Node."""
        target_rows = [0] if drone.player_id == 1 else [GRID_SIZE - 1]
        
        for target_y in target_rows:
            for target_x in range(drone.x - 5, drone.x + 6):
                if 0 <= target_x < GRID_SIZE:
                    if self.has_los(drone.x, drone.y, target_x, target_y, drone.player_id):
                        return True
        return False

    def check_anchor_connection(self, drone: Drone) -> bool:
        """Checks if a Drone is powered by any friendly Anchor's directional beam."""
        for anchor in self.anchors.values():
            if anchor.player_id == drone.player_id:
                # Anchors only shoot a beam in ONE orthogonal direction
                dist = max(abs(drone.x - anchor.x), abs(drone.y - anchor.y))
                
                if 0 < dist <= 5:
                    # Calculate where the Drone should be if it's perfectly in the beam
                    expected_x = anchor.x + (anchor.direction.value[0] * dist)
                    expected_y = anchor.y + (anchor.direction.value[1] * dist)
                    
                    if drone.x == expected_x and drone.y == expected_y:
                        # It is in the beam! Now check if the beam is obstructed.
                        if self.has_los(anchor.x, anchor.y, drone.x, drone.y, drone.player_id):
                            return True
        return False

    def resolve_power_network(self, update_batteries: bool = True):
        """
        The Engine Heartbeat for Power:
        Recalculates the entire tether grid using Breadth-First Search (BFS).
        If update_batteries is False, only updates is_powered status without draining/recharging batteries.
        """
        powered_drone_ids = set()
        
        # STEP 1: Find all Drones receiving direct "Root" power (Mother-Node or Anchor)
        for drone in self.drones.values():
            if self.check_mother_node_connection(drone) or self.check_anchor_connection(drone):
                powered_drone_ids.add(drone.drone_id)
                
        # STEP 2: Propagate power down the line (The Spider Web)
        # Using a list as a queue to pass power from connected drones to disconnected ones
        queue = list(powered_drone_ids)
        
        while queue:
            current_id = queue.pop(0)
            current_drone = self.drones[current_id]
            
            # Check all other friendly drones to see if this powered drone can plug them in
            for other_drone in self.drones.values():
                if other_drone.player_id == current_drone.player_id and other_drone.drone_id not in powered_drone_ids:
                    if self.has_los(current_drone.x, current_drone.y, other_drone.x, other_drone.y, current_drone.player_id):
                        powered_drone_ids.add(other_drone.drone_id)
                        queue.append(other_drone.drone_id)
                        
        # STEP 3: Apply the results to the Batteries
        for drone in self.drones.values():
            if drone.drone_id in powered_drone_ids:
                drone.is_powered = True
                if update_batteries:
                    drone.recharge() # Tops off the battery up to 3 and removes inert status
                else:
                    drone.is_inert = False
            else:
                drone.is_powered = False
                if update_batteries:
                    drone.tick_battery() # Subtracts 1 from battery. If 0, becomes inert.

    def is_valid_cell(self, x: int, y: int) -> bool:
        """Helper to check if coordinates are within the 50x50 grid."""
        return 0 <= x < GRID_SIZE and 0 <= y < GRID_SIZE

    def process_player_actions(self, player_id: int, commands: List[Dict]):
        """
        Executes a player's array of commands strictly in order.
        Example cmd: {"drone_id": 5, "action": "PUSH", "direction": Direction.UP}
        """
        # FIX: Track which drones have already acted this turn to prevent multi-action cheating
        processed_drones = set()
        
        for cmd in commands:
            drone_id = cmd.get("drone_id")
            
            # Enforce strict limit: 1 action per drone per turn
            if drone_id in processed_drones:
                continue
                
            drone = self.drones.get(drone_id)
            
            # Validation: Drone must exist, belong to player, and NOT be inert.
            if not drone or drone.player_id != player_id or drone.is_inert:
                continue
                
            # Mark this drone as having used its action for this turn
            processed_drones.add(drone_id)
            
            action = cmd.get("action")
            
            # ---------------------------------------------------------
            # 1. MOVE
            # ---------------------------------------------------------
            if action == "MOVE":
                d_dir = Direction[cmd.get("direction")]
                nx, ny = drone.x + d_dir.value[0], drone.y + d_dir.value[1]
                
                # Check bounds, walls, and check if cell is occupied by ANY entity
                if self.is_valid_cell(nx, ny) and self.walls_grid[ny][nx] == 0:
                    # Mother-Node rows are impassable (y=0 for P1, y=49 for P2)
                    if ny not in [0, GRID_SIZE - 1]: 
                        if self.get_entity_at(nx, ny) is None:
                            drone.x, drone.y = nx, ny

            # ---------------------------------------------------------
            # 2. PUSH
            # ---------------------------------------------------------
            elif action == "PUSH":
                d_dir = Direction[cmd.get("direction")]
                tx, ty = drone.x + d_dir.value[0], drone.y + d_dir.value[1]
                target_entity = self.get_entity_at(tx, ty)
                
                # We can only push Drones (Anchors are immune)
                if isinstance(target_entity, Drone):
                    push_x, push_y = target_entity.x + d_dir.value[0], target_entity.y + d_dir.value[1]
                    
                    # 1. Did we push them off the edge of the map completely?
                    if not self.is_valid_cell(push_x, push_y):
                        del self.drones[target_entity.drone_id]
                    else:
                        # 2. Are they being crushed into a wall or a Mother-Node row?
                        if self.walls_grid[push_y][push_x] == 1 or push_y in [0, GRID_SIZE - 1]:
                            del self.drones[target_entity.drone_id]  # BOOM. No inert check needed.
                        else:
                            # 3. Space is safe and empty, just slide them over.
                            if self.get_entity_at(push_x, push_y) is None:
                                target_entity.x, target_entity.y = push_x, push_y

            # ---------------------------------------------------------
            # 3. INTERACT (Hacking)
            # ---------------------------------------------------------
            elif action == "INTERACT":
                on_terminal = any(t.x == drone.x and t.y == drone.y for t in self.terminals)
                if on_terminal:
                    target_x, target_y = cmd.get("target_x"), cmd.get("target_y")
                    new_belt_dir = cmd.get("belt_dir") 
                    if new_belt_dir == "NONE": 
                        new_belt_dir = None

                    if self.is_valid_cell(target_x, target_y):
                        # Cannot place belts on Mother-Nodes, Walls, or under Anchors
                        if target_y not in [0, GRID_SIZE - 1] and self.walls_grid[target_y][target_x] == 0:
                            if not isinstance(self.get_entity_at(target_x, target_y), Anchor):
                                self.conveyor_grid[target_y][target_x] = new_belt_dir

            # ---------------------------------------------------------
            # 4. BUILD (Anchor)
            # ---------------------------------------------------------
            elif action == "BUILD":
                # Engine needs the IDs of the 2 other adjacent drones being sacrificed
                sacrifices = cmd.get("sacrifice_ids", [])
                facing_dir = Direction[cmd.get("direction")]
                
                if len(sacrifices) == 2:
                    d1 = self.drones.get(sacrifices[0])
                    d2 = self.drones.get(sacrifices[1])
                    
                    if d1 and d2 and d1.is_powered and d2.is_powered:
                        # Ensure both sacrifices belong to the player and are adjacent to the builder
                        d1_adj = max(abs(d1.x - drone.x), abs(d1.y - drone.y)) == 1
                        d2_adj = max(abs(d2.x - drone.x), abs(d2.y - drone.y)) == 1
                        
                        if d1_adj and d2_adj and d1.player_id == player_id and d2.player_id == player_id:
                            # Construct the Anchor!
                            new_anchor = Anchor(
                                anchor_id=len(self.anchors) + 1,
                                player_id=player_id, x=drone.x, y=drone.y, direction=facing_dir
                            )
                            self.anchors[new_anchor.anchor_id] = new_anchor
                            # Remove all 3 drones
                            self.drones.pop(drone.drone_id, None)
                            self.drones.pop(d1.drone_id, None)
                            self.drones.pop(d2.drone_id, None)

            # ---------------------------------------------------------
            # 5. SABOTAGE_ANCHOR
            # ---------------------------------------------------------
            elif action == "SABOTAGE_ANCHOR":
                target_anchor = self.anchors.get(cmd.get("anchor_id"))
                if target_anchor and target_anchor.player_id != player_id:
                    # Check if Drone is inside the 5x5 defense region (Chebyshev dist <= 2)
                    dist = max(abs(drone.x - target_anchor.x), abs(drone.y - target_anchor.y))
                    if dist <= 2:
                        target_anchor.hp -= 1
                        del self.drones[drone.drone_id] # Sacrifice the drone
                        
                        if target_anchor.hp <= 0:
                            del self.anchors[target_anchor.anchor_id]

    def get_random_empty_cell(self, y_min=None, y_max=None) -> Tuple[int, int]:
        """Finds a random valid tile strictly within given row bounds."""
        min_y = y_min if y_min is not None else int(GRID_SIZE * 0.20)
        max_y = y_max if y_max is not None else int(GRID_SIZE * 0.80)
        
        while True:
            x = random.randint(0, GRID_SIZE - 1)
            y = random.randint(min_y, max_y)
            
            if self.walls_grid[y][x] == 0 and self.get_entity_at(x, y) is None:
                if not any(t.x == x and t.y == y for t in self.terminals):
                    return x, y

    def shift_anima_storms(self):
        """Drifts ALL 10 Anima Storms while keeping them between rows 20 and 80."""
        min_y = int(GRID_SIZE * 0.20)
        max_y = int(GRID_SIZE * 0.80)
        
        for storm in self.storms:
            dx, dy = random.choice([(0,1), (0,-1), (1,0), (-1,0), (1,1), (-1,-1), (-1,1), (1,-1), (0,0)])
            new_x = max(1, min(GRID_SIZE - 2, storm.x + dx))
            new_y = max(min_y, min(max_y, storm.y + dy))
            storm.x, storm.y = new_x, new_y

    def end_of_round_mechanics(self):
        # 1. MOVE DRONES ON BELTS
        for drone in list(self.drones.values()):
            belt_dir = self.conveyor_grid[drone.y][drone.x]
            if belt_dir:
                dx, dy = 0, 0
                if belt_dir == "UP": dy = -1
                elif belt_dir == "DOWN": dy = 1
                elif belt_dir == "LEFT": dx = -1
                elif belt_dir == "RIGHT": dx = 1
                
                nx, ny = drone.x + dx, drone.y + dy
                if 0 < ny < GRID_SIZE - 1 and self.walls_grid[ny][nx] == 0:
                    if self.get_entity_at(nx, ny) is None:
                        drone.x, drone.y = nx, ny

        # 2. WIPE OLD BELTS EVERY ROUND
        self.conveyor_grid = [[None for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]

        # 3. SPAWN 2 NEW BELTS
        for _ in range(2):
            if self.drones:
                rd = random.choice(list(self.drones.values()))
                bx, by = rd.x + random.randint(-3, 3), rd.y + random.randint(-3, 3)
                if 0 < by < GRID_SIZE - 1 and 0 <= bx < GRID_SIZE and self.walls_grid[by][bx] == 0:
                    if not any(t.x == bx and t.y == by for t in self.terminals) and self.get_entity_at(bx, by) is None:
                        self.conveyor_grid[by][bx] = random.choice(["UP", "DOWN", "LEFT", "RIGHT"])

        # 4. TERMINALS & STORMS
        for i, terminal in enumerate(self.terminals):
            terminal.timer -= 1
            if terminal.timer <= 0:
                terminal.timer = TERMINAL_LIFESPAN
                tx, ty = self.get_random_empty_cell(10, 40)
                terminal.x, terminal.y = tx, ty
                
        if self.round > 0 and self.round % 25 == 0:
            self.storms = self._place_storms()

    def record_frame(self):
        """Called at the end of every round to snapshot the board."""
        frame = {
            "round": self.round,
            "turn": self.turn,
            "active_player": self.current_player,
            "scores": {"1": self.scores[1], "2": self.scores[2]},
            "drones": [],
            "anchors": [],
            "terminals": [],
            "belts": [{"x": x, "y": y, "dir": self.conveyor_grid[y][x]} 
                      for y in range(GRID_SIZE) for x in range(GRID_SIZE) if self.conveyor_grid[y][x]],
            "storms": [{"x": s.x, "y": s.y, "multiplier": s.multiplier} for s in self.storms]
        }
        
        # 1. Snapshot Drones
        for drone in self.drones.values():
            frame["drones"].append({
                "id": drone.drone_id,
                "p": drone.player_id,
                "x": drone.x, 
                "y": drone.y,
                "pow": drone.is_powered,
                "inert": drone.is_inert
            })
            
        # 2. Snapshot Anchors
        for anchor in self.anchors.values():
            frame["anchors"].append({
                "id": anchor.anchor_id,
                "p": anchor.player_id,
                "x": anchor.x,
                "y": anchor.y,
                "dir": anchor.direction.name,
                "hp": anchor.hp
            })
            
        # 3. Snapshot Terminals
        for terminal in self.terminals:
            frame["terminals"].append({
                "id": terminal.terminal_id,
                "x": terminal.x, 
                "y": terminal.y
            })
                    
        self.replay_data["frames"].append(frame)

    def export_replay(self, filename: str):
        """Saves the recorded frames and grid config to a JSON file for the visualizer."""
        with open(filename, 'w') as f:
            json.dump(self.replay_data, f)
    
    def generate_player_payload(self, player_id: int) -> dict:
        """
        Calculates Fog of War and generates the JSON payload for the current player.
        Only data within a 5x5 radius of POWERED friendly drones is included.
        """
        visible_coords = set()
        my_drones = []
        my_anchors = []
        
        # 1. GATHER MY ENTITIES & CALCULATE VISION
        for drone in self.drones.values():
            if drone.player_id == player_id:
                my_drones.append({
                    "id": drone.drone_id, "x": drone.x, "y": drone.y,
                    "battery": drone.battery, "is_powered": drone.is_powered, "is_inert": drone.is_inert
                })
                # Only powered drones grant vision (5x5 grid centered on the drone)
                if drone.is_powered:
                    for dy in range(-2, 3):
                        for dx in range(-2, 3):
                            vx, vy = drone.x + dx, drone.y + dy
                            if self.is_valid_cell(vx, vy):
                                visible_coords.add((vx, vy))

        for anchor in self.anchors.values():
            if anchor.player_id == player_id:
                my_anchors.append({
                    "id": anchor.anchor_id, "x": anchor.x, "y": anchor.y,
                    "direction": anchor.direction.name, "hp": anchor.hp
                })

        # 2. MASK THE ENVIRONMENT (Enemies, Terrain, Terminals)
        visible_enemy_drones = []
        visible_enemy_anchors = []
        visible_terminals = []
        visible_terrain = []
        
        # Check all enemies to see if they fall inside our vision coords
        for drone in self.drones.values():
            if drone.player_id != player_id and (drone.x, drone.y) in visible_coords:
                visible_enemy_drones.append({
                    "id": drone.drone_id, "x": drone.x, "y": drone.y, "is_inert": drone.is_inert
                }) # Notice we don't tell the player the exact battery level of enemies!
                
        for anchor in self.anchors.values():
            if anchor.player_id != player_id and (anchor.x, anchor.y) in visible_coords:
                visible_enemy_anchors.append({
                    "id": anchor.anchor_id, "x": anchor.x, "y": anchor.y, 
                    "direction": anchor.direction.name, "hp": anchor.hp
                })

        for terminal in self.terminals:
            if (terminal.x, terminal.y) in visible_coords:
                visible_terminals.append({
                    "id": terminal.terminal_id, "x": terminal.x, "y": terminal.y, "timer": terminal.timer
                })

        # To keep JSON fast, we only send terrain data if it is a Wall or a Conveyor Belt.
        # If a coordinate isn't in this list, the bot can assume it's an empty floor.
        for (vx, vy) in visible_coords:
            is_wall = self.walls_grid[vy][vx] == 1
            belt = self.conveyor_grid[vy][vx]
            if is_wall or belt is not None:
                visible_terrain.append({
                    "x": vx, "y": vy, 
                    "is_wall": is_wall, 
                    "belt_dir": belt if belt is not None else "NONE"
                })

        # 3. MASK THE ANIMA STORMS
        visible_storms = []
        for storm in self.storms:
            storm_coords = [(sx, sy) for sx in range(storm.x - 1, storm.x + 2) 
                                     for sy in range(storm.y - 1, storm.y + 2)]
            if any(sc in visible_coords for sc in storm_coords):
                visible_storms.append({"x": storm.x, "y": storm.y, "multiplier": storm.multiplier})

        # 4. PACKAGE THE FINAL PAYLOAD
        payload = {
            "turn": self.turn,
            "my_score": self.scores[player_id],
            "enemy_score": self.scores[2 if player_id == 1 else 1],
            "my_drones": my_drones,
            "my_anchors": my_anchors,
            "visible_enemy_drones": visible_enemy_drones,
            "visible_enemy_anchors": visible_enemy_anchors,
            "visible_terminals": visible_terminals,
            "visible_terrain": visible_terrain,
            "visible_storms": visible_storms
        }
        
        return payload

# ... [End of your GameState class and export_replay method] ...

# Global dictionary to hold the persistent bot processes
bot_processes = {}
bot_errors = {}

def start_bot(player_id: int, launch_command: list):
    """Spawns a persistent background process for a bot."""
    if launch_command:
        # Verify script existence before attempting to execute
        for arg in launch_command:
            if arg.endswith('.py') and not os.path.exists(arg):
                print(f"[ERROR] Could not find bot script: '{arg}'")
                print("Verify 'Dummy_Bot.py' is in the same directory as 'Game_Engine.py'.")
                return

        bot_processes[player_id] = subprocess.Popen(
            launch_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )

def get_commands_from_bot(player_id: int, payload: dict) -> list:
    """Sends the Fog of War payload to the bot and reads the JSON response."""
    process = bot_processes.get(player_id)
    
    if not process:
        return []
        
    # If process has already exited in a previous turn, return empty without re-printing
    if player_id in bot_errors:
        return []

    # Check if process crashed or exited
    if process.poll() is not None:
        _, err = process.communicate()
        bot_errors[player_id] = err
        print(f"\n[CRITICAL] Player {player_id} Bot process exited early!")
        if err and err.strip():
            print(f"Error log:\n{err.strip()}\n")
        return []

    try:
        # 1. Send data to the bot
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()
        
        # 2. Read the bot's response
        response_str = process.stdout.readline()
        
        # 3. Parse the JSON command array
        if response_str and response_str.strip():
            return json.loads(response_str)
        return []
        
    except Exception as e:
        if process.poll() is not None and player_id not in bot_errors:
            _, err = process.communicate()
            bot_errors[player_id] = err
            print(f"\n[CRITICAL] Player {player_id} Bot crashed during turn execution!")
            if err and err.strip():
                print(f"Error log:\n{err.strip()}\n")
        else:
            print(f"Error communicating with Player {player_id} Bot: {e}")
        return []

def play_match(p1_cmd="", p2_cmd="", custom_drones: Optional[List[Dict]] = None):
    game = GameState()
    
    # Boot up the bot processes
    start_bot(1, p1_cmd)
    start_bot(2, p2_cmd)
    
    # ---------------------------------------------------------
    # 1. MAP GENERATOR & INITIAL SPAWNS
    # ---------------------------------------------------------
    game.walls_grid = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
    
    # A. Scaled Symmetrical Walls (20 pairs = 40 walls)
    walls_placed = 0
    while walls_placed < 20: 
        wx = random.randint(0, GRID_SIZE - 1)
        wy = random.randint(2, (GRID_SIZE // 2) - 1)
        if 15 <= wx <= 35 and 15 <= wy <= 35: # Keep center clear
            continue
        game.walls_grid[wy][wx] = 1
        game.walls_grid[GRID_SIZE - 1 - wy][GRID_SIZE - 1 - wx] = 1
        walls_placed += 1

    # B. Spawn 10 Terminals 
    for i in range(10): 
        tx, ty = game.get_random_empty_cell(10, 40)
        game.terminals.append(Terminal(terminal_id=i, x=tx, y=ty))

    # C. Spawn Drones (Custom chosen squares or default 25 per player)
    drone_counter = 1
    if custom_drones:
        for cd in custom_drones:
            pid = cd.get("player_id", 1)
            cx, cy = cd.get("x"), cd.get("y")
            if game.is_valid_cell(cx, cy) and cy not in [0, GRID_SIZE - 1] and game.walls_grid[cy][cx] == 0:
                if game.get_entity_at(cx, cy) is None:
                    game.drones[drone_counter] = Drone(drone_id=drone_counter, player_id=pid, x=cx, y=cy)
                    drone_counter += 1
    else:
        # Default: 25 Drones per player (P1 on row 1, P2 on row 48)
        p1_occupied_x = set()
        p2_occupied_x = set()
        for i in range(25):
            x1 = random.randint(0, GRID_SIZE - 1)
            while x1 in p1_occupied_x:
                x1 = random.randint(0, GRID_SIZE - 1)
            p1_occupied_x.add(x1)
            game.drones[drone_counter] = Drone(drone_id=drone_counter, player_id=1, x=x1, y=1)
            drone_counter += 1

            x2 = random.randint(0, GRID_SIZE - 1)
            while x2 in p2_occupied_x:
                x2 = random.randint(0, GRID_SIZE - 1)
            p2_occupied_x.add(x2)
            game.drones[drone_counter] = Drone(drone_id=drone_counter, player_id=2, x=x2, y=GRID_SIZE - 2)
            drone_counter += 1

    game.resolve_power_network(update_batteries=False)

    game.resolve_power_network()
    
    # ---------------------------------------------------------
        
    game.initialize_replay()
    # Record Frame 0: Initial spawn state before any turns are taken
    game.round = 0
    game.current_player = 1
    game.record_frame()
    
    # ---------------------------------------------------------
    # 2. MATCH LOOP (TRUE TURN-BASED EXECUTION)
    # ---------------------------------------------------------
    for turn in range(3000):
        game.turn = turn
        active_player = 1 if turn % 2 == 0 else 2
        game.current_player = active_player
        game.round = (turn // 2) + 1
        
        # 1. Ask ONLY the active player for moves
        payload = game.generate_player_payload(active_player)
        actions = get_commands_from_bot(active_player, payload)

        # 2. Process ONLY the active player's actions
        game.process_player_actions(active_player, actions)

        # 3. Apply board mechanics after BOTH players have gone (every 1 full round = 2 turns)
        if turn % 2 == 1:
            game.end_of_round_mechanics()
            
        # 4. UNIVERSAL TURN MECHANICS: Resolve power, Score, and Drain Battery for ALL drones
        game.resolve_power_network(update_batteries=False)
        
        for storm in game.storms:
            sx_min, sx_max = storm.x - 1, storm.x + 1
            sy_min, sy_max = storm.y - 1, storm.y + 1
            for drone in game.drones.values():
                if not drone.is_inert and (sx_min <= drone.x <= sx_max) and (sy_min <= drone.y <= sy_max):
                    game.scores[drone.player_id] += storm.multiplier
                    
        for drone in game.drones.values():
            if drone.is_powered:
                drone.recharge()
            else:
                drone.tick_battery()

        # 5. Record the frame so the visualizer shows every single step back-and-forth
        game.record_frame()
        
    # 3. Game Over! Save the visualizer file securely in the project folder.
    engine_dir = os.path.dirname(os.path.abspath(__file__))
    replay_path = os.path.join(engine_dir, "match_final_replay.json")
    
    game.export_replay(replay_path)
    print(f"Match Complete! Score: P1 {game.scores[1]} - P2 {game.scores[2]}")
    print(f"Replay saved to: {replay_path}")

    # Terminate the background bot processes
    for process in bot_processes.values():
        process.terminate()

# This tells Python to run the engine and pass the executable commands for the bots
if __name__ == "__main__":
    import sys
    import os
    
    python_exe = sys.executable
    
    # Get the exact folder where Game_Engine.py is located
    engine_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Create the absolute path to Dummy_Bot.py
    bot_path = os.path.join(engine_dir, "Dummy_Bot.py")
    
    # Pass the absolute path to the bots
    play_match(
        p1_cmd=[python_exe, "-u", bot_path],
        p2_cmd=[python_exe, "-u", bot_path]
    )
