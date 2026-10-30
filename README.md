# THE TETHERED GRID

**The Tethered Grid** is a multi-agent programming battle where players command a massive swarm of Drones. The objective is to secure moving scoring zones on a shifting grid.

There is no traditional health (HP) for units and no basic attacking on drones. Combat is entirely based on logistics, positioning, and sabotage. You defeat enemies by severing their power supply lines.

The game operates strictly on a **turn-based system**. This eliminates paradoxes like two bots trying to push each other simultaneously or conflicting movements. Turn-based doesn’t favour the player starting first or second, as this is a massive grid and the game depends on a lot of other things and strategies.

## 1. THE MAP & ENTITIES

The game takes place on a 50 x 50 grid. The map consists of the following elements:

- **Normal Walls (#):** Impassable and immovable static blocks. Line of Sight cannot pass through them.
- **Conveyor Belts (~):** Moving floor tiles. At the end of the turn, they force any drone standing on them (except Anchors) to move 1 cell in a specific direction.
- **Terminals (T):** Hackable control panels that grant map manipulation. Terminals are volatile; they have a lifespan of some turns (adjustable) to prevent permanent camping. Once the timer reaches zero, the Terminal teleports to a new random location.
- **Anima Storms ($):** 3x3 scoring zones that occur randomly across the map.
- **Mother-Node (M):** The Mother-Nodes consist of **two full extreme rows** at opposite ends of the board (one row per player). These act as the permanent, unmovable primary power sources.
- **Drones (D):** Your controllable units. To support massive swarm tactics, players will get **25 reserved Drones** (adjustable). (Note: There is no respawning. Once a drone dies, it is permanently removed).
- **Anchors (A):** Permanent, independent power relays created by sacrificing 3 Drones. It can be destroyed by sabotage_anchor by 3 enemy drones ( see below ).

**Fog of War:** Bots do not receive the entire map state. Your bot will only receive data for a 5x5 grid centered on each of your active units. You must constantly move your swarm to scout for Storms, Terminals, and enemies.

## 2. POWER SYSTEM & BATTERIES

Drones do not have unlimited internal power. They can only execute commands and know about their surroundings if they are **Active**.

- **Line of Sight (LoS):** To receive direct power, a Drone must have an unobstructed straight line of sight (horizontal, vertical, or diagonal) back to the Mother-Node row OR to another **powered** friendly unit in the chain OR friendly Anchor.
- **Range Limit:** The maximum distance for a valid LoS power connection is **5 cells**.
- **Obstructions:** LoS is blocked by Normal Walls (#) and Enemy units and inert units of enemy as well.

**The Battery Mechanic & Inert State:**

- Every Drone possesses an internal battery with a **5-turn capacity (adjustable)**.
- If a Drone's LoS to the power chain is broken, it doesn't die immediately. It runs on its battery, allowing it to act normally for up to 5 turns.
- **Power Down:** Once the battery reaches 0, the Drone becomes an **Inert Obstacle (X)**. Inert drones cannot execute commands or move. However, they remain on the grid and act as physical obstructions that may block enemy LoS!
- **Recharging:** An inert Drone can be revived! If a friendly, fully-connected network extends its LoS back to the inert drone, it immediately regains connectivity. Its battery will then charge at a rate of **+1 per turn**, capped at 5 (adjustable).

### 3. TURN ACTIONS

Every turn, your bot must return an array of commands for its active Drones. You can issue exactly **one** command per active Drone per turn.

| **Action**   | **Command**     | **Parameters** | **Description**                                                    | **Movement / Interaction Rules**                                                                                                                                                                                                                       |
| ------------ | --------------- | -------------- | ------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Move**     | MOVE            | [Direction]    | Moves the Drone 1 cell orthogonally (Up, Down, Left, Right).       | Cannot move into cells containing walls, enemy Anchors or other Drones (friendly, enemy, or inert).                                                                                                                                                    |
| **Push**     | PUSH            | [Direction]    | Forces an adjacent entity 1 cell in the specified direction.       | Used for tactical positioning, breaking line-of-sight, or crushing enemy Drones against solid walls.                                                                                                                                                   |
| **Interact** | INTERACT        | [Type, Target tile, Direction]           | Hacks the underlying Terminal to manipulate map mechanics.         | The Drone must occupy the Terminal tile. Grants absolute control over any single **visible** grid tile for that turn (add for 2 turns, remove, or rotate existing conveyor belts). Targeting Mother-Nodes, Walls, or Enemy Anchors doesn’t make sense. |
| **Build**    | BUILD           | [Direction]           | Consumes 3 friendly Drones to construct a permanent Anchor.        | Requires 3 active friendly Drones on the same line to simultaneously issue BUILD. Placed directly on the central Drone's cell.                                                                                                                         |
| **Sabotage** | SABOTAGE_ANCHOR | [Target Anchor]           | Sacrifices the Drone to deal 1 point of damage to an enemy Anchor. | The Drone must be within the enemy Anchor's 5 x 5 defense region (centered on the Anchor). Permanently destroys the executing Drone.                                                                                                                      |
| **Wait**     | WAIT            | None           | Holds position and takes no action for the turn.                   | Useful for maintaining static tether nodes or holding scoring positions in an Anima Storm.                                                                                                                                                              |
| **Spawn**    | SPAWN           | [Target Cell]           | Spawns our drone at the cell we want, capped at 25.                | For spawning a drone, there must be at least 1 reserved drones( drones which weren’t spawned yet).<br>Also the spawned drone should get power supply immediately, otherwise it won’t spawn.                                                            |

**Note:** Reserved drones = 25 - Drones which already spawned (active and inert) - Drones that died/removed.

## 4. COMBAT & ELIMINATION

Because there is no standard HP for units, you eliminate enemies through positional sabotage and battery starvation. **Once a drone is destroyed, it is gone forever, no respawning.**

- **The Body-Block:** Simply moving your Drone between two enemy Drones breaks their Line of Sight.
- **The Push:** Pushing an enemy out of their 5-cell range may unplug them from power network.
- **Environmental Sabotage:** You cannot hack an enemy Drone directly, but you can hack the floor beneath them! Using INTERACT on a Terminal lets you manipulate a conveyor belt under an enemy squad. The floor will drag their network behind a wall, severing their connection simultaneously. This may even push an enemy drone into a wall and destroy it.
- **Permanent Destruction:** If you use PUSH to force an enemy Drone into a solid wall, the Drone is permanently crushed and removed from the game completely.

## 5. ANCHORS

Conveyor belts will constantly threaten to move your Drones out of alignment and snap your tethers. To lock down an area, you can build an **Anchor**.

- **How to Build:** 3 active Drones must be on the same line. All 3 must issue the BUILD command. They are permanently removed from your Drone count, and 1 Anchor is placed. (This is an irreversible operation).
- **Independent Power Source:** Unlike Drones, Anchors do not need to be tethered to the Mother-Node. They are completely independent power sources.
- **Directional Beam:** An Anchor does not provide 360-degree power. It provides power along **one specific orthogonal direction**, projecting a beam for a maximum distance of 5 cells.
- **Immunity:** Anchors are completely immune to Conveyor Belts and cannot be pushed.
- **Destruction:** Anchors are tough, but not invincible. They have **3 HP**. To destroy one, an enemy must send their own Drones into the Anchor's 5x5 defense region. The enemy must use the SABOTAGE_ANCHOR command, permanently sacrificing their own Drone to deal 1 damage to the Anchor.

## 6. SCORING & VICTORY

The game lasts for a fixed number of turns (e.g., 3000). The winner is determined by total score. If both have the same scores then we may look at other factors like anchors, drone count, battery, etc.

Points are awarded dynamically based on the **Anima Storms**. For every turn, you gain points equal to:

[Number of your active Drones inside the Storm] × [Storm's Current Multiplier]

(Note: Inert Drones that have 0 battery inside a Storm award 0 points. Drones must be **Active** to score points!)

## Note:

1. If 2 Conveyor belt tiles try to push drones on them onto the same square, their actions get nullified.
