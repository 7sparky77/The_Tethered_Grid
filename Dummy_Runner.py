import sys
import json

def main():
    while True:
        raw_input = sys.stdin.readline()
        if not raw_input:
            break
            
        payload = json.loads(raw_input)
        my_drones = payload.get("my_drones", [])
        
        commands = []
        for drone in my_drones:
            if drone["is_powered"]:
                commands.append({
                    "drone_id": drone["id"],
                    "action": "MOVE",
                    "direction": "RIGHT"
                })
                
        sys.stdout.write(json.dumps(commands) + "\n")
        sys.stdout.flush()

if __name__ == "__main__":
    main()
