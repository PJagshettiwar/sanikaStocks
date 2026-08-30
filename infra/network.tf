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
