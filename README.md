# Traefik Microservices Project — Ubuntu Deployment Guide

A complete walkthrough of the local deployment completed on Ubuntu in VMware Workstation on 4 October 2026. It covers tool setup, Dockerfiles, Docker Compose, routing, database initialization, troubleshooting, API tests, Redis caching, and screenshot documentation.

**Original source:** https://github.com/mennakamel25/traefik-project

The original checkout contained the application source and initialization scripts. We added four Dockerfiles and a Compose file, corrected Flask routes and PostgreSQL initialization, and deployed seven containers.

## 1. Architecture and lab environment

| Component | Purpose | Internal port | Database / cache |
| --- | --- | --- | --- |
| Traefik | Reverse proxy and API routing | 80; dashboard 8080 | Docker service discovery |
| Frontend | Nginx serves the HTML/JavaScript UI | 3000 | Calls `/api/*` |
| User service | List, create, and retrieve users | 5000 | `users_db` |
| Product service | List/create products and reserve stock | 5001 | `products_db` |
| Order service | Validate user, reserve stock, save order | 5002 | `orders_db` and Redis |
| PostgreSQL | Persistent storage for all three databases | 5432 | `postgres_data` volume |
| Redis | Cache order reads for 30 seconds | 6379 | `order:<id>` and `orders:all` |

Browser requests enter Traefik on port 80. Traefik sends `/` to the frontend and `/api/users`, `/api/products`, and `/api/orders` to their services after stripping the matching API prefix. The order service calls the user and product services internally.

The `web` network is named `traefik-web`. Application services also join `backend` to reach PostgreSQL and Redis. Database and Redis ports are not published to the host. The dashboard is published only on the Ubuntu host's loopback address.

**Observed lab versions:** Docker 29.7.2, Docker Compose 5.5.0, and Traefik 3.6.25. The Compose configuration uses the `traefik:v3.6` tag; future pulls can resolve to a different patch version. The original README used Traefik v2.11; this walkthrough uses v3.6, which worked with our Docker installation.

Start with an Ubuntu VM, internet access, a user with sudo access, and sufficient free disk space. Our expanded VM disk was 45 GB, with approximately 24 GB available after expanding the root filesystem.

## 2. Install tools on Ubuntu

If Docker and Git are already installed and working, skip installation and run the version checks below. These commands install Docker Engine and the Compose plugin using the official Ubuntu apt repository.

```bash
sudo apt update
sudo apt install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Log out and log back in to apply Docker group membership, then check:

```bash
docker --version
docker compose version
git --version
docker run --rm hello-world
sudo systemctl status docker --no-pager
```

Docker group membership grants privileged access to the host. On an existing machine with another Docker installation, review conflicting packages before changing it. Official installation reference: https://docs.docker.com/engine/install/ubuntu/

Screenshots: `01-Tools-Versions.png` and `03-Docker-Status.png`.

## 3. Clone the source

```bash
cd ~
git clone https://github.com/mennakamel25/traefik-project.git
cd traefik-project
ls
```

Run subsequent project commands from `~/traefik-project`. If it is already cloned, enter that directory instead of cloning again.

Screenshot: `02-Clone-Repository.png`.

## 4. Create the service Dockerfiles

### User service

```bash
cat > user-service/Dockerfile <<'EOF'
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
EXPOSE 5000
CMD ["python", "app.py"]
EOF
```

Screenshot: `04-User-Service-Dockerfile.png`.

### Product service

```bash
cat > product-service/Dockerfile <<'EOF'
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
EXPOSE 5001
CMD ["python", "app.py"]
EOF
```

Screenshot: `05-Product-Service-Dockerfile.png`.

### Order service

```bash
cat > order-service/Dockerfile <<'EOF'
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
EXPOSE 5002
CMD ["python", "app.py"]
EOF
```

Screenshot: `06-Order-Service-Dockerfile.png`.

### Frontend

```bash
cat > frontend/Dockerfile <<'EOF'
FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html /usr/share/nginx/html/index.html
EXPOSE 3000
EOF
```

Keep the existing `frontend/nginx.conf`, which listens on port 3000 and serves `/usr/share/nginx/html`. `EXPOSE` documents the internal port; Compose publishes only Traefik's ports.

Screenshot: `07-Frontend-Dockerfile.png`.

## 5. Fix the Flask routes

The original application files include empty route decorators, such as `@app.route("", methods=["GET"])`. Flask route rules must start with `/`. Remove only the empty route decorators, keeping the corresponding `/` routes:

```bash
sed -i.bak '/^@app\.route("", methods=/d' \
  user-service/app.py product-service/app.py order-service/app.py

