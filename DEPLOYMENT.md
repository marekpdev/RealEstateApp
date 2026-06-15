# Production Cloud Deployment & DevOps Guide

This guide provides the complete operational runbook required to provision infrastructure, toggle runtime environments, establish connections, configure security states, monitor runtimes, and execute cost-saving lifecycle management tasks for the Real Estate AI Investment Planner.

## 🛠️ System Prerequisites
Before initializing the workspace rollout, ensure your host computer has the following administration command-line interfaces installed locally:
*   **Azure CLI (`az`)**
*   **Kubernetes CLI (`kubectl`)**
*   **Terraform CLI**

---

## 🏗️ Step 1: Cluster Context Management
Before deploying resources or executing scripts, inspect your environment context profile mappings. This prevents accidental execution overhead across incorrect development spaces.

```powershell
# View all available terminal connection profiles registered on your host computer
kubectl config get-contexts

# Identify which cluster your command-line interface is currently actively targeting
kubectl config current-context

# Target your local laptop desktop sandbox environment for mock integration tests:
kubectl config use-context docker-desktop

# Switch target profile boundaries over to your remote Azure production cluster space:
kubectl config use-context RealEstateAppAKSCluster
```

## 🏗️ Step 2: Provision Cloud Landscapes with Terraform

The live Microsoft Azure environment grid is managed declaratively inside the `terraform/` directory via explicit backend structural signatures (`main.tf`, `provider.tf`).

#### ⚠️ Important Architecture Note on Resource Groups
Within `main.tf` file, the infrastructure references an existing resource group using a read-only data block
```terraform
data "azurerm_resource_group" "rg" {
  name = "rg-realestateapp"
}
```

**Why it works this way:** The resource group `rg-realestateapp` must be created manually prior to running Terraform. This design ensures strict decoupling: when you execute a teardown, Terraform destroys only the AKS cluster and its dependent networks, leaving the base resource group completely untouched. This protects persistent data assets belonging specifically to the Real Estate App ecosystem—such as the persistent Azure Blob Storage account hosting your municipal RAG documents—from being accidentally deleted during infrastructure teardowns.

```powershell
# Move into the dedicated infrastructure management directory
cd terraform

# Initialize workspace variables, evaluate backend states, and pull down provider dependencies
terraform init

# Compile and review an interactive planning blueprint map to audit changes before running
terraform plan

# Orchestrate the live AKS Cluster, computational node pools, and cloud subnets in Azure
terraform apply
```

## 🔐 Step 3: Link Cloud Cluster Access Credentials

Once Terraform completes the infrastructure rollout, link your authenticated command-line session directly to the new remote cluster context using the Azure platform manager.

```powershell
# Log into your secure personal Microsoft Azure subscription account
az login

# TROUBLESHOOTING TIP: If you face subscription or login errors, bypass global discovery 
# and authenticate directly into your organization partition using your specific Tenant ID:
az login --tenant your-tenant-id-here

# Pull down the remote cluster security tokens and merge them directly into your local configuration ledger
az aks get-credentials --resource-group rg-realestateapp --name RealEstateAppAKSCluster
```

## 🚀 Step 4: Inject Secrets & Deploy Application Layer

To maintain enterprise-grade security governance, application-layer dependencies are pulled directly from local environmental allocations and parsed as native secure secrets within cluster memory, avoiding plain-text code repository tracking.

```powershell
# Return back up into your main repository root workspace folder
cd ..

# EXTRA PRECAUTION: If you need to clear old keys or completely recreate your credentials,
# cleanly wipe the existing cluster secret cache without triggering a target error crash:
kubectl delete secret realestateapp-secrets --ignore-not-found

# Package and securely inject your local environment variable stack straight into the cluster memory
kubectl create secret generic realestateapp-secrets --from-env-file=.env

# Verify that the secure configuration secret bucket was created successfully inside the namespace
kubectl get secret realestateapp-secrets

# AUDIT & DEBUG SECRETS: View base64-encoded structural representations of keys loaded inside your 
# active deployment namespace memory to verify all fields mapped correctly:
kubectl get secret realestateapp-secrets -o jsonpath='{.data}'

# Apply the declarative layout manifests to construct your Pod allocations and LoadBalancer networks
kubectl apply -f k8s/

# Trigger a rolling application cluster update to securely mount the newly introduced secret mappings
kubectl rollout restart deployment realestateapp-deployment

# Monitor the current initialization and running state of your application container instances
kubectl get pods

# Capture your live public web portal routing address from the external service table
kubectl get service realestateapp-service
```

Copy the generated EXTERNAL-IP from your service mapping screen and paste it into your browser tab to access your multi-agent architecture live over the web!

## 🔍 DevOps Diagnostics & Observability Tracing

Use these monitoring utilities to trace application logic pipelines, audit logs, or evaluate runtime infrastructure anomalies.

### 🪵 Running Log Collection (Telemetry Analysis)

```powershell
# LOG DUMP: Capture the last 50 lines of background Python application and graph execution data
kubectl logs -l app=realestateapp --tail=50

# LIVE STREAM: Stream real-time standard output and tracing metrics straight from the container pod
kubectl logs -l app=realestateapp -f --tail=20
```

## 💰 Cloud Financial Optimization (Hibernation Routines)

To avoid excessive trail billing or computing costs when the multi-agent application is idle or not actively being demonstrated, put the virtual machine compute nodes into a zero-cost hibernation state without losing system configuration mappings:

```powershell
# Hibernate the cluster (Pauses all active virtual machines, drops compute node billing to zero)
az aks stop --resource-group rg-realestateapp --name RealEstateAppAKSCluster

# Wake the cluster (Re-allocates running pod networks and mounts active clusters within 2 minutes)
az aks start --resource-group rg-realestateapp --name RealEstateAppAKSCluster
```

## 🛑 Infrastructure Teardown & De-provisioning

To completely dissolve your production cloud workspace and delete all cluster groups, public IPs, networks, and container dependencies to stop all resource billing:

```powershell
# Ensure your terminal session is active inside your infrastructure automation folder
cd terraform

# Destroy all provisioned remote resources cleanly in one unified configuration wipe
terraform destroy
```
















