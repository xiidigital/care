# Cloud Storage.
#
# Private, always. CARE serves files through Django and its own HTTP transport
# (ADR-0001, ADR-0002); the browser never talks to GCS, holds no storage
# credential and follows no signed URL. So there is no requirement these buckets
# satisfy by being reachable, and every configuration below assumes they are not.

resource "google_storage_bucket" "buckets" {
  for_each = toset(local.managed_buckets)

  project  = var.project_id
  name     = each.value
  location = local.bucket_location

  storage_class = "STANDARD"

  # Object ACLs are off. Access is bucket IAM alone, which is the only way
  # "no public access" is checkable by reading one policy.
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = var.bucket_versioning
  }

  # No lifecycle deletion rules. Incomplete-upload cleanup is CARE's own
  # scheduled operation and stays application-owned; a bucket rule deleting
  # clinical attachments on an age condition would be a second, invisible
  # retention policy (ES-07 section 40).

  # Whether destroy may remove a bucket that still holds objects. False in
  # production, where a bucket holds patient files.
  force_destroy = var.bucket_force_destroy

  labels = local.labels

  depends_on = [google_project_service.required]
}