rg -n '@app\.(route|get)' user-service/app.py product-service/app.py order-service/app.py
```

If `rg` is unavailable, inspect the files with `cat`, or use `grep -n '@app'` on them. The `.bak` files preserve the previous content. Perform this fix before building the images.

Screenshot: `14-Fix-Flask-Routes.png`.

## 6. Correct PostgreSQL initialization

Replace `postgres-init/01-init-databases.sh` with:

```bash
cat > postgres-init/01-init-databases.sh <<'EOF'
#!/bin/bash
set -e

for db in users_db products_db orders_db; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
    SELECT 'CREATE DATABASE $db' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$db')\gexec
EOSQL
done
EOF
```

The important fix is `--dbname postgres`. Without an explicit database, `psql` attempts to connect to a database named after the user (`appuser`), which does not exist in this configuration.

Keep the existing `02-init-tables.sh`. It creates:

- A `users` table in `users_db`, with the seed user Menna Kamel (`menna@example.com`).
- A `products` table in `products_db`, with Sample Product priced at 19.99 and stock 100.
- An `orders` table in `orders_db`.

The scripts use conditional creation/insertion to allow recovery without deleting the existing volume. PostgreSQL automatically runs initialization scripts only when its data directory is empty. Repairing a script does not automatically rerun it on an existing volume; see Section 10 for recovery.

## 7. Create the complete Docker Compose file

Create `docker-compose.yml` in the project root and paste the following configuration. Keep only one active Compose configuration for this walkthrough; avoid a conflicting `compose.yaml`.

```yaml
services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: appuser
      POSTGRES_PASSWORD: labpassword
      POSTGRES_DB: postgres
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./postgres-init:/docker-entrypoint-initdb.d:ro
    networks:
      - backend
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U appuser -d postgres"]
      interval: 5s
      timeout: 5s
      retries: 20

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    networks:
      - backend
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  user-service:
    build: ./user-service
    restart: unless-stopped
    environment:
      DATABASE_URL: postgresql://appuser:labpassword@postgres:5432/users_db
      PORT: "5000"
    networks:
      - web
      - backend
    depends_on:
      postgres:
        condition: service_healthy
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.users.rule=PathPrefix(`/api/users`)"
      - "traefik.http.routers.users.entrypoints=web"
      - "traefik.http.routers.users.middlewares=users-strip"
      - "traefik.http.middlewares.users-strip.stripprefix.prefixes=/api/users"
      - "traefik.http.services.users.loadbalancer.server.port=5000"

  product-service:
    build: ./product-service
    restart: unless-stopped
    environment:
      DATABASE_URL: postgresql://appuser:labpassword@postgres:5432/products_db
      PORT: "5001"
    networks:
      - web
      - backend
    depends_on:
      postgres:
        condition: service_healthy
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.products.rule=PathPrefix(`/api/products`)"
      - "traefik.http.routers.products.entrypoints=web"
      - "traefik.http.routers.products.middlewares=products-strip"
      - "traefik.http.middlewares.products-strip.stripprefix.prefixes=/api/products"
      - "traefik.http.services.products.loadbalancer.server.port=5001"

  order-service:
    build: ./order-service
    restart: unless-stopped
    environment:
      DATABASE_URL: postgresql://appuser:labpassword@postgres:5432/orders_db
      PORT: "5002"
      REDIS_URL: redis://redis:6379/0
      USER_SERVICE_URL: http://user-service:5000
      PRODUCT_SERVICE_URL: http://product-service:5001
    networks:
      - web
      - backend
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      user-service:
        condition: service_started
      product-service:
        condition: service_started
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.orders.rule=PathPrefix(`/api/orders`)"
      - "traefik.http.routers.orders.entrypoints=web"
      - "traefik.http.routers.orders.middlewares=orders-strip"
      - "traefik.http.middlewares.orders-strip.stripprefix.prefixes=/api/orders"
      - "traefik.http.services.orders.loadbalancer.server.port=5002"

  frontend:
    build: ./frontend
    restart: unless-stopped
    networks:
      - web
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.frontend.rule=PathPrefix(`/`)"
      - "traefik.http.routers.frontend.entrypoints=web"
      - "traefik.http.routers.frontend.priority=1"
      - "traefik.http.services.frontend.loadbalancer.server.port=3000"

  traefik:
    image: traefik:v3.6
    restart: unless-stopped
    command:
      - "--api.insecure=true"
      - "--providers.docker=true"
      - "--providers.docker.exposedbydefault=false"
      - "--providers.docker.network=traefik-web"
      - "--entrypoints.web.address=:80"
      - "--accesslog=true"
      - "--log.level=INFO"
    ports:
      - "80:80"
      - "127.0.0.1:8080:8080"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks:
      - web

