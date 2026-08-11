# Cloud SQL for PostgreSQL.
#
# The durable store, and — in the Redis-free profile — also the application
# cache, the rate-limit counters, recent views and the advisory locks. None of
# those needs a resource of its own here: they need tables and a lock manager,
# which Django creates and PostgreSQL provides. There is no schema SQL in this
# file and there must never be (ADR-0007 "Application schemas").

resource "google_sql_database_instance" "this" {
  project          = var.project_id
  name             = local.sql_instance_name
  region           = var.region
  database_version = var.database_version

  # The service-side guard. It refuses the delete regardless of what a plan
  # says, which is the protection that survives someone running destroy with
  # -auto-approve.
  deletion_protection = var.sql_deletion_protection

  settings {
    tier              = var.sql_tier
    availability_type = var.sql_availability_type
    disk_size         = var.sql_disk_size_gb
    disk_autoresize   = var.sql_disk_autoresize
    disk_type         = "PD_SSD"

    edition = "ENTERPRISE"

    backup_configuration {
      enabled                        = var.sql_backup_enabled
      start_time                     = var.sql_backup_start_time
      point_in_time_recovery_enabled = var.sql_point_in_time_recovery
      location                       = var.region

      dynamic "backup_retention_settings" {
        for_each = var.sql_backup_enabled ? [1] : []
        content {
          retained_backups = var.sql_backup_retained_count
          retention_unit   = "COUNT"
        }
      }
    }

    ip_configuration {
      # No public IP and no authorized networks. Cloud Run reaches the instance
      # over the native integration's unix socket, which needs neither
      # (ES-07 section 31). Adding a public IP here would create an
      # internet-reachable database to save nothing.
      ipv4_enabled = false

      # Private Service Access would require a VPC and a peering range.
      # ADR-0007 forbids introducing that unless a dependency actually needs it,
      # and with ipv4_enabled false plus the socket mount, nothing does.
      ssl_mode = "ENCRYPTED_ONLY"
    }

    insights_config {
      query_insights_enabled = true
      # Query text is truncated and stored by Cloud SQL. CARE queries carry
      # clinical identifiers in parameters, and parameters are not recorded, but
      # the length stays modest rather than maximal.
      query_string_length = 1024
    }

    maintenance_window {
      day          = 7 # Sunday
      hour         = 4
      update_track = "stable"
    }

    user_labels = local.labels
  }

  # The provider-side guard, kept in step with the service-side one. A plan that
  # would replace or delete a protected instance fails here before it reaches
  # the API (ES-07 section 27).
  lifecycle {
    precondition {
      condition     = var.sql_terraform_deletion_protection == var.sql_deletion_protection
      error_message = "sql_deletion_protection and sql_terraform_deletion_protection must agree. Setting one without the other leaves a plan that proposes a delete the service will then refuse, or a service that permits a delete no plan review would have caught."
    }
  }

  depends_on = [google_project_service.required]
}

# The application database. Its *tables* are Django's, created by the init Job.
resource "google_sql_database" "care" {
  project   = var.project_id
  instance  = google_sql_database_instance.this.name
  name      = var.database_name
  charset   = "UTF8"
  collation = "en_US.UTF8"

  # Dropping the database drops every patient record in the environment.
  # deletion_policy ABANDON leaves it in place when the resource leaves state.
  deletion_policy = var.sql_deletion_protection ? "ABANDON" : "DELETE"
}

# ---------------------------------------------------------------------------
# The application database user is deliberately NOT declared here.
#
# google_sql_user requires the password as an argument, which would write it to
# OpenTofu state in cleartext. ADR-0007 ("State") forbids putting application
# secrets in state when an alternative operational mechanism exists, and one
# does: infrastructure/scripts/provision-secrets.sh creates the user through
# gcloud and writes DATABASE_URL and the password straight into Secret Manager,
# so no credential ever passes through OpenTofu.
#
# ES-07 section 29 makes the user optional ("Terraform MAY create ... an
# infrastructure-level database user where required"), and section 22 asks for
# exactly this: secret values injected by an operational step.
#
# The cost is one documented command per environment. The benefit is that
# `tofu show` and the state bucket never hold a database password.
# ---------------------------------------------------------------------------
