# Jellyfin - Media server

> Jellyfin is the volunteer-built media solution that puts you in control of
> your media. Stream to any device from your own server, with no strings
> attached. ~ https://jellyfin.org

Jellyfin presents and streams your media library. Also consider auditing
where you place your "bulk" data - this kelso app uses a bulk volume to 
place metadata.

## manifest.toml
```toml klso_path="manifest.toml"
[app]
version      = "12.1"
display_name = "Jellyfin Media Server"
description  = "Stream your own movies, shows and music to any device"

[adv_config]
subdomain = { default = "jelly" }

[volumes]
config     = { kind = "data", desc = "Server config, users, playback state" }
metadata   = { kind = "bulk", desc = "Artwork, trickplay images, subtitles" }
cache      = { kind = "temp", desc = "Transcode and image cache; safe to lose" }
tricklplay = { kind = "temp", desc = "Trickplay images so you can seek through the timeline" }
media      = { kind = "host",  desc = "The library itself; bind to the media share", readonly = true }

[run.main]
image   = "jellyfin/jellyfin:12.1"
volumes = { config = "/config", metadata = "/metadata", cache = "/cache", media = "/media", trickplay = "/config/data/trickplay" }

[run.main.routes]
# Pinned rather than kelso-allocated: TVs and phones already point at :8096,
# and jellyfin's own autodiscovery advertises that port.
main = { port = "8096:8096" }

[run.main.compose]
# Run as the owner of the media share rather than root.
user = "1000:1000"

[run.main.env]
# Autodiscovery hands clients this address instead of the container's own.
# kelso fills it in from the `main` route once the app is loaded.
JELLYFIN_PublishedServerUrl = "${routes.main}"
```

## Loading

The media share has to be mounted on the host first — kelso binds a directory,
it does not speak NFS. Mount your media through fstab, autofs, or
a systemd `.mount` unit, declare it in config.toml, then bind the app volume:

```toml
[host_volume.media]
path = "/mnt/my-media"
readonly = true
```

```
kelso config jellyfin --bind media=media
kelso start jellyfin
```

Until that bind exists, `kelso ps` reports jellyfin as needing config and
refuses to start it.

## Hardware transcoding

This bundle does not pass a GPU through. To use Intel or AMD hardware
transcoding (VA-API / QSV), add `devices` to the `[run.main.compose]` table the
manifest already has. TOML allows a table only once, so add the key to it rather
than writing a second `[run.main.compose]`:

```toml
[run.main.compose]
user    = "1000:1000"
devices = ["/dev/dri/renderD128:/dev/dri/renderD128", "/dev/dri/card0:/dev/dri/card0"]
```

`devices` is not on kelso's allowlist, so loading the edited bundle warns about
it and asks before continuing. That is expected: a device is host hardware the
container can reach. The user it runs as must be able to open the device, which
on most distros means being in the `render` group; check with
`ls -l /dev/dri`. Then enable hardware acceleration in Jellyfin's own
dashboard.