networks:
  web:
    name: traefik-web
  backend:

volumes:
  postgres_data:
```

This combines the sections we built incrementally. PostgreSQL and Redis have health checks. The order service waits for healthy PostgreSQL and Redis, but `service_started` for the other Flask services only confirms that their containers started.

The frontend's routing priority is 1, allowing the more specific API routes to match first. Each strip-prefix middleware turns, for example, `/api/users/1` into `/1` before forwarding it to Flask.

During the incremental walkthrough, use these screenshot names:

| Compose section | Screenshot |
| --- | --- |
| PostgreSQL, Redis, networks, volume | `08-Compose-Database-Redis.png` |
| User service | `09-Compose-User-Service.png` |
| Product service | `10-Compose-Product-Service.png` |
| Order service | `11-Compose-Order-Service.png` |
| Frontend | `12-Compose-Frontend.png` |
| Traefik | `13-Compose-Traefik.png` |

## 8. Build and start the project

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

Expect seven running containers: Traefik, frontend, user service, product service, order service, PostgreSQL, and Redis. PostgreSQL and Redis should become healthy.

Screenshots: `15-Build-And-Start.png` and `16-Containers-Status.png`.

## 9. Optional recovery: expand a full Ubuntu VM disk

This was required in our lab after a build failed with `No space left on device`. Skip this section if the VM has sufficient free space.

### Inspect disk and filesystem usage

```bash
df -h /
df -i /
lsblk -f
sudo du -xhd1 /var /home 2>/dev/null
```

Our root filesystem was `/dev/sda2`, about 20 GB and 100% full. Inode usage was about 23%, so the problem was disk capacity.

Screenshots: `15A-Disk-Space-Check.png` and `15B-Disk-Usage-Details.png`.

### Expand the virtual disk in VMware Workstation

1. Shut down Ubuntu and power off the VM.
2. Open VM settings and select its hard disk.
3. Use **Utilities → Expand** and increase the disk to 45 GB.
4. Start Ubuntu again.

Screenshot: `15C-VMware-Expand-Disk.png`.

If the desktop cannot start because the root filesystem is full, use `Ctrl+Alt+F3` to open a text console and log in. VMware may intercept the keys; use its keyboard forwarding controls if necessary.

### Expand the root partition and ext4 filesystem

```bash
lsblk -f
```

Screenshot: `15D-Disk-Layout-After-Expand.png`.

**These commands apply to our verified layout only:** root on the ext4 partition `/dev/sda2`, with free space after that partition. If your VM uses LVM, another device, or a different filesystem, determine its layout before proceeding. Keep a backup before changing partitions.

`growpart` is supplied by `cloud-guest-utils`; install it if missing and there is enough space:

```bash
sudo apt install -y cloud-guest-utils
```

For our layout:

```bash
sudo env TMPDIR=/run growpart /dev/sda 2
sudo resize2fs /dev/sda2
df -h /
```

Using `/run` for temporary files allowed `growpart` to work while the root filesystem was full. Our result was approximately 45 GB total, 20 GB used, and 24 GB available. Reboot if needed to restore the desktop, then retry the build from Section 8.

Screenshot: `15E-Expand-Ubuntu-Partition.png`.

## 10. Optional recovery: missing databases and API errors

A `/health` endpoint returning 200 does not prove that the database is initialized: these Flask health endpoints return a basic service status without querying PostgreSQL.

If listing users/products returns HTTP 500, inspect:

```bash
docker compose logs --tail=100 user-service
docker compose logs --tail=100 postgres
docker compose exec postgres psql -U appuser -d postgres -c '\l'
```

Screenshots:

- `20A-User-Service-Error-Logs.png`
- `20B-Postgres-Logs.png`
- `20C-Database-Initialization-Check.png`

In our lab, only the default PostgreSQL databases existed because the first initialization script failed. After applying Section 6, run:

```bash
docker compose exec postgres bash /docker-entrypoint-initdb.d/01-init-databases.sh
docker compose exec postgres bash /docker-entrypoint-initdb.d/02-init-tables.sh
```

Screenshot: `20D-Initialize-Databases-And-Tables.png`.

This repairs initialization without removing the PostgreSQL volume. Then retry the API tests. Do not use `docker compose down -v` for this recovery; it deletes the project's named volume and its database data.

If application code was fixed after building, rebuild the relevant services:

```bash
docker compose up -d --build user-service product-service order-service
```

## 11. Check API health and seed data

```bash
curl -i http://localhost/api/users/health
curl -i http://localhost/api/products/health
curl -i http://localhost/api/orders/health
```

Expect HTTP 200 and status `ok`. Screenshot: `17-API-Health-Checks.png`.

```bash
curl -i http://localhost/api/users/
curl -i http://localhost/api/products/
```

On a fresh initialized lab, expect user ID 1 and product ID 1. If you have changed the data, use the actual IDs returned by the APIs in the next step.

Screenshots: `20-Users-API-Success.png` and `21-Products-API-Success.png`. Earlier combined evidence may be named `20-Users-And-Products.png`.

## 12. Create an order

For the fresh seed data:

```bash
curl -i -X POST http://localhost/api/orders/ \
  -H "Content-Type: application/json" \
  -d '{"user_id":1,"product_id":1,"quantity":2}'
