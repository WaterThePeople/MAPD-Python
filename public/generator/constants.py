from __future__ import annotations

from dataclasses import dataclass


DEFAULT_MODE_CHOICES = ["Set", "Available"]
DEFAULT_STATION_CHOICES = ["Set", "Available"]
DEFAULT_STRATEGY_CHOICES = ["FCFS", "Robin", "GreedyCost"]
DEFAULT_ALGORITHM_CHOICES = ["WHCA*", "SIPP", "BFS"]
DEFAULT_FAILURE_MODEL_CHOICES = ["None", "AgentDelay"]
DEFAULT_TYPE_CHOICES = ["Square", "Hexagon", "Triangle"]
DEFAULT_HOTSPOT_SHELF_SHARE = 0.10
DEFAULT_HOTSPOT_TASK_SHARE = 0.70
DEFAULT_WAVE_ZONE = "Center"
DEFAULT_SET_ASSIGNMENT_POLICY = "RoundRobin"
DEFAULT_BURST_SHARE = 0.30
DEFAULT_BURST_START_SHARE = 0.40
DEFAULT_BURST_DURATION_SHARE = 0.20
DEFAULT_BURST_AMPLITUDE = 3.0
DEFAULT_MAX_SIMULATION_TIME_SECONDS = 600
DEFAULT_FAILURE_PROBABILITY = 0.001
DEFAULT_FAILURE_DURATION_MIN = 5
DEFAULT_FAILURE_DURATION_MAX = 20
INFLUX_CHOICES = ("Random", "Gaussian", "Burst")
SPATIAL_DISTRIBUTIONS = ("Uniform", "Hotspot", "Wave")


@dataclass(frozen=True)
class SizeProfile:
    label: str
    min_agents: int
    max_agents: int
    station_count: int
    shelf_count: int
    delivery_count: int
    default_wave_radius: int


SIZE_PROFILES = {
    "small": SizeProfile(
        label="Small",
        min_agents=1,
        max_agents=20,
        station_count=20,
        shelf_count=120,
        delivery_count=10,
        default_wave_radius=12,
    ),
    "medium": SizeProfile(
        label="Medium",
        min_agents=21,
        max_agents=40,
        station_count=40,
        shelf_count=240,
        delivery_count=20,
        default_wave_radius=12,
    ),
    "large": SizeProfile(
        label="Large",
        min_agents=41,
        max_agents=60,
        station_count=60,
        shelf_count=360,
        delivery_count=30,
        default_wave_radius=18,
    ),
}
