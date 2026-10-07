"""App options: config every app has, defined here rather than in a manifest.

They share the config namespace. A manifest that declares one of these names in
`[config]` or `[adv_config]` takes over its default, description, and whether it
is required, but the value is still checked by the option's validator.
"""

from collections.abc import Callable
from dataclasses import dataclass

from kelso.lib.apps import AppID
from kelso.lib.util import validate_identifier


@dataclass(frozen=True)
class AppOption:
  name: str
  desc: str
  default: Callable[[AppID], str]
  check: Callable[[str], None]

  def validate(self, value: str) -> None:
    """Raise ValueError naming the option if `value` is not acceptable."""
    try:
      self.check(value)
    except ValueError as error:
      raise ValueError(f"{self.name} {value!r}: {error}") from None


def _subdomain(value: str) -> None:
  try:
    validate_identifier(value)
  except ValueError:
    raise ValueError("use letters, digits, _ and -, with no periods") from None


def _int_between(low: int, high: int | None) -> Callable[[str], None]:
  def check(value: str) -> None:
    number = int(value) if value.lstrip("-").isdigit() else None
    if number is None or number < low or (high is not None and number > high):
      bounds = f"from {low} to {high}" if high is not None else f"{low} or more"
      raise ValueError(f"must be a whole number {bounds}")

  return check


# The named start_order groups. The odd numbers between them are free, for
# squeezing something in.
START_GROUPS = {
  0: "init",
  2: "support services",
  4: "routing & connections",
  6: "applications",
  8: "lazy applications",
}


def start_group_name(order: int) -> str:
  """The group as shown: its number, then its name if it has one."""
  name = START_GROUPS.get(order)
  return f"{order} - {name}" if name else str(order)


APP_OPTIONS: dict[str, AppOption] = {
  option.name: option
  for option in (
    AppOption(
      "subdomain",
      "DNS label this app's routes are published under",
      lambda app: AppID(app).stem,
      _subdomain,
    ),
    AppOption(
      "start_order",
      "Start group, 0 to 9: "
      + ", ".join(f"{n} {name}" for n, name in START_GROUPS.items())
      + ". Group 0 runs while kelsod does; `kelso up` starts the rest in order",
      lambda app: "6",
      _int_between(0, 9),
    ),
  )
}


def validate_option(name: str, value: str) -> None:
  """Check `value` if `name` is an app option; anything else passes."""
  option = APP_OPTIONS.get(name)
  if option is not None:
    option.validate(value)
