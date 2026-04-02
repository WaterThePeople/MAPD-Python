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
python public/main.py --suite 10-60-small-63234971

# Launches a suite faster with multiple workers when the environment allows it
python public/main.py --suite 20-120-small-280713582 --jobs 8

# Launches only a filtered subset of suite variants
python public/main.py --suite 20-120-small-280713582 --type square --mode Set --station Set --algorithm BFS

# Launches the simulator with custom paramaters
python public/main.py --scenario example.txt --layout 0 --mode Available --station Available --strategy FCFS --algorithm BFS --type square --gif --debugging

# Launches the simulator with custom parameters and returns a .gif file, even when collisions are present.
python public/main.py --scenario example.txt --layout 0 --mode Set --station Set --algorithm A* --type square --gif --fallback-gif
```

## Scenario generator
```
# Generates a full scenario batch for layout 0
python public/generator.py 5 50 0

# Generates a full scenario batch for layouts 0 and 1 with a fixed seed
python public/generator.py 20 120 0,1 --seed 123456
```
The first generator argument is the number of agents.
The second generator argument is the number of tasks.
The third generator argument is a comma-separated list of layout ids.
The layout size is selected automatically from the number of agents:
- `1-20` agents -> `small`
- `21-60` agents -> `medium`
- `61-132` agents -> `large`

The generator creates a dedicated folder in `public/scenarios/`:
`public/scenarios/<agents>-<tasks>-<size>-<seed>/`

Example:
`public/scenarios/10-79-small-123456/`

For every provided layout the generator creates all combinations of:
- `Influx`: `Random`, `Poisson`, `Burst`
- `SpatialDistribution`: `Uniform`, `Hotspot`, `Wave`

That means `9` scenarios per layout.

When running `--suite`, every scenario file can still expand into multiple variants across:
- `Type`
- `Mode`
- `Station`
- `Strategy`
- `Algorithm`

To reduce runtime, you can filter suite runs with the same flags used for single scenarios, for example:
`python public/main.py --suite 20-120-small-280713582 --type square --mode Set --station Set --algorithm BFS`

The `--jobs` value is capped to `10%` of all suite variants, rounded down, with a minimum of `1`.
For example, `648` variants allow at most `64` workers.

## Layouts visualization
By creating a simple live server of the `public/layouts/layouts.html` file, it is possible to see the layouts of the warehouses of the simulator.
