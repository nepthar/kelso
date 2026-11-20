# Upgrade kelso on a box

```bash
cd "$(kelso root)"
tar czf ~/kelso-preupgrade-$(date +%Y%m%d).tgz conf config.toml var/run
uv tool install --force "git+https://github.com/nepthar/kelso@<tag or branch>"
kelso --version           # shows the commit now installed
kelso ps                  # must list the apps; see below if it refuses
kelso system service      # rewrites kelsod's unit and restarts it
kelso repo update
kelso ps && kelso system doctor
```

Apps keep running throughout, except those in start group 0, which restart
with kelsod. Loaded apps stay at the version they were loaded at;
`kelso update <app> --yes` moves one to what its repo holds now.

## If `kelso ps` refuses the config

`Extra inputs are not permitted` names a `config.toml` key this version no
longer has. Known renames: `kelso_address` is now `route_address`;
`repos_root` is gone, delete the line. Edit `config.toml` and run it again.

## If an app's id changed in its repo

After `kelso repo update`, an app whose bundle was renamed has no source:
`kelso app which <old id>` prints `<old id> -`. To move it to the new id,
keeping its data, config and routes:

```bash
cd "$(kelso root)"
kelso unload --yes <old>
for k in data temp bulk logs; do
  [ -e volumes/$k/<old> ] && mv volumes/$k/<old> volumes/$k/<new>; done
mv conf/apps/<old>.logtab conf/apps/<new>.logtab
kelso start --force <new>
```

`--force` is right here and `rm --purge` is not: the message offers both, and
purge deletes the data. The app's host port may change; its route is published
again at the same address. Afterwards `kelso cleanup` lists the old id's
leftover route allocation; `--apply` releases it and also deletes unused
images.

## Going back

`uv tool install --force "git+https://github.com/nepthar/kelso@<old commit>"`,
restore `config.toml` from the archive, and `kelso system service`.
