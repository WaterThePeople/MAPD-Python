# Multi-Agent Pickup and Delivery

This is a repository that contains the simulator for MAPD in a warehouse environment. 
The layouts of warehouses are in the "layout" folder.
The scenarios are in the "scenarios" folder.
The "mapd" folder contains all of the code written for the simulator.
While using the program you can visualize the output in the form of a .gif file, which will be generated into the "gifs" folder.
By launching the simulator with all of its scenario variants, the .xlsx file that contains the results of the simulation will be generated into the "results" folder.
Debugging option generates frames of a GIF in the "debugging" folder.

## Technology
- Python 3.14.0
- Pillow 12.1.1

## Usage
```
# Launches the simulator with the default settings
python main.py

# Launches the simulator with a single scenario and saves the frames for debugging
python main.py --scenario 0_map0.txt --debugging

# Launches the simulator with all of its scenario variants
python main.py --suite 2

# Launches the simulator with custom paramaters
python main.py --scenario 2 --layout 1 --mode Available --station Available --strategy FCFS --algorithm BFS --type square --gif --debugging

# Launches the simulator with custom parameters and returns a .gif file, even when collisions are present.
python main.py --scenario 2 --layout 9 --mode Set --station Set --algorithm A* --type square --gif --fallback-gif
```

## Layouts visualization
By creating a simple live server of the layouts.html file in the "layouts" folder, it is possible to see the layouts of the warehouses of the simulator.