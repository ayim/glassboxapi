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

# Asana OAuth configuration
ASANA_CLIENT_ID = os.getenv("ASANA_CLIENT_ID")
ASANA_CLIENT_SECRET = os.getenv("ASANA_CLIENT_SECRET")
ASANA_ACCESS_TOKEN = os.getenv("ASANA_ACCESS_TOKEN")  # This should be the OAuth token
ASANA_WORKSPACE_ID = os.getenv("ASANA_WORKSPACE_ID")
ASANA_PROJECT_ID = os.getenv("ASANA_PROJECT_ID")

# Slack configuration
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
    if not ASANA_ACCESS_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="Missing Asana OAuth access token. Please authenticate first."
        )
    
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

@app.get("/auth/asana")
async def asana_auth(request: Request):
    """Generate Asana OAuth authorization URL."""
    if not ASANA_CLIENT_ID:
        raise HTTPException(
            status_code=500,
            detail="Missing Asana client ID"
        )
    
    # Generate the authorization URL with the correct redirect URI
    redirect_uri = f"{request.base_url}auth/callback"
    auth_url = (
        f"https://app.asana.com/-/oauth_authorize"
        f"?client_id={ASANA_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope=webhooks:write%20webhooks:read%20projects:read"
    )
    
    print(f"\n🔑 Generated Asana OAuth URL:")
    print(f"  Redirect URI: {redirect_uri}")
    print(f"  Auth URL: {auth_url}")
    
    return {"auth_url": auth_url}

