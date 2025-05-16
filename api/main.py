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
import logging

load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

async def get_asana_client():
    if not ASANA_ACCESS_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="Missing Asana OAuth access token. Please authenticate first."
        )
    
    return httpx.AsyncClient(
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
    
    logger.info(f"\n🔑 Generated Asana OAuth URL:")
    logger.info(f"  Redirect URI: {redirect_uri}")
    logger.info(f"  Auth URL: {auth_url}")
    
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
        logger.info(f"\n🔄 Exchanging OAuth code for token:")
        logger.info(f"  Redirect URI: {redirect_uri}")
        
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
            
            logger.info(f"  ✅ Successfully obtained access token")
            logger.info(f"  Token type: {token_data.get('token_type')}")
            logger.info(f"  Expires in: {token_data.get('expires_in')} seconds")
            
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
        
        logger.error(f"  ❌ OAuth error: {error_message}")
        logger.error(f"  Response status: {e.response.status_code}")
        logger.error(f"  Response body: {error_body}")
        
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"OAuth error: {error_message}"
        )
    except Exception as e:
        logger.error(f"  ❌ Unexpected error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )

@app.post("/webhook")
async def asana_webhook(request: Request, payload: Optional[WebhookPayload] = None):
    """Handle Asana webhook events and handshake."""
    # Log all incoming requests
    logger.info(f"📥 Received webhook request at {datetime.datetime.now().isoformat()}")
    logger.info(f"Headers: {dict(request.headers)}")
    
    # Handle webhook verification (handshake)
    x_hook_secret = request.headers.get("X-Hook-Secret")
    if x_hook_secret:
        logger.info(f"🤝 Asana handshake received")
        logger.info(f"X-Hook-Secret: {x_hook_secret}")
        # Return immediately with the same secret
        return Response(
            status_code=200,
            headers={"X-Hook-Secret": x_hook_secret},
            content="Handshake successful"
        )

    # Handle empty or invalid payloads
    if not payload or not payload.events:
        logger.warning("⚠️ Empty or invalid payload received")
        return Response(
            status_code=200,
            content="Acknowledged empty or invalid payload."
        )

    # Process webhook events
    logger.info("🔄 Processing Asana webhook event:")
    for event_data in payload.events:
        resource = event_data.resource if event_data.resource else {}
        resource_gid = resource.get("gid", "N/A")
        resource_type = resource.get("resource_type", "N/A")
        action = event_data.action
        
        logger.info(f"Event Action: {action}")
        logger.info(f"Resource GID: {resource_gid}")
        logger.info(f"Resource Type: {resource_type}")
        
        # Check if this is a task update
        if resource_type == "task" and action == "changed":
            try:
                client = await get_asana_client()
                logger.info(f"Fetching task details for GID: {resource_gid}")
                task_response = await client.get(f"/tasks/{resource_gid}")
                task_response.raise_for_status()
                task_data = task_response.json().get("data", {})
                
                # Get task details
                assignee = task_data.get("assignee", {})
                assignee_name = assignee.get("name", "Unassigned")
                assignee_gid = assignee.get("gid")
                task_name = task_data.get("name", "Unnamed Task")
                task_url = f"https://app.asana.com/0/{ASANA_PROJECT_ID}/{resource_gid}"
                
                # Fetch the issue description (notes field)
                issue_description = task_data.get("notes", "")
                logger.info(f"Task Name: {task_name}")
                logger.info(f"Assigned To: {assignee_name}")
                logger.info(f"Task Description: {issue_description}")
                
                # Fetch attachments for the task
                logger.info(f"Fetching attachments for task {resource_gid}")
                attachments_response = await client.get(f"/tasks/{resource_gid}/attachments")
                attachments_response.raise_for_status()
                attachments_data = attachments_response.json().get("data", [])
                attachment_urls = []
                for att in attachments_data:
                    url = att.get("download_url") or att.get("permanent_url")
                    if url:
                        attachment_urls.append(url)
                logger.info(f"Found {len(attachment_urls)} attachments: {attachment_urls}")

                # Prepare input for the agent
                agent_input = {
                    "description": issue_description,
                    "attachments": attachment_urls,
                    "task_name": task_name,
                    "task_url": task_url,
                    "assignee": assignee_name
                }
                logger.info(f"Calling orchestrator_agent with input: {agent_input}")

                # Call the orchestrator_agent asynchronously (run in threadpool)
                from agent.langtrace import orchestrator_agent
                import asyncio
                loop = asyncio.get_event_loop()
                agent_result = await loop.run_in_executor(None, orchestrator_agent, json.dumps(agent_input))

                logger.info(f"LLM agent result: {agent_result}")

                # Use the LLM output for the Slack message
                llm_output = agent_result.get("parsed_llm", {})
                chain_of_thought = llm_output.get("chain_of_thought", "")
                routing_decision = llm_output.get("routing_decision", [])
                confidences = llm_output.get("confidences", {})

                # Check if assigned to claims agent
                if assignee_name.lower() == "glassbox":
                    logger.info("🚨 Task assigned to claims agent - preparing Slack notification")
                    # Prepare Slack message (now with LLM output)
                    slack_message = {
                        "text": f"Task {task_name} has been assigned to Claims Agent",
                        "channel": "#claims-escalation",
                        "blocks": [
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": f"🔺 *Task escalated by agent*\nTask ID: `{resource_gid}`\n<{task_url}|View task in Asana>"
                                }
                            },
                            {
                                "type": "context",
                                "elements": [
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Chain of Thought:* {chain_of_thought}"
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Routing Decision:* {routing_decision}"
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Confidences:* {confidences}"
                                    },
                                    {
                                        "type": "mrkdwn",
                                        "text": f"*Attachments:* {'; '.join(attachment_urls) if attachment_urls else 'None'}"
                                    }
                                ]
                            }
                        ]
                    }
                    logger.info(f"Sending Slack message: {slack_message}")
                    # Send to Slack (after LLM output is ready)
                    try:
                        slack_response = slack_client.chat_postMessage(**slack_message)
                        logger.info(f"✅ Slack notification sent successfully: {slack_response}")
                    except SlackApiError as e:
                        logger.error(f"❌ Failed to send Slack notification: {str(e)}")
                await client.aclose()
            except Exception as e:
                logger.error(f"⚠️ Failed to fetch task details: {str(e)}")

    return Response(
        status_code=200,
        content="Webhook event processed successfully"
    )

