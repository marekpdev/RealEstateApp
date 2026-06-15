data "azurerm_resource_group" "rg" {
  name = "rg-realestateapp"
}

resource "azurerm_kubernetes_cluster" "aks" {
  name                = "RealEstateAppAKSCluster"
  location            = "swedencentral"
  resource_group_name = data.azurerm_resource_group.rg.name
  dns_prefix          = "realestateapp-k8s"

  # System Node Pool Configuration (Locked down to 1 cheap node)
  default_node_pool {
    name       = "agentpool"
    node_count = 1
    vm_size    = "Standard_D2as_v5" # 2 vCPUs, 8 GiB RAM (~$0.10/hour)

    # Cost Controls
    enable_auto_scaling = false

    # Weasy Print / Portal clean configs
    zones = [] # Disable availability zones to restrict vCPU allocation
  }

  # Network Configuration Profile (includes the fix for the 4-vCPU trial account quota block)
  network_profile {
    network_plugin    = "kubenet" # Lightweight, skips complex multi-core requirements
    load_balancer_sku = "standard"
  }

  identity {
    type = "SystemAssigned"
  }

  tags = {
    Environment = "Development"
    Project     = "RealEstateApp"
    CostCenter  = "Sandbox"
  }
}

output "client_certificate" {
  value     = azurerm_kubernetes_cluster.aks.kube_config.0.client_certificate
  sensitive = true
}

output "kube_config" {
  value     = azurerm_kubernetes_cluster.aks.kube_config_raw
  sensitive = true
}