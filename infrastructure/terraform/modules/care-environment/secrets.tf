# Secret Manager.
#
# OpenTofu manages the containers and the access bindings. It never manages a
# value: no google_secret_manager_secret_version appears in this file, so no
# payload reaches state, a plan, or `tofu show`
# (ADR-0007 "Secret Manager", ES-07 section 22).
#
# Values are written by infrastructure/scripts/provision-secrets.sh, which
# generates them locally and pipes them to `gcloud secrets versions add`.
#
# Access is per identity and per secret. Nothing here grants a role every
# secret (ES-07 section 23).

locals {
  # The four secrets every CARE environment needs, and which roles read each.
  #
  # DJANGO_SECRET_KEY   settings import it in every process.
  # DATABASE_URL        every role reaches PostgreSQL.
  # JWKS_BASE64         config/settings/deployment.py imports the key set at
  #                     settings load, for every role. It must be supplied:
  #                     the fallback, get_jwks_from_file, *generates a fresh
  #                     random key set* when the file is absent, so each Cloud
  #                     Run instance would sign with a different key and tokens
  #                     issued by one would be rejected by the next.
  # POSTGRES_PASSWORD   read by scripts/wait_for_db.sh, which start.sh and
  #                     start-worker.sh run before gunicorn. The init role does
  #                     not run it, so init does not get this secret.
  required_secrets = {
    DJANGO_SECRET_KEY = {
      secret_id = "${local.name_prefix}-django-secret-key"
      roles     = ["api", "worker", "init"]
    }
    DATABASE_URL = {
      secret_id = "${local.name_prefix}-database-url"
      roles     = ["api", "worker", "init"]
    }
    JWKS_BASE64 = {
      secret_id = "${local.name_prefix}-jwks-base64"
      roles     = ["api", "worker", "init"]
    }
    POSTGRES_PASSWORD = {
      secret_id = "${local.name_prefix}-database-password"
      roles     = ["api", "worker"]
    }
  }

  optional_secrets = {
    for env_name, roles in var.optional_secrets : env_name => {
      secret_id = "${local.name_prefix}-${lower(replace(env_name, "_", "-"))}"
      roles     = roles
    }
  }

  all_secrets = merge(local.required_secrets, local.optional_secrets)

  service_account_emails = {
    api    = google_service_account.api.email
    worker = google_service_account.worker.email
    init   = google_service_account.init.email
  }

  # One binding per (secret, role that reads it).
  secret_access = {
    for pair in flatten([
      for env_name, spec in local.all_secrets : [
        for role in spec.roles : {
          key      = "${spec.secret_id}:${role}"
          env_name = env_name
          role     = role
        }
      ]
    ]) : pair.key => pair
  }

  secret_env_by_role = {
    for role in ["api", "worker", "init"] : role => {
      for env_name, spec in local.all_secrets : env_name => spec.secret_id
      if contains(spec.roles, role)
    }
  }
}

resource "google_secret_manager_secret" "this" {
  for_each = local.all_secrets

  project   = var.project_id
  secret_id = each.value.secret_id
  labels    = local.labels

  replication {
    auto {}
  }

  # A secret whose versions are gone cannot be recovered, and every runtime role
  # fails to start without them. Deleting one is a deliberate act, not a
  # side effect of reordering configuration.
  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_iam_member" "accessors" {
  for_each = local.secret_access

  project   = var.project_id
  secret_id = google_secret_manager_secret.this[each.value.env_name].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.service_account_emails[each.value.role]}"
}
