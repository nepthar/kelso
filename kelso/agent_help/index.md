# Driving kelso as an agent

Kelso runs apps on this machine. An app is loaded from a bundle (a `.klso`
folder or `.klso.md` file) and named by its app id. These pages describe the
kelso that is installed here; prefer them to anything on the web.

## Rules that hold for every command

- **Results go to stdout, narration to stderr.** A table, a path or a value is
  on stdout. Progress, warnings and errors are on stderr.
- **Exit codes.** 0: done. 1: failed, and something may have changed. 2: kelso
  refused before changing anything (a question it could not ask, or a usage
  error); the message names what to pass.
- **Kelso never waits for an answer you cannot give.** A command that would ask
  `[y/N]` refuses with exit 2 when there is no terminal. Pass `--yes` once you
  have read what it is about to do.
- **An error names its fix.** Read the whole message before trying something
  else. Do not reach for `--force` or `rm --purge` unless the message offers it
  and you understand why: `--purge` deletes the app's data.
- **Never handle secrets.** Do not read, print or pass secret values. A person
  sets them with `kelso system secret <name>` or `kelso config <app> --edit`.
- **Do not edit files under `var/` or `conf/`** in the kelso root. Change
  things with commands. `config.toml` is the one file meant to be edited.

## Finding your way

```bash
kelso --version           # version and the git commit this install is from
kelso root                # the kelso root; everything lives under it
kelso status              # host, apps, routes, storage, last backup
kelso ps                  # loaded apps and their state
kelso inspect <app>       # one app: containers, ports, routes, volumes, config
kelso logs <app> --tail 50
kelso system doctor       # what is wrong on this box, with the fix for each
kelso system activity     # what kelso did recently; --show N prints a run's log
kelso repo list           # every app that can be started here
```

Docker containers kelso did not start are invisible to these. `docker ps -a`
shows everything on the box.

## Over ssh

Run kelso on another box with `ssh <host> '<command>'`. If that says `kelso:
command not found`, kelso is in `~/.local/bin`, which a non-interactive shell
does not add: prefix the command with `export PATH=$HOME/.local/bin:$PATH;`.
`kelso deploy` adds that itself.
