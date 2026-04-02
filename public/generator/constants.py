from __future__ import annotations

from dataclasses import dataclass


DEFAULT_MODE_CHOICES = ["Set", "Available"]
DEFAULT_STATION_CHOICES = ["Set", "Available"]
DEFAULT_STRATEGY_CHOICES = ["FCFS", "Robin", "GreedyCost"]
DEFAULT_ALGORITHM_CHOICES = ["A*", "SIPP", "BFS"]
DEFAULT_TYPE_CHOICES = ["Square", "Hexagon", "Triangle"]
DEFAULT_HOTSPOT_SHELF_SHARE = 0.10
DEFAULT_HOTSPOT_TASK_SHARE = 0.70
DEFAULT_WAVE_ZONE = "Center"
DEFAULT_SET_ASSIGNMENT_POLICY = "RoundRobin"
DEFAULT_CAPACITY_MODEL = "LayoutExpectedCost"
DEFAULT_BURST_SHARE = 0.30
DEFAULT_BURST_START_SHARE = 0.40
DEFAULT_BURST_DURATION_SHARE = 0.20
DEFAULT_BURST_AMPLITUDE = 3.0
INFLUX_CHOICES = ("Random", "Poisson", "Burst")
SPATIAL_DISTRIBUTIONS = ("Uniform", "Hotspot", "Wave")


@dataclass(frozen=True)
class SizeProfile:
    label: str
    min_agents: int
    max_agents: int
    station_count: int
    shelf_count: int
    max_replans: int
    default_wave_radius: int


SIZE_PROFILES = {
    "small": SizeProfile(
        label="Small",
        min_agents=1,
        max_agents=20,
        station_count=20,
        shelf_count=120,
        max_replans=5,
        default_wave_radius=12,
    ),
    "medium": SizeProfile(
        label="Medium",
        min_agents=21,
        max_agents=60,
        station_count=60,
        shelf_count=360,
        max_replans=10,
        default_wave_radius=12,
    ),
    "large": SizeProfile(
        label="Large",
        min_agents=61,
        max_agents=132,
        station_count=132,
        shelf_count=792,
        max_replans=15,
        default_wave_radius=18,
    ),
}
