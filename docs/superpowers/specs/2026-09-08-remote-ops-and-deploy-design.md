# Remote operations and deploy design

Date: 2026-09-08
Status: scope narrowed to two goals after review. Terraform safety and log
access are built; everything else is recorded but not being done.

This file is tracked in a public repo, so it does not name the box's IP address
or any corporate network address. Those live in `infra/DEPLOY_STATUS.md`, which
is gitignored for that reason.

## What this is about

Yesterday a day was lost to "SSH is broken". SSH was not broken. The corporate
VPN on the laptop blocks SSH, and the box was healthy the whole time. While
proving that, several real faults turned up on the server, including one that
had silently disabled all health alerting for a week.

This document records what was measured, what was decided, and what to build.

## Part 1: why SSH appeared broken

`ssh ubuntu@<box-ip>` failed with "Connection timed out during banner
exchange". TCP to port 22 completed, so something answered the handshake and
then never spoke.

What was measured:

- The laptop's default route goes through `utun4` at a `10.183.x.x` gateway,
  which is Palo Alto GlobalProtect running as a full tunnel. All traffic,
  including internet traffic, exits through the corporate network.
- SSH to `github.com:22` works. SSH to `gitlab.com:22` and `bitbucket.org:22`
  fail identically to the box. GitHub is on an allowlist; everything else is
  blocked.
- SSH to `altssh.gitlab.com:443` also fails. The filter recognises the SSH
  protocol and blocks it on any port, so moving sshd to another port does not
  help.
- The OCI Bastion service host, an unrelated machine on Oracle's network, fails
  the same way. That was the finding that ruled out the box.
- HTTPS to arbitrary hosts works, and certificates come back with their real
  issuers (Cloudflare, GoDaddy, DigiCert), so TLS is passed through without
  interception.

With the VPN off, SSH to the box worked on the first attempt.

The box was never unhealthy. During the outage OCI Monitoring reported CPU
under 1.5% and memory at 10%. Once connected: disk 9% used, 574 MB of 5903 MB
memory in use, `ssh.socket` and `ssh.service` both active, clean boot log.

### The tunnel idea, and why it was dropped

Because TLS passes untouched, SSH can be wrapped inside TLS on port 443. This
was tested rather than assumed:

- A self-signed TLS listener on the box, port 443, handshook successfully with
  the VPN on, both with and without SNI.
- A socat TLS-to-SSH proxy on 443 gave a working `ssh` session with the VPN off.
- The same test with the VPN on failed. But between the passing and failing
  runs, two things changed: the VPN moved to a different corporate exit node,
  and the listener changed from a plain TLS server to socat. Which of the two
  caused the failure was never isolated.

Decision: abandon the tunnel. The owner is content to disconnect the VPN when
hands-on work is needed. A channel that depends on which corporate exit node
you happen to land on is not worth maintaining, and Cloudflare Tunnel was
rejected as it needs a domain, an account, and a third party in the path of
trading infrastructure.

Consequence: SSH stays the hands-on path, with the VPN off as an accepted
precondition. Everything routine must stop requiring a shell.

### Cleanup owed on the box

The test left artefacts. Port 443 was already removed from the OCI security
list, so none of this is reachable from the internet, but it must still be
removed:

- `tlsproxy.service` and `tlstest.service`, transient units created with
  `systemd-run`
- the local iptables rule `-A INPUT -p tcp --dport 443 -m state --state NEW -j ACCEPT`
- `/tmp/tlstest/` containing a self-signed key and certificate
- the `socat` package, installed for the test

## Part 2: faults found on the server

### Health alerting has been dead since the first deploy

`infra/cloud-init/setup.sh:70` writes the watchdog script to
`/opt/stock-agent/scripts/health-watchdog.sh`. The file is not there now, and
systemd has been failing `stock-agent-watchdog.service` with `203/EXEC` every
five minutes for about a week. No container-down alert, no CPU, memory or disk
alert has been possible in that time.

Why the file is missing is not yet known. An earlier draft of this document
blamed `git reset --hard origin/main` in `infra/scripts/deploy.sh:11`. That is
wrong: a hard reset only touches files git tracks, and this script has never
been tracked (`git log --all -- '*health-watchdog*'` is empty), so the reset
cannot have removed it.

The likelier explanation is that it was never written. `setup.sh:2` sets
`set -euo pipefail`, so if anything earlier in the script failed, provisioning
stopped before line 70. And until the escaping was fixed, the `setup.sh` in the
repo could not be rendered by Terraform at all (see the next fault), so the box
was built from an older copy of it.

`203/EXEC` also happens when the file exists but is not executable, or is not
readable by the user running it. `setup.sh:150-151` makes it mode 700 owned by
`stockagent`.

