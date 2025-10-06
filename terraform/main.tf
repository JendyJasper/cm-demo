terraform {
  required_providers {
    kind = {
      source  = "kyma-incubator/kind"
      version = "0.0.9"
    }
  }
}

provider "kind" {}

resource "kind_cluster" "user_service_cluster" {
  name = "cardmarket"

  kind_config {
    kind        = "Cluster"
    api_version = "kind.x-k8s.io/v1alpha4"

    node {
      role  = "control-plane"
      image = "kindest/node:v1.27.1"

      kubeadm_config_patches = [
        yamlencode({
          kind = "InitConfiguration"
          nodeRegistration = {
            kubeletExtraArgs = {
              "node-labels" = "ingress-ready=true"
            }
          }
        })
      ]

      extra_port_mappings {
        container_port = 80
        host_port      = 80
        protocol       = "TCP"
      }

      extra_port_mappings {
        container_port = 443
        host_port      = 443
        protocol       = "TCP"
      }
      
      extra_port_mappings {
        container_port = 5432
        host_port      = 5432
        protocol       = "TCP"
      }
    }

    node {
      role  = "worker"
      image = "kindest/node:v1.27.1"
    }
  }
}