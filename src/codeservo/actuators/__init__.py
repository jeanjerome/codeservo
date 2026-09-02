"""The backends that propose a change, and the one that reviews it.

An actuator is loaded by name and exposes three operations and nothing else.
Backend-specific behaviour stays inside its own adapter module: no other
layer names a command-line flag, a configuration key or an event field.
"""

from .base import (
    ACTUATOR_ENV_VAR,
    ACTUATOR_NAMES,
    DEFAULT_ACTUATOR,
    Actuator,
    ActuatorError,
    default_actuator_name,
    load_actuator,
)

__all__ = [
    "ACTUATOR_ENV_VAR",
    "ACTUATOR_NAMES",
    "DEFAULT_ACTUATOR",
    "Actuator",
    "ActuatorError",
    "default_actuator_name",
    "load_actuator",
]