First implementation step, before writing any code: read
`journalctl -u stock-agent-watchdog.service --no-pager | head -50` for the first
failure, `ls -l /opt/stock-agent/scripts/` to see whether the file is absent or
just not executable, and `/var/log/cloud-init-output.log` to see whether
provisioning finished. Everything else in Part 3 is worth doing regardless, but
do not call any of it the fix until this is answered.

### Terraform cannot render the cloud-init file

`infra/compute.tf:34` passes `cloud-init/setup.sh` through Terraform's
`templatefile()`, which treats `${...}` as its own syntax. Two shell expansions
were left unescaped:

- `setup.sh:103` — `${COOLDOWN_TS%%.*}`
- `setup.sh:106` — `${REMAINING}s`

Three others further down are correctly written as `$${...}` at lines 135, 141
and 147, so the rule was known and then missed twice. Until these two are
fixed, `terraform plan` and `terraform apply` both error out, and nothing on
the Terraform side of this plan can be done.

### Generated files live inside the git checkout

`.env`, `.env.docker` and the watchdog script all sit under `/opt/stock-agent`,
which is a git checkout that gets hard-reset on every deploy. Untracked files
survive a hard reset, so nothing is being deleted today, but it means
`git clean -fd` can never be used, and the layout is one command away from
losing operational files.

### Nothing reports the watchdog's own failure

The watchdog alerts on container and resource problems, but its own failure is
silent. It failed 200-odd times without a single notification.

### The watchdog quietly does nothing when `.env` is missing

`setup.sh:76-78` exits with status 0 if `/opt/stock-agent/.env` is not there.
systemd records that as success, so no alert fires and nothing looks wrong.
That is a second way for monitoring to be dead while everything reports
healthy, and moving `.env` is the most likely thing to trigger it.

### The container is not started by systemd on boot

`stock-agent.service` is `disabled`; `setup.sh:179-182` enables only the
watchdog timer. After the reboot the container came back only because of
Docker's restart policy, and `docker-compose.yml:8` sets `restart: on-failure:3`,
which stops trying after three failures and does not restart a container that
exited cleanly.

### Changes to cloud-init cannot reach the running box

Both systemd units, the watchdog script and the `.env.docker` file are all
written by `infra/cloud-init/setup.sh`. Cloud-init runs once, at first boot.
Editing that file and running `terraform apply` changes instance metadata and
provisions nothing; the only way it takes effect is replacing the instance,
which is exactly what we are trying to avoid.

`infra/scripts/post-setup.sh` is a one-shot migration for an older layout. It
does not write systemd units or the watchdog. So today there is no way to apply
any of Part 3 to the box that needs it.

Worse than that: editing `setup.sh` changes `user_data`, which is part of
`oci_core_instance.metadata`, and OCI cannot change metadata on a running
instance. `terraform plan` therefore reports "must be replaced". This was
confirmed by running it. So the file cannot be edited safely at all until the
instance has `lifecycle { ignore_changes = [metadata] }`.

### The server is running stale code and stale configuration

- Server HEAD is `f136fb3`, from before the history rewrite. Local `main` is
  `9b3ec80`. `git fetch` plus `git reset --hard origin/main` handles the
  divergence; an ordinary `git pull` will not.
- `.env` on the server has no `LLM_PROVIDER` line and still sets both
  `TIER1_MODEL` and `TIER2_MODEL` to `minimax/minimax-m3:free`, so it is on
  OpenRouter, not Gemini. `config.py:13` defaults `LLM_PROVIDER` to
  `openrouter`, so this does not crash. It runs the wrong models quietly, and
  nothing in the monitoring design would notice.
- `WATCHED_CHANNELS` is already correct at `-1004323736609,8718990088`.

This drift is a direct consequence of `.env` being edited by hand over SSH in
one place and maintained locally in another.

### Terraform would destroy the instance

`infra/compute.tf:23` takes `data.oci_core_images.ubuntu.images[0].id` from a
data source sorted newest-first. When Canonical publishes a newer 24.04 image,
`terraform apply` selects it and plans to replace the instance.

Replacement is worse than it first looks. The reserved public IP is whitelisted
with INDstocks, and `data/` lives on the boot volume, which is destroyed with
the instance. That means the SQLite database, the message watermarks and the
Telegram session file, and recovering the session file needs an interactive
login that this plan deliberately leaves manual. The only current protection is
the weekly backup policy at `compute.tf:68-71`.

### Port 22 gets reopened on every connect

`infra/network.tf:34` describes its ingress rule as "SSH from home IP", but the
description is not the problem. `infra/scripts/ssh-connect.sh:9` reads the
laptop's current public IP from `ifconfig.me`, and lines 28-44 rewrite the OCI
security list to it on every run. Because the laptop was on the full-tunnel VPN
last time, the value became the corporate shared egress, so port 22 was open to
everyone on the corporate network rather than to one house.

