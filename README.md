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
