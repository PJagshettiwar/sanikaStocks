# Oracle Cloud Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Terraform-managed Oracle Cloud Free Tier infrastructure to host the sanikaStocks Telegram trading agent with health monitoring via Telegram alerts.

**Architecture:** Single ARM compute instance on OCI Free Tier (Mumbai region) with VCN networking, reserved public IP for broker API whitelisting, cloud-init provisioning (Docker, systemd service), and a systemd-timer health watchdog that checks container status + system metrics and alerts via Telegram bot API. No paid OCI services.

**Tech Stack:** Terraform (OCI provider), Bash (cloud-init, watchdog, deploy script), systemd (service + timer units)

## Global Constraints

- Every OCI resource must be within the always-free tier. No trial credits, no paid services.
- Region: ap-mumbai-1
- Compute shape: VM.Standard.A1.Flex (ARM/Ampere), 1 OCPU, 6 GB RAM, 50 GB boot volume
- OS: Ubuntu 24.04 LTS (Canonical aarch64)
- Git repo: https://github.com/PJagshettiwar/sanikaStocks.git
- Telegram bot token and chat ID are passed as Terraform variables for the watchdog
- No `.env` or session files are managed by Terraform (manual first-run step)

## File Map

```
infra/
  main.tf                    - OCI provider config, compartment data source
  network.tf                 - VCN, subnet, internet gateway, route table, security list
  compute.tf                 - A1.Flex instance, reserved public IP, boot volume backup policy
  variables.tf               - All configurable inputs with descriptions and defaults
  outputs.tf                 - Public IP, SSH command, instance OCID
  terraform.tfvars.example   - Template with placeholder values (no secrets)
  cloud-init/
    setup.sh                 - First-boot: install Docker, clone repo, create systemd units
  scripts/
    health-watchdog.sh       - Container + system metrics check, Telegram alerts
    deploy.sh                - Manual deploy helper: git pull, rebuild, restart
docker-compose.yml           - Updated: mount approval_bot.session volume
.env.example                 - Updated: add missing INDSTOCKS_CLIENT_ID, TOTP_SECRET, MPIN
README.md                    - Updated: add Deployment section with free tier cost breakdown
```

---

### Task 1: Terraform Foundation (provider, variables, outputs)

**Files:**
- Create: `infra/main.tf`
- Create: `infra/variables.tf`
- Create: `infra/outputs.tf`
- Create: `infra/terraform.tfvars.example`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `var.compartment_id`, `var.tenancy_ocid`, `var.user_ocid`, `var.fingerprint`, `var.private_key_path`, `var.region`, `var.ssh_public_key_path`, `var.allowed_ssh_cidr`, `var.instance_shape`, `var.instance_ocpus`, `var.instance_memory_gb`, `var.boot_volume_gb`, `var.bot_token`, `var.alert_chat_id`, `var.repo_url`. Outputs are populated in later tasks.

- [ ] **Step 1: Create `infra/variables.tf`**

```hcl
variable "compartment_id" {
  description = "OCI compartment OCID"
  type        = string
}

variable "tenancy_ocid" {
  description = "OCI tenancy OCID"
  type        = string
}

variable "user_ocid" {
  description = "OCI user OCID"
  type        = string
}

variable "fingerprint" {
  description = "OCI API key fingerprint"
  type        = string
}

variable "private_key_path" {
  description = "Path to OCI API private key file"
  type        = string
}

variable "region" {
  description = "OCI region"
  type        = string
  default     = "ap-mumbai-1"
}

variable "ssh_public_key_path" {
  description = "Path to SSH public key for instance access"
  type        = string
  default     = "~/.ssh/oracle_cloud.pub"
}

variable "allowed_ssh_cidr" {
  description = "CIDR block allowed to SSH (your home IP as x.x.x.x/32)"
  type        = string
}

variable "instance_shape" {
  description = "OCI compute shape"
  type        = string
  default     = "VM.Standard.A1.Flex"
}

variable "instance_ocpus" {
  description = "Number of OCPUs"
  type        = number
  default     = 1
}

variable "instance_memory_gb" {
  description = "Memory in GB"
  type        = number
  default     = 6
}

variable "boot_volume_gb" {
  description = "Boot volume size in GB"
  type        = number
  default     = 50
}

variable "bot_token" {
  description = "Telegram bot token for health watchdog alerts"
  type        = string
  sensitive   = true
}

variable "alert_chat_id" {
  description = "Telegram chat ID for health watchdog alerts"
  type        = string
}

variable "repo_url" {
  description = "Git repository URL to clone on the instance"
  type        = string
  default     = "https://github.com/PJagshettiwar/sanikaStocks.git"
}
```

