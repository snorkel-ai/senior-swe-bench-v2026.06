"""Validation drivers for multi-stack test execution.

Drivers are selected per-story via the `driver` field in validation_spec.toml.
"""

from .base import CaseResult as CaseResult
from .base import Driver
from .base import DriverResult as DriverResult
from .cargo_test_driver import CargoTestDriver
from .go_test_driver import GoTestDriver
from .jest_driver import JestDriver
from .mix_test_driver import MixTestDriver
from .playwright_driver import PlaywrightDriver
from .pytest_driver import PytestDriver

DRIVERS: dict[str, type[Driver]] = {
    "pytest": PytestDriver,
    "jest": JestDriver,
    "go-test": GoTestDriver,
    "cargo-test": CargoTestDriver,
    "playwright": PlaywrightDriver,
    "mix-test": MixTestDriver,
}


def get_driver(name: str) -> Driver:
    """Get a driver instance by name."""
    cls = DRIVERS.get(name)
    if cls is None:
        raise ValueError(f"Unknown driver: {name!r}. Available: {list(DRIVERS)}")
    return cls()
