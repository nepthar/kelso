"""What has to be configured, as data.

A `ConfigRequest` describes the fields one thing needs -- an app's `[config]`,
a route provider's `args` -- with each field's current value, so any front end
can render the same question. A front end hands back a `ConfigResponse`, which
the source that built the request applies. This module owns the shapes and the
validation; applying belongs to the source, because an app writes its store and
a route provider writes config.toml.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


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
    Front ends show `still_needed` instead.
    """
    errors = []
    for name, value in values.items():
      entry = self.field(name)
      if entry is None:
        errors.append(f"{self.title}: no config named {name!r}")
      elif not value:
        errors.append(f"{self.title}: {name} cannot be empty")
    return errors

  def still_needed(self, values: dict[str, str]) -> list[str]:
    """Required fields this response would leave with nothing to fall back on."""
    return [name for name in self.missing() if not values.get(name)]

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


@runtime_checkable
class HasConfig(Protocol):
  """Something an operator configures: it describes itself, and applies answers.

  Implementations are bound to one thing (this app, this provider tag), so a
  front end renders and applies without knowing which kind it holds.
  """

  def config_request(self) -> ConfigRequest:
    """What this needs, with what is on file now."""
    ...

  def apply_config(self, response: ConfigResponse) -> list[str]:
    """Apply a response, returning the field names actually written."""
    ...
