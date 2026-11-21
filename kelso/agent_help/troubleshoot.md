# An app is not running, or not healthy

Start with `kelso ps`, then `kelso system doctor`. Doctor's findings each name
their fix.

| `kelso ps` says | Means | Do |
| --- | --- | --- |
| `running (degraded)` | A unit is down or failing its healthcheck | `kelso status <app>`, `kelso logs <app> --tail 50`, then `kelso start <app>` |
| `stopped`, last action `start-failed` | The last start did not finish | `kelso system activity <app> --show` for why, fix it, `kelso start <app>` |
| `stopped`, last action `stopped` | Someone ran `kelso stop` | Leave it unless asked |
| `finished` | Every unit exited cleanly | Normal for a one-shot app; otherwise `kelso start <app>` |

`kelso start` on an app that is partly up brings up the units that are down
and leaves the rest alone.

## After a reboot

kelsod starts every app whose last action was `started`. An app whose last
action was `start-failed` is left alone and needs `kelso start <app>`.

## A route does not answer

If `kelso system doctor` says an app is "running, but its routes are not
published", its route provider did not answer when the app started. The app is
up; run `kelso start <app>` again once the provider is reachable.

1. `kelso status <app>` shows the route as `container port <- host address
   <- public URL`. Fetch the host address from the box. If that fails, the app
   is the problem: read its logs.
2. If the host address answers and the public URL does not, the route provider
   is the problem: `kelso route check <provider tag>`.

## Docker Desktop only

`bind source path does not exist` for a folder that is there: Docker Desktop
has a stale view of a run directory that was deleted and recreated. Bind its
parent once with `docker run --rm -v <dir>:/x alpine ls /x`, then start again.
