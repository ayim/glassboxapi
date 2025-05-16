# Asana Project Webhook API

A FastAPI backend that integrates with Asana to track assigned projects through webhooks.

## Setup

1. Create a `.env` file based on `.env.example` and add your Asana credentials:
   - `ASANA_ACCESS_TOKEN`: Your Asana Personal Access Token
   - `ASANA_WORKSPACE_ID`: Your Asana Workspace ID

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Run the server:
```bash
uvicorn main:app --reload
```

## API Endpoints

- `GET /`: Health check endpoint
- `POST /webhook`: Asana webhook endpoint that receives project updates
- `GET /projects`: Lists all projects with assignees in the workspace

## Setting up Asana Webhook

1. Create a webhook in Asana pointing to your endpoint: `https://your-domain.com/webhook`
2. Asana will send events to this endpoint whenever project changes occur
3. The API will track projects and their assignments
