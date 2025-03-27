import requests
import json
import os
from dotenv import load_dotenv
import time

# Load environment variables
load_dotenv()

# API configuration
API_BASE_URL = "http://localhost:8000"  # Update with your API server address
API_KEY = os.getenv("APP_API_KEY", "test-api-key")

headers = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json"
}

def call_api(endpoint, method="GET", data=None, files=None):
    """Helper function to call API endpoints"""
    url = f"{API_BASE_URL}{endpoint}"
    
    custom_headers = headers.copy()
    
    if method == "GET":
        response = requests.get(url, headers=custom_headers)
    elif method == "POST":
        if files:
            # For file uploads, don't use JSON content type
            custom_headers.pop("Content-Type", None)
            response = requests.post(url, headers=custom_headers, data=data, files=files)
        else:
            response = requests.post(url, headers=custom_headers, json=data)
    elif method == "PUT":
        response = requests.put(url, headers=custom_headers, json=data)
    elif method == "DELETE":
        response = requests.delete(url, headers=custom_headers)
    else:
        raise ValueError(f"Invalid method: {method}")

    if response.status_code >= 400:
        print(f"Error {response.status_code}: {response.text}")
        return None
    
    if response.headers.get("Content-Type") == "application/json":
        return response.json()
    return response.text

def demo_family_management():
    """Demonstrate family management functionality"""
    print("\n=== Family Management Demo ===")
    
    # List all family members
    print("\nCurrent family members:")
    family_members = call_api("/api/family")
    print(json.dumps(family_members, indent=2))
    
    # Add a new family member
    print("\nAdding a new family member:")
    new_member = {
        "name": "John Doe",
        "age": "35",
        "preferences": {
            "food": "Pizza",
            "hobby": "Reading",
            "color": "Blue"
        }
    }
    result = call_api("/api/family", method="POST", data=new_member)
    print(json.dumps(result, indent=2))
    
    # Get the member ID from result
    member_id = result["id"]
    
    # Update a preference
    print("\nUpdating a preference:")
    update_pref = {
        "id": member_id,
        "key": "food",
        "value": "Sushi"
    }
    result = call_api("/api/family/preference", method="POST", data=update_pref)
    print(json.dumps(result, indent=2))
    
    # Get updated family member
    print(f"\nUpdated family member {member_id}:")
    member = call_api(f"/api/family/{member_id}")
    print(json.dumps(member, indent=2))
    
    return member_id

def demo_event_management(member_id):
    """Demonstrate event management functionality"""
    print("\n=== Event Management Demo ===")
    
    # List all events
    print("\nCurrent events:")
    events = call_api("/api/events")
    print(json.dumps(events, indent=2))
    
    # Add a new event
    print("\nAdding a new event:")
    new_event = {
        "title": "Family Dinner",
        "date": "2025-04-01",
        "time": "19:00",
        "description": "Monthly family dinner at home",
        "participants": ["John Doe"]
    }
    
    # Using the query parameter for created_by
    result = call_api(f"/api/events?created_by={member_id}", method="POST", data=new_event)
    print(json.dumps(result, indent=2))
    
    # Get the event ID from result
    event_id = result["id"]
    
    # Update the event
    print("\nUpdating the event:")
    update_event = {
        "title": "Family Dinner & Movie Night",
        "description": "Monthly family dinner followed by a movie"
    }
    result = call_api(f"/api/events/{event_id}", method="PUT", data=update_event)
    print(json.dumps(result, indent=2))
    
    # Get events by member
    print(f"\nEvents for member {member_id}:")
    member_events = call_api(f"/api/events?member_id={member_id}")
    print(json.dumps(member_events, indent=2))
    
    return event_id

def demo_note_management(member_id):
    """Demonstrate note management functionality"""
    print("\n=== Note Management Demo ===")
    
    # List all notes
    print("\nCurrent notes:")
    notes = call_api("/api/notes")
    print(json.dumps(notes, indent=2))
    
    # Add a new note
    print("\nAdding a new note:")
    new_note = {
        "title": "Shopping List",
        "content": "Milk, eggs, bread, fruits",
        "tags": ["shopping", "groceries"]
    }
    
    # Using the query parameter for created_by
    result = call_api(f"/api/notes?created_by={member_id}", method="POST", data=new_note)
    print(json.dumps(result, indent=2))
    
    # Get notes by member
    print(f"\nNotes for member {member_id}:")
    member_notes = call_api(f"/api/notes?member_id={member_id}")
    print(json.dumps(member_notes, indent=2))
    
    return result["id"]

def demo_chat(member_id):
    """Demonstrate chat functionality"""
    print("\n=== Chat Demo ===")
    
    # Chat request with the assistant
    chat_request = {
        "messages": [
            {
                "role": "user",
                "content": "Can you add a birthday party for my mom next Saturday at 3pm?"
            }
        ],
        "member_id": member_id
    }
    
    print("\nSending chat request...")
    result = call_api("/api/chat", method="POST", data=chat_request)
    print("\nAssistant Response:")
    print(result["response"])
    
    if result["commands"]:
        print("\nCommands executed:")
        for cmd in result["commands"]:
            print(f"  - {cmd['type']}: {cmd['success']}")
    
    # Get chat history
    print(f"\nChat history for member {member_id}:")
    history = call_api(f"/api/chat-history/{member_id}")
    print(f"Found {len(history)} chat entries")
    
    if history:
        print(f"Latest chat summary: {history[0].get('summary', 'No summary')}")

def demo_suggested_questions(member_id):
    """Demonstrate suggested questions functionality"""
    print("\n=== Suggested Questions Demo ===")
    
    result = call_api(f"/api/suggested-questions?member_id={member_id}&count=3")
    print("\nSuggested questions:")
    for i, question in enumerate(result["questions"], 1):
        print(f"{i}. {question}")

def demo_search():
    """Demonstrate search functionality"""
    print("\n=== Search Demo ===")
    
    search_request = {
        "query": "Latest AI advancements 2024"
    }
    
    print("\nSearching for information...")
    result = call_api("/api/search", method="POST", data=search_request)
    
    if result:
        print("\nSearch Results:")
        print(result["result"])

def main():
    """Main demo function"""
    print("Family Assistant API Demo")
    print("========================")
    
    # Check if API is running
    status = call_api("/")
    if not status:
        print("Error: API server is not running. Please start the API server.")
        return
    
    print(f"API Status: {status['status']}")
    
    try:
        # Run demos
        member_id = demo_family_management()
        event_id = demo_event_management(member_id)
        note_id = demo_note_management(member_id)
        
        # Wait a moment for data to be saved
        time.sleep(1)
        
        demo_chat(member_id)
        demo_suggested_questions(member_id)
        
        # This requires TAVILY_API_KEY to be set
        if os.getenv("TAVILY_API_KEY"):
            demo_search()
        else:
            print("\nSkipping search demo (TAVILY_API_KEY not set)")
        
        print("\nDemo completed successfully!")
        
    except Exception as e:
        print(f"Error during demo: {e}")

if __name__ == "__main__":
    main()