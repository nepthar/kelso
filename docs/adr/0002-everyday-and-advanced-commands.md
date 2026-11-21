# Two tiers of commands: everyday and advanced

The CLI serves two places, a workstation (develop and deploy) and a kelso host
(run and manage), from one binary, and every command runs in both. `kelso help`
lists only what a developer or operator reaches for in a normal week, under
Develop, Apps and Host, and names `kelso help advanced`, which lists only the
rest. There is no third, hidden tier: what kelsod, cron or scripts call is
advanced, not concealed, so an agent can always find it.

## Considered Options

- Namespaces per mode (`kelso dev run`, `kelso host ...`): rejected because app
  commands are the same in both places, so the prefix is noise on every call.
- A separate internal tier hidden from all help: rejected as one more thing to
  decide per command, for no reader who benefits.
