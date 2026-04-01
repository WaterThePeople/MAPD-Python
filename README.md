# Multi-Agent Pickup and Delivery

This is a repository that contains the simulator for MAPD in a warehouse environment.
The application code, layouts and scenarios are in the `public/` folder.
Generated GIF files, Excel reports and debug frames are saved in the `local/` folder.

## Technology
- Python 3.14.0
- Pillow 12.1.1

## Usage
```
# Launches the simulator with the default settings
python public/main.py

# Launches the simulator with a single scenario and saves the frames for debugging
python public/main.py --scenario example.txt --debugging

# Launches the simulator with all of its scenario variants
python public/main.py --suite example

# Launches the simulator for every scenario file inside a generated folder
python public/main.py --suite 10-60-small-360-63234971

# Launches the simulator with custom paramaters
python public/main.py --scenario example.txt --layout 0 --mode Available --station Available --strategy FCFS --algorithm BFS --type square --gif --debugging

# Launches the simulator with custom parameters and returns a .gif file, even when collisions are present.
python public/main.py --scenario example.txt --layout 0 --mode Set --station Set --algorithm A* --type square --gif --fallback-gif
```

## Scenario generator
```
# Generates a full scenario batch for layout 0
python public/generator.py 5 360 0

# Generates a full scenario batch for layouts 0 and 1 with a fixed seed
python public/generator.py 20 720 0,1 --seed 123456
```
The first generator argument is the number of agents.
The second generator argument is the maximum makespan in steps.
One simulation action takes `10` seconds, so:
- `1` step = `10` seconds
- `360` steps = `3600` seconds = `1` hour
- `720` steps = `7200` seconds = `2` hours

The generator creates a dedicated folder in `public/scenarios/`:
`public/scenarios/<agents>-<tasks>-<size>-<time_limit_steps>-<seed>/`

Example:
`public/scenarios/10-79-small-360-123456/`

For every provided layout the generator creates all combinations of:
- `Influx`: `Random`, `Poisson`, `Burst`
- `SpatialDistribution`: `Uniform`, `Hotspot`, `Wave`

That means `9` scenarios per layout.

## Layouts visualization
By creating a simple live server of the `public/layouts/layouts.html` file, it is possible to see the layouts of the warehouses of the simulator.
