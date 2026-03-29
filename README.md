
# ☁️ Cloud Resource Lifecycle Manager

A **cloud-native REST API** that simulates the full lifecycle of cloud resources — provisioning, governance, and deprovisioning — using the same patterns found in real cloud control planes (AWS EC2/S3, GCP Compute, Azure ARM).

Built with **Python · FastAPI · SQLite · Docker · docker-compose**.  
Runs entirely locally — no cloud account, no paid tools, no licences required.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     docker-compose                          │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              api container  (:8000)                 │    │
│  │                                                     │    │
│  │   HTTP Request                                      │    │
│  │       │                                             │    │
│  │       ▼                                             │    │
│  │  ┌──────────┐    route     ┌──────────────────────┐ │    │
│  │  │  FastAPI │ ──────────►  │  resources router    │ │    │
│  │  │  (ASGI)  │              │  POST   /resources   │ │    │
│  │  │  Uvicorn │              │  GET    /resources   │ │    │
│  │  └──────────┘              │  GET    /resources/id│ │    │
│  │                            │  PATCH  /../policy   │ │    │ 
│  │                            │  DELETE /resources/id│ │    │
│  │                            └──────────┬───────────┘ │    │
│  │                                       │ SQL         │    │
│  │                                       ▼             │    │
│  │                            ┌──────────────────────┐ │    │
│  │                            │   SQLite (resources  │ │    │ 
│  │                            │   .db on named       │ │    │
│  │                            │   Docker volume)     │ │    │
│  │                            └──────────────────────┘ │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
          ▲
          │  curl / Postman / browser
          │
       localhost:8000
```

### Key design decisions

| Decision | Rationale |
|---|---|
| **FastAPI** (not Flask/Django) | Async-first, automatic OpenAPI docs, Pydantic validation — mirrors real microservice stacks |
| **SQLite** (not Postgres) | Zero external dependencies; single-file DB perfect for a containerised demo |
| **Soft-delete** on deprovision | Real cloud platforms keep audit trails; hard-deleting billing records is an anti-pattern |
| **Non-root container user** | Security best practice; mirrors production Kubernetes pod security contexts |
| **Multi-stage Dockerfile** | Smaller final image; pip cache discarded after build |
| **Named Docker volume** | DB persists across `docker compose down / up` cycles |
| **Health check endpoint** | `/health` liveness probe mirrors Kubernetes health-check patterns |

---

## Resource data model

```
Resource
├── id           UUID (auto-generated)
├── name         string
├── type         "vm" | "bucket" | "function"
├── status       "active" | "stopped" | "deprovisioned"
├── owner        string (email / username)
├── policy_tags  JSON object  { "env": "prod", "cost-centre": "eng" }
├── region       string  (default "us-east-1")
├── created_at   UTC timestamp
└── updated_at   UTC timestamp
```

---

## API endpoints

| Method  | Path                     | Description 
|---------|--------------------------|-------------
| `POST`  | `/resources`             | Provision a new resource (status → **active**) 
| `GET`   | `/resources`             | List all resources (filter by `?type=`, `?status=`, `?owner=`) 
| `GET`   | `/resources/{id}`        | Get one resource by ID 
| `PATCH` | `/resources/{id}/policy` | Update policy tags and/or status 
| `DELETE`| `/resources/{id}`        | Deprovision (status → **deprovisioned**, audit record kept) 
| `GET`   | `/health`                | Liveness probe 
| `GET`   | `/docs`                  | Interactive OpenAPI UI (Swagger) 

---

## Quick-start (local, no Docker)

```bash

git clone https://github.com/ad-kat/cloud-resource-manager.git
cd cloud-resource-manager

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000 # 3. Run the development server

# 4. Open the interactive API docs → http://localhost:8000/docs
```
---

## Quick-start (Docker / docker-compose)

```bash

docker compose up --build -d
docker compose ps
docker compose logs api
docker compose down
```

---

## curl test walkthrough

### 1 — Provision a VM

```bash
curl -s -X POST http://localhost:8000/resources \
  -H "Content-Type: application/json" \
  -d '{
    "name": "web-server-01",
    "type": "vm",
    "owner": "alice@example.com",
    "policy_tags": {"env": "prod", "cost-centre": "eng-platform"},
    "region": "us-east-1"
  }' | python3 -m json.tool
