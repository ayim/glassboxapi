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
    print(f"📥 Received webhook request at {datetime.datetime.now().isoformat()}")
    print(f"Headers: {dict(request.headers)}")
    
    # Handle webhook verification (handshake)
    x_hook_secret = request.headers.get("X-Hook-Secret")
    if x_hook_secret:
        print(f"🤝 Asana handshake received")
        print(f"X-Hook-Secret: {x_hook_secret}")
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
    print("🔄 Processing Asana webhook event:")
    for event_data in payload.events:
        resource = event_data.resource if event_data.resource else {}
        resource_gid = resource.get("gid", "N/A")
        resource_type = resource.get("resource_type", "N/A")
        action = event_data.action
        
        print(f"Event Action: {action}")
        print(f"Resource GID: {resource_gid}")
        print(f"Resource Type: {resource_type}")
        
        # Check if this is a task update
        if resource_type == "task" and action == "changed":
            # Check if the change is related to the task's assignee
            change = event_data.change if event_data.change else {}
            print(f"Debug - Change object: {change}")
            print(f"Debug - Change field: {change.get('field')}")
            if not change.get("field") or change.get("field").strip().lower() != "assignee":
                print(f"Skipping non-assignee change: {change}")
                continue

            try:
                client = await get_asana_client()
                print(f"Fetching task details for GID: {resource_gid}")
                task_response = await client.get(f"/tasks/{resource_gid}")
                task_response.raise_for_status()
                task_data = task_response.json().get("data", {})
                
                # Get task details
                assignee = task_data.get("assignee", {})
                assignee_name = assignee.get("name", "Unassigned")
                assignee_gid = assignee.get("gid")
                task_name = task_data.get("name", "Unnamed Task")
                task_url = f"https://app.asana.com/0/{ASANA_PROJECT_ID}/{resource_gid}"
                
                # Only proceed if the task is assigned to Glassbox
                if assignee_name.lower() != "glassbox":
                    print(f"Skipping task not assigned to Glassbox. Current assignee: {assignee_name}")
                    continue

                # Fetch the issue description (notes field)
                issue_description = task_data.get("notes", "")
                print(f"Task Name: {task_name}")
                print(f"Assigned To: {assignee_name}")
                print(f"Task Description: {issue_description}")
                
                # Fetch attachments for the task
                print(f"Fetching attachments for task {resource_gid}")
                attachments_response = await client.get(f"/tasks/{resource_gid}/attachments")
                attachments_response.raise_for_status()
                attachments_data = attachments_response.json().get("data", [])
                attachment_urls = []
                for att in attachments_data:
                    url = att.get("download_url") or att.get("permanent_url")
                    if url:
                        attachment_urls.append(url)
                print(f"Found {len(attachment_urls)} attachments: {attachment_urls}")

                # Prepare input for the agent
                agent_input = {
                    "description": issue_description,
                    "attachments": attachment_urls,
                    "task_name": task_name,
                    "task_url": task_url,
                    "assignee": assignee_name
                }
                print(f"Calling orchestrator_agent with input: {agent_input}")

                # Call the orchestrator_agent asynchronously (run in threadpool)
                from agent.langtrace import orchestrator_agent
                import asyncio
                loop = asyncio.get_event_loop()
                agent_result = await loop.run_in_executor(None, orchestrator_agent, json.dumps(agent_input))

                print(f"LLM agent result: {agent_result}")

                # Use the LLM output for the Slack message
                llm_output = agent_result.get("parsed_llm", {})
                chain_of_thought = llm_output.get("chain_of_thought", "")
                routing_decision = llm_output.get("routing_decision", [])
                confidences = llm_output.get("confidences", {})

                # Map routing decisions to Asana sections and Slack channels
                routing_mapping = {
                    "declaration_review": {
                        "asana_section": "Escalation: Declaration Review / Ambiguity",
                        "slack_channel": "#cases-escalation-ambiguous-regulation"
                    },
                    "regulatory_sustainability": {
                        "asana_section": "Agent Handoff: Regulatory and Sustainability",
                        "slack_channel": "#cases-escalation-regulatory-sustainability"
                    },
                    "sourcing_logistics": {
                        "asana_section": "Routine Processing",
                        "slack_channel": "#cases-handoff-sourcing-logistics"
                    }
                }

                # Move task to appropriate section in Asana if routing decision matches
                if routing_decision and isinstance(routing_decision, list) and len(routing_decision) > 0:
                    primary_route = routing_decision[0].lower()
                    print(f"Checking if '{primary_route}' exists in routing_mapping for task movement")
                    if primary_route in routing_mapping:
                        try:
                            print(f"Attempting to move task to section: {routing_mapping[primary_route]['asana_section']}")
                            # First, get all sections in the project
                            sections_response = await client.get(f"/projects/{ASANA_PROJECT_ID}/sections")
                            sections_response.raise_for_status()
                            sections_data = sections_response.json().get("data", [])
                            
                            # Find the target section
                            target_section = None
                            print(f"Looking for section named '{routing_mapping[primary_route]['asana_section']}' among {len(sections_data)} sections")
                            for section in sections_data:
                                if section.get("name") == routing_mapping[primary_route]["asana_section"]:
                                    target_section = section
                                    print(f"Found target section with GID: {section.get('gid')}")
                                    break
                            
                            if target_section:
                                # Move the task to the target section
                                print(f"Moving task {resource_gid} to section {target_section['gid']} in project {ASANA_PROJECT_ID}")
                                move_response = await client.post(
                                    f"/tasks/{resource_gid}/addProject",
                                    json={
                                        "data": {
                                            "project": ASANA_PROJECT_ID,
                                            "section": target_section["gid"]
                                        }
                                    }
                                )
                                move_response.raise_for_status()
                                print(f"✅ Moved task to section: {routing_mapping[primary_route]['asana_section']}")
                                print(f"Response: {move_response.json()}")
                            else:
                                print(f"⚠️ Target section not found: {routing_mapping[primary_route]['asana_section']}")
                                print(f"Available sections: {[s.get('name') for s in sections_data]}")
                        except Exception as e:
                            print(f"❌ Failed to move task to section: {str(e)}")
                            import traceback
                            print(f"Traceback: {traceback.format_exc()}")
                    else:
                        print(f"⚠️ Primary route '{primary_route}' not found in routing_mapping, cannot move task")
                        print(f"Available routes in mapping: {list(routing_mapping.keys())}")
                else:
                    print(f"⚠️ No valid routing decision to determine task movement")
                    print(f"Routing decision: {routing_decision}")

                # Post the analysis as a comment on the Asana task
                comment_text = (
                    "🤖 *AI Analysis Report*\n\n"
                    "*Initial Diagnosis*\n"
                    f"{chain_of_thought}\n\n"
                    "*Tools and Documents Referenced*\n"
                    f"{'; '.join(attachment_urls) if attachment_urls else 'No documents attached'}\n\n"
                    "*Next Call / Final Call*\n"
                    f"Based on the analysis, this issue will be routed to: {routing_decision}\n\n"
                    "*Final Decision*\n"
                    f"Confidence levels for each potential route:\n{json.dumps(confidences, indent=2)}"
                )
                try:
                    comment_response = await client.post(
                        f"/tasks/{resource_gid}/stories",
                        json={
                            "data": {
                                "text": comment_text,
                                "type": "comment"
                            }
                        }
                    )
                    comment_response.raise_for_status()
                    print(f"✅ Posted analysis as comment on Asana task")
                except Exception as e:
                    print(f"❌ Failed to post comment on Asana task: {str(e)}")

                # Check if assigned to claims agent
                if assignee_name.lower() == "glassbox":
                    print("🚨 Task assigned to claims agent - preparing Slack notification")
                    try:
                        # Get primary route for channel selection
                        primary_route = ""
                        if routing_decision and isinstance(routing_decision, list) and len(routing_decision) > 0:
                            primary_route = routing_decision[0].lower()
                            print(f"Primary route: {primary_route}")
                        
                        # Check if route exists in mapping
                        if primary_route not in routing_mapping:
                            print(f"⚠️ Warning: Primary route '{primary_route}' not found in routing_mapping")
                            slack_channel = "#claims-escalation"
                        else:
                            slack_channel = routing_mapping.get(primary_route, {}).get("slack_channel", "#claims-escalation")
                        
                        print(f"Selected Slack channel: {slack_channel}")
                        
                        # Debug data going into Slack message
                        print(f"Task name: {task_name}")
                        print(f"Routing decision type: {type(routing_decision)}, value: {routing_decision}")
                        print(f"Chain of thought type: {type(chain_of_thought)}, length: {len(str(chain_of_thought))}")
                        
                        # Prepare Slack message (now with LLM output)
                        slack_message = {
                            "text": f"Task {task_name} has been routed to {routing_decision}",
                            "channel": slack_channel,
                            "blocks": [
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": f"🔺 *Task escalated by agent*\nTask ID: `{resource_gid}`\n<{task_url}|View task in Asana>"
                                    }
                                },
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": f"*Routing Decision:*\n{routing_decision}"
                                    }
                                },
                                {
                                    "type": "context",
                                    "elements": [
                                        {
                                            "type": "mrkdwn",
                                            "text": f"*Chain of Thought:* {chain_of_thought[:500]}..." if len(str(chain_of_thought)) > 500 else f"*Chain of Thought:* {chain_of_thought}"
                                        },
                                        {
                                            "type": "mrkdwn", 
                                            "text": f"*Confidences:* {str(confidences)[:500]}..." if len(str(confidences)) > 500 else f"*Confidences:* {confidences}"
                                        },
                                        {
                                            "type": "mrkdwn",
                                            "text": f"*Attachments:* {'; '.join(attachment_urls) if attachment_urls else 'None'}"
                                        }
                                    ]
                                }
                            ]
                        }
                        print(f"Prepared Slack message structure")
                        
                        # Send to Slack (after LLM output is ready)
                        try:
                            print(f"Attempting to send Slack message to channel: {slack_message['channel']}")
                            slack_response = slack_client.chat_postMessage(**slack_message)
                            print(f"✅ Slack notification sent successfully: {slack_response}")
                        except SlackApiError as e:
                            print(f"❌ Failed to send Slack notification: {str(e)}")
                            print(f"Error details: {e.response.data}")
                    except Exception as e:
                        print(f"❌ Error preparing or sending Slack notification: {str(e)}")
                        import traceback
                        print(f"Traceback: {traceback.format_exc()}")
                await client.aclose()
            except Exception as e:
                print(f"⚠️ Failed to fetch task details: {str(e)}")

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
        client = await get_asana_client()
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
            project_response = await client.get(f"/projects/{ASANA_PROJECT_ID}")
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
        
        print(f"  Registration payload: {json.dumps(registration_payload, indent=2)}")
        
        # Try to register the webhook
        print("  About to attempt webhook registration...")
        try:
            print("  📤 Sending webhook registration request to Asana...")
            print("  Request URL: https://app.asana.com/api/1.0/webhooks")
            print("  Request Headers:", client.headers)
            
            response = await client.post("/webhooks", json=registration_payload)
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
        finally:
            await client.aclose()
        
    except HTTPException:
        print(f"  ❌ HTTP Exception raised")
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
    """List all existing webhooks for the workspace."""
    if not all([ASANA_ACCESS_TOKEN, ASANA_WORKSPACE_ID]):
        raise HTTPException(status_code=500, detail="Missing required Asana configuration")
    
    try:
        print("\n🔍 Listing existing webhooks...")
        client = await get_asana_client()
        
        try:
            response = await client.get(
                "/webhooks",
                params={"workspace": ASANA_WORKSPACE_ID}
            )
            response.raise_for_status()
            webhooks_data = response.json()
            
            print(f"  ✅ Found {len(webhooks_data.get('data', []))} webhooks")
            
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
            
            print(f"  ❌ Asana API error: {error_message}")
            print(f"  Response status: {e.response.status_code}")
            print(f"  Response body: {error_body}")
            
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"Asana API error: {error_message}"
            )
        finally:
            await client.aclose()
            
    except Exception as e:
        print(f"❌ Error listing webhooks: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/webhooks/{webhook_gid}")
async def delete_webhook(webhook_gid: str):
    """Delete a webhook by its GID."""
    if not ASANA_ACCESS_TOKEN:
        raise HTTPException(status_code=500, detail="Missing required Asana configuration")
    
    try:
        print(f"\n🗑️ Deleting webhook {webhook_gid}...")
        client = await get_asana_client()
        
        try:
            response = await client.delete(f"/webhooks/{webhook_gid}")
            response.raise_for_status()
            
            print(f"  ✅ Successfully deleted webhook {webhook_gid}")
            
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
            
            print(f"  ❌ Asana API error: {error_message}")
            print(f"  Response status: {e.response.status_code}")
            print(f"  Response body: {error_body}")
            
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"Asana API error: {error_message}"
            )
        finally:
            await client.aclose()
            
    except Exception as e:
        print(f"❌ Error deleting webhook: {str(e)}")
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
