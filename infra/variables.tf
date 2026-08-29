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
