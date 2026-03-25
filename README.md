# Multiple Agents Pickup and Delivery

This is a repository that contains the simulator for MAPD in warehouse environment. 
The layouts of warehouses are in the "layout" folder.
The scenarios are in the "scenarios" folder.
The "mapd" folder contains all of code written for simulator.
While using the program you can visualize the output in the form of .gif file, which will be generated into "gifs" folder.
By launching simulator with all of it's scenario variants, the .xlsx file that contains the results of simulation will be generated into "results" folder.
Debugging option generates frames of a GIF in "debugging" folder.

## Technology
- Python 3.14.0
- Pillow 12.1.1

## Usage
```
# Launches simulator with default settings
python main.py

# Launches simulator with single scenario and saves the frames for debugging
python main.py --scenario 0_map0.txt --debugging

# Launches simulator with all variants of scenario 
python main.py --suite 2

# Launches simulator with custom paramaters
python main.py --layout 1 --mode Available --station Available --strategy FCFS --algorithm BFS --type square --gif --debugging
```

## Layouts visualization
By creating a simple live server of layouts.html file in "layouts" folder, it is possible to see the layouts of the warehouses of the simulator.