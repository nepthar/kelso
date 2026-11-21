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
  choices: tuple[str, ...] | None = None
  labels: tuple[str, ...] = ()

  def validate(self, value: str) -> None:
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


# The named start groups. The odd numbers between them are free, for
# squeezing something in.
START_GROUPS = {
  0: "init",
  2: "support services",
  4: "routing & connections",
  6: "apps (default)",
  9: "last",
}


def start_group_name(order: int) -> str:
  name = START_GROUPS.get(order)
  return f"{order}: {name}" if name else str(order)


APP_OPTIONS: dict[str, AppOption] = {
  option.name: option
  for option in (
    AppOption(
      "subdomain",
      "Subdomain for published routes",
      lambda app: AppID(app).stem,
      _subdomain,
    ),
    AppOption(
      "start_group",
      "Startup group order used with `kelso up` and `kelso down`",
      lambda app: "6",
      _int_between(0, 9),
      tuple(str(n) for n in range(10)),
      tuple(start_group_name(n) for n in range(10)),
    ),
  )
}


def validate_option(name: str, value: str) -> None:
  option = APP_OPTIONS.get(name)
  if option is not None:
    option.validate(value)
