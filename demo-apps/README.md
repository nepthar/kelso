# Demo apps

Small apps that each show off one kelso feature. Every one is a single
`.klso.md` file, so the manifest, scripts and notes are all readable in one
place; open any of them to see how a feature is declared before you use it in
an app of your own.

## Installing them

Add this folder as a repo, then start whichever demo you want:

```bash
kelso repo add github://nepthar/kelso/main/demo-apps --name demos
kelso start hello-world
```

`kelso repo list` shows every app the repo carries. A started demo is an
ordinary kelso app: `kelso logs <app>`, `kelso cmd <app>` and the web UI all
work on it. When you are done, `kelso unload <app>` removes a demo and
`kelso repo remove demos` removes the repo.

## What they show

| App | Feature |
| --- | --- |
| [hello-world](net.klso.hello-world.klso.md) | The smallest possible app: one container, no config |
| [demo-markdown](net.klso.demo-markdown.klso.md) | Distributing an app as a single markdown file |
| [demo-config](net.klso.demo-config.klso.md) | `[config]` values and secrets, set with `kelso config` or `--set` |
| [demo-volumes](net.klso.demo-volumes.klso.md) | Volume kinds, and binding a host directory with `--bind` |
| [demo-routes](net.klso.demo-routes.klso.md) | Primary, secondary and private routes, and route providers |
| [demo-cron](net.klso.demo-cron.klso.md) | `[commands]` and `[cron]`: run by hand, on a schedule, or detached |
| [demo-backup](net.klso.demo-backup.klso.md) | A volume that changes constantly, to back up and restore |
| [demo-compose](net.klso.demo-compose.klso.md) | `[run.<unit>.compose]` for compose options kelso doesn't model |
| [demo-danger](net.klso.demo-danger.klso.md) | Compose keys kelso warns about before loading |