@app.get("/auth/callback")
async def asana_callback(request: Request, code: str):
    """Handle Asana OAuth callback and exchange code for access token."""
    if not all([ASANA_CLIENT_ID, ASANA_CLIENT_SECRET]):
        raise HTTPException(
            status_code=500,
            detail="Missing Asana OAuth configuration"
        )
    
    try:
        # Get the redirect URI from the request
        redirect_uri = f"{request.base_url}auth/callback"
        print(f"\n🔄 Exchanging OAuth code for token:")
        print(f"  Redirect URI: {redirect_uri}")
        
        # Exchange the code for an access token
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://app.asana.com/-/oauth_token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": ASANA_CLIENT_ID,
                    "client_secret": ASANA_CLIENT_SECRET,
                    "redirect_uri": redirect_uri,
                    "code": code
                }
            )
            response.raise_for_status()
            token_data = response.json()
            
            print(f"  ✅ Successfully obtained access token")
            print(f"  Token type: {token_data.get('token_type')}")
            print(f"  Expires in: {token_data.get('expires_in')} seconds")
            
            # Store the access token (in production, use a secure storage)
            global ASANA_ACCESS_TOKEN
            ASANA_ACCESS_TOKEN = token_data["access_token"]
            
            return {
                "status": "success",
                "message": "Successfully authenticated with Asana",
                "token_type": token_data["token_type"],
                "expires_in": token_data["expires_in"]
            }
            
    except httpx.HTTPStatusError as e:
        error_body = e.response.text
        try:
            error_json = json.loads(error_body)
            error_message = error_json.get('error_description', str(e))
        except:
            error_message = str(e)
        
        print(f"  ❌ OAuth error: {error_message}")
        print(f"  Response status: {e.response.status_code}")
        print(f"  Response body: {error_body}")
        
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"OAuth error: {error_message}"
        )
    except Exception as e:
        print(f"  ❌ Unexpected error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )

@app.post("/webhook")
async def asana_webhook(request: Request, payload: Optional[WebhookPayload] = None):
    """Handle Asana webhook events and handshake."""
    # Log all incoming requests
    print(f"\n📥 Received webhook request at {datetime.datetime.now().isoformat()}")
    print(f"  Headers: {dict(request.headers)}")
    
    # Handle webhook verification (handshake)
    x_hook_secret = request.headers.get("X-Hook-Secret")
    if x_hook_secret:
        print(f"🤝 Asana handshake received")
        print(f"  X-Hook-Secret: {x_hook_secret}")
        # Return immediately with the same secret
        return Response(
            status_code=200,
            headers={"X-Hook-Secret": x_hook_secret},
            content="Handshake successful"
        )

    # Handle empty or invalid payloads
    if not payload or not payload.events:
        print("⚠️ Empty or invalid payload received")
        return Response(
            status_code=200,
            content="Acknowledged empty or invalid payload."
        )

    # Process webhook events
    print("\n🔄 Processing Asana webhook event:")
    for event_data in payload.events:
        resource = event_data.resource if event_data.resource else {}
        resource_gid = resource.get("gid", "N/A")
        resource_type = resource.get("resource_type", "N/A")
        action = event_data.action
        
        print(f"  Event Action: {action}")
        print(f"  Resource GID: {resource_gid}")
        print(f"  Resource Type: {resource_type}")

    return Response(
        status_code=200,
        content="Webhook event processed successfully"
    )

@app.post("/register-webhook")
async def register_webhook(request: Request):
    """Register a webhook with Asana."""
    print("\n🔍 Starting webhook registration process...")
    
    if not all([ASANA_ACCESS_TOKEN, ASANA_PROJECT_ID]):
        print("❌ Missing configuration:")
        print(f"  ASANA_ACCESS_TOKEN present: {bool(ASANA_ACCESS_TOKEN)}")
        print(f"  ASANA_PROJECT_ID present: {bool(ASANA_PROJECT_ID)}")
        raise HTTPException(status_code=500, detail="Missing required Asana configuration")
    
    try:
        print("\n🚀 Registering webhook with Asana...")
        print(f"  Access Token: {ASANA_ACCESS_TOKEN[:10]}...")  # Log first 10 chars of token
        
        print("  Creating Asana client...")
        client = get_asana_client()
        print("  ✅ Asana client created successfully")
        
        # Get the base URL from the request
        base_url = str(request.base_url).rstrip('/')
        target_url = f"{base_url}/webhook"
        print(f"  Using target URL: {target_url}")
        print(f"  For Asana Project ID: {ASANA_PROJECT_ID}")
        print(f"  Request headers: {dict(request.headers)}")
        print(f"  Request URL: {request.url}")

        # First, verify the project exists
        try:
            print("  🔍 Verifying project exists...")
            project_response = client.get(f"/projects/{ASANA_PROJECT_ID}")
            print("  Project response received")
            project_response.raise_for_status()
            print(f"  ✅ Project verification successful: {project_response.json()}")
        except Exception as e:
            print(f"  ❌ Project verification failed: {str(e)}")
            print(f"  Error type: {type(e)}")
            raise HTTPException(
                status_code=400,
                detail=f"Invalid project ID or insufficient permissions: {str(e)}"
            )

        # Prepare the registration payload
        print("  Preparing registration payload...")
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
        
        print(f"  Registration payload: {json.dumps(registration_payload, indent=2)}")
        
        # Try to register the webhook
        print("  About to attempt webhook registration...")
        try:
            print("  📤 Sending webhook registration request to Asana...")
            print("  Request URL: https://app.asana.com/api/1.0/webhooks")
            print("  Request Headers:", client.headers)
            
            response = client.post("/webhooks", json=registration_payload)
            print("  📥 Received response from Asana")
            print(f"  Response Status: {response.status_code}")
            print(f"  Response Headers: {dict(response.headers)}")
            
            response.raise_for_status()
            
            webhook_data = response.json().get('data', {})
            print(f"\n✅ Webhook registered successfully:")
            print(f"  Webhook GID: {webhook_data.get('gid')}")
            print(f"  Target URL: {webhook_data.get('target')}")
            print(f"  Active: {webhook_data.get('active')}")
            
            return {"status": "success", "webhook_details": webhook_data}
            
        except httpx.HTTPStatusError as e:
            error_body = e.response.text
            try:
                error_json = json.loads(error_body)
                error_message = error_json.get('errors', [{}])[0].get('message', str(e))
            except:
                error_message = str(e)
            
            print(f"  ❌ Asana API error: {error_message}")
            print(f"  Response status: {e.response.status_code}")
            print(f"  Response body: {error_body}")
            print(f"  Response headers: {dict(e.response.headers)}")
            
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"Asana API error: {error_message}"
            )
        except Exception as e:
            print(f"  ❌ Unexpected error during webhook registration: {str(e)}")
            print(f"  Error type: {type(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Unexpected error during webhook registration: {str(e)}"
            )
        
    except HTTPException:
        print("  ❌ HTTP Exception raised")
        raise
    except Exception as e:
        print(f"  ❌ Unexpected error: {str(e)}")
        print(f"  Error type: {type(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )

@app.get("/list-webhooks")
async def list_existing_webhooks():
    if not all([ASANA_ACCESS_TOKEN, ASANA_ACCESS_TOKEN]):
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

@app.get("/test-webhook")
async def test_webhook():
    """Test endpoint to verify webhook accessibility."""
    return {
        "status": "success",
        "message": "Webhook endpoint is accessible",
        "timestamp": datetime.datetime.now().isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
