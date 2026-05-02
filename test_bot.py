import requests
import json
import time

BOT_URL = "http://localhost:8080"

print("--- Testing Bot Endpoints ---")

# 1. Health check
print("\n[1] Checking /v1/healthz...")
try:
    resp = requests.get(f"{BOT_URL}/v1/healthz")
    print("Healthz:", json.dumps(resp.json(), indent=2))
except Exception as e:
    print("Failed to reach bot:", e)
    exit(1)

# 2. Push Context (Merchant + Trigger)
print("\n[2] Pushing context layers...")

# Load from expanded dataset
with open("expanded/merchants/m_001_drmeera_dentist_delhi.json", encoding="utf-8") as f:
    merchant_data = json.load(f)
    
with open("expanded/triggers/trg_022_cde_webinar_dentists.json", encoding="utf-8") as f:
    trigger_data = json.load(f)

with open("expanded/categories/dentists.json", encoding="utf-8") as f:
    category_data = json.load(f)

requests.post(f"{BOT_URL}/v1/context", json={
    "scope": "category", "context_id": category_data["slug"], 
    "version": 1, "payload": category_data, "delivered_at": "2026-05-02T10:00:00Z"
})
requests.post(f"{BOT_URL}/v1/context", json={
    "scope": "merchant", "context_id": merchant_data["merchant_id"], 
    "version": 1, "payload": merchant_data, "delivered_at": "2026-05-02T10:00:00Z"
})
requests.post(f"{BOT_URL}/v1/context", json={
    "scope": "trigger", "context_id": trigger_data["id"], 
    "version": 1, "payload": trigger_data, "delivered_at": "2026-05-02T10:00:00Z"
})
print("Pushed Category Context: Dentists")
print("Pushed Merchant Context: Dr. Meera")
print("Pushed Trigger Context: CDE Webinar Opportunity")

# 3. Trigger a Tick
print("\n[3] Triggering /v1/tick to generate message...")
tick_resp = requests.post(f"{BOT_URL}/v1/tick", json={
    "now": "2026-05-02T10:00:00Z",
    "available_triggers": [trigger_data["id"]]
}).json()

actions = tick_resp.get("actions", [])
print(f"Bot returned {len(actions)} action(s):")
for action in actions:
    print(f"\n====================================")
    print(f"Message sent as: {action['send_as']}")
    print(f"CTA: {action['cta']}")
    print(f"\nBody:\n\"{action['body']}\"")
    print(f"====================================\n")

print("\n[4] Simulating Merchant Reply...")
reply_resp = requests.post(f"{BOT_URL}/v1/reply", json={
    "conversation_id": actions[0]["conversation_id"],
    "merchant_id": merchant_data["merchant_id"],
    "customer_id": None,
    "from_role": "merchant",
    "message": "Yes please, do it",
    "received_at": "2026-05-02T10:05:00Z",
    "turn_number": 2
}).json()

print("Merchant replied: 'Yes please, do it'")
print("Bot response:", json.dumps(reply_resp, indent=2))