Two consequences. Fixing only the description leaves the behaviour intact and
gets overwritten on the next connect. And because the script edits a
Terraform-managed resource behind Terraform's back, `terraform plan` will
always show a pending firewall change.

### OCI Run Command is unusable

The Oracle Cloud Agent plugin reports `RUNNING`, and OCI Monitoring metrics
arrive on time, so the agent is alive and has working egress. But two commands
submitted through `oci instance-agent command create`, one before the reboot and
one after, both sat at `lifecycle-state: ACCEPTED` with
`delivery-state: VISIBLE` and were never executed.

This matters because Run Command was the only shell-like path that works over
HTTPS. It cannot be relied on, and diagnosing it needs a shell, so it is
deliberately not part of any path below.
## Part 3: what to build

Two goals, decided by the owner after reading the review: `terraform apply`
must be safe to run, and container logs must be readable from the laptop
without opening a shell on the box. Deploying stays exactly as it is today:
disconnect the VPN, SSH in, run `deploy.sh`. Everything else found in Part 2 is
recorded above but deliberately not being built.

The VPN is not part of any solution here. It is only ever a thing to turn off
before SSH. Nothing below should be read as "this is how you work with the VPN
connected".

### 1. Fix the cloud-init escaping

Two characters. `${COOLDOWN_TS%%.*}` becomes `$${COOLDOWN_TS%%.*}` at
`setup.sh:103`, and `${REMAINING}s` becomes `$${REMAINING}s` at `setup.sh:106`.
Terraform reads `${...}` as its own syntax, so a shell variable in brace form
has to be doubled. Plain `$VAR` needs nothing.

Nothing else Terraform-related works until this is fixed.

### 2. Stop `terraform apply` from destroying the box

Four small changes in the Terraform files, all confirmed by running
`terraform plan` against the live box.

- `lifecycle { ignore_changes = [metadata] }` on
  `oci_core_instance.stock_agent`. `user_data` is part of `metadata`, and OCI
  cannot change metadata on a running instance, so any edit to `setup.sh` plans
  a replacement. Cloud-init only matters at first boot anyway.
- Pin the Ubuntu image OCID, replacing the newest-first data source lookup. Use
  the value the instance was actually built from, which is
  `source_details.source_id` in `terraform.tfstate`. Pinning today's newest
  24.04 image would plan the replacement we are trying to prevent.
- `lifecycle { prevent_destroy = true }` on `oci_core_public_ip.stock_agent`.
  That address is whitelisted with INDstocks and is not recoverable.
- `lifecycle { ignore_changes = [ingress_security_rules] }` on the security
  list, because `ssh-connect.sh` rewrites that rule on every connect. Without
  it, `terraform plan` never comes back clean.

Before this, `terraform plan` said "must be replaced" and would have taken the
boot volume with it, including `data/`. After it, the plan adds five resources
and destroys nothing.

### 3. Ship container logs to OCI Logging

Routine log reading should not need a shell at all. This path is an HTTPS call
to Oracle from the laptop, so there is nothing to disconnect and nothing to
open in the firewall.

New file `infra/logging.tf`:

- a log group and a custom log
- an `oci_logging_unified_agent_configuration` telling the already-running agent
  what to read. Without this resource nothing is forwarded.
- the path is a glob, `/var/lib/docker/containers/*/*-json.log`. The container
  ID is in the filename and changes on every rebuild.
- the parser is `JSON`, because Docker wraps each line in a JSON envelope. It
  must also set `field_time_key = "time"`, `time_type = "STRING"` and
  `time_format = "%Y-%m-%dT%H:%M:%S.%N%z"`. Fluentd's JSON parser defaults
  `time_type` to float, so it reads Docker's `"2026-09-08T..."` as the number
  2026 and stamps every record at 1970-01-01T00:33:46. Ingestion returns 200,
  the console shows a healthy log, and no search over a recent window ever
  finds anything. This cost most of a session to find.
- a dynamic group matching the instance and a policy letting it write log
  content, so no credentials sit on the box. These are identity resources, so
  they are created through a second provider pinned to the tenancy home region.

Plus `infra/scripts/logs.sh`, which reads them back:

```
./infra/scripts/logs.sh        last 30 minutes
./infra/scripts/logs.sh 6h     last 6 hours
```

Docker's log rotation stays as it is: `max-size: 10m`, `max-file: 3`.

### 4. Do not hang when the VPN is on

`ssh-connect.sh` checks the default route first. If it goes through a
GlobalProtect tunnel it prints one line and stops.

