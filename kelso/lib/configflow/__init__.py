"""What has to be configured, as data, and the functions that fill it in.

A `ConfigRequest` describes the fields one thing needs -- an app's `[config]`,
a route provider's `args` -- with each field's current value, so any front end
can render the same question. A front end hands back a `ConfigResponse`. The
`app` and `route_provider` modules build requests and apply responses; this one
owns the shapes and the validation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConfigField:
  name: str
  # The value on file, or None when nothing is set. Never a secret's plaintext.
  value: str | None = None
  default: str | None = None
  secret: bool = False
  # Secrets never carry their value, so "is it set" has to be said separately.
  secret_set: bool = False
  desc: str = ""
  advanced: bool = False
  required: bool = True
  # None for free text. Otherwise the value must be one of these; an empty tuple
  # means "pick one" with nothing defined to pick from yet.
  choices: tuple[str, ...] | None = None

  @property
  def is_set(self) -> bool:
    return self.secret_set if self.secret else self.value is not None

  def display(self) -> str:
    if self.secret:
      return "(set)" if self.secret_set else "(not set)"
    if self.value is not None:
      return self.value
    if self.default is not None:
      return f"{self.default} (default)"
    return "(required)" if self.required else "(unset)"


@dataclass(frozen=True)
class ConfigRequest:
  """The fields one thing needs, and what each holds now."""

  title: str
  fields: tuple[ConfigField, ...] = ()
  note: str = ""

  def field(self, name: str) -> ConfigField | None:
    for entry in self.fields:
      if entry.name == name:
        return entry
    return None

  def missing(self) -> list[str]:
    """Required fields with nothing on file and no default."""
    return [
      f.name for f in self.fields if f.required and not f.is_set and f.default is None
    ]

  def validate(self, values: dict[str, str]) -> list[str]:
    """Errors that make a response unapplicable.

    A required field left unset is not one of them: a partial answer is still
    worth storing, and `start` is what refuses to run an under-configured app.
    """
    errors = []
    for name, value in values.items():
      entry = self.field(name)
      if entry is None:
        errors.append(f"{self.title}: no config named {name!r}")
      elif not value:
        errors.append(f"{self.title}: {name} cannot be empty")
      elif entry.choices is not None and value not in entry.choices:
        known = ", ".join(entry.choices) or "(none defined)"
        errors.append(f"{self.title}: {name} must be one of: {known}")
    return errors

  def response(self, values: dict[str, str]) -> ConfigResponse:
    """Build a response for this request, refusing one that cannot apply."""
    errors = self.validate(values)
    if errors:
      raise ValueError("; ".join(errors))
    return ConfigResponse(values=dict(values))


@dataclass(frozen=True)
class ConfigResponse:
  """What a front end collected, ready for the source to apply."""

  values: dict[str, str] = field(default_factory=dict)


EMPTY_CONFIG_RESPONSE = ConfigResponse(values=dict())
