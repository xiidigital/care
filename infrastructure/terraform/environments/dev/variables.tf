variable "project_id" {
  description = "GCP project for the dev environment."
  type        = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "image" {
  description = "CARE application image. Publish it to Artifact Registry before applying runtime resources; prefer a digest."
  type        = string
}

variable "django_allowed_hosts" {
  description = "Custom hostnames. .run.app is added by the module, so the generated Cloud Run URL needs nothing here."
  type        = list(string)
  default     = []
}

variable "csrf_trusted_origins" {
  type    = list(string)
  default = []
}

variable "cors_allowed_origins" {
  description = "Frontend origins allowed to call the API from a browser."
  type        = list(string)
  default     = []
}

variable "current_domain" {
  description = "Frontend host used when building links in outbound email."
  type        = string
  default     = ""
}

variable "optional_secrets" {
  description = "Extra Secret Manager containers, as env var name => roles that read it."
  type        = map(list(string))
  default     = {}
}

variable "labels" {
  type    = map(string)
  default = {}
}

variable "worker_url_override" {
  description = "The worker's own GCP_WORKER_URL, when Cloud Run's assigned URL is not the derivable form. The check block prints the value to use. Nothing dispatches with it; see the module variable."
  type        = string
  default     = ""
}
