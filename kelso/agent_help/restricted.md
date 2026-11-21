# Find what a restricted app needs to reach

An app with `network_mode = "restrict"` reaches only the domains its manifest
lists under `[connections]`. To work out that list:

1. Run the app in `watch` mode, which lets everything through and counts it:
   `kelso config <app> network.mode=watch`, then `kelso restart <app>`. Under
   `kelso dev`, set `network_mode = "watch"` in the manifest instead.
2. Use the app. Do the things that fetch from outside: metadata lookups,
   plugin lists, update checks. Apps cache, so start from a fresh container.
3. Read `kelso net <app>`: one row per domain, with connections and bytes.
   `kelso dev` prints the same table when it stops, with a `[connections]`
   line for each domain the manifest does not list.
4. Copy the domains into the manifest's `[connections]`, set the mode back
   (`kelso config <app> network.mode=default`), and restart.

`restrict` itself tells you nothing: a refused name fails at its DNS lookup and
is never counted. A domain covers its subdomains, so list the root.

Behind kproxy an app sees every client as kproxy's address, and only HTTP and
HTTPS go out. An app that decides things by client IP should stay `open`.
`kelso agent-help manifest` has the details under "Network modes".
