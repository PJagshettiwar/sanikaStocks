# Oracle Cloud Infrastructure - Terraform Design Spec

**Date:** 2026-08-29
**Status:** Approved
**Approach:** Full IaC with Cloud-Init + Health Watchdog (Approach 2)

## Overview

Terraform-managed Oracle Cloud Free Tier infrastructure to host the Telegram stock trading agent bot. Everything tracked as code, production-ready with monitoring and alerts via Telegram.

## Requirements

- Oracle Cloud Free Tier (always free, not trial)
- Region: Mumbai (ap-mumbai-1) for low latency to Indian markets
- Static IP for INDstocks broker API whitelisting
- Alerts via existing Telegram bot
- Manual deployment (SSH + git pull + docker compose)
- Fully reproducible: tear down and recreate from Terraform
- **Zero cost**: every resource must be within Oracle Cloud's always-free tier. No paid OCI services, no trial credits.

## Architecture

### Network (network.tf)

| Resource | Config |
|----------|--------|
| VCN | 10.0.0.0/16 CIDR |
| Public Subnet | 10.0.1.0/24 |
| Internet Gateway | outbound to Telegram, OpenRouter, INDstocks APIs |
| Route Table | default route via internet gateway |
| Security List - Ingress | SSH (port 22), restricted to user's home IP (variable) |
| Security List - Egress | all outbound allowed |
| Reserved Public IP | attached to compute instance, whitelisted with INDstocks |

All networking resources are free forever on Oracle Cloud. No bandwidth charges under 10 TB/month.

### Compute (compute.tf)

| Setting | Value |
|---------|-------|
| Shape | VM.Standard.A1.Flex (ARM/Ampere, always free) |
| OCPU | 1 (free tier: up to 4 total) |
| Memory | 6 GB (free tier: up to 24 GB total) |
| Boot Volume | 50 GB (free tier: up to 200 GB total) |
| OS | Ubuntu 24.04 LTS (Canonical aarch64) |

ARM chosen over AMD micro (E2.1.Micro) because:
- 6 GB RAM vs 1 GB
- python:3.12-slim Docker image has aarch64 support
- Same free tier, more resources

### Cloud-Init (cloud-init/setup.sh)

Runs on first boot:
1. apt update && upgrade
2. Install Docker, docker-compose, git
3. Create `stockagent` system user
4. Clone repo to `/opt/stock-agent`
5. Create systemd service (`stock-agent.service`) for Docker container
6. Install health watchdog timer
7. Does NOT auto-start the bot (manual first-run required for .env and session file)

### Monitoring & Alerts

All alerts delivered via existing Telegram bot (@sanika_stocks_update_bot). No OCI Monitoring Alarms or Notification topics used (avoids complexity, stays purely free tier with zero OCI service dependencies for alerting).

**Systemd Health Watchdog (scripts/health-watchdog.sh):**
- Systemd timer runs every 5 minutes
- Checks: container running, CPU usage, memory usage, disk usage
- Thresholds: CPU > 80%, memory > 85%, disk > 80%
- On container failure: sends Telegram alert, attempts restart, reports outcome
- On resource threshold breach: sends Telegram warning
- Uses BOT_TOKEN and CHAT_ID from /opt/stock-agent/.env
- Uses standard Linux tools (`docker ps`, `top`, `free`, `df`) for all checks

**Boot Volume Backup:**
- Weekly automatic backup via OCI backup policy
- Free tier includes 5 backup slots
- Protects SQLite database and Telegram session file

**What's excluded (future iteration):**
- No Prometheus/Grafana
- No log aggregation (Docker logs via SSH for now)
- Future: Loki + Grafana or Oracle Log Analytics (10 GB/month free)

### Log Analysis

Claude Code CLI can SSH into the VM and analyze Docker logs directly:
```bash
ssh -i ~/.ssh/oracle_cloud ubuntu@<VM_IP> "docker compose -f /opt/stock-agent/docker-compose.yml logs --tail 500"
```

No extra logging infrastructure needed for current scale.

## File Structure

```
infra/
  main.tf              # provider config, compartment data source
  network.tf           # VCN, subnet, internet gateway, security list
  compute.tf           # instance, reserved IP, boot volume backup
  variables.tf         # all configurable inputs
  outputs.tf           # IP address, SSH command, instance OCID
  terraform.tfvars.example  # template with placeholder values (no secrets)
  cloud-init/
    setup.sh           # first-boot provisioning script
  scripts/
    health-watchdog.sh  # container health + system metrics check + Telegram alert
    deploy.sh           # manual deploy helper (git pull, rebuild, restart)
README.md              # updated with deployment section + free tier cost breakdown
```

## Variables (variables.tf)

| Variable | Description | Default |
|----------|-------------|---------|
| compartment_id | OCI compartment OCID | required |
| tenancy_ocid | OCI tenancy OCID | required |
| user_ocid | OCI user OCID | required |
| fingerprint | API key fingerprint | required |
| private_key_path | Path to OCI API private key | required |
| region | OCI region | ap-mumbai-1 |
| ssh_public_key_path | Path to SSH public key | ~/.ssh/oracle_cloud.pub |
| allowed_ssh_cidr | IP range for SSH access | required (your home IP/32) |
| instance_shape | Compute shape | VM.Standard.A1.Flex |
| instance_ocpus | Number of OCPUs | 1 |
| instance_memory_gb | Memory in GB | 6 |
| boot_volume_gb | Boot volume size | 50 |
| bot_token | Telegram bot token (for watchdog alerts) | required |
| alert_chat_id | Telegram chat ID (for watchdog alerts) | required |

## Outputs (outputs.tf)

- `instance_public_ip` - the reserved IP to whitelist with INDstocks
- `ssh_command` - ready-to-paste SSH command
- `instance_ocid` - for OCI console reference

## First-Run Steps (after terraform apply)

1. Copy SSH command from Terraform output
2. SSH into the VM
3. Create `/opt/stock-agent/.env` with all environment variables (see `.env.example` for the full list: Telegram API creds, OpenRouter key, INDstocks auth: CLIENT_ID, TOTP_SECRET, MPIN, TOKEN, plus bot/risk config)
4. SCP the Telegram session files (`stock_agent.session`, `approval_bot.session`) to `/opt/stock-agent/`
5. Verify `docker-compose.yml` mounts both session files (update if needed for `approval_bot.session`)
6. Run `sudo systemctl start stock-agent`
7. Verify with `/status` command in Telegram

## Cost

Everything in this design is within Oracle Cloud's always-free tier:
- Compute: 1 A1.Flex OCPU + 6 GB (of 4 OCPU + 24 GB free)
- Storage: 50 GB boot volume (of 200 GB free)
- Network: VCN, subnet, gateway, reserved IP all free
- Monitoring: local watchdog script (no OCI services needed)
- Backups: 5 boot volume backups free
- Bandwidth: 10 TB/month outbound free

**Monthly cost: $0**
