# README plan (post-1.0)

Working notes, not user docs. Delete once the README is done.

## Problems with the current README
- Taglines: DONE, "A declarative selfhosting platform for managing and distributing apps".
  "your apps, your hardware. No rack required.", GitHub description "An
  opinionated, portable container stack management tool". Pick one, use it
  everywhere (README, pyproject, GitHub description).
- No image at all. Nothing above the fold shows what kelso looks like.
- The pitch ("Why kelso?") comes *after* a very long Getting Started that
  includes upgrade, uninstall and volume-symlink advice. Readers decide in
  30 seconds; the pitch has to come first.
- The web UI is mentioned once, in passing, near the bottom of Getting Started.
- The staples apps (immich, jellyfin, mealie, nginx-proxy-manager, cloudflared,
  unifi, adguard) are never listed. That is the strongest "this is real" signal.
- No badges, no repo topics, no license/contributing section, no TOC.
- No CI workflow, so no test badge is possible yet.

## Proposed structure
1. Hero: name, one tagline, badges (release tag, license, python 3.12,
   "linux + docker"), then the screenshot.
2. Three-bullet pitch: declare what an app needs, kelso wires it; no lock-in,
   it is just compose projects on disk; coherent snapshots.
3. Quick look: the trimmed manifest (keep), `kelso load` + `kelso start`,
   then the terminal recording.
4. What you get: web UI, secrets, snapshots, cron, routes, app repos. One line
   each, link to docs/manifest.md.
5. Apps today: the staples list with one-line descriptions, plus "write your
   own in a few minutes: case study".
6. Install: prerequisites in one sentence, three commands, link to a new
   docs/install.md that absorbs prerequisites-on-Ubuntu, upgrade, uninstall,
   and volume locations.
7. Why kelso / why not kelso (keep, tighten).
8. Docs, contributing (link CONTRIBUTING.md and DCO), license, name, and the
   "software like it's 1997" philosophy as the closer.

## Media
- Screenshot: kelso-ui apps overview with several apps running, in a scratch
  root with demo/staples apps only (no personal apps or hostnames). Capture at
  2x, crop to the content, ~1200px wide, PNG, compressed. Store in
  docs/images/. Use `<picture>` with prefers-color-scheme if the UI has a
  dark theme, otherwise one image.
- Terminal recording: `kelso start demo-cron`, `kelso cmd demo-cron stamp
  hello`, `kelso logs demo-cron`. Record with asciinema, render to gif with
  agg (or use vhs with a .tape file checked into docs/images so it is
  reproducible). Keep it under 20 seconds.
- Optional wordmark/logo. A plain text title is fine for 1.0.

## Repo side
- Set GitHub topics: self-hosting, docker, docker-compose, homelab, python.
- Set the GitHub description and homepage to match the tagline.
- Add a minimal CI workflow (ruff + pytest without docker marks) so a status
  badge is honest.
- Add screenshots and demo gif to the v1.0 release notes too.