- [ ] **Step 2: Create `infra/main.tf`**

```hcl
terraform {
  required_version = ">= 1.5"
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "~> 5.0"
    }
  }
}

provider "oci" {
  tenancy_ocid     = var.tenancy_ocid
  user_ocid        = var.user_ocid
  fingerprint      = var.fingerprint
  private_key_path = var.private_key_path
  region           = var.region
}

data "oci_identity_compartment" "this" {
  id = var.compartment_id
}
```

- [ ] **Step 3: Create `infra/outputs.tf`** (placeholder, populated in Task 3)

```hcl
output "instance_public_ip" {
  description = "Reserved public IP - whitelist this with INDstocks"
  value       = oci_core_public_ip.stock_agent.ip_address
}

output "ssh_command" {
  description = "Ready-to-paste SSH command"
  value       = "ssh -i ${replace(var.ssh_public_key_path, ".pub", "")} ubuntu@${oci_core_public_ip.stock_agent.ip_address}"
}

output "instance_ocid" {
  description = "Compute instance OCID for OCI console reference"
  value       = oci_core_instance.stock_agent.id
}
```

- [ ] **Step 4: Create `infra/terraform.tfvars.example`**

```hcl
# OCI Authentication
compartment_id   = "ocid1.compartment.oc1..your_compartment_ocid"
tenancy_ocid     = "ocid1.tenancy.oc1..your_tenancy_ocid"
user_ocid        = "ocid1.user.oc1..your_user_ocid"
fingerprint      = "aa:bb:cc:dd:ee:ff:00:11:22:33:44:55:66:77:88:99"
private_key_path = "~/.oci/oci_api_key.pem"

# SSH Access
ssh_public_key_path = "~/.ssh/oracle_cloud.pub"
allowed_ssh_cidr    = "YOUR_HOME_IP/32"

# Telegram Watchdog
bot_token    = "your_telegram_bot_token"
alert_chat_id = "your_chat_id"

# Defaults (uncomment to override)
# region            = "ap-mumbai-1"
# instance_shape    = "VM.Standard.A1.Flex"
# instance_ocpus    = 1
# instance_memory_gb = 6
# boot_volume_gb    = 50
```

- [ ] **Step 5: Validate Terraform init**

Run: `cd infra && terraform init`
Expected: Provider downloaded, "Terraform has been successfully initialized!"

- [ ] **Step 6: Validate syntax**

