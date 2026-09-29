# Secure Distributed Library Management System

A secure distributed library management system built as a final project for **Security of Distributed Systems**.

The system follows a microservices-based architecture and includes authentication, role-based access control, secure file handling, asynchronous processing with RabbitMQ, centralized auditing, and an AI/RAG component using Qdrant for vector search.

## Architecture

```text
                         ┌─────────────────────┐
                         │       Client        │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │   Nginx API Gateway │
                         │ HTTPS / Rate Limit  │
                         └──────────┬──────────┘
                                    │
          ┌─────────────┬───────────┼───────────┬──────────────┐
          ▼             ▼           ▼           ▼              ▼
     Auth Service  Library Service  Loan     Notification   Audit
                                  Service      Service       Service
          │             │           │           │              │
          └─────────────┴───────────┴───────────┴──────────────┘
                                    │
                                    ▼
                              PostgreSQL

     Library Upload
           │
           ▼
       RabbitMQ
           │
           ▼
     Worker Service
           │
           ▼
   Library Internal API

     AI Knowledge
           │
           ▼
      AI Service
           │
           ▼
       Qdrant
     Vector Database
```

## Services

### Auth Service

Handles:

* User registration and login
* JWT authentication
* bcrypt password hashing
* Role-based access control (RBAC)
* Google OAuth callbacks
* User profile endpoint

### Library Service

Handles:

* Book CRUD operations for administrators
* Book categories
* Secure document uploads
* Cover uploads
* File encryption using Fernet
* SHA-256 integrity verification
* Administrative dashboard

### Loan Service

Handles:

* Book borrowing workflow
* Reservations
* Loan and request management
* Ownership checks

### Notification Service

Provides an internal endpoint used by background worker jobs for notifications.

### Audit Service

Provides:

* Centralized audit logging
* Monitoring metrics
* Audit log storage

### AI Service

Provides an AI/RAG-style component for the library knowledge base:

* Knowledge indexing
* Vector search
* Simple chatbot endpoint
* Qdrant integration

### Worker Service

Consumes background jobs from RabbitMQ.

Current background flows include:

```text
Document Upload
      ↓
   RabbitMQ
      ↓
 Worker Service
      ↓
Library Internal API
```

and:

```text
Book Borrowing
      ↓
   RabbitMQ
      ↓
 Worker Service
      ↓
Notification Service
```

### Nginx

Acts as the public API gateway.

It provides:

* HTTPS termination
* HTTP to HTTPS redirection
* Request size limiting
* Security headers
* Rate limiting

Internal services are not directly exposed to the public.

## Infrastructure

The project uses:

| Component        | Purpose                                               |
| ---------------- | ----------------------------------------------------- |
| PostgreSQL       | Users, roles, books, documents, loans, and audit logs |
| RabbitMQ         | Asynchronous message processing                       |
| Qdrant           | Vector database for AI/RAG search                     |
| Nginx            | API gateway and TLS termination                       |
| Docker Compose   | Service orchestration                                 |
| Encrypted volume | Storage for uploaded documents                        |

## Security Features

The project demonstrates several distributed-system security controls:

* JWT-based authentication
* bcrypt password hashing
* Role-based access control
* Google OAuth integration
* HTTPS with a self-signed certificate
* Nginx rate limiting
* Security headers
* Input validation using Pydantic
* Secure file upload validation
* File size and MIME-type checks
* Safe filename handling
* Fernet file encryption
* SHA-256 integrity verification
* Internal service authentication using `X-Internal-Token`
* Least-privilege database users
* Custom RabbitMQ credentials
* Centralized audit logging
* Safe HTTP error responses
* Environment-based secret configuration

## Database

PostgreSQL is used for the main application data, including:

* Users
* Roles
* Books
* Documents
* Loans
* Audit logs

Database service accounts are configured with least-privilege access.

## Message Queue

RabbitMQ is used for asynchronous background processing.

### Document Processing

```text
Library Service
      │
      │ document job
      ▼
  RabbitMQ
      │
      ▼
Worker Service
      │
      │ internal API
      ▼
Library Service
```

### Borrowing Notifications

```text
Loan Service
      │
      │ notification job
      ▼
  RabbitMQ
      │
      ▼
Worker Service
      │
      ▼
Notification Service
```

## AI / RAG Component

The project includes an AI service for knowledge retrieval.

The general flow is:

```text
Knowledge
   │
   ▼
AI Service
   │
   ▼
Qdrant
   │
   ▼
Vector Search
   │
   ▼
Chat Endpoint
```

Available AI endpoints include:

```text
/rag/index
/rag/search
/chat
```

## Running the Project

### Requirements

The project uses Docker Compose to run its services.

### 1. Initialize the Environment

From the project root:

```powershell
.\scripts\init-env.ps1
```

This prepares the environment configuration.

### 2. Start the Services

```powershell
docker compose up --build
```

### 3. Open the Application

API Gateway:

```text
https://localhost:3443
```

HTTP redirect endpoint:

```text
http://localhost:3000
```

RabbitMQ Management UI:

```text
http://127.0.0.1:15672
```

Admin dashboard:

```text
https://localhost:3443/library/admin/dashboard
```

