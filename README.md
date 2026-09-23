# Flask Auth DB App — Production-Grade DevOps Reference Project

A simple Flask **user authentication app** (Register/Login + MySQL) — the application itself is intentionally minimal. The real substance of this repository is everything *around* it: a complete, real-world DevOps pipeline built from scratch, covering infrastructure-as-code, GitOps, secrets management, observability, and alerting on AWS EKS.

This project evolved iteratively — each piece was added, broken, debugged, and fixed for real, and this README documents the *actual* working setup, including the gotchas that cost real debugging time.

---

## Table of Contents

- [What This Project Demonstrates](#what-this-project-demonstrates)
- [Architecture](#architecture)
- [Repository Structure](#repository-structure)
- [Prerequisites](#prerequisites)
- [Local Development](#local-development)
- [Full Production Setup — Step by Step](#full-production-setup--step-by-step)
  1. [Terraform Remote State Backend](#1-terraform-remote-state-backend)
  2. [EKS Cluster + OIDC + IAM](#2-eks-cluster--oidc--iam)
  3. [RDS (MySQL Database)](#3-rds-mysql-database)
  4. [AWS Load Balancer Controller](#4-aws-load-balancer-controller)
  5. [Sealed Secrets](#5-sealed-secrets)
  6. [Database Initialization](#6-database-initialization)
  7. [ArgoCD (GitOps CD)](#7-argocd-gitops-cd)
  8. [CI Pipeline (GitHub Actions)](#8-ci-pipeline-github-actions)
  9. [HTTPS via Route 53 + ACM](#9-https-via-route-53--acm)
  10. [Monitoring — Prometheus + Grafana](#10-monitoring--prometheus--grafana)
  11. [Alerting — Alertmanager + Email](#11-alerting--alertmanager--email)
- [Environment Variables Reference](#environment-variables-reference)
- [CI/CD Flow Explained](#cicd-flow-explained)
- [Security Notes](#security-notes)
- [Known Gotchas & Lessons Learned](#known-gotchas--lessons-learned)
- [Cost / Teardown](#cost--teardown)

---

## What This Project Demonstrates

| Category | Tooling |
|---|---|
| App | Flask, MySQL (Flask-MySQLdb) |
| Containerization | Docker, Docker Compose |
| Infrastructure as Code | Terraform (3 separate state-isolated stacks) |
| Orchestration | Amazon EKS (Kubernetes) |
| Ingress / Load Balancing | AWS Load Balancer Controller → real ALB |
| TLS | ACM certificate, DNS-validated via Route 53, fully automated |
| Secrets Management | Sealed Secrets (Bitnami/Kubeseal) |
| CI | GitHub Actions — build, SonarCloud scan, Trivy scan, image push |
| CD | ArgoCD — GitOps, auto-sync, self-heal, prune |
| Monitoring | Prometheus + Grafana (`kube-prometheus-stack`) |
| Alerting | Alertmanager → Email (SMTP) |
| Code Quality / Security | SonarCloud, Trivy vulnerability scanning |

---

## Architecture

```
                                   ┌─────────────────────┐
  Developer pushes app code  ───▶  │   GitHub Actions     │
                                   │  1. SonarCloud scan  │
                                   │  2. Build image      │
                                   │  3. Trivy scan       │
                                   │  4. Push to DockerHub│
                                   │  5. Bump image tag   │
                                   │     in k8s/deploy.yaml│
                                   │     and commit back  │
                                   └──────────┬───────────┘
                                              │ (git push, k8s/ folder changes)
                                              ▼
                                   ┌──────────────────────┐
                                   │       ArgoCD          │
                                   │  watches k8s/ folder  │
                                   │  auto-sync + selfHeal │
                                   └──────────┬───────────┘
                                              ▼
                        ┌──────────────────────────────────────────┐
                        │                EKS Cluster                 │
                        │                                            │
                        │  Ingress (ALB) ── HTTPS (ACM cert)          │
                        │       │                                    │
                        │  flask-service (ClusterIP)                 │
                        │       │                                    │
                        │  flask-app Deployment (2 replicas)         │
                        │       │  ├─ reads Secret (from SealedSecret)│
                        │       │  └─ exposes /metrics                │
                        │       │                                    │
                        │  db-init Job (ArgoCD PreSync hook)         │
                        │       │  creates `users` table              │
                        │       ▼                                    │
                        │  ───────────────▶  RDS (MySQL, private SG) │
                        │                                            │
                        │  Prometheus ── scrapes /metrics via         │
                        │      │          ServiceMonitor              │
                        │      ▼                                     │
                        │  Alertmanager ── emails on threshold breach │
                        │      │                                     │
                        │  Grafana ── dashboards                     │
                        └──────────────────────────────────────────┘

  Route 53 (app.lucktales.in) ──▶ ALB ──▶ Ingress ──▶ flask-service
```

---

## Repository Structure

```
flask-auth-db-app/
├── app/                          # Flask application package
│   ├── __init__.py               # App factory, DB config, Prometheus metrics init
│   ├── models.py                 # Raw SQL: insert_user / validate_user
│   └── routes.py                 # / , /register , /login
├── templates/                     # Login/Register/Home HTML
├── run.py                         # Entry point (debug=False — see Security Notes)
├── requirements.txt
├── Dockerfile
├── docker-compose.yaml            # Local dev: Flask + MySQL containers
├── .dockerignore                  # Keeps Terraform/.git/etc out of the build context
│
├── .github/workflows/build.yaml   # CI: SonarCloud → build → Trivy → push → bump tag
├── sonar-project.properties       # SonarCloud scan config
│
├── terraform-backend/             # STACK 1 — bootstraps the S3 state bucket + DynamoDB lock table
│   ├── main.tf / variables.tf / outputs.tf
│
├── terraform/                     # STACK 2 — RDS (MySQL)
│   ├── backend.tf                 # Remote state in S3 (key: rds/terraform.tfstate)
│   ├── main.tf                    # DB instance, subnet group, SG scoped to EKS node SG
│   ├── variables.tf / outputs.tf / provider.tf
│
├── eks-cluster/                   # STACK 3 — EKS cluster + everything cluster-identity related
│   ├── backend.tf                 # Remote state in S3 (key: eks/terraform.tfstate)
│   ├── main.tf                    # Cluster, node group, IAM roles, OIDC provider
│   ├── subnet-tags.tf             # Tags subnets for ALB Controller auto-discovery
│   ├── lbc-irsa.tf                # IAM policy + IRSA role for AWS Load Balancer Controller
│   ├── dns-cert.tf                # ACM cert, DNS-validated via Route 53
│   ├── iam/iam-policy.json        # Official AWS Load Balancer Controller IAM policy
│   ├── variables.tf / outputs.tf / provider.tf
│
├── k8s/                            # Synced automatically by ArgoCD
│   ├── namespace.yaml
│   ├── sealed-secret.yaml         # Encrypted — safe to commit (see Sealed Secrets)
│   ├── deployment.yaml            # Image tag auto-updated by CI on every app-code push
│   ├── service.yaml               # ClusterIP, named "http" port (needed by ServiceMonitor)
│   ├── ingress.yaml                # ALB, HTTPS + HTTP→HTTPS redirect
│   ├── db-init-configmap.yaml     # The actual CREATE TABLE SQL
│   ├── db-init-job.yaml           # ArgoCD PreSync hook — runs once per sync, self-deletes
│   ├── servicemonitor.yaml        # Tells Prometheus to scrape /metrics
│   └── flask-app-alerts.yaml      # PrometheusRule: 5xx errors, pod restarts
│
├── argocd/application.yaml         # Bootstrap: tells ArgoCD to watch k8s/ (applied once, manually)
│
└── monitoring/
    └── alertmanager-values.yaml.example   # SMTP config template (real file is git-ignored)
```

---

## Prerequisites

### Local development only
- Python 3.10+
- Docker + Docker Compose
- A MySQL server (or use the one in `docker-compose.yaml`)

### Full production setup
- An AWS account with permissions to create EKS, RDS, IAM, VPC/subnet tags, S3, DynamoDB, ACM, Route 53 resources
- A **domain hosted in Route 53** (this project uses `app.lucktales.in`, a subdomain of `lucktales.in`)
- `terraform` >= 1.5.0
- `kubectl`, configured against the cluster once it exists
- `helm` 3.x
- `aws` CLI, authenticated
- A **Docker Hub** account (for pushing the app image)
- A **SonarCloud** account (free tier), with the repo imported and a token generated
- A **Gmail account with an App Password** (or any SMTP provider) for email alerts — see [Alerting](#11-alerting--alertmanager--email)
- `git`

---

## Local Development

### Option A — Docker Compose (fastest)

```bash
docker-compose up --build
```
Spins up MySQL + the Flask app together. First run only — create the table manually:
```bash
docker compose exec db sh -c 'mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"'
```
```sql
CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(100) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```
App available at `http://localhost:5000`.

### Option B — Python venv

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```
Create a `.env` file (see [Environment Variables Reference](#environment-variables-reference)), create the `users` table in your own MySQL instance (same schema as above), then:
```bash
python run.py
```

---

## Full Production Setup — Step by Step

> This mirrors the actual order the infrastructure was built in. Each stack depends on the ones before it.

### 1. Terraform Remote State Backend

Bootstraps the S3 bucket + DynamoDB lock table every other stack stores its state in. Run once, with local state (deliberately — a bucket can't store its own creation).

```bash
cd terraform-backend
terraform init
terraform apply
```

### 2. EKS Cluster + OIDC + IAM

```bash
cd eks-cluster
terraform init
terraform apply
```
This single stack provisions:
- The EKS cluster + managed node group
- An **OIDC provider**, required for IRSA (IAM Roles for Service Accounts)
- IAM roles for the cluster and nodes
- Subnet tags (`kubernetes.io/role/elb`, `kubernetes.io/cluster/<name>`) so the ALB Controller can auto-discover where to build load balancers
- An IAM policy + IRSA role for the AWS Load Balancer Controller (`lbc-irsa.tf`)
- The ACM certificate + Route 53 DNS validation (`dns-cert.tf`) — see [step 9](#9-https-via-route-53--acm)

```bash
aws eks update-kubeconfig --name flask-eks-cluster --region us-east-2
terraform output alb_controller_role_arn
terraform output acm_certificate_arn
```
Keep both outputs — needed in later steps.

**⚠️ Provider version note:** `provider.tf` pins the AWS provider to `< 5.79.0`. Versions from 5.79.0 onward introduced EKS "Auto Mode" fields that trigger a provider bug (`InvalidParameterException: The type for cluster update was not provided`) against clusters that predate those fields. See [Known Gotchas](#known-gotchas--lessons-learned).

### 3. RDS (MySQL Database)

```bash
cd ../terraform
terraform init
export TF_VAR_db_password="pick-a-real-password"   # never in .tfvars
terraform apply
terraform output db_host
```
The security group only allows inbound MySQL from the EKS node security group (read directly from `eks-cluster`'s state via `terraform_remote_state` — no manual ID copy-pasting, no `0.0.0.0/0`).

### 4. AWS Load Balancer Controller

```bash
helm repo add eks https://aws.github.io/eks-charts
helm repo update

helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  --set clusterName=flask-eks-cluster \
  --set serviceAccount.create=true \
  --set serviceAccount.name=aws-load-balancer-controller \
  --set serviceAccount.annotations."eks\.amazonaws\.com/role-arn"="<alb_controller_role_arn output>" \
  --set vpcId=<your-default-vpc-id>
```
`vpcId` is set explicitly rather than relying on EC2 instance metadata auto-discovery — see [Known Gotchas](#known-gotchas--lessons-learned) for why.

```bash
kubectl get pods -n kube-system -l app.kubernetes.io/name=aws-load-balancer-controller
```
Expect `2/2 Running`.

### 5. Sealed Secrets

```bash
helm repo add sealed-secrets https://bitnami.github.io/sealed-secrets
helm repo update

helm install sealed-secrets sealed-secrets/sealed-secrets \
  -n kube-system \
  --set-string fullnameOverride=sealed-secrets-controller
```
Install the `kubeseal` CLI, then seal your real DB credentials into `k8s/sealed-secret.yaml` (already committed, safe — it's ciphertext, decryptable only by this specific cluster's controller):
```bash
kubeseal --format=yaml \
  --controller-name=sealed-secrets-controller \
  --controller-namespace=kube-system \
  < your-plain-secret.yaml > k8s/sealed-secret.yaml
```

**⚠️ Back up the controller's private key immediately** — losing it makes every `SealedSecret` in this repo permanently undecryptable if the cluster is ever recreated:
```bash
kubectl get secret -n kube-system \
  -l sealedsecrets.bitnami.com/sealed-secrets-key \
  -o yaml > sealed-secrets-key-backup.yaml
```
Store this **outside git**, somewhere durable (password manager, encrypted storage).

### 6. Database Initialization

`k8s/db-init-job.yaml` is an ArgoCD **PreSync hook** — it runs the `CREATE TABLE IF NOT EXISTS` SQL from `k8s/db-init-configmap.yaml` once before every sync, then deletes itself on success. It reuses the same `flask-secret` the app uses — no separate DB credentials to manage. This is applied automatically once ArgoCD is set up (next step) — no manual step needed here.

### 7. ArgoCD (GitOps CD)

```bash
helm repo add argo https://argoproj.github.io/argo-helm
helm repo update

helm install argocd argo/argo-cd -n argocd --create-namespace
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath="{.data.password}" | base64 -d; echo
```
Then bootstrap the Application (applied once, directly — this is what tells ArgoCD to start watching `k8s/`):
```bash
kubectl apply -f argocd/application.yaml
kubectl get application flask-db-auth-app -n argocd
```
Expect `Synced` / `Healthy`. From this point forward, **every change to `k8s/` in git is applied to the cluster automatically** — no manual `kubectl apply` for app resources ever again.

### 8. CI Pipeline (GitHub Actions)

Requires, in **Settings → Secrets and variables → Actions**:
| Secret | Purpose |
|---|---|
| `DOCKER_USERNAME` / `DOCKER_PASSWORD` | Docker Hub push |
| `SONAR_TOKEN` | SonarCloud scan |

And in **Settings → Actions → General → Workflow permissions**: set to **"Read and write permissions"** (the pipeline commits the new image tag back to `main`).

See [CI/CD Flow Explained](#cicd-flow-explained) for what the pipeline actually does.

### 9. HTTPS via Route 53 + ACM

Already created in step 2 (`eks-cluster/dns-cert.tf`) — fully automated: Terraform requests the ACM cert, writes the DNS validation records into the Route 53 zone, and waits for AWS to confirm validation before `apply` finishes.

`k8s/ingress.yaml` references the resulting certificate ARN and redirects all HTTP traffic to HTTPS via a synthetic `ssl-redirect` backend action (a standard AWS ALB Controller pattern).

Visit `https://app.lucktales.in` — should load with a valid, trusted certificate.

### 10. Monitoring — Prometheus + Grafana

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
kubectl create namespace monitoring

helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack -n monitoring
```
The app exposes `/metrics` via `prometheus-flask-exporter` (already wired into `app/__init__.py`). `k8s/servicemonitor.yaml` tells Prometheus to scrape it.

```bash
kubectl get secret -n monitoring -l app.kubernetes.io/name=grafana \
  -o jsonpath="{.items[0].data.admin-password}" | base64 -d; echo
kubectl port-forward -n monitoring svc/kube-prometheus-stack-grafana 3000:80
```
Dashboard panels built manually in the Grafana UI, covering: request rate by status, 5xx error rate, P95 latency, pod restarts, CPU/memory per pod. Queries are documented inline in Grafana; the underlying metric is `flask_http_request_duration_seconds_count` / `_bucket`.

### 11. Alerting — Alertmanager + Email

Copy the example and fill in real SMTP values (Gmail App Password, not your account password):
```bash
cp monitoring/alertmanager-values.yaml.example monitoring/alertmanager-values.yaml
# edit with real values — this file is git-ignored, never commit it
```

**Important:** the `receivers` list must include a `"null"` receiver alongside your real one — the chart auto-injects a route for its built-in `Watchdog` heartbeat alert pointing at a receiver named `"null"`, and omitting it causes the Alertmanager Operator to silently reject the entire config (see [Known Gotchas](#known-gotchas--lessons-learned)):
```yaml
receivers:
  - name: "null"
  - name: "email-notifications"
    email_configs:
      - to: "your-email@gmail.com"
        send_resolved: true
```

```bash
helm upgrade kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  -n monitoring \
  -f monitoring/alertmanager-values.yaml \
  --reuse-values
```

Alert rules live in `k8s/flask-app-alerts.yaml` (synced by ArgoCD like any other manifest):
- **`FlaskHigh5xxErrorRate`** — fires if 5xx errors persist for 5+ minutes
- **`FlaskPodRestartingFrequently`** — fires if a pod restarts more than 3 times in 15 minutes

---

## Environment Variables Reference

| Variable | Used by | Description |
|---|---|---|
| `MYSQL_HOST` | app, db-init Job | RDS endpoint (or `db` for docker-compose) |
| `MYSQL_PORT` | app, db-init Job | Default `3306` |
| `MYSQL_USER` | app, db-init Job | DB username |
| `MYSQL_PASSWORD` | app, db-init Job | DB password |
| `MYSQL_DB` | app, db-init Job | Database name |
| `SECRET_KEY` | app | Flask session secret |

In production, all of the above come from the `flask-secret` Kubernetes Secret (created by decrypting `k8s/sealed-secret.yaml`) — never from a plain file on disk.

---

## CI/CD Flow Explained

```
Push app code (app/, templates/, Dockerfile, requirements.txt, run.py)
        │
        ▼
GitHub Actions:
  1. SonarCloud scan            (job: sonarqube — must pass before build proceeds)
  2. Build Docker image
  3. Trivy vulnerability scan   (CRITICAL, HIGH severities)
  4. Push image to Docker Hub, tagged with the short commit SHA (+ :latest)
  5. sed-replace the image tag in k8s/deployment.yaml
  6. Commit that change back to main as github-actions[bot], with [skip ci]
        │
        ▼ (this is a SEPARATE trigger — ArgoCD, not GitHub Actions, reacts to it)
ArgoCD notices k8s/ changed → syncs → new pods roll out
```

**Why the image is tagged with a commit SHA, not just `:latest`:** immutability. `deployment.yaml` always references an exact SHA, so what's deployed is always traceable to an exact commit, and rollback is just "point the tag back at an older SHA."

**Why CI never touches the cluster directly:** separation of concerns. CI's only job is "build and record what should run." ArgoCD's only job is "make the cluster match git." Neither needs to know the other exists.

**Path filters** (`app/**`, `templates/**`, `Dockerfile`, `requirements.txt`, `run.py`) mean infrastructure-only changes (Terraform, `k8s/`, docs) never trigger a rebuild — and since the bot's own commit only touches `k8s/deployment.yaml`, it can't trigger itself into a loop either.

---

## Security Notes

Fixed over the course of this project (see the original prototype repo for the "before" state):
- ✅ Passwords are no longer stored in plaintext in git — Sealed Secrets encrypts everything committed
- ✅ RDS security group scoped to the EKS node security group only, not `0.0.0.0/0`
- ✅ `debug=True` removed from `run.py`
- ✅ HTTPS enforced via ACM + automatic HTTP→HTTPS redirect
- ✅ CI pipeline includes SonarCloud (code quality) and Trivy (image vulnerability scanning)

Still worth addressing (not yet done):
- ⚠️ **Passwords are still stored/compared in plaintext in the database itself** (`app/models.py` inserts and checks `password` directly, no hashing). This is the most important remaining gap — worth fixing with `werkzeug.security` or `bcrypt` before this goes anywhere beyond a learning project.
- ⚠️ `eks-cluster/variables.tf` and `k8s/ingress.yaml` have real domain names / a real ACM ARN (containing an AWS account ID) committed as defaults. Account IDs and cert ARNs aren't secrets on their own, but consider parameterizing fully via `.tfvars` if forking this for another account.
- ⚠️ No CSRF protection or secure cookie flags (`SESSION_COOKIE_SECURE`, `HTTPONLY`, `SAMESITE`) on the Flask session.

---

## Known Gotchas & Lessons Learned

Genuine issues hit and resolved while building this — documented so they don't need re-debugging:

1. **AWS provider `InvalidParameterException` on EKS apply** — provider versions ≥5.79.0 add EKS "Auto Mode" fields that break `terraform apply` on pre-existing clusters. Fixed by pinning `< 5.79.0` in `eks-cluster/provider.tf`.
2. **ALB Controller `CrashLoopBackOff`: `failed to get VPC ID from instance metadata`** — pods can't reach EC2 IMDS due to the default hop-limit. Fixed by passing `--set vpcId=<id>` explicitly to the Helm install instead of relying on auto-discovery.
3. **`kubectl apply -k ".../crds?ref=master"` fails / is unnecessary** — that command is only needed for `helm upgrade` on existing installs; `helm install` applies CRDs automatically on a first install.
4. **Docker build context was 2GB+** — no `.dockerignore` meant `COPY . .` picked up `.terraform/` provider binaries and `.git` history from three separate Terraform stacks. Fixed with `.dockerignore`.
5. **`SealedSecret` stuck: `"flask-secret" already exists and is not managed by SealedSecret"`** — a Secret created manually (outside Sealed Secrets) before the SealedSecret existed blocks the controller from ever managing it, even after being deleted, because the controller only re-attempts on a *change* to the SealedSecret itself, not on the Secret's absence. Fixed by deleting and reapplying the `SealedSecret` object itself (not just the Secret) to force a fresh reconcile.
6. **`ServiceMonitor` / `PrometheusRule` silently ignored** — by default, Prometheus only picks up `ServiceMonitor`/`PrometheusRule` resources carrying a `release: <helm-release-name>` label matching its own Helm release. Missing this label causes no error anywhere — the resource just gets ignored.
7. **Alertmanager wouldn't reload after `helm upgrade`** — traced to the Prometheus Operator rejecting the entire config with `undefined receiver "null" used in route`. The chart auto-injects a route for its built-in `Watchdog` heartbeat alert pointing at a receiver named `"null"`; overriding `receivers:` without including that entry breaks reconciliation silently (the `-generated` Secret just never updates, with the real error buried in the operator's own logs, not anywhere obvious).
8. **`helm upgrade --reuse-values` can be unreliable for deeply nested config** — when in doubt, `helm get values -o yaml` the current release, merge manually, and apply as one complete file instead of relying on Helm to reconcile two partial sources.
9. **CI committing back to `main` causes local push rejections** — since every CI run creates a new commit, `git pull --rebase` before pushing new work is a required habit, not optional.

---

## Cost / Teardown

To pause without fully tearing down (useful between work sessions):
```bash
# Scale EKS nodes to zero (stops EC2 billing, keeps the cluster control plane)
aws eks update-nodegroup-config --cluster-name flask-eks-cluster \
  --nodegroup-name <name> --scaling-config minSize=0,maxSize=3,desiredSize=0

# Pause RDS (up to 7 days)
aws rds stop-db-instance --db-instance-identifier flask-db
```

Full teardown — **in this order** to avoid orphaned AWS load balancers:
```bash
helm uninstall aws-load-balancer-controller -n kube-system
helm uninstall argocd -n argocd
kubectl delete namespace flask

cd eks-cluster && terraform destroy
cd ../terraform && terraform destroy
```
Then manually verify in the AWS Console: no leftover Elastic IPs, NAT Gateways, EBS volumes, or Load Balancers — Kubernetes-created AWS resources (like ALBs from `Service`/`Ingress` objects) aren't tracked by Terraform and won't be cleaned up by `terraform destroy`.

The `terraform-backend/` stack (S3 + DynamoDB) is deliberately **not** destroyed by default (`prevent_destroy = true`) — remove that lifecycle block first if you genuinely want it gone.
