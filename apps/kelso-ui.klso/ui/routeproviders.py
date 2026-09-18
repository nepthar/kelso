"""The Routes page: configured route providers, and the form that sets one up."""

from urllib.parse import quote

from api import api
from configform import config_form
from layout import esc, icon_button


def provider_rows(providers):
  if not providers:
    return '<p class="empty">No route providers configured yet.</p>'
  rows = "".join(
    "<tr>"
    f'<td class="name"><a href="/routes/{quote(p["tag"])}">{esc(p["tag"])}</a></td>'
    f'<td class="muted">{esc(p["kind"])}</td>'
    f'<td class="muted">{esc(p["domain"])}</td>'
    "</tr>"
    for p in providers
  )
  return (
    '<div class="scroll"><table><thead><tr><th>Name</th><th>Kind</th>'
    f"<th>Domain</th></tr></thead><tbody>{rows}</tbody></table></div>"
  )


def provider_add_form(kinds):
  options = "".join(f'<option value="{esc(k)}">{esc(k)}</option>' for k in kinds)
  return (
    '<form method="get" action="/routes/new" class="row">'
    '<input name="tag" placeholder="name (e.g. web)" required>'
    f'<select name="kind" required>{options}</select>'
    f"{icon_button('add', 'plus-box-outline', submit=True)}</form>"
  )


def routes_page(notice=""):
  body = api("/route-providers")
  return (
    notice
    + "<h2>Route providers</h2>"
    + '<p class="lede">Where app routes are published, one per domain</p>'
    + f'<div class="card">{provider_rows(body["route_providers"])}</div>'
    + f'<div class="card entry">{provider_add_form(body["kinds"])}</div>'
  )


def provider_page(tag, kind="", notice=""):
  """The config form for `tag`; `kind` only for a provider not configured yet."""
  query = f"?kind={quote(kind)}" if kind else ""
  request = api(f"/route-providers/{quote(tag)}/config-request{query}")
  hidden = {"kind": kind} if kind else {}
  return (
    notice
    + f'<p class="lede">{esc(request["title"])}</p>'
    + f'<p class="muted">{esc(request.get("note") or "")}</p>'
    + '<div class="card">'
    + config_form(request, f"/routes/{quote(tag)}", hidden)
    + "</div>"
  )
