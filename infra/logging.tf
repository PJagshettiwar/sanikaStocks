# Ship container logs to OCI Logging so routine checks need no shell.

resource "oci_logging_log_group" "stock_agent" {
  compartment_id = var.compartment_id
  display_name   = "stock-agent-logs"
}

resource "oci_logging_log" "container" {
  log_group_id       = oci_logging_log_group.stock_agent.id
  display_name       = "stock-agent-container"
  log_type           = "CUSTOM"
  is_enabled         = true
  retention_duration = 30
}

# Dynamic groups and policies are identity resources, so they have to be
# created in the tenancy home region.
resource "oci_identity_dynamic_group" "stock_agent" {
  provider       = oci.home
  compartment_id = var.tenancy_ocid
  name           = "stock-agent-instance"
  description    = "The stock-agent VM, so it can write its own logs"
  matching_rule  = "instance.id = '${oci_core_instance.stock_agent.id}'"
}

# Lets the instance write logs using its own identity, so no keys on the box.
resource "oci_identity_policy" "stock_agent_logging" {
  provider       = oci.home
  compartment_id = var.compartment_id
  name           = "stock-agent-logging"
  description    = "Allow the stock-agent VM to write log content"
  statements = [
    "allow dynamic-group ${oci_identity_dynamic_group.stock_agent.name} to use log-content in tenancy"
  ]
}

resource "oci_logging_unified_agent_configuration" "stock_agent" {
  compartment_id = var.compartment_id
  display_name   = "stock-agent-container-logs"
  description    = "Tail the Docker json-file logs"
  is_enabled     = true

  group_association {
    group_list = [oci_identity_dynamic_group.stock_agent.id]
  }

  service_configuration {
    configuration_type = "LOGGING"

    sources {
      name        = "docker-container-logs"
      source_type = "LOG_TAIL"
      # A glob, not a fixed path: the container ID is in the filename and
      # changes on every rebuild.
      paths = ["/var/lib/docker/containers/*/*-json.log"]

      advanced_options {
        is_read_from_head = true
      }

      # Docker wraps each line in a JSON envelope. Without time_type STRING the
      # parser reads "2026-09-08T..." as a float, so every record lands in 1970
      # and no search over a recent window finds it.
      parser {
        parser_type      = "JSON"
        field_time_key   = "time"
        time_type        = "STRING"
        time_format      = "%Y-%m-%dT%H:%M:%S.%N%z"
        is_keep_time_key = false
      }
    }

    destination {
      log_object_id = oci_logging_log.container.id
    }
  }
}
