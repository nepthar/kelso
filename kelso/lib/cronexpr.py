"""Five-field cron schedules: minute, hour, day of month, month, day of week.

Each field is `*` or a comma list of `n`, `a-b`, `*/s`, `a-b/s` or `a/s`. Day of
week runs 0-7 with both 0 and 7 meaning Sunday. When both day fields are
restricted, a day matching either one matches, as in every cron.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

_FIELDS = (
  ("minute", 0, 59),
  ("hour", 0, 23),
  ("day of month", 1, 31),
  ("month", 1, 12),
  ("day of week", 0, 7),
)

# A schedule that matches nothing (`0 0 30 2 *`) would otherwise search forever.
_SEARCH_DAYS = 366 * 5


@dataclass(frozen=True)
class CronSchedule:
  text: str
  minutes: frozenset[int]
  hours: frozenset[int]
  days: frozenset[int]
  months: frozenset[int]
  weekdays: frozenset[int]
  any_day: bool
  any_weekday: bool

  @classmethod
  def parse(cls, text: str) -> "CronSchedule":
    """Raises ValueError naming the field that is wrong."""
    parts = text.split()
    if len(parts) != 5:
      raise ValueError(
        f"cron schedule {text!r} needs 5 fields "
        f"(minute hour day-of-month month day-of-week), got {len(parts)}"
      )
    values = [
      _parse_field(part, name, low, high)
      for part, (name, low, high) in zip(parts, _FIELDS, strict=True)
    ]
    weekdays = frozenset(0 if day == 7 else day for day in values[4])
    return cls(
      text=text,
      minutes=values[0],
      hours=values[1],
      days=values[2],
      months=values[3],
      weekdays=weekdays,
      any_day=parts[2] == "*",
      any_weekday=parts[4] == "*",
    )

  def next_after(self, after: datetime) -> datetime:
    """The first matching minute strictly after `after`, in `after`'s clock."""
    at = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = at + timedelta(days=_SEARCH_DAYS)
    while at < limit:
      if at.month not in self.months:
        at = (at.replace(day=1) + timedelta(days=32)).replace(day=1, hour=0, minute=0)
      elif not self._day_matches(at):
        at = (at + timedelta(days=1)).replace(hour=0, minute=0)
      elif at.hour not in self.hours:
        at = (at + timedelta(hours=1)).replace(minute=0)
      elif at.minute not in self.minutes:
        at += timedelta(minutes=1)
      else:
        return at
    raise ValueError(f"cron schedule {self.text!r} never matches a real date")

  def _day_matches(self, at: datetime) -> bool:
    in_days = at.day in self.days
    # Python counts Monday as 0; cron counts Sunday as 0.
    in_weekdays = (at.weekday() + 1) % 7 in self.weekdays
    if self.any_day and self.any_weekday:
      return True
    if self.any_day:
      return in_weekdays
    if self.any_weekday:
      return in_days
    return in_days or in_weekdays


def _parse_field(text: str, name: str, low: int, high: int) -> frozenset[int]:
  values: set[int] = set()
  for item in text.split(","):
    span, _, step_text = item.partition("/")
    step = _number(step_text, name) if step_text else 1
    if step < 1:
      raise ValueError(f"cron {name}: step must be at least 1, got {item!r}")
    if span == "*":
      start, end = low, high
    elif "-" in span:
      start_text, _, end_text = span.partition("-")
      start, end = _number(start_text, name), _number(end_text, name)
    else:
      start = _number(span, name)
      end = high if step_text else start
    if not low <= start <= end <= high:
      raise ValueError(f"cron {name}: {item!r} is outside {low}-{high}")
    values.update(range(start, end + 1, step))
  return frozenset(values)


def _number(text: str, name: str) -> int:
  if not text.isdigit():
    raise ValueError(f"cron {name}: {text!r} is not a number")
  return int(text)