```

Expected response (HTTP 201):
```json
{
  "id": "a1b2c3d4-...",
  "name": "web-server-01",
  "type": "vm",
  "status": "active",
  "owner": "alice@example.com",
  "policy_tags": {"env": "prod", "cost-centre": "eng-platform"},
  "region": "us-east-1",
  "created_at": "2025-...",
  "updated_at": "2025-..."
}
```

### 2 — Provision a bucket

```bash
curl -s -X POST http://localhost:8000/resources \
  -H "Content-Type: application/json" \
  -d '{
    "name": "logs-bucket",
    "type": "bucket",
    "owner": "alice@example.com",
    "policy_tags": {"env": "prod", "retention": "90d"}
  }' | python3 -m json.tool
```

### 3 — List all resources

```bash
curl -s http://localhost:8000/resources | python3 -m json.tool
```

### 4 — Filter by type and status

```bash
curl -s "http://localhost:8000/resources?type=vm&status=active" | python3 -m json.tool
```

### 5 — Update policy tags and stop the VM
Replace `RESOURCE_ID` with the `id` from step 1.

```bash
curl -s -X PATCH http://localhost:8000/resources/RESOURCE_ID/policy \
  -H "Content-Type: application/json" \
  -d '{
    "policy_tags": {"env": "staging", "reviewed": "true"},
    "status": "stopped"
  }' | python3 -m json.tool
```

### 6 — Deprovision the resource

```bash
curl -s -X DELETE http://localhost:8000/resources/RESOURCE_ID | python3 -m json.tool
```

Expected response (HTTP 200):
```json
{
  "message": "Resource 'web-server-01' has been deprovisioned.",
  "resource_id": "a1b2c3d4-..."
}
```

### 7 — Verify the audit record is preserved

```bash
curl -s http://localhost:8000/resources/RESOURCE_ID | python3 -m json.tool
# status will be "deprovisioned" — record kept for audit trail
```

---

## Project structure

```
cloud-resource-manager/
├── app/
│   ├── __init__.py         # Python package marker
│   ├── main.py             # FastAPI app, startup lifespan, router mount
│   ├── database.py         # SQLite connection + schema initialisation
│   ├── models.py           # Pydantic request/response models + enums
│   └── routers/
│       ├── __init__.py
│       └── resources.py    # All CRUD endpoint handlers
├── Dockerfile              # Multi-stage build, non-root user
├── docker-compose.yml      # Service definition, port mapping, named volume
├── requirements.txt        # Pinned dependencies
├── .gitignore
└── README.md
```

---

## Tech stack (all free and open source)

| Tool | Licence | Purpose |
|------|---------|---------|
| Python 3.12 | PSF | Runtime |
| FastAPI 0.111 | MIT | Web framework |
| Uvicorn 0.29 | BSD | ASGI server |
| Pydantic v2 | MIT | Data validation & serialisation |
| SQLite 3 | Public domain | Embedded database |
| Docker Engine | Apache 2.0 | Container runtime |
| docker-compose v2 | Apache 2.0 | Local orchestration |

---

## Installing Docker Engine on WSL2 Ubuntu (no Docker Desktop needed)

```bash
# 1. Remove any old versions
sudo apt-get remove -y docker docker-engine docker.io containerd runc

# 2. Install prerequisites
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg lsb-release

# 3. Add Docker's official GPG key
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

# 4. Add the stable repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 5. Install Docker Engine + Compose plugin
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
                        docker-buildx-plugin docker-compose-plugin

# 6. Start the daemon (WSL2 uses service, not systemctl)
sudo service docker start

# 7. Allow your user to run docker without sudo
sudo usermod -aG docker $USER
newgrp docker          # apply group change in current shell

# 8. Verify
docker --version       # Docker version 26.x.x
docker compose version # Docker Compose version v2.x.x
docker run hello-world
```