This is not only about the 30-second hang. That script reads the laptop's
current public IP and rewrites the OCI security list to it. With the VPN on,
that IP is the corporate shared egress, which is how port 22 came to be open to
everyone on the corporate network. The check has to come before the firewall
update, not just before the `ssh` call.

## Data flow after the changes

Deploying: unchanged. VPN off, `ssh-connect.sh`, then `deploy.sh` on the box,
which fetches, hard-resets to `origin/main`, rebuilds and restarts.

Watching: the container writes to stdout. Docker captures it. The OCI logging
agent forwards it. The laptop reads it with `logs.sh` over HTTPS, no shell
involved.

Permissions from the laptop: nothing to grant. The OCI CLI on the laptop
authenticates as `pjagshettiwar@gmail.com`, which is the tenancy administrator,
so it can already run `terraform apply` and read every log. The only thing SSH
needs is the VPN off, and that is a corporate network restriction, not a
missing permission.

Infrastructure: `terraform apply` is now safe to run. It will not replace the
instance, cannot delete the reserved IP, and ignores the firewall rule that
`ssh-connect.sh` manages.

## What is knowingly left broken

These are real and were verified. They are not being fixed now, and the reason
is the same for all of them: they are not needed for the two goals above.

- Health alerting is dead. `stock-agent-watchdog.service` fails every five
  minutes with `203/EXEC`, so no container, CPU, memory or disk alert has fired
  for about a week. The cause is now known: `/opt/stock-agent/scripts/` does not
  contain `health-watchdog.sh` at all. `/var/log/cloud-init.log` records
  `Failed to run module scripts_user` at 2026-08-30 06:53:49, so the first-boot
  script did not finish. The box is unmonitored.
- The watchdog's own failure is silent, and it exits 0 when `.env` is missing,
  so systemd records success either way.
- `stock-agent.service` is disabled, so after a reboot the container comes back
  only through Docker's restart policy, which gives up after three failures.
- `.env` on the server is still edited by hand and still points at OpenRouter
  and `minimax`. `config.py:13` defaults `LLM_PROVIDER` to `openrouter`, so this
  does not crash. It quietly runs the wrong models.
- `.env` and the watchdog script still live inside the git checkout, so
  `git clean -fd` can never be run there.
- Cloud-init still only runs at first boot, so the escape fix above changes
  nothing on the current box. It matters the next time one is built. Anything
  that has to change on the running box has to be done over SSH by hand.

## Testing

Verified on the laptop, nothing touched the box:

1. Terraform renders `cloud-init/setup.sh` without error and the result is
   valid bash. This was the failing case before the escape fix.
2. `terraform plan` against the live box adds only the five logging resources.
   No instance replacement, nothing destroyed, no security list change.
3. `ssh-connect.sh` refuses to run while the default route is a VPN tunnel.
   Checked against a live GlobalProtect connection.

4. Applied, then found broken: the agent shipped every record successfully and
   nothing was searchable, because of the timestamp bug in section 3. The check
   that catches it is `sudo grep default_log_entry_time
   /var/log/unified-monitoring-agent/unified-monitoring-agent.log` on the box.
   A 1970 date there means the parser time settings are wrong. A 200 response
   proves nothing.

5. `logs.sh 4h` returns real container output from the laptop with no SSH.
   Confirmed after the parser fix. `logs.sh` also had to read the `msg` field
   rather than `log`.

Still to check:

6. Deploy once and run `logs.sh` again, to confirm the glob still matches after
   the container ID changes.

Note on rollout: `terraform apply` changes the config in Oracle's service, but
the box caches its generated fluentd config. Restarting
`unified-monitoring-agent` does not refresh it. Start
`unified-monitoring-agent_config_downloader.service` instead.

## Out of scope

- Any tunnel, proxy or VPN workaround for the corporate filter
- Cloudflare Tunnel and Tailscale
- Moving the instance to a private subnet, which the broker IP whitelist
  prevents
- Diagnosing OCI Run Command
- Secrets in OCI Vault or Object Storage
- Pull-based or one-command deploys; SSH with the VPN off is the accepted path
- Automating the interactive Telegram session login
- Any change to trading logic

## Open questions

1. Serial console break-glass needs a password on an account, which means
   choosing where that password lives and accepting that it exists. Worth doing,
   but it is a security decision rather than a bug fix, and it is not settled.
2. The stale `.env` on the server needs correcting by hand over SSH. Values are
   in `docs/HANDOFF_2026-09-07.md`. Nothing automates this now.
3. `data/` sits on the boot volume with only a weekly backup. If losing up to a
   week of trade history and the Telegram session is not acceptable, that needs
   its own change. Not decided.
4. The SSH ingress rule is now owned by `ssh-connect.sh`, with Terraform told
   to ignore it. If that ever feels wrong, the alternative is having the script
   write the CIDR back to `terraform.tfvars`.