@app.post("/register-webhook")
async def register_webhook(request: Request):
    """Register a webhook with Asana."""
    logger.info("\n🔍 Starting webhook registration process...")
    
    if not all([ASANA_ACCESS_TOKEN, ASANA_PROJECT_ID]):
        logger.error("❌ Missing configuration:")
        logger.error(f"  ASANA_ACCESS_TOKEN present: {bool(ASANA_ACCESS_TOKEN)}")
        logger.error(f"  ASANA_PROJECT_ID present: {bool(ASANA_PROJECT_ID)}")
        raise HTTPException(status_code=500, detail="Missing required Asana configuration")
    
    try:
        logger.info("\n🚀 Registering webhook with Asana...")
        logger.info(f"  Access Token: {ASANA_ACCESS_TOKEN[:10]}...")  # Log first 10 chars of token
        
        logger.info("  Creating Asana client...")
        client = await get_asana_client()
        logger.info("  ✅ Asana client created successfully")
        
        # Get the base URL from the request
        base_url = str(request.base_url).rstrip('/')
        target_url = f"{base_url}/webhook"
        logger.info(f"  Using target URL: {target_url}")
        logger.info(f"  For Asana Project ID: {ASANA_PROJECT_ID}")
        logger.info(f"  Request headers: {dict(request.headers)}")
        logger.info(f"  Request URL: {request.url}")

        # First, verify the project exists
        try:
            logger.info("  🔍 Verifying project exists...")
            project_response = await client.get(f"/projects/{ASANA_PROJECT_ID}")
            logger.info("  Project response received")
            project_response.raise_for_status()
            logger.info(f"  ✅ Project verification successful: {project_response.json()}")
        except Exception as e:
            logger.error(f"  ❌ Project verification failed: {str(e)}")
            logger.error(f"  Error type: {type(e)}")
            raise HTTPException(
                status_code=400,
                detail=f"Invalid project ID or insufficient permissions: {str(e)}"
            )

        # Prepare the registration payload
        logger.info("  Preparing registration payload...")
        registration_payload = {
            "data": {
                "resource": ASANA_PROJECT_ID,
                "target": target_url,
                "filters": [
                    {
                        "resource_type": "task",
                        "action": "added"
                    },
                    {
                        "resource_type": "task",
                        "action": "changed"
                    },
                    {
                        "resource_type": "task",
                        "action": "deleted"
                    }
                ]
            }
        }
        
        logger.info(f"  Registration payload: {json.dumps(registration_payload, indent=2)}")
        
        # Try to register the webhook
        logger.info("  About to attempt webhook registration...")
        try:
            logger.info("  📤 Sending webhook registration request to Asana...")
            logger.info("  Request URL: https://app.asana.com/api/1.0/webhooks")
            logger.info("  Request Headers:", client.headers)
            
            response = await client.post("/webhooks", json=registration_payload)
            logger.info("  📥 Received response from Asana")
            logger.info(f"  Response Status: {response.status_code}")
            logger.info(f"  Response Headers: {dict(response.headers)}")
            
            response.raise_for_status()
            
            webhook_data = response.json().get('data', {})
            logger.info(f"\n✅ Webhook registered successfully:")
            logger.info(f"  Webhook GID: {webhook_data.get('gid')}")
            logger.info(f"  Target URL: {webhook_data.get('target')}")
            logger.info(f"  Active: {webhook_data.get('active')}")
            
            return {"status": "success", "webhook_details": webhook_data}
            
        except httpx.HTTPStatusError as e:
            error_body = e.response.text
            try:
                error_json = json.loads(error_body)
                error_message = error_json.get('errors', [{}])[0].get('message', str(e))
            except:
                error_message = str(e)
            
            logger.error(f"  ❌ Asana API error: {error_message}")
            logger.error(f"  Response status: {e.response.status_code}")
            logger.error(f"  Response body: {error_body}")
            logger.error(f"  Response headers: {dict(e.response.headers)}")
            
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"Asana API error: {error_message}"
            )
        except Exception as e:
            logger.error(f"  ❌ Unexpected error during webhook registration: {str(e)}")
            logger.error(f"  Error type: {type(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Unexpected error during webhook registration: {str(e)}"
            )
        finally:
            await client.aclose()
        
    except HTTPException:
        logger.error("  ❌ HTTP Exception raised")
        raise
    except Exception as e:
        logger.error(f"  ❌ Unexpected error: {str(e)}")
        logger.error(f"  Error type: {type(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )

@app.get("/list-webhooks")
async def list_existing_webhooks():
    """List all existing webhooks for the workspace."""
    if not all([ASANA_ACCESS_TOKEN, ASANA_WORKSPACE_ID]):
        raise HTTPException(status_code=500, detail="Missing required Asana configuration")
    
    try:
        logger.info("\n🔍 Listing existing webhooks...")
        client = await get_asana_client()
        
        try:
            response = await client.get(
                "/webhooks",
                params={"workspace": ASANA_WORKSPACE_ID}
            )
            response.raise_for_status()
            webhooks_data = response.json()
            
            logger.info(f"  ✅ Found {len(webhooks_data.get('data', []))} webhooks")
            
            return {
                "status": "success",
                "webhooks": webhooks_data.get("data", [])
            }
            
        except httpx.HTTPStatusError as e:
            error_body = e.response.text
            try:
                error_json = json.loads(error_body)
                error_message = error_json.get('errors', [{}])[0].get('message', str(e))
            except:
                error_message = str(e)
            
            logger.error(f"  ❌ Asana API error: {error_message}")
            logger.error(f"  Response status: {e.response.status_code}")
            logger.error(f"  Response body: {error_body}")
            
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"Asana API error: {error_message}"
            )
        finally:
            await client.aclose()
            
    except Exception as e:
        logger.error(f"❌ Error listing webhooks: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/webhooks/{webhook_gid}")
