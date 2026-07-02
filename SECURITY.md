# Security

If you find a vulnerability in this tool (e.g. a way to make it leak credentials,
write outside its configured directories, or bypass the `promote` confirmation gate),
please report it privately via [GitHub's private vulnerability reporting](../../security/advisories/new)
rather than a public issue. You'll get a response within a week.

Notes on scope:

- This repo contains no live infrastructure, credentials, or real device data — device
  configs and secrets live outside the repo by design (`secrets.env` and the backup
  repo are gitignored). A report that a *fixture* contains a secret is still welcome:
  fixtures are required to be fully sanitized (RFC 5737 addresses, fake hostnames, no
  real hashes) and a lapse there is a bug.
- The tool connects to network devices with credentials you supply at runtime. Treat
  the machine running it, and its `secrets.env`, with the same care as the devices.