Run: `cd infra && terraform validate`
Expected: "Success! The configuration is valid." (will warn about missing resource references in outputs.tf, that's expected until Task 2-3 are done)

- [ ] **Step 7: Commit**

```bash
git add infra/main.tf infra/variables.tf infra/outputs.tf infra/terraform.tfvars.example
git commit -m "feat(infra): add Terraform foundation - provider, variables, outputs"
```

---

### Task 2: Networking (VCN, subnet, security list, internet gateway)

**Files:**
- Create: `infra/network.tf`

**Interfaces:**
- Consumes: `var.compartment_id`, `var.allowed_ssh_cidr` from Task 1
- Produces: `oci_core_subnet.public.id` (used by Task 3 for instance placement)

- [ ] **Step 1: Create `infra/network.tf`**

```hcl
resource "oci_core_vcn" "stock_agent" {
  compartment_id = var.compartment_id
  display_name   = "stock-agent-vcn"
  cidr_blocks    = ["10.0.0.0/16"]
  dns_label      = "stockagent"
}

resource "oci_core_internet_gateway" "stock_agent" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.stock_agent.id
  display_name   = "stock-agent-igw"
  enabled        = true
}

resource "oci_core_route_table" "public" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.stock_agent.id
  display_name   = "stock-agent-public-rt"

  route_rules {
    destination       = "0.0.0.0/0"
    network_entity_id = oci_core_internet_gateway.stock_agent.id
  }
}

resource "oci_core_security_list" "stock_agent" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.stock_agent.id
  display_name   = "stock-agent-sl"

  ingress_security_rules {
    protocol    = "6" # TCP
    source      = var.allowed_ssh_cidr
    description = "SSH from home IP"
    tcp_options {
      min = 22
      max = 22
    }
  }

  egress_security_rules {
    protocol    = "all"
    destination = "0.0.0.0/0"
    description = "Allow all outbound"
  }
}

resource "oci_core_subnet" "public" {
  compartment_id             = var.compartment_id
  vcn_id                     = oci_core_vcn.stock_agent.id
  display_name               = "stock-agent-public-subnet"
  cidr_block                 = "10.0.1.0/24"
  route_table_id             = oci_core_route_table.public.id
  security_list_ids          = [oci_core_security_list.stock_agent.id]
  dns_label                  = "public"
  prohibit_public_ip_on_vnic = false
}
```

- [ ] **Step 2: Validate**

Run: `cd infra && terraform validate`
Expected: "Success! The configuration is valid."

- [ ] **Step 3: Commit**

```bash
git add infra/network.tf
git commit -m "feat(infra): add VCN networking - subnet, security list, internet gateway"
```

---

### Task 3: Compute Instance + Reserved IP + Boot Volume Backup

**Files:**
- Create: `infra/compute.tf`

**Interfaces:**
- Consumes: `oci_core_subnet.public.id` from Task 2, `var.instance_shape`, `var.instance_ocpus`, `var.instance_memory_gb`, `var.boot_volume_gb`, `var.ssh_public_key_path`, `var.compartment_id`, `var.bot_token`, `var.alert_chat_id`, `var.repo_url` from Task 1
- Produces: `oci_core_instance.stock_agent.id`, `oci_core_public_ip.stock_agent.ip_address` (used by outputs.tf)

- [ ] **Step 1: Create `infra/compute.tf`**

```hcl
data "oci_core_images" "ubuntu" {
  compartment_id           = var.compartment_id
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "24.04"
  shape                    = var.instance_shape
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

resource "oci_core_instance" "stock_agent" {
  compartment_id      = var.compartment_id
  display_name        = "stock-agent"
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[0].name
  shape               = var.instance_shape

  shape_config {
    ocpus         = var.instance_ocpus
    memory_in_gbs = var.instance_memory_gb
  }

  source_details {
    source_type             = "image"
    source_id               = data.oci_core_images.ubuntu.images[0].id
    boot_volume_size_in_gbs = var.boot_volume_gb
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.public.id
    assign_public_ip = false
  }

  metadata = {
    ssh_authorized_keys = file(var.ssh_public_key_path)
    user_data           = base64encode(templatefile("${path.module}/cloud-init/setup.sh", {
      repo_url      = var.repo_url
      bot_token     = var.bot_token
      alert_chat_id = var.alert_chat_id
    }))
  }
}

data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

# Reserved public IP (survives instance recreation)
data "oci_core_vnic_attachments" "stock_agent" {
  compartment_id = var.compartment_id
  instance_id    = oci_core_instance.stock_agent.id
}

data "oci_core_vnic" "stock_agent" {
  vnic_id = data.oci_core_vnic_attachments.stock_agent.vnic_attachments[0].vnic_id
}

resource "oci_core_public_ip" "stock_agent" {
  compartment_id = var.compartment_id
  display_name   = "stock-agent-ip"
  lifetime       = "RESERVED"
  private_ip_id  = data.oci_core_private_ips.stock_agent.private_ips[0].id
}

data "oci_core_private_ips" "stock_agent" {
  vnic_id = data.oci_core_vnic.stock_agent.id
}

# Boot volume backup policy (weekly, free tier: 5 slots)
resource "oci_core_volume_backup_policy_assignment" "stock_agent" {
  asset_id  = oci_core_instance.stock_agent.boot_volume_id
  policy_id = data.oci_core_volume_backup_policies.silver.volume_backup_policies[0].id
}

data "oci_core_volume_backup_policies" "silver" {
  filter {
    name   = "display_name"
    values = ["silver"]
  }
}
```

- [ ] **Step 2: Validate**

Run: `cd infra && terraform validate`
Expected: "Success! The configuration is valid." (cloud-init file doesn't exist yet, will be added in Task 4)

- [ ] **Step 3: Commit**

```bash
git add infra/compute.tf
git commit -m "feat(infra): add compute instance, reserved IP, boot volume backup"
```

---

### Task 4: Cloud-Init Provisioning Script

**Files:**
- Create: `infra/cloud-init/setup.sh`

**Interfaces:**
- Consumes: Template variables `${repo_url}`, `${bot_token}`, `${alert_chat_id}` passed from `compute.tf` via `templatefile()`
- Produces: A fully provisioned VM with Docker, the repo cloned to `/opt/stock-agent`, systemd service unit for the container, systemd timer + watchdog script for health checks

- [ ] **Step 1: Create `infra/cloud-init/setup.sh`**

```bash
#!/bin/bash
set -euo pipefail

# Cloud-init first-boot provisioning for stock-agent VM

export DEBIAN_FRONTEND=noninteractive

# System updates
apt-get update -y
apt-get upgrade -y

# Install Docker
apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Install git
apt-get install -y git

# Create system user
useradd --system --shell /usr/sbin/nologin --create-home stockagent
usermod -aG docker stockagent

# Clone repo
git clone ${repo_url} /opt/stock-agent
chown -R stockagent:stockagent /opt/stock-agent

# Create systemd service for stock-agent container
cat > /etc/systemd/system/stock-agent.service <<'UNIT'
[Unit]
Description=Stock Trading Agent (Docker Compose)
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=stockagent
WorkingDirectory=/opt/stock-agent
ExecStart=/usr/bin/docker compose up -d --build
ExecStop=/usr/bin/docker compose down

[Install]
WantedBy=multi-user.target
UNIT

# Install health watchdog script
mkdir -p /opt/stock-agent/scripts
cat > /opt/stock-agent/scripts/health-watchdog.sh <<'WATCHDOG'
#!/bin/bash
set -euo pipefail

ENV_FILE="/opt/stock-agent/.env"
if [ ! -f "$ENV_FILE" ]; then
  exit 0
fi

BOT_TOKEN="${bot_token}"
CHAT_ID="${alert_chat_id}"
HOSTNAME=$(hostname)

send_alert() {
  local message="$1"
  curl -s -X POST "https://api.telegram.org/bot$BOT_TOKEN/sendMessage" \
    -d chat_id="$CHAT_ID" \
    -d text="$message" \
    -d parse_mode="HTML" > /dev/null 2>&1
}

# Check container
if ! docker compose -f /opt/stock-agent/docker-compose.yml ps --status running 2>/dev/null | grep -q stock-agent; then
  send_alert "⚠️ <b>[$HOSTNAME] Container down</b>%0AAttempting restart..."
  if docker compose -f /opt/stock-agent/docker-compose.yml up -d 2>/dev/null; then
    sleep 10
    if docker compose -f /opt/stock-agent/docker-compose.yml ps --status running 2>/dev/null | grep -q stock-agent; then
      send_alert "✅ <b>[$HOSTNAME] Container restarted successfully</b>"
    else
      send_alert "❌ <b>[$HOSTNAME] Container restart FAILED</b>%0AManual intervention required."
    fi
  else
    send_alert "❌ <b>[$HOSTNAME] Container restart FAILED</b>%0AManual intervention required."
  fi
fi

# Check CPU (>80%)
CPU_USAGE=$(top -bn1 | grep "Cpu(s)" | awk '{print int($2 + $4)}')
if [ "$CPU_USAGE" -gt 80 ]; then
  send_alert "⚠️ <b>[$HOSTNAME] High CPU</b>: $${CPU_USAGE}%"
fi

# Check memory (>85%)
MEM_USAGE=$(free | awk '/Mem:/ {printf "%d", $3/$2 * 100}')
if [ "$MEM_USAGE" -gt 85 ]; then
  send_alert "⚠️ <b>[$HOSTNAME] High Memory</b>: $${MEM_USAGE}%"
fi

# Check disk (>80%)
DISK_USAGE=$(df / | awk 'NR==2 {print int($5)}')
if [ "$DISK_USAGE" -gt 80 ]; then
  send_alert "⚠️ <b>[$HOSTNAME] High Disk</b>: $${DISK_USAGE}%"
fi
WATCHDOG
chmod +x /opt/stock-agent/scripts/health-watchdog.sh
chown stockagent:stockagent /opt/stock-agent/scripts/health-watchdog.sh

# Create systemd timer for watchdog (every 5 minutes)
cat > /etc/systemd/system/stock-agent-watchdog.service <<'WDUNIT'
[Unit]
Description=Stock Agent Health Watchdog

[Service]
Type=oneshot
User=stockagent
ExecStart=/opt/stock-agent/scripts/health-watchdog.sh
WDUNIT

cat > /etc/systemd/system/stock-agent-watchdog.timer <<'TIMER'
[Unit]
Description=Run Stock Agent Health Watchdog every 5 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
TIMER

# Enable timer (starts on next boot), but do NOT start the main service
systemctl daemon-reload
systemctl enable stock-agent-watchdog.timer
systemctl start stock-agent-watchdog.timer

echo "Cloud-init provisioning complete. Run 'sudo systemctl start stock-agent' after setting up .env and session files."
```

- [ ] **Step 2: Verify cloud-init is valid bash**

Run: `bash -n infra/cloud-init/setup.sh`
Expected: No syntax errors (exit code 0)

- [ ] **Step 3: Validate Terraform with cloud-init in place**

Run: `cd infra && terraform validate`
Expected: "Success! The configuration is valid."

- [ ] **Step 4: Commit**

```bash
git add infra/cloud-init/setup.sh
git commit -m "feat(infra): add cloud-init provisioning - Docker, systemd, health watchdog"
```

---

### Task 5: Deploy Script

**Files:**
- Create: `infra/scripts/deploy.sh`

**Interfaces:**
- Consumes: nothing (standalone helper script)
- Produces: a script that can be run on the VM to pull latest code, rebuild, and restart

- [ ] **Step 1: Create `infra/scripts/deploy.sh`**

```bash
#!/bin/bash
set -euo pipefail

cd /opt/stock-agent

echo "Pulling latest code..."
git pull origin main

echo "Rebuilding and restarting containers..."
docker compose down
docker compose up -d --build

echo "Waiting for container to start..."
sleep 5

if docker compose ps --status running | grep -q stock-agent; then
  echo "Deploy successful. Container is running."
else
  echo "WARNING: Container may not be running. Check logs:"
  echo "  docker compose logs --tail 50"
  exit 1
fi
```

- [ ] **Step 2: Commit**

```bash
git add infra/scripts/deploy.sh
git commit -m "feat(infra): add manual deploy helper script"
```

---

### Task 6: Fix docker-compose.yml + .env.example + README Deployment Section

**Files:**
- Modify: `docker-compose.yml` (add `approval_bot.session` volume)
- Modify: `.env.example` (add missing `INDSTOCKS_CLIENT_ID`, `INDSTOCKS_TOTP_SECRET`, `INDSTOCKS_MPIN`, `FIXED_ALLOCATION_AMOUNT`)
- Modify: `README.md` (add Deployment section with free tier cost breakdown)

**Interfaces:**
- Consumes: nothing
- Produces: updated project files that match the actual runtime requirements

- [ ] **Step 1: Update `docker-compose.yml` to mount `approval_bot.session`**

Add the `approval_bot.session` volume mount:

```yaml
services:
  stock-agent:
    build: .
    env_file: .env
    volumes:
      - ./agent.db:/app/agent.db
      - ./stock_agent.session:/app/stock_agent.session
      - ./approval_bot.session:/app/approval_bot.session
    restart: unless-stopped
```

- [ ] **Step 2: Update `.env.example` with missing variables**

Add the missing INDstocks auth variables and FIXED_ALLOCATION_AMOUNT:

```
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_SESSION_NAME=stock_agent
WATCHED_CHANNELS=
TELEGRAM_BOT_TOKEN=
APPROVAL_CHAT_ID=

OPENROUTER_API_KEY=
TIER1_MODEL=nvidia/nemotron-3.5-lightning:free
TIER2_MODEL=nvidia/nemotron-3-super-120b-a12b:free

INDSTOCKS_CLIENT_ID=
INDSTOCKS_TOTP_SECRET=
INDSTOCKS_MPIN=
INDSTOCKS_TOKEN=

DEFAULT_STOP_LOSS_PCT=15
DEFAULT_ALLOCATION_PCT=10
FIXED_ALLOCATION_AMOUNT=5000
MAX_SIGNAL_AGE_MINUTES=60
POLL_INTERVAL_MINUTES=10
```

- [ ] **Step 3: Add Deployment section to README.md**

Append the following after the existing "Current State" section:

```markdown
## Cloud Deployment (Oracle Cloud Free Tier)

This project is designed to run on Oracle Cloud's **always-free tier** at **$0/month**.

### Infrastructure (Terraform)

All cloud resources are managed as code in the `infra/` directory.

| Resource | Free Tier Allocation | This Project Uses |
|----------|---------------------|-------------------|
| Compute (A1.Flex ARM) | 4 OCPU + 24 GB RAM | 1 OCPU + 6 GB RAM |
| Boot Volume | 200 GB | 50 GB |
| Network (VCN, subnet, gateway) | Unlimited | 1 VCN |
| Reserved Public IP | 1 | 1 (for broker API whitelisting) |
| Boot Volume Backups | 5 slots | Weekly (silver policy) |
| Outbound Bandwidth | 10 TB/month | Minimal |

### Deploy from scratch

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your OCI credentials and home IP

terraform init
terraform plan
terraform apply
```

### First run on the VM

```bash
# 1. SSH in (command printed by terraform output)
ssh -i ~/.ssh/oracle_cloud ubuntu@<INSTANCE_IP>

# 2. Create .env
sudo -u stockagent nano /opt/stock-agent/.env

# 3. Copy session files from your local machine
scp -i ~/.ssh/oracle_cloud stock_agent.session approval_bot.session ubuntu@<INSTANCE_IP>:/opt/stock-agent/

# 4. Start the service
sudo systemctl start stock-agent

# 5. Verify
sudo systemctl status stock-agent
docker compose -f /opt/stock-agent/docker-compose.yml logs -f
```

### Update deployment

```bash
ssh -i ~/.ssh/oracle_cloud ubuntu@<INSTANCE_IP>
sudo -u stockagent /opt/stock-agent/infra/scripts/deploy.sh
```

### Monitoring

A systemd timer runs every 5 minutes and checks:
- Container is running (auto-restarts on failure)
- CPU usage (alerts if > 80%)
- Memory usage (alerts if > 85%)
- Disk usage (alerts if > 80%)

All alerts are sent to Telegram via the existing bot.
```

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml .env.example README.md
git commit -m "fix: add approval_bot.session volume, complete .env.example, add deployment docs"
```

---

### Task 7: Terraform Plan Dry Run + .gitignore

**Files:**
- Modify: `.gitignore` (add Terraform state files)

**Interfaces:**
- Consumes: all Terraform files from Tasks 1-4
- Produces: verified Terraform configuration ready for `terraform apply`

- [ ] **Step 1: Add Terraform entries to `.gitignore`**

Append to `.gitignore`:

```
# Terraform
infra/.terraform/
infra/*.tfstate
infra/*.tfstate.backup
infra/*.tfvars
!infra/terraform.tfvars.example
```

Note: `terraform.tfvars` is gitignored (contains secrets), only `terraform.tfvars.example` is committed. `.terraform.lock.hcl` is NOT gitignored (locks provider versions, should be committed).

- [ ] **Step 2: Run `terraform init`**

Run: `cd infra && terraform init`
Expected: "Terraform has been successfully initialized!"

- [ ] **Step 3: Run `terraform validate`**

Run: `cd infra && terraform validate`
Expected: "Success! The configuration is valid."

- [ ] **Step 4: Run `terraform fmt`**

Run: `cd infra && terraform fmt -recursive`
Expected: formats all `.tf` files consistently

- [ ] **Step 5: Commit**

```bash
git add .gitignore infra/.terraform.lock.hcl
git commit -m "chore: add Terraform gitignore, lock provider versions"
```