async def delete_webhook(webhook_gid: str):
    """Delete a webhook by its GID."""
    if not ASANA_ACCESS_TOKEN:
        raise HTTPException(status_code=500, detail="Missing required Asana configuration")
    
    try:
        logger.info(f"\n🗑️ Deleting webhook {webhook_gid}...")
        client = await get_asana_client()
        
        try:
            response = await client.delete(f"/webhooks/{webhook_gid}")
            response.raise_for_status()
            
            logger.info(f"  ✅ Successfully deleted webhook {webhook_gid}")
            
            return {
                "status": "success",
                "message": f"Webhook {webhook_gid} deleted successfully"
            }
            
        except httpx.HTTPStatusError as e:
            error_body = e.response.text
            try:
                error_json = json.loads(error_body)
                error_message = error_json.get('errors', [{}])[0].get('message', str(e))
            except:
                error_message = str(e)
            
            logger.error(f"  ❌ Asana API error: {error_message}")
            logger.error(f"  Response status: {e.response.status_code}")
            logger.error(f"  Response body: {error_body}")
            
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"Asana API error: {error_message}"
            )
        finally:
            await client.aclose()
            
    except Exception as e:
        logger.error(f"❌ Error deleting webhook: {str(e)}")
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
        logger.error(f"❌ Error sending Slack message: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send Slack message: {str(e)}"
        )
    except Exception as e:
        logger.error(f"❌ Unexpected error: {str(e)}")
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
