# Volumes

A volume is a directory an app keeps something in. The app's manifest says what
*kind* of thing each one holds; you decide, once, where each kind lives on your
machine. Kelso puts every app's volumes of a kind under the same root, so one
decision covers every app you will ever load.

## The kinds

| Kind | Holds | Lives in | Backed up |
| --- | --- | --- | --- |
| `data` | State the app must not lose | `volumes/data/<app>/` | Always |
| `bulk` | Large files: media libraries, archives, photo originals | `volumes/bulk/<app>/` | Off by default; turn it on per volume |
| `temp` | Caches and scratch space | `volumes/temp/<app>/` | Never |
| `logs` | Output that can be rotated away | `volumes/logs/<app>/` | Never |
| `host` | A directory you already have, bound by you | wherever you bound it | Never |
| `app` | Files the app's bundle ships | inside the loaded bundle | With the app itself |

### `data`

The app's database, its settings, its users: anything that would hurt to lose.
It is usually small next to `bulk`, and it changes while the app runs, so a
backup stops the app for the moment it reads it, then starts it again. That is
the price of a copy you can trust: a database read while it is being written to
can come back corrupt.

Keep `data` on your fastest, most reliable disk. The default, inside the kelso
root, is usually right.

### `bulk`

Big things that are mostly written once: a movie library, photo originals,
downloaded archives. Too large to copy casually, and often already somewhere
safe, such as a NAS with its own redundancy.

Bulk volumes are not backed up unless you ask, one volume at a time, with the
checkbox on the app's page or `kelso config <app> --backup <volume>=on`. They
are read without stopping the app: files that are written once do not change
under the reader.

`volumes/bulk` is the kind most worth moving to a big disk; see
[Volume storage locations](install.md#volume-storage-locations).

### `temp`

Transcode output, thumbnails, caches: anything the app can make again. Kelso
may clear it while the app is stopped, and never backs it up. An app that puts
something here it cannot rebuild has declared the wrong kind.

### `logs`

What the app writes about itself. Useful while you are looking at a problem,
not worth keeping forever, and never backed up.

### `host`

A directory you already have that an app should see: your media share, a
downloads folder. The manifest asks for one by name and you bind it to a host
volume you declared:

```
kelso config <app> --bind media=photos
```

It is your directory, not the app's, so kelso never backs it up: it may be
terabytes, it may already be backed up, and it is not kelso's to copy. Back it
up the way you back up the rest of that disk.

### `app`

Files that ship inside the app's bundle, such as an init script, mounted
read-only. They come back with the app: a backup keeps the bundle the app was
loaded from.

## What a backup holds

For each app: its `data` volumes, the bulk volumes you turned on, the bundle it
was loaded from, and its configuration and secrets. For kelso itself: its
configuration, kelsodb, and your own apps in the `local` repo. Restoring an
app puts all of that back together, as it was at the moment it was backed up.

Secrets stay encrypted in the backup. Reading them anywhere but this machine
takes your recovery phrase (`kelso system recovery-phrase`).
