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

variable "enable_fixture_loader" {
  description = "Create the manually invoked load_fixtures Job. Development tooling: it seeds synthetic accounts and clinical data so there is something to authenticate as. Not part of any deployment, and rejected by the module outside dev."
  type        = bool
  default     = false
}

variable "fixture_image" {
  description = "Image for the fixture Job, built by docker/fixtures.Dockerfile FROM the runtime image because load_fixtures imports Faker. Empty uses var.image, which fails unless that image carries Faker."
  type        = string
  default     = ""
}

# --- Delivery (ES-08) -------------------------------------------------------

variable "deployment_principals" {
  description = "IAM members allowed to deploy application revisions into this environment. Take them from the bootstrap root's github_deployment_principals output."
  type        = list(string)
  default     = []
}

variable "image_publisher_principals" {
  description = "IAM members allowed to publish images to this environment's Artifact Registry repository."
  type        = list(string)
  default     = []
}