The TLS certificate is self-signed and generated by the Nginx container on the first run, so browsers and `curl` may display a certificate warning.

## Environment Configuration

The project uses `.env` for environment-specific configuration.

A template is provided in:

```text
.env.example
```

The actual `.env` file should not be committed to the repository.

The default administrator account is configured through environment variables:

```env
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=StrongPass123
ADMIN_FULL_NAME=System Admin
```

Self-registration creates `user` accounts only. Administrative access is controlled by the authentication service.

## Google OAuth Setup

Google OAuth is implemented but requires external credentials.

### 1. Create Google OAuth Credentials

Create OAuth 2.0 credentials through Google Cloud.

### 2. Configure the Redirect URI

```text
https://localhost:3443/auth/oauth/google/callback
```

### 3. Add the Credentials to `.env`

```env
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
OAUTH_REDIRECT_BASE=https://localhost:3443
```

The OAuth login endpoint is:

```text
https://localhost:3443/auth/oauth/google/login
```

The login page also provides a Google login option.

## Demo

The API can be tested through the gateway using `curl`.

Because the local TLS certificate is self-signed, use:

```powershell
curl -k
```

### Login

```powershell
$ADMIN = (curl -k -s -X POST https://localhost:3443/auth/login `
  -H "Content-Type: application/json" `
  -d '{"email":"admin@example.com","password":"StrongPass123"}' |
  ConvertFrom-Json).access_token
```

### Protected Endpoint

Without a token:

```powershell
curl -k https://localhost:3443/auth/me
```

With a valid token:

```powershell
curl -k https://localhost:3443/auth/me `
  -H "Authorization: Bearer $ADMIN"
```

### Create a Book

```powershell
curl -k -X POST https://localhost:3443/library/books `
  -H "Authorization: Bearer $ADMIN" `
  -H "Content-Type: application/json" `
  -d '{"title":"Distributed Systems","author":"Tanenbaum","isbn":"1234567890","total_copies":3}'
```

### Upload a Document

```powershell
"safe document" | Set-Content sample.txt

curl -k -X POST https://localhost:3443/library/documents/upload `
  -H "Authorization: Bearer $ADMIN" `
  -F "file=@sample.txt;type=text/plain"
```

## Security Test Examples

The project includes example tests for common invalid requests.

### Invalid Login

```powershell
curl -k -X POST https://localhost:3443/auth/login `
  -H "Content-Type: application/json" `
  -d '{"email":"admin@example.com","password":"wrong"}'
```

### Invalid JWT

```powershell
curl -k https://localhost:3443/auth/me `
  -H "Authorization: Bearer invalid-token"
```

### Invalid Book Input

```powershell
curl -k -X POST https://localhost:3443/library/books `
  -H "Authorization: Bearer $ADMIN" `
  -H "Content-Type: application/json" `
  -d '{"title":"Bad","author":"Bad","isbn":"bad","total_copies":-1}'
```

### Blocked File Type

```powershell
"bad" | Set-Content virus.exe

curl -k -X POST https://localhost:3443/library/documents/upload `
  -H "Authorization: Bearer $ADMIN" `
  -F "file=@virus.exe;type=application/octet-stream"
```

Expected response:

```json
{"detail":"File type is not allowed"}
```

### Rate Limiting

```powershell
1..12 | ForEach-Object {
    curl -k -s -o NUL -w "%{http_code}`n" https://localhost:3443/auth/me
}
```

## Requirement Mapping

| Requirement                 | Implementation                             |
| --------------------------- | ------------------------------------------ |
| Authentication              | `auth-service/main.py`                     |
| Password hashing            | bcrypt in `common/security.py`             |
| RBAC                        | JWT roles and `require_role`               |
| OAuth                       | Google OAuth routes                        |
| API Gateway                 | `nginx/nginx.conf`                         |
| HTTPS                       | Nginx TLS termination                      |
| Rate limiting               | Nginx `limit_req_zone`                     |
| Input validation            | Pydantic models and validation             |
| Secure file upload          | Extension, size, MIME, and filename checks |
| File encryption             | Fernet                                     |
| Integrity verification      | SHA-256                                    |
| Service-to-service security | `X-Internal-Token`                         |
| Secrets management          | `.env` and `.env.example`                  |
| Database security           | Least-privilege database users             |
| Message queue               | RabbitMQ `document_jobs`                   |
| Queue security              | Environment-based RabbitMQ credentials     |
| Audit logging               | Audit service and `audit_logs`             |
| AI service                  | `ai-service/main.py`                       |
| Vector database             | Qdrant                                     |
| Monitoring                  | Admin dashboard and audit metrics          |
| Error handling              | Safe HTTP errors and container logs        |
| Container orchestration     | Docker Compose                             |

## Project Structure

```text
.
├── ai-service/
├── audit-service/
├── auth-service/
├── common/
├── db/
├── library-service/
├── loan-service/
├── nginx/
├── notification-service/
├── scripts/
├── worker-service/
├── docker-compose.yml
├── Dockerfile.service
├── requirements.txt
├── .env.example
└── README.md
```

## Notes

* The local HTTPS certificate is self-signed for development.
* Google OAuth requires external Google credentials.
* The actual `.env` file is intentionally excluded from version control.
* The system is designed to run through Docker Compose.

