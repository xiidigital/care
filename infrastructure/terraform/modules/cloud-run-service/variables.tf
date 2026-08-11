variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "name" {
  description = "Cloud Run service name, e.g. care-dev-api."
  type        = string
}

variable "image" {
  description = "Fully qualified image reference. The same image serves every role; only the command and environment differ (ADR-0007 \"Runtime roles\")."
  type        = string
}

variable "command" {
  description = "Container entrypoint. One of the committed role scripts — ./start.sh or ./start-worker.sh — never an inline command that duplicates what the application already defines."
  type        = list(string)
}

variable "process_role" {
  description = "CARE_PROCESS_ROLE for this service. Only the two HTTP-serving roles are valid here; scheduler and init do not serve HTTP and belong in a Job."
  type        = string

  validation {
    condition     = contains(["api", "task_worker"], var.process_role)
    error_message = "process_role must be 'api' or 'task_worker'. 'scheduler' and 'init' do not serve HTTP and cannot be a Cloud Run service."
  }
}

variable "service_account_email" {
  description = "Runtime identity. Each role gets its own; see ES-07 sections 17 and 18."
  type        = string
}

variable "allow_unauthenticated" {
  description = <<-EOT
    Whether Cloud Run permits anonymous invocation. This is a *platform*
    decision and is independent of CARE's own authentication: a public API still
    enforces application auth on protected routes (ADR-0007 "Cloud Run API").

    It is guarded below: the task worker may never set this true. That guard is
    the enforceable form of ADR-0007's requirement that a deployment exposing
    the worker without its IAM boundary is invalid.
  EOT
  type        = bool
  default     = false
}

variable "ingress" {
  description = "Cloud Run ingress setting."
  type        = string
  default     = "INGRESS_TRAFFIC_ALL"

  validation {
    condition = contains([
      "INGRESS_TRAFFIC_ALL",
      "INGRESS_TRAFFIC_INTERNAL_ONLY",
      "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER",
    ], var.ingress)
    error_message = "Unsupported ingress value."
  }
}

variable "invoker_members" {
  description = <<-EOT
    IAM members granted roles/run.invoker on this service.

    Guarded below: allUsers and allAuthenticatedUsers are rejected. Public access
    is expressed by allow_unauthenticated, which the worker cannot set, so there
    is no path by which the worker becomes publicly invocable.
  EOT
  type        = list(string)
  default     = []
}

variable "env" {
  description = "Non-secret environment variables. Values are infrastructure facts and backend selections, never credentials."
  type        = map(string)
  default     = {}
}

variable "secret_env" {
  description = "Environment variables sourced from Secret Manager, as name => secret id. Values never pass through OpenTofu and never enter state."
  type        = map(string)
  default     = {}
}

variable "cloudsql_instance_connection_name" {
  description = "Cloud SQL instance to mount over the native Cloud Run integration. Empty disables the mount."
  type        = string
  default     = ""
}

variable "cpu" {
  type    = string
  default = "1"
}

variable "memory" {
  type    = string
  default = "1Gi"
}

variable "min_instances" {
  description = "Zero preserves the scale-to-zero goal of ADR-0006. A non-zero value is a standing cost and needs justification."
  type        = number
  default     = 0
}

variable "max_instances" {
  description = "Explicit ceiling. Unbounded scaling is what exhausts Cloud SQL connections (ES-07 section 120)."
  type        = number
}

variable "concurrency" {
  description = "Requests served simultaneously per instance. Chosen against the database connection budget, not maximised."
  type        = number
}

variable "request_timeout_seconds" {
  type    = number
  default = 300
}

variable "port" {
  description = "Container port. Cloud Run injects PORT with this value, and the role scripts bind it."
  type        = number
  default     = 9000
}

variable "probe_path" {
  description = "Path for startup and liveness probes. /ping/ is registered by every role and is deliberately cheap; /health/ runs dependency checks and must not be probed on every liveness cycle (ES-07 section 76)."
  type        = string
  default     = "/ping/"
}

variable "startup_probe_failure_threshold" {
  description = "Startup budget is period x threshold. The role scripts run collectstatic and compilemessages before gunicorn binds, so the budget is generous by necessity."
  type        = number
  default     = 24
}

variable "deletion_protection" {
  type    = bool
  default = false
}

variable "labels" {
  type    = map(string)
  default = {}
}
