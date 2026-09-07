# Remote operations and deploy design

Date: 2026-09-08
Status: approved, ready for implementation planning

## What this is about

Yesterday a day was lost to "SSH is broken". SSH was not broken. The corporate
VPN on the laptop blocks SSH, and the box was healthy the whole time. While
proving that, four real faults turned up on the server, including one that had
silently disabled all health alerting for a week.

This document records what was measured, what was decided, and what to build.

## Part 1: why SSH appeared broken

`ssh ubuntu@130.210.55.97` failed with "Connection timed out during banner
exchange". TCP to port 22 completed, so something answered the handshake and
then never spoke.

What was measured:

- The laptop's default route goes through `utun4` at a `10.183.x.x` gateway,
  which is Palo Alto GlobalProtect running as a full tunnel. All traffic,
  including internet traffic, exits through Autodesk.
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
  runs, two things changed: the VPN exit node moved from `134.238.241.162` to
  `165.85.176.122`, and the listener changed from a plain TLS server to socat.
  Which of the two caused the failure was never isolated.

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
`/opt/stock-agent/scripts/health-watchdog.sh`. That path is inside the git
checkout, and the script is not tracked in the repository.
`infra/scripts/deploy.sh:11` runs `git reset --hard origin/main`, which removed
it.

The file is gone. systemd has been failing `stock-agent-watchdog.service` with
`203/EXEC` every five minutes ever since. No container-down alert, no CPU,
memory or disk alert has been possible for about a week.

The same trap applies to `.env` and `.env.docker`, both of which live inside the
checkout. They survive today only because git leaves untracked files alone
unless `git clean` runs, which nothing currently does.

### Nothing reports the watchdog's own failure

The watchdog alerts on container and resource problems, but its own failure is
silent. It failed 200-odd times without a single notification.

### The container is not started by systemd on boot

`stock-agent.service` is `disabled`. After the reboot the container came back
only because of Docker's restart policy, and `docker-compose.yml:9` sets
`restart: on-failure:3`, which stops trying after three failures and does not
restart a container that exited cleanly.

### The server is running stale code and stale configuration

- Server HEAD is `f136fb3`, from before the history rewrite. Local `main` is
  `9b3ec80`. `git fetch` plus `git reset --hard origin/main` handles the
  divergence; an ordinary `git pull` will not.
- `.env` on the server has no `LLM_PROVIDER` line and still sets both
  `TIER1_MODEL` and `TIER2_MODEL` to `minimax/minimax-m3:free`, so it is on
  OpenRouter, not Gemini.
- `WATCHED_CHANNELS` is already correct at `-1004323736609,8718990088`.

This drift is a direct consequence of `.env` being edited by hand over SSH in
one place and maintained locally in another.

### Terraform would destroy the instance

`infra/compute.tf:23` takes `data.oci_core_images.ubuntu.images[0].id` from a
data source sorted newest-first. When Canonical publishes a newer 24.04 image,
`terraform apply` selects it and plans to replace the instance. The reserved
public IP is whitelisted with INDstocks, so an unplanned replacement is
expensive.

### The firewall rule is broader than it reads

`infra/network.tf:34` describes its ingress rule as "SSH from home IP". Because
the laptop was on the full-tunnel VPN when the rule was last set, the value was
`134.238.241.162/32`, which is Autodesk's shared egress. Port 22 was open to
everyone on the corporate network, not to one house.

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

Six changes. The ordering is deliberate: alerting first, because until it works
there is no way to know whether anything else regressed.

### 1. Move generated files out of the git checkout

Root-cause fix for the dead watchdog.

- Watchdog script moves to `/usr/local/bin/stock-agent-watchdog`
- `.env` and `.env.docker` move to `/etc/stock-agent/`
- `docker-compose.yml` reads `env_file: /etc/stock-agent/.env`
- Both systemd units reference the new paths
- `/opt/stock-agent` holds only the git checkout and the `data/` volume

After this, no deploy can delete operational files, and `git clean -fd` becomes
safe to add later.

### 2. Alert when the watchdog itself fails

- `stock-agent-watchdog.service` gets
  `OnFailure=stock-agent-alert@%n.service`
- `stock-agent-alert@.service` is a template unit that sends one Telegram
  message naming the failed unit
- A daily heartbeat message at a fixed time, so silence becomes evidence of a
  problem rather than evidence of health

The alert sender must not depend on anything inside `/opt/stock-agent`, or fault
1 comes back through a different door.

### 3. Make the container start on boot and stay up

- `systemctl enable stock-agent.service`
- `docker-compose.yml` changes `restart: on-failure:3` to `restart: unless-stopped`
- Watchdog keeps its existing container check as the backstop