```

Our confirmed response was HTTP **201 Created**, with:

| Field | Observed value |
| --- | --- |
| Order ID | 1 |
| User ID | 1 |
| Product ID | 1 |
| Quantity | 2 |
| Total price | 39.98 |
| Status | `CONFIRMED` |

Each successful POST creates another order and reduces stock again. Record the returned order ID if it differs from 1.

Screenshot: `22-Create-Order.png`.

## 13. Verify stock deduction

```bash
curl -i http://localhost/api/products/1
```

Our product stock changed from **100 to 98** after the order for two units. Expected response: HTTP 200.

Screenshot: `23-Stock-After-Order.png`.

## 14. Verify Redis caching

The order cache expires after **30 seconds**. Creating an order also populates its cache immediately, so a read just after creation may already return `cache`.

For a clear database-to-cache demonstration, wait more than 30 seconds after the latest read/creation of this order, then run both requests together:

```bash
curl -s http://localhost/api/orders/1; echo
curl -s http://localhost/api/orders/1; echo
```

Expected: the first response contains `"source":"db"`; the second contains `"source":"cache"`, with the same order data. Replace `1` with your order ID if necessary.

In our first attempt, the calls were 34 seconds apart and both returned `db`. Running them consecutively confirmed `db` followed by `cache`.

Screenshot: `24-Redis-Cache-Test.png`.

Optional Redis inspection:

```bash
docker compose exec redis redis-cli GET order:1
docker compose exec redis redis-cli TTL order:1
```

## 15. Verify the frontend

Open **http://localhost/** in a browser inside Ubuntu and refresh the page.

Our final UI displayed:

- Menna Kamel and `menna@example.com` in Users.
- Sample Product priced at **$19.99**, with stock **98**.
- Order ID 1, quantity **2**, total **$39.98**, status **CONFIRMED**.

Screenshot: `25-Frontend-Final-Check.png`. The initial frontend screenshot was named `18-Frontend-Website.png`.

From Windows, the frontend can be accessed using `http://<Ubuntu-VM-IP>/` if the VMware network permits access. `localhost` always refers to the machine running the browser. The dashboard remains restricted to Ubuntu loopback in this configuration.

## 16. Verify the Traefik dashboard

Open **http://localhost:8080/dashboard/** inside Ubuntu. Keep the trailing slash.

Our final screenshot showed:

| Dashboard item | Observed result |
| --- | --- |
| Entrypoints | `web :80`, `traefik :8080` |
| HTTP routers | 6 successful |
| HTTP services | 7 successful |
| Router/service warnings | 0 |
| Router/service errors | 0 |

These totals include Traefik's internal dashboard/API resources. They are not the number of application microservices. Green dashboard status confirms routing configuration; the API and UI tests above confirm application behavior.

Screenshot: `26-Traefik-Final-Check.png`. The initial dashboard screenshot was named `19-Traefik-Dashboard.png`.

## 17. Daily operation commands

Run from the project directory:

