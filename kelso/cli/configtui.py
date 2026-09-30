"""Fill in a ConfigRequest in a compact inline form, with every field on one screen."""

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Input, Label, Select, Static

from kelso.lib.configflow import (
  EMPTY_CONFIG_RESPONSE,
  ConfigField,
  ConfigRequest,
  ConfigResponse,
)


class ConfigApp(App[ConfigResponse]):
  """Exits with the collected response, or EMPTY_CONFIG_RESPONSE."""

  ENABLE_COMMAND_PALETTE = False

  CSS = """
  Screen:inline { height: auto; max-height: 80vh; border: none; }
  VerticalScroll { height: auto; }
  #title { text-style: bold; padding: 1 1 0 1; }
  #note { color: $text-muted; padding: 0 1; }
  .group {
    height: auto;
    margin: 1 1 0 1;
    padding: 1 1;
    border: round $success;
    border-title-align: left;
    border-title-color: $success;
  }
  .field { height: auto; margin-top: 1; }
  .field:first-of-type { margin-top: 0; }
  .desc { color: $text-muted; }
  .row { height: 1; }
  .name { height: 1; }
  .missing { color: $warning; }
  Input, Select { width: 1fr; }
  Input { background: $panel; }
  Input:focus { background: $primary-muted; }
  .folded { display: none; }
  #form.unfolded .folded { display: block; }
  #more { color: $accent; text-style: bold; padding: 1 1 0 1; }
  #form.unfolded #more { display: none; }
  #help { color: $text-muted; padding: 1 1 0 1; }
  """

  BINDINGS = [
    Binding("ctrl+o", "toggle_folded"),
    Binding("ctrl+s", "submit", priority=True),
    Binding("ctrl+q", "cancel", priority=True),
    Binding("escape", "cancel"),
  ]

  def __init__(self, request: ConfigRequest) -> None:
    super().__init__()
    self.request = request
    missing = request.missing()
    # Advanced config and app options fold away, unless one is what is missing.
    self._folded = {
      f.name for f in request.fields if f.section != "config" and f.name not in missing
    }
    # Widget ids are positional: field names such as `volume.media` hold dots,
    # which Textual refuses in an id.
    self._ids = {f.name: f"field-{i}" for i, f in enumerate(request.fields)}
    # Names line up so every input starts in the same column.
    self._name_width = max((len(f.name) for f in request.fields), default=0) + 2

  def _field(self, entry: ConfigField) -> Vertical:
    widgets = []
    if entry.desc:
      widgets.append(Static(entry.desc, classes="desc"))

    name = Label(f"{entry.name}:", classes="name")
    name.styles.width = self._name_width
    if entry.name in self.request.missing():
      name.add_class("missing")
    widgets.append(Horizontal(name, self._control(entry), classes="row"))

    classes = "field folded" if entry.name in self._folded else "field"
    return Vertical(*widgets, classes=classes)

  def _control(self, entry: ConfigField) -> Input | Select:
    if entry.choices is not None:
      return Select(
        [(choice, choice) for choice in entry.choices],
        value=entry.value if entry.value in entry.choices else Select.NULL,
        prompt=entry.display() if entry.choices else "(none defined)",
        disabled=not entry.choices,
        compact=True,
        id=self._ids[entry.name],
      )
    placeholder = entry.display() if entry.secret or entry.value is None else ""
    return Input(
      value="" if entry.secret else (entry.value or ""),
      placeholder=placeholder,
      password=entry.secret,
      compact=True,
      id=self._ids[entry.name],
    )

  def control(self, name: str) -> Input | Select:
    return self.query_one(f"#{self._ids[name]}")

  def compose(self) -> ComposeResult:
    # The scroll container is focusable by default, which would make it the
    # first thing focused and a tab stop between fields.
    with VerticalScroll(can_focus=False):
      yield Static(self.request.title, id="title")
      if self.request.note:
        yield Static(self.request.note, id="note")
      boxes = []
      for title, fields in self.request.groups():
        folded = all(f.name in self._folded for f in fields)
        box = Vertical(
          *(self._field(f) for f in fields),
          classes="group folded" if folded else "group",
        )
        box.border_title = title
        boxes.append(box)
      if self._folded:
        boxes.append(Static(f"To set {self._folded_titles()}, press ctrl+o", id="more"))
      yield Vertical(*boxes, id="form")
    yield Static(id="help")

  def on_mount(self) -> None:
    self._show_help()

  @property
  def unfolded(self) -> bool:
    return self.query_one("#form").has_class("unfolded")

  def values(self) -> dict[str, str]:
    """What the operator changed; an empty or untouched field keeps what is on file."""
    edits = {}
    for entry in self.request.fields:
      control = self.control(entry.name)
      if isinstance(control, Select):
        value = "" if control.is_blank() else str(control.value)
      else:
        value = control.value.strip()
      if value and value != (entry.value or ""):
        edits[entry.name] = value
    return edits

  def _show_help(self) -> None:
    keys = []
    if self._folded:
      verb = "hide" if self.unfolded else "show"
      keys.append(f"^o to {verb} {self._folded_titles()}")
    keys += ["^s save", "^q or esc to quit without saving"]
    self.query_one("#help", Static).update(", ".join(keys))

  def _folded_titles(self) -> str:
    return " and ".join(
      title.lower()
      for title, fields in self.request.groups()
      if any(f.name in self._folded for f in fields)
    )

  def action_toggle_folded(self) -> None:
    if not self._folded:
      return
    self.query_one("#form").toggle_class("unfolded")
    self._show_help()

  def action_submit(self) -> None:
    edits = self.values()
    errors = self.request.validate(edits)
    if errors:
      self.notify("\n".join(errors), severity="error")
      return
    self.exit(ConfigResponse(values=edits) if edits else EMPTY_CONFIG_RESPONSE)

  def action_cancel(self) -> None:
    self.exit(EMPTY_CONFIG_RESPONSE)


def run_tui(request: ConfigRequest) -> ConfigResponse:
  # Inline draws below the prompt and keeps scrollback. With mouse capture on,
  # Textual swallows drags and the terminal can't select text.
  # `run` returns None when the app ends without an exit value, e.g. on ctrl+c.
  return ConfigApp(request).run(inline=True, mouse=False) or EMPTY_CONFIG_RESPONSE
