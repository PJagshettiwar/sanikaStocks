# Reading logs and debugging with them

The container writes to stdout, Docker captures it, and Oracle's unified
monitoring agent forwards it to OCI Logging. You read it from the laptop over
HTTPS. No SSH, no firewall change, no VPN consideration either way.

## Read the logs

```bash
./infra/scripts/logs.sh        # last 30 minutes
./infra/scripts/logs.sh 6h     # last 6 hours
./infra/scripts/logs.sh 2d     # last 2 days
```

Windows are `m`, `h` or `d`. The script asks for the newest 1000 lines and
reverses them, so if you hit the cap you lose the oldest lines, not the newest.
OCI rejects any window longer than 14 days.

Expect a delay of two to four minutes. The agent flushes every 180 seconds and
the search index lags a little behind that.

## Where things live

| Thing | Where |
|---|---|
| Container stdout on the box | `/var/lib/docker/containers/*/*-json.log` |
| Forwarding agent log | `/var/log/unified-monitoring-agent/unified-monitoring-agent.log` |
| Generated agent config | `/etc/unified-monitoring-agent/conf.d/fluentd_config/fluentd.conf` |
| Read position | `/etc/unifiedmonitoringagent/pos/*.pos` |
| Pending records | `/opt/unifiedmonitoringagent/run/buffer/` |
| Terraform source of truth | `infra/logging.tf` |

Docker rotates at `max-size: 10m`, `max-file: 3`. Restarting the container makes
a new container ID and a new log file; the glob in `logging.tf` is what keeps
that working, and the agent logs `detected rotation` when it happens.

## Debugging a real failure

1. Read the window around the failure: `./infra/scripts/logs.sh 6h`.
2. Find the first error, not the last. The app crash-loops, so `docker logs`
   and OCI both interleave the tail of one run with the start of the next. The
   traceback you see at the bottom usually belongs to the *previous* run.
3. Watch for a swallowed cause. Telethon in particular raises
   `ValueError: Request was unsuccessful 6 time(s)`, which says nothing. The
   real reason is in the six `[WARNING]` lines just above it, for example
   `RpcCallFailError: Telegram is having internal issues`.
4. If the app never got far enough to log, go to the box:
   `./infra/scripts/ssh-connect.sh 'sudo docker ps -a; sudo docker logs --tail 80 stock-agent-stock-agent-1'`.

## When logs.sh returns nothing

Work down this list. Most of it can be done from the laptop.

1. Is the container actually producing output? `./infra/scripts/ssh-connect.sh
   'sudo docker ps -a --format "{{.Names}} {{.Status}}"'`. An exited container
   has nothing to send.
2. Are records reaching OCI with a sane date? This is the check that matters:

   ```bash
   ./infra/scripts/ssh-connect.sh 'sudo grep default_log_entry_time /var/log/unified-monitoring-agent/unified-monitoring-agent.log | tail -3'
   ```

   A `1970-01-01` date means the parser time settings are wrong. See below.
3. Is anything queued? An empty
   `/opt/unifiedmonitoringagent/run/buffer/*/` means everything flushed.
4. Does the agent's config match Terraform? `sudo grep -A9 '<parse>'
   /etc/unified-monitoring-agent/conf.d/fluentd_config/fluentd.conf`.

A 200 response in the agent log proves nothing. It only means Oracle accepted
the batch, not that you can find it.

## The 1970 trap

Records can ship perfectly and still be unfindable. Fluentd's JSON parser
defaults `time_type` to float. Docker writes `"time":"2026-09-08T04:36:32Z"`,
fluentd converts that string to the number `2026`, and 2026 seconds past the
epoch is `1970-01-01T00:33:46`. Every record lands there, ingestion returns 200,
the console shows a healthy log, and no search over a recent window finds
anything.

`infra/logging.tf` guards against it:

```hcl
parser {
  parser_type      = "JSON"
  field_time_key   = "time"
  time_type        = "STRING"
  time_format      = "%Y-%m-%dT%H:%M:%S.%N%z"
  is_keep_time_key = false
}
```

If you ever change the parser, check `default_log_entry_time` afterwards.

## Pushing a logging config change to the box

`terraform apply` updates the config in Oracle's service, but the box serves a
cached copy. Restarting `unified-monitoring-agent` does **not** refresh it, and
neither does waiting.

1. `cd infra && terraform apply`
2. Confirm the service has it:
   `oci logging agent-configuration get --config-id <ocid> --query 'data."service-configuration".sources[0].parser'`
3. Force the box to fetch it:
   `./infra/scripts/ssh-connect.sh 'sudo systemctl start unified-monitoring-agent_config_downloader.service'`
4. Confirm it landed: `sudo grep time_format /etc/unified-monitoring-agent/conf.d/fluentd_config/fluentd.conf`
5. To re-ship history rather than only new lines, stop the agent, delete
   `/etc/unifiedmonitoringagent/pos/*.pos`, start it again. Only files still on
   disk can be re-read; logs from a deleted container are gone.
6. Wait four minutes, then `./infra/scripts/logs.sh 1h`.

## Querying OCI directly

`logs.sh` reads the `msg` field. If you need the full record, including the
timestamp OCI assigned:

```bash
cd infra
P=$(terraform output -raw container_log_search_path)
SUPPRESS_LABEL_WARNING=True oci logging-search search-logs \
  --search-query "search \"$P\"" \
  --time-start 2026-09-08T04:00:00Z --time-end 2026-09-08T05:00:00Z --limit 5
```

Useful fields on each result: `logContent.time` is the record's own timestamp,
`logContent.oracle.ingestedtime` is when Oracle received it. Those two being far
apart is the 1970 problem.

`--raw-output` only flattens a single string, never a list, which is why
`logs.sh` uses `join()` around a `reverse()`.
