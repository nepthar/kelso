"""Signing in and out. See auth.py for what a session is."""

import auth
from fastapi import APIRouter, Request
from web import NO_STORE, field, render, see

router = APIRouter()


def safe_next(value):
  """A path on this server, or `/`. Keeps `?next=` from becoming a redirector."""
  return value if value.startswith("/") and not value.startswith("//") else "/"


@router.get("/login")
def login(request: Request, next: str = "/"):
  return render(request, "signin.html", "Sign in", next_to=safe_next(next), error="")


@router.post("/login")
async def login_post(request: Request):
  form = await request.form()
  target = safe_next(field(form, "next"))
  if not auth.check_password(field(form, "password")):
    return render(
      request,
      "signin.html",
      "Sign in",
      status_code=401,
      headers=NO_STORE,
      next_to=target,
      error="That is not the admin password.",
    )
  value, max_age = auth.issue()
  response = see(target)
  # SameSite=Lax keeps other sites' requests from carrying the cookie; the
  # origin check in frontdoor.py covers the sibling subdomains Lax lets in.
  response.set_cookie(
    auth.COOKIE,
    value,
    max_age=max_age,
    path="/",
    httponly=True,
    samesite="lax",
    secure=request.url.scheme == "https",
  )
  return response


@router.post("/logout")
def logout():
  response = see("/login")
  response.delete_cookie(auth.COOKIE, path="/")
  return response
