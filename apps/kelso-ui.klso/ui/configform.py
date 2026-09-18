"""A kelsod ConfigRequest as one form, and the values a submit carried back."""

from layout import esc


def _select(field):
  choices = field["choices"]
  current = field.get("value")
  # The blank option submits nothing, which leaves what is on file alone.
  blank = "choose one" if choices else "none defined yet"
  options = f'<option value="">{esc(blank)}</option>' + "".join(
    f'<option value="{esc(c)}"{" selected" if c == current else ""}>{esc(c)}</option>'
    for c in choices
  )
  disabled = "" if choices else " disabled"
  return f'<select name="set.{esc(field["name"])}"{disabled}>{options}</select>'


def _row(field, missing):
  name = field["name"]
  if field.get("choices") is not None:
    control = _select(field)
  elif field["secret"]:
    hint = "set — type to replace" if field["secret_set"] else "not set"
    control = (
      f'<input type="password" name="set.{esc(name)}" '
      f'placeholder="{esc(hint)}" autocomplete="new-password">'
    )
  else:
    default = field.get("default")
    hint = f"{default} (default)" if default is not None else ""
    control = (
      f'<input name="set.{esc(name)}" value="{esc(field.get("value") or "")}" '
      f'placeholder="{esc(hint)}">'
    )

  subs = ""
  if field["secret"]:
    subs += "<span class=sub>secret</span>"
  if name in missing:
    subs += '<span class="sub warnish">required</span>'
  return (
    f'<tr><td class="key">{esc(name)}{subs}</td>'
    f'<td class="field">{control}</td>'
    f'<td class="muted wrap">{esc(field.get("desc") or "")}</td></tr>'
  )


def _table(fields, missing):
  rows = "".join(_row(field, missing) for field in fields)
  return f'<div class="scroll"><table class="kv"><tbody>{rows}</tbody></table></div>'


def config_form(request, action, hidden=None):
  """Every field of `request` in one form posting to `action`."""
  fields = request.get("fields") or []
  if not fields:
    return '<p class="empty">Nothing to configure.</p>'

  missing = set(request.get("missing") or [])
  basic = [f for f in fields if not f.get("advanced") or f["name"] in missing]
  advanced = [f for f in fields if f not in basic]

  body = "".join(
    f'<input type="hidden" name="{esc(k)}" value="{esc(v)}">'
    for k, v in (hidden or {}).items()
  )
  body += _table(basic, missing) if basic else ""
  if advanced:
    body += (
      '<details class="reveal">'
      "<summary>Show advanced configuration options</summary>"
      f"{_table(advanced, missing)}</details>"
    )
  body += (
    '<div class="cfg-actions">'
    '<button type="submit" class="cfg-save" disabled>Save</button></div>'
  )
  return f'<form method="post" action="{esc(action)}" class="cfg-form">{body}</form>'


def submitted_values(form):
  """The `set.*` fields of a posted form, minus blanks.

  Blank means "leave it alone": a secret's input is always empty, since the UI
  never had its value, and kelsod skips anything unchanged.
  """
  return {
    key.removeprefix("set."): str(form.get(key) or "").strip()
    for key in form
    if key.startswith("set.") and str(form.get(key) or "").strip()
  }
