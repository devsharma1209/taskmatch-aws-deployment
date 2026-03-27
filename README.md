# TaskMatch — Simplified Airtasker Clone on AWS

A minimal job-posting web app built with **Flask**, **Jinja2**, and **Bootstrap 5**, deployed on a production-grade AWS architecture with VPC, ALB, Auto Scaling, and RDS Multi-AZ.

---

## Table of Contents

1. [Project Structure](#1-project-structure)
2. [Database Schema](#2-database-schema)
3. [Application Code](#3-application-code)
4. [Local Development](#4-local-development)
5. [AWS Deployment Guide](#5-aws-deployment-guide)
6. [Mandatory Testing](#6-mandatory-testing)
7. [Cost Saving](#7-cost-saving)
8. [Marking Rubric Checklist](#8-marking-rubric-checklist)

---

## 1. Project Structure

```
taskmatch/
├── app.py                      # Flask app (routes, auth, DB, rendering)
├── requirements.txt            # Python dependencies
├── nginx.conf                  # Nginx reverse proxy config
├── templates/
│   ├── base.html               # Shared layout (navbar, Bootstrap CDN)
│   ├── home.html               # Landing page
│   ├── jobs.html               # Browse & search jobs
│   ├── job_detail.html         # Job detail + offers
│   ├── login.html              # Login form
│   ├── register.html           # Registration form
│   └── post.html               # Post a job form
├── deploy/
│   ├── userdata.sh             # EC2 bootstrap script (paste into Launch Template)
│   └── schema.sql              # MySQL schema + seed data
└── README.md
```

No frontend build step. No separate API. No JavaScript framework. Flask renders HTML directly via Jinja2. Nginx proxies port 80 to Gunicorn on 127.0.0.1:5000.

---

## 2. Database Schema

### `deploy/schema.sql`

Three tables. Run once against RDS after first EC2 instance is healthy.

```sql
CREATE DATABASE IF NOT EXISTS taskmatch;
USE taskmatch;

CREATE TABLE users (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(50)     NOT NULL UNIQUE,
    password_hash VARCHAR(256)    NOT NULL,
    created_at    TIMESTAMP       DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE jobs (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    title       VARCHAR(150)    NOT NULL,
    description TEXT            NOT NULL,
    budget      DECIMAL(10, 2)  NOT NULL,
    category    VARCHAR(50)     NOT NULL,
    posted_by   VARCHAR(100)    NOT NULL,
    user_id     INT,
    created_at  TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE offers (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    job_id      INT             NOT NULL,
    user_id     INT             NOT NULL,
    amount      DECIMAL(10, 2)  NOT NULL,
    message     TEXT            NOT NULL,
    created_at  TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES jobs(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- Seed data so the app is never empty during demo
INSERT INTO jobs (title, description, budget, category, posted_by) VALUES
('Fix leaking kitchen tap', 'Need a plumber to fix a dripping tap in the kitchen. Standard mixer tap.', 80.00, 'Home Repairs', 'Alice'),
('Move a single bed frame', 'Need help moving a single bed frame from Surry Hills to Newtown. Ground floor both ends.', 50.00, 'Moving', 'Bob'),
('Assemble IKEA bookshelf', 'BILLY bookshelf, still in box. Tools provided.', 40.00, 'Assembly', 'Charlie');
```

---

## 3. Application Code

### `requirements.txt`

```
flask==3.1.1
pymysql==1.1.1
gunicorn==23.0.0
```

### `app.py` — key design decisions

- `SECRET_KEY` uses `os.environ["SECRET_KEY"]` with no fallback — crashes at startup if missing rather than using a predictable key
- All DB connections use `try/finally` to prevent connection leaks
- Sessions are signed cookies — no Redis needed, multiple EC2 instances work fine as long as `SECRET_KEY` is identical across all (guaranteed by SSM Parameter Store)
- `/health` tests actual RDS connectivity via `conn.ping()` — ALB uses this to decide if an instance is healthy
- Global `@app.errorhandler(500)` and `@app.errorhandler(404)` prevent raw error pages

### Routes

| Route | Method | Auth | Description |
|---|---|---|---|
| `/health` | GET | No | ALB health check — pings RDS |
| `/` | GET | No | Landing page |
| `/register` | GET/POST | No | User registration |
| `/login` | GET/POST | No | User login |
| `/logout` | GET | No | Clear session |
| `/jobs` | GET | No | Browse/search jobs |
| `/jobs/<id>` | GET | No | Job detail + offers |
| `/jobs/<id>/offer` | POST | Yes | Submit offer |
| `/post` | GET/POST | Yes | Post a new job |

### `nginx.conf`

```nginx
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

---

## 4. Local Development

```bash
# Set environment variables
export SECRET_KEY=any-local-dev-string
export DB_HOST=localhost
export DB_USER=root
export DB_PASS=yourpassword
export DB_NAME=taskmatch

# Install dependencies
pip install -r requirements.txt

# Initialise the database (run once)
mysql -u root -p < deploy/schema.sql

# Start the app
python app.py
# Open http://localhost:8080
```

---

## 5. AWS Deployment Guide

Region: **us-east-1**

---

### Step 1 — VPC and Networking

**Create VPC**

| Setting   | Value           |
|-----------|-----------------|
| Name      | `taskmatch-vpc` |
| IPv4 CIDR | `10.0.0.0/16`   |

**Create 6 subnets across 2 AZs:**

| Name                   | AZ       | CIDR          | Type    |
|------------------------|----------|---------------|---------|
| `public-subnet-1`      | us-east-1a | `10.0.1.0/24` | Public  |
| `public-subnet-2`      | us-east-1b | `10.0.2.0/24` | Public  |
| `private-app-subnet-1` | us-east-1a | `10.0.3.0/24` | Private |
| `private-app-subnet-2` | us-east-1b | `10.0.4.0/24` | Private |
| `private-db-subnet-1`  | us-east-1a | `10.0.5.0/24` | Private |
| `private-db-subnet-2`  | us-east-1b | `10.0.6.0/24` | Private |

Enable **Auto-assign public IPv4** on the two public subnets only.

**Create Internet Gateway**

- Name: `taskmatch-igw`
- Attach to `taskmatch-vpc`

**Create NAT Gateway** (one only — cost saving)

- Name: `taskmatch-nat`
- Subnet: `public-subnet-1`
- Allocate a new Elastic IP

**Route Tables**

*Public route table* (`taskmatch-rt-public`):

| Destination   | Target          |
|---------------|-----------------|
| `10.0.0.0/16` | local           |
| `0.0.0.0/0`   | `taskmatch-igw` |

Associate with: `public-subnet-1`, `public-subnet-2`

*Private route table* (`taskmatch-rt-private`):

| Destination   | Target          |
|---------------|-----------------|
| `10.0.0.0/16` | local           |
| `0.0.0.0/0`   | `taskmatch-nat` |

Associate with: all 4 private subnets

---

### Step 2 — Security Groups

Create in this order — each references the one before it.

**SG-ALB** (`taskmatch-sg-alb`) — create first:

| Type    | Port | Source        |
|---------|------|---------------|
| Inbound | 80   | `0.0.0.0/0`  |
| Inbound | 443  | `0.0.0.0/0`  |

**SG-App** (`taskmatch-sg-app`) — create second:

| Type    | Port | Source                            |
|---------|------|-----------------------------------|
| Inbound | 80   | `taskmatch-sg-alb` (SG ID, not CIDR) |

> Port 80 source must be the ALB **security group ID**. No SSH rule. No key pair.

**SG-DB** (`taskmatch-sg-db`) — create third:

| Type    | Port | Source                            |
|---------|------|-----------------------------------|
| Inbound | 3306 | `taskmatch-sg-app` (SG ID, not CIDR) |

---

### Step 3 — IAM Role for EC2

**Create IAM Role** (`taskmatch-ec2-role`):

- Trusted entity: EC2
- Attach managed policies:
  - `AmazonSSMManagedInstanceCore` — enables SSM Session Manager (no SSH needed)
  - `AmazonS3ReadOnlyAccess` — pull app bundle from S3
  - `AmazonSSMReadOnlyAccess` — read secrets from SSM Parameter Store

---

### Step 4 — SSM Parameter Store

Create these 5 parameters in **Systems Manager → Parameter Store** (us-east-1) before launching any EC2 instances. `DB_PASS` and `SECRET_KEY` must be **SecureString**. The rest are **String**.

| Name | Type | Value |
|---|---|---|
| `/taskmatch/DB_HOST` | String | Your RDS endpoint |
| `/taskmatch/DB_USER` | String | `admin` |
| `/taskmatch/DB_NAME` | String | `taskmatch` |
| `/taskmatch/DB_PASS` | SecureString | Your RDS password |
| `/taskmatch/SECRET_KEY` | SecureString | Run `openssl rand -hex 32` and paste the output |

Every EC2 instance reads these at boot — this ensures all instances share the same `SECRET_KEY`, which is required for session cookies to work across instances.

---

### Step 5 — S3 Bucket

**Create bucket** (`taskmatch-deploy-k3dmx`):

- Region: `us-east-1`
- Block all public access: ON
- Default encryption: SSE-S3

**Package and upload the app:**

```bash
# On your local machine — exclude userdata.sh (goes into Launch Template, not the bundle)
tar -czf taskmatch-deploy-k3dmx.tar.gz \
  app.py requirements.txt nginx.conf templates/ deploy/schema.sql

# Upload to S3
aws s3 cp taskmatch-deploy-k3dmx.tar.gz s3://taskmatch-deploy-k3dmx/ --region us-east-1
```

---

### Step 6 — RDS MySQL

**Create DB Subnet Group** (`taskmatch-db-subnets`):

- VPC: `taskmatch-vpc`
- Subnets: `private-db-subnet-1` + `private-db-subnet-2`

**Create RDS Instance:**

| Setting               | Value                    |
|-----------------------|--------------------------|
| Engine                | MySQL 8.0                |
| Template              | Dev/Test                 |
| DB instance ID        | `taskmatch-db`           |
| Master username       | `admin`                  |
| Master password       | Store in SSM (see above) |
| Instance class        | `db.t3.micro`            |
| Storage               | 20 GB GP3                |
| Multi-AZ              | **Yes**                  |
| VPC                   | `taskmatch-vpc`          |
| Subnet group          | `taskmatch-db-subnets`   |
| Public access         | **No**                   |
| Security group        | `taskmatch-sg-db`        |
| Encryption at rest    | **Enabled**              |
| Initial database name | `taskmatch`              |

Copy the **RDS Endpoint** and save it as the `/taskmatch/DB_HOST` SSM parameter.

**Initialise the schema** — SSM Session Manager into one EC2 instance after it boots, then run:

```bash
cd /opt/taskmatch
source /etc/taskmatch.env
mysql -h $DB_HOST -u $DB_USER -p$DB_PASS $DB_NAME < deploy/schema.sql
mysql -h $DB_HOST -u $DB_USER -p$DB_PASS $DB_NAME -e "SHOW TABLES;"
```

Expected output: `jobs`, `offers`, `users`

Run on **one instance only** — schema applies to the shared RDS database.

---

### Step 7 — Launch Template + Auto Scaling Group

### `deploy/userdata.sh`

Runs on every new EC2 instance at boot. Fetches secrets from SSM, pulls the app from S3, installs dependencies, configures Nginx, and starts Gunicorn. All output is logged to `/var/log/userdata.log`.

```bash
#!/bin/bash
exec > /var/log/userdata.log 2>&1
set -e

# ---- Only two things you edit ----
S3_BUCKET="taskmatch-deploy-k3dmx"
REGION="us-east-1"

echo "[1/8] Installing packages..."
dnf update -y
dnf install -y python3-pip python3 nginx awscli mariadb105

echo "[2/8] Fetching secrets from SSM Parameter Store..."
get_param() {
    aws ssm get-parameter \
        --name "$1" \
        --with-decryption \
        --query "Parameter.Value" \
        --output text \
        --region "$REGION"
}

DB_HOST=$(get_param "/taskmatch/DB_HOST")
DB_USER=$(get_param "/taskmatch/DB_USER")
DB_PASS=$(get_param "/taskmatch/DB_PASS")
DB_NAME=$(get_param "/taskmatch/DB_NAME")
SECRET_KEY=$(get_param "/taskmatch/SECRET_KEY")

echo "[3/8] Writing env file..."
cat > /etc/taskmatch.env <<EOF
DB_HOST=$DB_HOST
DB_USER=$DB_USER
DB_PASS=$DB_PASS
DB_NAME=$DB_NAME
SECRET_KEY=$SECRET_KEY
EOF
chmod 600 /etc/taskmatch.env

echo "[4/8] Pulling app bundle from S3..."
mkdir -p /opt/taskmatch
aws s3 cp "s3://$S3_BUCKET/taskmatch-deploy-k3dmx.tar.gz" /tmp/ --region "$REGION"
tar -xzf /tmp/taskmatch-deploy-k3dmx.tar.gz -C /opt/taskmatch

echo "[5/8] Setting up Python venv and installing dependencies..."
cd /opt/taskmatch
python3 -m venv venv
source venv/bin/activate
pip install cryptography
pip install -r requirements.txt

echo "[6/8] Configuring Nginx..."
cp /opt/taskmatch/nginx.conf /etc/nginx/conf.d/taskmatch.conf
sed -i '/^    server {/,/^    }/d' /etc/nginx/nginx.conf
nginx -t && systemctl restart nginx

echo "[7/8] Creating and starting Gunicorn systemd service..."
cat > /etc/systemd/system/taskmatch.service <<UNIT
[Unit]
Description=TaskMatch Flask App
After=network.target

[Service]
WorkingDirectory=/opt/taskmatch
EnvironmentFile=/etc/taskmatch.env
ExecStart=/opt/taskmatch/venv/bin/gunicorn -w 2 -b 127.0.0.1:5000 app:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable taskmatch
systemctl start taskmatch

echo "[8/8] === Boot complete ==="
```

**Notes:**
- Uses `dnf` — this is **Amazon Linux 2023** (not Ubuntu)
- `mariadb105` provides the `mysql` client command for schema initialisation
- `pip install cryptography` runs before `requirements.txt` to avoid dependency failures
- Nginx config goes to `/etc/nginx/conf.d/` (AL2023 style, not `sites-available`)
- Secrets come from SSM Parameter Store — nothing sensitive is hardcoded
- Check boot progress: `sudo cat /var/log/userdata.log` — look for `[8/8] === Boot complete ===`

**Create Launch Template** (`taskmatch-lt`):

| Setting              | Value                        |
|----------------------|------------------------------|
| AMI                  | Amazon Linux 2023            |
| Instance type        | `t3.micro`                   |
| IAM instance profile | `taskmatch-ec2-role`         |
| Security group       | `taskmatch-sg-app`           |
| Key pair             | **None**                     |
| User data            | Paste `userdata.sh` contents |

**Create Auto Scaling Group** (`taskmatch-asg`):

| Setting            | Value                                          |
|--------------------|------------------------------------------------|
| Launch template    | `taskmatch-lt`                                 |
| VPC subnets        | `private-app-subnet-1`, `private-app-subnet-2` |
| Min capacity       | 2                                              |
| Desired capacity   | 2                                              |
| Max capacity       | 4                                              |
| Health check type  | **ELB** (not EC2)                              |
| Health check grace | 300 seconds                                    |

Add **Target Tracking Scaling Policy**: average CPU at 60%.

---

### Step 8 — Application Load Balancer

**Create Target Group** (`taskmatch-tg`):

| Setting           | Value           |
|-------------------|-----------------|
| Target type       | Instance        |
| Protocol          | HTTP            |
| Port              | 80              |
| VPC               | `taskmatch-vpc` |
| Health check path | `/health`       |
| Success codes     | 200             |

**Create ALB** (`taskmatch-alb`):

| Setting        | Value                                   |
|----------------|-----------------------------------------|
| Scheme         | Internet-facing                         |
| Subnets        | `public-subnet-1`, `public-subnet-2`    |
| Security group | `taskmatch-sg-alb`                      |
| Listener       | HTTP:80 → forward to `taskmatch-tg`     |

Attach `taskmatch-tg` to the Auto Scaling Group.

**Your app is live at:** `http://taskmatch-alb-xxxx.us-east-1.elb.amazonaws.com`

> Use `http://` not `https://` — no SSL certificate is configured.

---

## 6. Mandatory Testing

Screenshot every step. These are required by the rubric.

### Test 1 — High Availability (Instance Failure)

1. **EC2 → Instances** — screenshot showing 2 healthy instances in different AZs
2. **Terminate** one instance
3. **Target Groups → Targets** — screenshot showing one target draining/unhealthy
4. Wait 2–3 minutes
5. **EC2 → Instances** — screenshot showing ASG launched a replacement
6. **Target Groups → Targets** — screenshot showing 2 healthy targets again
7. Open ALB URL in browser — screenshot showing app still works

### Test 2 — Database Failover

1. **RDS → Databases → taskmatch-db** — screenshot showing Multi-AZ = Yes, note current AZ
2. **Actions → Reboot** with **"Reboot with failover"** checked
3. Wait ~1 minute
4. **RDS → Databases** — screenshot showing the AZ has changed
5. Open ALB URL, submit a new job — screenshot showing app still works
6. **RDS → Events** — screenshot showing the failover event

---

## 7. Cost Saving

To pause spending between sessions (e.g. overnight):

**Turn off:**

1. **ASG → Edit → Min = 0, Desired = 0** — terminates all EC2 instances
2. **RDS → Actions → Stop temporarily** — stops DB compute, keeps data
3. **VPC → NAT Gateways → Delete** + **Elastic IPs → Release** — saves ~$1/day

**Keep running** (cheap):
- ALB — ~$0.19/day, keep so DNS name stays the same

**Before demo — do 10 mins before:**

1. Recreate NAT Gateway in `public-subnet-1`, update private route table `0.0.0.0/0 → new NAT`
2. RDS → Start
3. ASG → Min = 2, Desired = 2
4. Wait ~5 mins for instances to show healthy in Target Group

---

## 8. Marking Rubric Checklist

**Architecture Design & Cloud Implementation (12%)**

- [ ] VPC with `10.0.0.0/16` CIDR in us-east-1
- [ ] 6 subnets across 2 AZs (2 public, 2 private app, 2 private DB)
- [ ] Internet Gateway attached to VPC
- [ ] NAT Gateway in public subnet with Elastic IP
- [ ] Public route table → IGW, private route table → NAT
- [ ] ALB in public subnets, internet-facing
- [ ] Auto Scaling Group with min 2 instances in private app subnets
- [ ] RDS Multi-AZ in private DB subnets with DB subnet group
- [ ] All resources correctly configured and connected

**Scalability, HA & Fault Tolerance (10%)**

- [ ] ASG health check type set to ELB (not EC2)
- [ ] Target tracking scaling policy on CPU
- [ ] Instance failure test completed with screenshots
- [ ] DB failover test completed with screenshots
- [ ] App remained accessible throughout both tests

**Security & Best Practices (8%)**

- [ ] SG-ALB: inbound 80/443 from `0.0.0.0/0`
- [ ] SG-App: inbound 80 from SG-ALB (security group ID, not CIDR)
- [ ] SG-DB: inbound 3306 from SG-App (security group ID, not CIDR)
- [ ] No SSH key pair on any instance
- [ ] IAM role with `AmazonSSMManagedInstanceCore` attached to EC2
- [ ] Secrets stored in SSM Parameter Store (not hardcoded)
- [ ] RDS not publicly accessible
- [ ] RDS encryption at rest enabled
- [ ] S3 bucket: public access blocked, default encryption on

**Application Functionality & Documentation (5%)**

- [ ] Homepage loads and displays job listings from RDS
- [ ] Register, login, post job, make offer all work end-to-end
- [ ] Architecture diagram included in submission
- [ ] Design document is clear and concise

**Teamwork & Collaboration (5%)**

- [ ] Equal contribution documented
- [ ] Presentation is clear and professional