### 4. Ship container logs to OCI Logging

This is what removes the shell from routine work, and it is the one path that
works with the VPN connected.

- A log group and a custom log, created in Terraform
- The already-running Custom Logs Monitoring plugin tails the container's
  json-file log
- A dynamic group matching the instance, and an IAM policy letting it write log
  content, so no credentials are stored on the box
- A wrapper script, `infra/scripts/logs.sh`, running `oci logging-search
  search-logs` with a sensible default time window

Docker's log rotation stays as it is: `max-size: 10m`, `max-file: 3`.

### 5. One command to deploy from the laptop

- `infra/scripts/deploy-remote.sh` connects over SSH and runs the existing
  `deploy.sh` on the box, streaming its output back
- `.env` is generated from a single source rather than hand-edited on the
  server, so drift like the current OpenRouter and `minimax` values cannot recur
- `ssh-connect.sh` gains a preflight check: if the default route is a
  GlobalProtect tunnel, print a plain instruction to disconnect and stop, rather
  than hanging for 30 seconds

The Telegram session file still needs a one-off interactive login inside the
container, as recorded in `infra/DEPLOY_STATUS.md`. That stays a manual,
VPN-off task and is not automated.

### 6. Terraform correctness

- Pin the Ubuntu image OCID, replacing the newest-first data source lookup
- Correct the ingress rule description so it says what it does
- Keep `oci_core_public_ip.stock_agent` out of any destroy, as it is whitelisted
  with the broker

## Data flow after the changes

Deploying: laptop, VPN off, runs `deploy-remote.sh`. That SSHes in and runs
`deploy.sh`, which fetches, hard-resets to `origin/main`, rebuilds and restarts
the container. Generated files in `/etc/stock-agent` and `/usr/local/bin` are
untouched.

Watching: the container writes to stdout. Docker captures it. The logging agent
forwards it to OCI Logging. The laptop reads it with `logs.sh` over HTTPS, VPN
on or off, no shell involved.

Being told: the watchdog runs every five minutes and messages Telegram on
container, CPU, memory or disk problems. If the watchdog fails, `OnFailure`
messages Telegram. If everything is fine, one heartbeat a day.

Break-glass: if sshd genuinely dies, the OCI web console's serial console works
in a browser over HTTPS. That needs a password on a login account, which cloud
images do not have by default. Setting one is listed as an open question below,
not a decision.

## Error handling

The failure that must not repeat is a monitoring path that fails silently. Each
new piece needs its own answer:

- Watchdog fails: `OnFailure` Telegram message
- Alert sender fails: cannot be caught in-band. The daily heartbeat covers it;
  no heartbeat for a day means investigate
- Logging agent stops forwarding: `logs.sh` returning nothing recent is the
  signal
- Deploy fails: `deploy.sh` already exits non-zero and prints 30 lines of
  container log; `deploy-remote.sh` must propagate that exit code
- Telegram unreachable: alerts are lost with no queue. Accepted, since the
  heartbeat bounds how long a problem can hide

## Testing

Each item has to be verified by breaking something, not by reading
configuration.

1. Kill the container. A Telegram alert arrives and it restarts.
2. Rename the watchdog script. A Telegram alert names the failed unit.
3. Run a deploy twice. The watchdog script and `.env` still exist afterwards.
   This is the regression test for the original fault.
4. Reboot the box. The container returns without help, and `systemctl
   is-system-running` reports `running`, not `degraded`.
5. Run `logs.sh` with the VPN connected. Recent log lines come back.
6. Run `terraform plan`. It reports no changes, and specifically no instance
   replacement.
7. Run `ssh-connect.sh` with the VPN connected. It refuses immediately with an
   instruction, rather than hanging.
8. Wait for the heartbeat window. One message arrives.

## Out of scope

- Any tunnel, proxy or VPN workaround for the corporate filter
- Cloudflare Tunnel and Tailscale
- Moving the instance to a private subnet, which the broker IP whitelist
  prevents
- Diagnosing OCI Run Command
- Secrets in OCI Vault or Object Storage; `.env` from one local source is
  enough for a single-operator setup
- Automating the interactive Telegram session login
- Any change to trading logic

## Open questions

1. Serial console break-glass needs a password on an account, which means
   choosing where that password lives and accepting that it exists. Worth doing,
   but it is a security decision rather than a bug fix, and it is not settled.
2. The stale `.env` on the server needs correcting for Gemini as part of the
   first deploy. The values are in `docs/HANDOFF_2026-09-07.md`. Whether that
   happens before or as part of change 5 is an implementation choice.
</content>
</invoke>
