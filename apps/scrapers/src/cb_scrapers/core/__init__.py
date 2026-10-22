"""The framework every adapter plugs into."""

from .adapter import DAILY, MONTHLY, TWICE_DAILY, WEEKLY, Adapter, Cadence, Fetched, Item, Pace, Task
from .hashing import content_hash, listing_hash, normalize, obs_hash, schedule_signature, stable_hash
from .http import USER_AGENT, BlockedError, DisallowedError, PoliteClient
from .registry import all_adapters, register

__all__ = [
    "DAILY",
    "MONTHLY",
    "TWICE_DAILY",
    "USER_AGENT",
    "WEEKLY",
    "Adapter",
    "BlockedError",
    "Cadence",
    "DisallowedError",
    "Fetched",
    "Item",
    "Pace",
    "PoliteClient",
    "Task",
    "all_adapters",
    "content_hash",
    "listing_hash",
    "normalize",
    "obs_hash",
    "register",
    "schedule_signature",
    "stable_hash",
]
