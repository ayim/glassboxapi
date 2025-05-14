from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import os
import json
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
import datetime

load_dotenv()

ASANA_ACCESS_TOKEN = os.getenv("ASANA_ACCESS_TOKEN")
ASANA_WORKSPACE_ID = os.getenv("ASANA_WORKSPACE_ID")
ASANA_PROJECT_ID = os.getenv("ASANA_PROJECT_ID")

app = FastAPI()

# CORS middleware (optional, for frontend interaction if needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Pydantic models for request/response validation
class AsanaEvent(BaseModel):
    action: Optional[str] = None
    resource: Optional[Dict[str, Any]] = None
    user: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    parent: Optional[Dict[str, Any]] = None # Can be null
    change: Optional[Dict[str, Any]] = None # For "changed" actions

class WebhookPayload(BaseModel):
    events: List[AsanaEvent] = []

class ProjectDetails(BaseModel):
    gid: str
    name: str
    owner: Optional[str] = None
    due_on: Optional[str] = None
    current_status: Optional[str] = None

@app.post("/webhook")
async def asana_webhook(request: Request, payload: Optional[WebhookPayload] = None):
    # Handle webhook verification (handshake)
    x_hook_secret = request.headers.get("X-Hook-Secret")
    if x_hook_secret:
        handshake_start_time = datetime.datetime.now()
        print(f"\n🤝🤝🤝 !!! ASANA HANDSHAKE RECEIVED AT {handshake_start_time.isoformat()} !!! 🤝🤝🤝")
        # print(f"  Responding with X-Hook-Secret: {x_hook_secret}") # Secret can be long, let's not flood logs unless needed for debug
        
        response_headers = {"X-Hook-Secret": x_hook_secret}
        response_to_send = Response(status_code=200, headers=response_headers)
        
        handshake_end_time = datetime.datetime.now()
        processing_duration = (handshake_end_time - handshake_start_time).total_seconds() * 1000 # in milliseconds
        print(f"  Handshake processing took: {processing_duration:.2f} ms before sending response.")
        return response_to_send

    if not payload or not payload.events:
        print("⚠️ Received event on /webhook without valid payload.events or empty events list (and not a handshake).")
        # Asana expects 200 OK for events it sends. Respond 200 to prevent retries.
        return Response(status_code=200, content="Acknowledged empty or invalid payload.")

    # Process actual event payload
    print("\n🔄 Received Asana webhook event:")
    for event_data in payload.events:
        resource = event_data.resource if event_data.resource else {}
        resource_gid = resource.get("gid", "N/A")
        resource_type = resource.get("resource_type", "N/A")
        action = event_data.action
        
        print(f"  Event Action: {action}")
        print(f"  Resource GID: {resource_gid}")
        print(f"  Resource Type: {resource_type}")

        if resource_type == "project" and action == "changed":
            print(f"  Project {resource_gid} changed.")
            # Further processing for project changes
            change_details = event_data.change if event_data.change else {}
            if change_details.get("field") == "custom_fields": # Example: check for a specific custom field change
                # You might need to fetch the project again to see updated custom field values
                # or parse them from event_data.change.new_value if available and structured
                print(f"    Custom fields for project {resource_gid} were modified.")
            
            # Check for owner change to "Glassbox" - this requires knowing how owner is represented
            # Asana API usually represents users/owners by GID. "Glassbox" might be a name.
            # We may need to fetch project details to get owner name or rely on 'new_value' structure.
            if change_details.get("field") == "owner":
                new_owner_info = change_details.get("new_value", {})
                owner_name = new_owner_info.get("name") # Assuming 'name' is part of new_value for owner
                print(f"    Project owner changed. New owner info: {new_owner_info}")
                if owner_name == "Glassbox":
                    print("🔥 Project owner changed to Glassbox! Triggering langgraph agent...")
                    # TODO: Implement langgraph agent trigger

    return {"status": "success", "message": "Webhook event processed"}

