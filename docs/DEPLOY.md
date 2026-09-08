# Deploying

The agent runs in Docker on one Oracle Cloud VM in Mumbai. Deploying means
pulling `main` on that box and rebuilding the container. There is no CI and no
pull-based deploy; you do it by hand over SSH.

## Before you start

Disconnect the corporate VPN. It blocks SSH on every port, and it is never part
of the answer here. Do not build a tunnel or proxy around it. If your default
route goes through a `utun*` interface, `ssh-connect.sh` will refuse to run and
tell you so.

This also matters for safety, not just convenience. `ssh-connect.sh` rewrites
the OCI firewall rule to your current public IP. On the VPN that IP is the
shared corporate exit, which would open port 22 to everyone on the corporate
network.

Everything else you need is already granted. The OCI CLI on the laptop
authenticates as the tenancy administrator, so `terraform apply` and log reads
work with no extra setup.

## Deploy

1. Disconnect the VPN.
2. Merge your change to `main` and confirm it is there: `git log --oneline -1 origin/main`.
3. Run `./infra/scripts/ssh-connect.sh`. It prints your IP, updates the firewall
   rule if needed, and drops you on the box.
4. Run `sudo -u stockagent /opt/stock-agent/infra/scripts/deploy.sh`. It fetches
   `main`, hard-resets to it, rebuilds the image and restarts the container.
5. Wait for `==> Deploy successful. Container is running.` Anything else and it
   prints the last 30 log lines and exits non-zero.
6. Log out and confirm from the laptop with `./infra/scripts/logs.sh 15m`. See
   [Reading logs](LOGS.md).

You can also do it in one line without an interactive session:

```bash
./infra/scripts/ssh-connect.sh 'sudo -u stockagent /opt/stock-agent/infra/scripts/deploy.sh'
```

## Never run compose as root

Run `deploy.sh`, not `docker compose` directly. `docker-compose.yml` sets
`user: "${DOCKER_UID:-1000}:${DOCKER_GID:-1000}"`. As root that resolves to UID
1000, but `data/` belongs to `stockagent`, so the app dies with:

```
sqlite3.OperationalError: attempt to write a readonly database
```

That looks like a database bug and is not one. `deploy.sh` sets `DOCKER_UID`
from `id -u` and writes `.env.docker`, which the health watchdog also reads.

## Changing infrastructure

Terraform runs from the laptop, not the box:

```bash
cd infra && terraform plan     # read this before applying
terraform apply
```

`terraform apply` is safe to run. The instance and the reserved public IP both
have `prevent_destroy`, `metadata` is ignored so editing `cloud-init/setup.sh`
does not plan a replacement, and the ingress rule is ignored because
`ssh-connect.sh` rewrites it on every connect. The reserved IP is whitelisted
with INDstocks and is not recoverable, so treat any plan that touches it as a
bug in the change, not something to push through.

Two things Terraform will not do for you:

Cloud-init only runs at first boot. Editing `infra/cloud-init/setup.sh` changes
nothing on the running box. It matters the next time one is built. Anything
that has to change on the live box has to be done over SSH by hand.

Logging config is cached on the box. See [Reading logs](LOGS.md) for how to push
a change through.

## Do not rebuild the box

`data/` on the boot volume holds the SQLite database and two Telegram session
files. The sessions can only be recreated by an interactive login. If a rebuild
ever is genuinely necessary, copy `/opt/stock-agent/data/` off the box first.

## What restarts the container

`restart: unless-stopped` in `docker-compose.yml`. It used to be
`on-failure:3`, which meant a few minutes of Telegram trouble killed the agent
permanently.

`stock-agent-watchdog.timer` also runs every five minutes. It restarts the
container if it is down, alerts on high CPU, memory or disk, and skips the
restart while a broker auth cooldown is in force. The script lives at
`/opt/stock-agent/scripts/health-watchdog.sh` on the box and is written by
cloud-init from the template in `infra/cloud-init/setup.sh`.

If the watchdog fails with `203/EXEC`, that script is missing. Render it and
put it back:

```bash
python3 - <<'PY'
import re
tf = open('infra/terraform.tfvars').read()
v = lambda k: re.search(rf'^\s*{k}\s*=\s*"([^"]*)"', tf, re.M).group(1)
src = open('infra/cloud-init/setup.sh').read()
block = re.search(r"cat > /opt/stock-agent/scripts/health-watchdog\.sh <<'WATCHDOG'\n(.*?)\nWATCHDOG\n", src, re.S).group(1)
out = block.replace('$${', '\x00').replace('${bot_token}', v('bot_token')).replace('${alert_chat_id}', v('alert_chat_id')).replace('\x00', '${')
open('/tmp/health-watchdog.sh', 'w').write(out + '\n')
PY
./infra/scripts/ssh-connect.sh 'cat > /tmp/wd.sh && sudo install -o stockagent -g stockagent -m 700 /tmp/wd.sh /opt/stock-agent/scripts/health-watchdog.sh && rm /tmp/wd.sh' < /tmp/health-watchdog.sh
./infra/scripts/ssh-connect.sh 'sudo systemctl start stock-agent-watchdog.service && systemctl status stock-agent-watchdog.service --no-pager | head -6'
```

The rendered file contains the real bot token, so keep it out of the repo.
Success looks like `status=0/SUCCESS`.

## Known rough edges

`stock-agent.service` is disabled, so after a reboot the container comes back
only through Docker's restart policy.

`.env` on the box is hand-edited and lives inside the git checkout, so
`git clean -fd` can never be run there. It currently sets `TIER1_MODEL` and
`TIER2_MODEL` to `minimax/minimax-m3:free`.

INDstocks token generation is intermittently flaky. It can return 400 twice and
200 on the third identical attempt. Do not add more retries; the app already has
three and hammering it earns a 429 plus a 30-minute cooldown.
