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