| Task | Command |
| --- | --- |
| Start / recreate as needed | `docker compose up -d` |
| Stop while retaining containers and data | `docker compose stop` |
| Restart existing containers | `docker compose restart` |
| Check container status | `docker compose ps` |
| View recent logs | `docker compose logs --tail=100` |
| Follow logs | `docker compose logs -f` |
| Rebuild after application changes | `docker compose up -d --build` |
| Remove containers and networks, retain named volume | `docker compose down` |
| Validate configuration | `docker compose config --quiet` |

PostgreSQL data persists in `postgres_data`. Redis is used as a short-lived cache and has no persistent volume in this lab. Do not add `-v` to `down` unless you intentionally want to delete database data.

## 18. Screenshot checklist

Names below are documentation labels; rename your captured images accordingly. This README does not embed the screenshot files.

| Number | Filename | Evidence |
| --- | --- | --- |
| 01 | `01-Tools-Versions.png` | Docker, Compose, Git versions |
| 02 | `02-Clone-Repository.png` | Source checkout |
| 03 | `03-Docker-Status.png` | Docker service status |
| 04 | `04-User-Service-Dockerfile.png` | User image definition |
| 05 | `05-Product-Service-Dockerfile.png` | Product image definition |
| 06 | `06-Order-Service-Dockerfile.png` | Order image definition |
| 07 | `07-Frontend-Dockerfile.png` | Nginx image definition |
| 08 | `08-Compose-Database-Redis.png` | Database/cache configuration |
| 09 | `09-Compose-User-Service.png` | User routing |
| 10 | `10-Compose-Product-Service.png` | Product routing |
| 11 | `11-Compose-Order-Service.png` | Order dependencies/routing |
| 12 | `12-Compose-Frontend.png` | Frontend configuration |
| 13 | `13-Compose-Traefik.png` | Proxy configuration |
| 14 | `14-Fix-Flask-Routes.png` | Correct Flask decorators |
| 15 | `15-Build-And-Start.png` | Successful build and startup |
| 15A | `15A-Disk-Space-Check.png` | Full root filesystem |
| 15B | `15B-Disk-Usage-Details.png` | Usage investigation |
| 15C | `15C-VMware-Expand-Disk.png` | VMware disk expansion |
| 15D | `15D-Disk-Layout-After-Expand.png` | Expanded virtual disk |
| 15E | `15E-Expand-Ubuntu-Partition.png` | Expanded root filesystem |
| 16 | `16-Containers-Status.png` | Seven containers |
| 17 | `17-API-Health-Checks.png` | Health endpoints |
| 18 | `18-Frontend-Website.png` | Initial UI |
| 19 | `19-Traefik-Dashboard.png` | Initial dashboard |
| 20 | `20-Users-API-Success.png` | Successful user query |
| 20A | `20A-User-Service-Error-Logs.png` | API error investigation |
| 20B | `20B-Postgres-Logs.png` | Initialization error |
| 20C | `20C-Database-Initialization-Check.png` | Missing databases |
| 20D | `20D-Initialize-Databases-And-Tables.png` | Initialization recovery |
| 21 | `21-Products-API-Success.png` | Product seed data |
| 22 | `22-Create-Order.png` | HTTP 201 and confirmed order |
| 23 | `23-Stock-After-Order.png` | Stock reduced to 98 |
| 24 | `24-Redis-Cache-Test.png` | Database read then cache hit |
| 25 | `25-Frontend-Final-Check.png` | Final UI data |
| 26 | `26-Traefik-Final-Check.png` | Zero routing errors/warnings |

For sections requiring multiple captures, append `-01`, `-02`, etc. Recovery captures are needed only if you encountered those issues. The numbering preserves the labels from our walkthrough; the guide orders fixes before startup to make a fresh deployment easier.

## 19. Completion and scope

The local deployment was completed and verified: frontend data, API operations, order creation, stock deduction, Redis caching, and Traefik routing all worked.

This is a learning lab using Flask's development server, HTTP, and example credentials. Before a public deployment, use production application serving, HTTPS, protected dashboard access, and managed secrets. The stock reservation and order insertion occur in separate service/database transactions; the current application has no compensating rollback if stock reservation succeeds but order persistence fails.

## References

- Original application: https://github.com/mennakamel25/traefik-project
- Docker Engine installation on Ubuntu: https://docs.docker.com/engine/install/ubuntu/

The deployment configuration and observed test values above document our completed lab. Installation instructions may evolve; consult the official Docker guide when setting up a new host.
