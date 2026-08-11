# IntelliResume Backend

## Overview

IntelliResume Backend is a Django REST Framework application that provides APIs for resume analysis and future AI integration.

## Tech Stack

- Python 3.14.3
- Django 6.0.7
- Django REST Framework
- django-cors-headers
- python-dotenv
- SQLite

## Project Structure

```
backend/
├── api/
├── config/
├── manage.py
├── requirements.txt
└── .env
```

## Setup

### 1. Create Virtual Environment

```bash
python -m venv venv
```

### 2. Activate Virtual Environment

```bash
venv\Scripts\Activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Apply Migrations

```bash
python manage.py migrate
```

### 5. Run Server

```bash
python manage.py runserver
```

## Health Check API

**Endpoint**

```
GET /api/health/
```

**Response**

```json
{
    "status": "OK"
}
```

## Local Ollama setup

Resume parsing uses a locally running Ollama model. Install Ollama from
https://ollama.com/download, start the local server (the desktop application
starts it automatically, or run `ollama serve`), then download a compact model:

```bash
ollama pull llama3.2:latest
```

Set these values in `.env` before running Django:

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2:latest
LLM_TIMEOUT=120
LLM_MAX_RETRIES=2
LLM_TEMPERATURE=0
```

The model is never called by views directly. Failed local LLM processing keeps
the existing upload API response unchanged and marks the uploaded resume as
`failed`; diagnostic details are written only to server logs.

## Production deployment with Docker Compose

Docker Compose is the supported production deployment for this repository. It
runs Django and Ollama on a private Docker network; Ollama does not publish
port `11434`. The backend is bound only to host loopback (`127.0.0.1:8000`) so
a host reverse proxy can be the only internet-facing component.

### Prerequisites

- Docker Engine with the Compose plugin.
- At least 8 GB RAM, 4 CPU cores, and 10 GB free disk for `llama3.2:latest`.
  The verified development host has 15.7 GB RAM, 12 physical cores, and
  118.4 GB free disk. The Compose limits reserve up to 6 GB/6 CPUs for Ollama
  and 1 GB/2 CPUs for Django; review these limits for the target server.
- A production `.env` created from `.env.example`. Use a strong unique
  `SECRET_KEY` and set the model name. Do not commit this file.

For Compose, keep `OLLAMA_MODEL` in `.env`, but the Compose file automatically
uses `http://ollama:11434` inside the private network. Direct host development
continues to use `http://localhost:11434`.

### Start and prepare the model

```bash
docker compose up -d --build
docker compose exec ollama ollama pull llama3.2:latest
docker compose ps
curl http://127.0.0.1:8000/api/health/
curl http://127.0.0.1:8000/api/ready/
```

Model download is intentionally an explicit administrator action. Compose and
the application never pull a missing model automatically. Until it is pulled,
the backend remains alive and `/api/ready/` returns `503` with
`LLM_MODEL_UNAVAILABLE`.

### Operations

```bash
# Stop / restart the complete deployment
docker compose down
docker compose restart

# Restart one service (the other stays running)
docker compose restart ollama
docker compose restart backend

# Status and logs
docker compose ps
docker compose logs -f backend
docker compose logs -f ollama

# Confirm the configured model is present
docker compose exec ollama ollama list
```

After changing `OLLAMA_MODEL`, edit `.env`, pull that exact model with
`docker compose exec ollama ollama pull <model-name>`, then run
`docker compose up -d`. Ollama data is persisted in the `ollama-data` Docker
volume, while SQLite and uploaded media use separate backend volumes. Server
reboots and container restarts retain these volumes; both services use
`restart: unless-stopped`.

`/api/health/` remains the existing liveness endpoint. `/api/ready/` is a
separate operational readiness check: it calls the configured `LLMService`,
checks Ollama's `/api/tags` response, and confirms `OLLAMA_MODEL` exists. It
does not send a resume, prompt, or LLM response to logs.

If Ollama is temporarily unavailable, the backend stays running and AI uploads
continue to follow the existing safe failure behavior. If Ollama crashes,
Compose restarts it and its health check waits for the local API to respond.
