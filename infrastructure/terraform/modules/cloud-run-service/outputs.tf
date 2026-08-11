output "name" {
  value = google_cloud_run_v2_service.this.name
}

output "uri" {
  description = "The service's HTTPS endpoint."
  value       = google_cloud_run_v2_service.this.uri
}

output "id" {
  value = google_cloud_run_v2_service.this.id
}

output "latest_ready_revision" {
  value = google_cloud_run_v2_service.this.latest_ready_revision
}
