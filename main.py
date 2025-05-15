from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import os
import json
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
import datetime
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

load_dotenv()

ASANA_ACCESS_TOKEN = os.getenv("ASANA_ACCESS_TOKEN")
ASANA_WORKSPACE_ID = os.getenv("ASANA_WORKSPACE_ID")
ASANA_PROJECT_ID = os.getenv("ASANA_PROJECT_ID")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_DEFAULT_CHANNEL = os.getenv("SLACK_DEFAULT_CHANNEL", "#general")

app = FastAPI()

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models
class AsanaEvent(BaseModel):
    action: Optional[str] = None
    resource: Optional[Dict[str, Any]] = None
    user: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    parent: Optional[Dict[str, Any]] = None
    change: Optional[Dict[str, Any]] = None

class WebhookPayload(BaseModel):
    events: List[AsanaEvent] = []

class SlackMessage(BaseModel):
    text: str
    channel: Optional[str] = SLACK_DEFAULT_CHANNEL
    blocks: Optional[List[Dict[str, Any]]] = None

def get_asana_client():
    return httpx.Client(
        base_url="https://app.asana.com/api/1.0",
        headers={
            "Authorization": f"Bearer {ASANA_ACCESS_TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        },
        timeout=30.0
    )

# Initialize Slack client
slack_client = WebClient(token=SLACK_BOT_TOKEN)

@app.get("/")
async def root():
    return {"status": "healthy", "service": "Asana Webhook API"}

@app.post("/webhook")
async def asana_webhook(request: Request, payload: Optional[WebhookPayload] = None):
    # Handle webhook verification (handshake)
    x_hook_secret = request.headers.get("X-Hook-Secret")
    if x_hook_secret:
        print(f"\n🤝 Asana handshake received at {datetime.datetime.now().isoformat()}")
        return Response(status_code=200, headers={"X-Hook-Secret": x_hook_secret})

    if not payload or not payload.events:
        return Response(status_code=200, content="Acknowledged empty or invalid payload.")

    # Process webhook events
    print("\n🔄 Received Asana webhook event:")
    for event_data in payload.events:
        resource = event_data.resource if event_data.resource else {}
        resource_gid = resource.get("gid", "N/A")
        resource_type = resource.get("resource_type", "N/A")
        action = event_data.action
        
        print(f"  Event Action: {action}")
        print(f"  Resource GID: {resource_gid}")
        print(f"  Resource Type: {resource_type}")

    return {"status": "success", "message": "Webhook event processed"}

@app.post("/register-webhook")
async def register_webhook(request: Request):
    if not all([ASANA_ACCESS_TOKEN, ASANA_PROJECT_ID]):
        raise HTTPException(status_code=500, detail="Missing required Asana configuration")
    
    try:
        print("\n🚀 Registering webhook with Asana...")
        with get_asana_client() as client:
            target_url = "https://glassboxapi.onrender.com/webhook"
            
            registration_payload = {
                "data": {
                    "resource": ASANA_PROJECT_ID,
                    "target": target_url,
                    "filters": [
                        {
                            "resource_type": "project",
                            "action": "changed"
                        }
                    ]
                }
            }

            response = client.post("/webhooks", json=registration_payload)
            response.raise_for_status()
            
            webhook_data = response.json().get('data', {})
            print(f"\n✅ Webhook registered successfully:")
            print(f"  Webhook GID: {webhook_data.get('gid')}")
            print(f"  Target URL: {webhook_data.get('target')}")
            print(f"  Active: {webhook_data.get('active')}")
            
            return {"status": "success", "webhook_details": webhook_data}
            
    except Exception as e:
        print(f"❌ Error registering webhook: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/list-webhooks")
async def list_existing_webhooks():
    if not all([ASANA_ACCESS_TOKEN, ASANA_WORKSPACE_ID]):
        raise HTTPException(status_code=500, detail="Missing required Asana configuration")
    
    try:
        with get_asana_client() as client:
            response = client.get(
                "/webhooks",
                params={"workspace": ASANA_WORKSPACE_ID}
            )
            response.raise_for_status()
            webhooks_data = response.json()
            
            return {
                "status": "success",
                "webhooks": webhooks_data.get("data", [])
            }
            
    except Exception as e:
        print(f"❌ Error listing webhooks: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/send-slack-message")
async def send_slack_message(message: SlackMessage):
    """Send a message to a Slack channel."""
    try:
        # Prepare the message payload
        payload = {
            "channel": message.channel,
            "text": message.text
        }
        
        # Add blocks if provided
        if message.blocks:
            payload["blocks"] = message.blocks
            
        # Send the message
        response = slack_client.chat_postMessage(**payload)
        
        return {
            "status": "success",
            "message": "Message sent to Slack",
            "slack_response": response
        }
        
    except SlackApiError as e:
        print(f"❌ Error sending Slack message: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send Slack message: {str(e)}"
        )
    except Exception as e:
        print(f"❌ Unexpected error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
