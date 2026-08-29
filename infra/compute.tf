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