def get_asana_client():
    return httpx.Client(
        base_url="https://app.asana.com/api/1.0",
        headers={
            "Authorization": f"Bearer {ASANA_ACCESS_TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        },
        timeout=30.0  # Increased timeout for Asana API calls
    )

@app.get("/")
async def root():
    return {"status": "healthy", "service": "Asana Webhook API"}

@app.post("/register-webhook")
async def register_webhook(request: Request):
    if not all([ASANA_ACCESS_TOKEN, ASANA_PROJECT_ID]):
        raise HTTPException(status_code=500, detail="Missing required Asana configuration")
    
    try:
        print("\n🚀 Attempting to register webhook with Asana...")
        with get_asana_client() as client:
            # Ensure your ngrok URL is up-to-date if it's dynamic
            # The user confirmed NGROK_URL is https://32d6-2600-4041-799a-f100-94fa-84f-c740-d6d2.ngrok-free.app
            target_url = "https://e0d9-2600-4041-799a-f100-94fa-84f-c740-d6d2.ngrok-free.app/webhook"
            print(f"  Using target URL: {target_url}")
            print(f"  For Asana Project ID: {ASANA_PROJECT_ID}")

            registration_payload = {
                "data": {
                    "resource": ASANA_PROJECT_ID,
                    "target": target_url,
                    "filters": [
                        {
                            "resource_type": "project",
                            "action": "changed",
                            # "fields": ["owner", "custom_fields"] # Optional: specify fields to watch
                        }
                    ]
                }
            }
            print(f"  Registration payload: {json.dumps(registration_payload, indent=2)}")

            response = client.post(
                "/webhooks", # General endpoint for creating webhooks
                json=registration_payload
            )
            
            print("\nRaw Asana Response from /webhooks endpoint:")
            print(f"  Status Code: {response.status_code}")
            print(f"  Response Body: {response.text}")
            
            response.raise_for_status() # This will raise an HTTPStatusError for 4xx/5xx responses

            webhook_data = response.json().get('data', {})
            print(f"\n✅ Webhook registration successful with Asana:")
            print(f"  Webhook GID: {webhook_data.get('gid')}")
            print(f"  Target URL: {webhook_data.get('target')}")
            print(f"  Active: {webhook_data.get('active')}")
            print(f"  Resource GID: {webhook_data.get('resource', {}).get('gid')}")
            print("----------------------------------------")
            
            return {"status": "success", "webhook_details": webhook_data}
            
    except httpx.HTTPStatusError as e:
        error_details = e.response.text
        try:
            # Try to parse Asana's error message for better logging
            error_json = json.loads(e.response.text)
            error_details = json.dumps(error_json, indent=2)
        except json.JSONDecodeError:
            pass # Keep original text if not JSON
        print(f"❌ HTTPStatusError during webhook registration: {e.response.status_code}\n{error_details}")
        raise HTTPException(status_code=e.response.status_code, detail=f"Asana API error: {error_details}")
    except Exception as e:
        print(f"❌ Exception during webhook registration: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get("/list-webhooks")
async def list_existing_webhooks():
    if not all([ASANA_ACCESS_TOKEN, ASANA_PROJECT_ID, ASANA_WORKSPACE_ID]):
        raise HTTPException(status_code=500, detail="Missing required Asana configuration for listing")
    
    our_ngrok_url = "https://32d6-2600-4041-799a-f100-94fa-84f-c740-d6d2.ngrok-free.app/webhook"

    try:
        print(f"\n🔍 Broadly listing ALL webhooks for workspace {ASANA_WORKSPACE_ID} FIRST...")
        with get_asana_client() as client:
            # First, get all webhooks for the workspace to see everything
            all_hooks_response = client.get(
                "/webhooks",
                params={"workspace": ASANA_WORKSPACE_ID} # NO resource filter here initially
            )
            all_hooks_response.raise_for_status()
            all_webhooks_data = all_hooks_response.json()
            print(f"  Raw list of ALL webhooks in workspace ({ASANA_WORKSPACE_ID}):")
            any_hooks_in_workspace = False
            if "data" in all_webhooks_data and all_webhooks_data["data"]:
                any_hooks_in_workspace = True
                for i, hook_info in enumerate(all_webhooks_data["data"]):
                    print(f"    Hook {i+1}: GID: {hook_info.get('gid')}, Active: {hook_info.get('active')}, Target: {hook_info.get('target')}, Resource GID: {hook_info.get('resource', {}).get('gid')}, Resource Type: {hook_info.get('resource', {}).get('resource_type')}")
            if not any_hooks_in_workspace:
                print("    No webhooks found in this workspace at all.")
            print("  --- End of broad webhooks list ---")

            # Now, specifically check for our target project and URL among these (client-side filtering for sanity check)
            print(f"\n🔍 Client-side filtering for Project GID {ASANA_PROJECT_ID} and Target {our_ngrok_url} from the broad list...")
            found_hooks_client_filtered = []
            active_hook_found_client_filtered = False
            if any_hooks_in_workspace:
                for hook in all_webhooks_data["data"]:
                    if hook.get("resource", {}).get("gid") == ASANA_PROJECT_ID and hook.get("target") == our_ngrok_url:
                        found_hooks_client_filtered.append(hook)
                        if hook.get("active"):
                            active_hook_found_client_filtered = True
                        print(f"    MATCH (client-side): GID: {hook.get('gid')}, Active: {hook.get('active')}")
            
            if active_hook_found_client_filtered:
                print(f"\n✅ SUCCESS (client-side filter): Active webhook confirmed for project {ASANA_PROJECT_ID} targeting {our_ngrok_url}")
                return {"status": "success_client_filtered", "message": "Active webhook confirmed via client-side filtering.", "webhooks": found_hooks_client_filtered}
            elif found_hooks_client_filtered:
                print(f"\n⚠️ WARNING (client-side filter): Matching webhook(s) found but none are active for {our_ngrok_url}")
                return {"status": "found_inactive_client_filtered", "message": "Matching webhook(s) found but none active (client-side filter).", "webhooks": found_hooks_client_filtered}
            else:
                print(f"\nℹ️ No matching webhook found (client-side filter) for project {ASANA_PROJECT_ID} targeting {our_ngrok_url}")
                # Fallback to original server-side filtered query for final result if client-side yielded nothing
                print(f"\n🔍 Re-querying Asana with server-side filter for project {ASANA_PROJECT_ID} as a final check...")
                response = client.get(
                    "/webhooks",
                    params={
                        "workspace": ASANA_WORKSPACE_ID,
                        "resource": ASANA_PROJECT_ID, # Original server-side filter
                    }
                )
                response.raise_for_status()
                webhooks_data_server_filtered = response.json()
                if "data" in webhooks_data_server_filtered and webhooks_data_server_filtered["data"]:
                    # This case should ideally not be hit if client-side filtering of a broader list already failed
                    print(f"  Server-side filter found hooks: {json.dumps(webhooks_data_server_filtered['data'])}") 
                    return {"status": "found_server_filtered_unexpectedly", "message": "Server-side filter found hooks, but client-side did not. Check logic.", "webhooks": webhooks_data_server_filtered['data']}
                else:
                    print("  Server-side filter also confirms no matching webhooks.")
                    return {"status": "not_found_confirmed", "message": "No matching webhook found (confirmed by server-side filter).", "webhooks": []}

    except httpx.HTTPStatusError as e:
        error_details = e.response.text
        try: error_details = json.dumps(json.loads(e.response.text), indent=2)
        except: pass
        print(f"❌ HTTPStatusError during listing webhooks: {e.response.status_code}\n{error_details}")
        raise HTTPException(status_code=e.response.status_code, detail=f"Asana API error while listing: {error_details}")
    except Exception as e:
        print(f"❌ Exception during listing webhooks: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error while listing: {str(e)}")

@app.get("/test-ngrok-reachability")
async def test_ngrok_reachability():
    print("\n🌐 TEST ENDPOINT /test-ngrok-reachability WAS HIT VIA NGROK 🌐")
    return {"status": "success", "message": "If you see this, ngrok is forwarding to the FastAPI app correctly."}

# To run the server (example):
# uvicorn main:app --reload
