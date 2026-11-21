# Secrets never pass through the command line in plain sight

A secret given as `name=value` lands in shell history and in the process list, so
kelso refuses it outright rather than warning. A secret is set with `name=-`,
which prompts without echo on a terminal and reads stdin otherwise, so one call
sets at most one secret; `kelso config <app> --edit` sets several. The same rule
holds for app secrets and host secrets.

Reading one back is a separate, deliberately named command, `kelso secret
reveal`, rather than a flag on `config`, so printing a secret is never a side
effect of asking for config.
