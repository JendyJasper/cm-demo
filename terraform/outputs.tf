output "kubeconfig_path" {
  value = kind_cluster.user_service_cluster.kubeconfig_path
}

output "cluster_name" {
  value = kind_cluster.user_service_cluster.name
}