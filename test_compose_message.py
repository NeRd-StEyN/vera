#!/usr/bin/env python3
"""
Unit tests for compose_message personality-aware improvements
"""
import json
import sys
# Fix Unicode encoding on Windows
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from bot import compose_message, detect_merchant_personality, select_best_offer, select_dynamic_cta

# Load test data
with open("expanded/merchants/m_001_drmeera_dentist_delhi.json", encoding="utf-8") as f:
    merchant = json.load(f)

with open("expanded/categories/dentists.json", encoding="utf-8") as f:
    category = json.load(f)

print("=" * 80)
print("TEST 1: compose_message for Customer - Recall Due Trigger")
print("=" * 80)
print(f"Merchant ID: {merchant.get('merchant_id')}")
print(f"Merchant Name: {merchant.get('identity', {}).get('name')}")

# Test trigger for customer
trigger = {
    "kind": "recall_due",
    "payload": {
        "due_date": "2026-05-15",
        "available_slots": [
            {"label": "Tomorrow 10:00 AM"},
            {"label": "Tomorrow 2:00 PM"}
        ]
    }
}

customer = {
    "identity": {
        "name": "Priya",
    },
    "relationship": {
        "visits_total": 8,  # loyal customer
        "last_visit_iso": "2026-04-15T14:00:00Z"
    }
}

body_text, cta, send_as, rationale = compose_message(merchant, category, trigger, customer)
print(f"Send As: {send_as}")
print(f"CTA: {cta}")
print(f"Rationale: {rationale}")
print(f"\nMessage:\n{body_text}")

print("\n" + "=" * 80)
print("TEST 2: compose_message for Customer - Lapsed Hard (At-Risk)")
print("=" * 80)

trigger = {
    "kind": "customer_lapsed_hard",
    "payload": {
        "days_since_last_visit": 120
    }
}

at_risk_customer = {
    "identity": {"name": "Rahul"},
    "relationship": {
        "visits_total": 3,  # at risk
        "last_visit_iso": "2025-12-15T14:00:00Z",
        "days_since_last_visit": 120,
        "loyalty_points": 250
    }
}

body_text, cta, send_as, rationale = compose_message(merchant, category, trigger, at_risk_customer)
print(f"Send As: {send_as}")
print(f"CTA: {cta}")
print(f"\nMessage:\n{body_text}")

print("\n" + "=" * 80)
print("TEST 3: compose_message for Merchant - Research Digest (Personality-Aware)")
print("=" * 80)

trigger = {
    "kind": "research_digest",
    "payload": {
        "top_item_id": "dentists_digest_001",
        "digest_item_id": "dentists_digest_001"
    }
}

# Override merchant personality for testing (simulate high engagement)
from bot import merchant_personality, engagement_scores
merchant_id = merchant.get('merchant_id', 'test_m_001')
merchant_personality[merchant_id] = {
    "personality_type": "enthusiastic",
    "engagement_level": 0.8,
    "response_rate": 0.85,
    "busyness_indicator": 0.2,
    "turn_count": 0,
    "response_times": [],
    "avg_response_time": 2.5
}
engagement_scores[merchant_id] = 80

body_text, cta, send_as, rationale = compose_message(merchant, category, trigger, None)
print(f"Send As: {send_as}")
print(f"CTA: {cta}")
print(f"Rationale: {rationale}")
print(f"\nMessage:\n{body_text}")

print("\n" + "=" * 80)
print("TEST 4: compose_message for Merchant - Performance Spike")
print("=" * 80)

trigger = {
    "kind": "perf_spike",
    "payload": {
        "metric": "calls",
        "delta_pct": 0.35
    }
}

body_text, cta, send_as, rationale = compose_message(merchant, category, trigger, None)
print(f"CTA: {cta}")
print(f"Rationale: {rationale}")
print(f"\nMessage:\n{body_text}")

print("\n" + "=" * 80)
print("TEST 5: compose_message for Merchant - Performance Dip (Price-Sensitive)")
print("=" * 80)

# Simulate price-sensitive merchant
merchant_personality[merchant_id] = {
    "personality_type": "price-sensitive",
    "engagement_level": 0.5,
    "response_rate": 0.65,
    "busyness_indicator": 0.3,
    "turn_count": 0,
    "response_times": [],
    "avg_response_time": 4.0
}
engagement_scores[merchant_id] = 45

trigger = {
    "kind": "perf_dip",
    "payload": {
        "metric": "views",
        "delta_pct": -0.15
    }
}

body_text, cta, send_as, rationale = compose_message(merchant, category, trigger, None)
print(f"CTA: {cta}")
print(f"Rationale: {rationale}")
print(f"\nMessage:\n{body_text}")

print("\n" + "=" * 80)
print("TEST 6: Personality Detection")
print("=" * 80)

# Test personality detection from message history
merchant_id = merchant.get('merchant_id', 'test_m_001')
message_history = [
    {"msg": "That sounds great! I'd definitely love to try it.", "from_role": "merchant"},
    {"msg": "This is perfect for my business!", "from_role": "merchant"}
]

personality = detect_merchant_personality(merchant_id, 2, message_history)
print(f"Personality Type: {personality['personality_type']}")
print(f"Engagement Level: {personality['engagement_level']}")
print(f"Response Rate: {personality['response_rate']}")

print("\n" + "=" * 80)
print("TEST 7: Smart Offer Selection")
print("=" * 80)

offers = merchant.get('offers', [])
merchant_with_high_perf = {**merchant, 'performance': {'views': 500, 'calls': 25, 'ctr': 0.08}}
best_offer = select_best_offer(merchant_with_high_perf, category, personality)
print(f"Recommended Offer (High Performer): {best_offer}")

# Test price-sensitive merchant
price_sensitive_personality = {
    "engagement_level": 0.4,
    "personality_type": "price-sensitive",
    "response_rate": 0.6
}
best_offer_price_sensitive = select_best_offer(merchant, category, price_sensitive_personality)
print(f"Recommended Offer (Price-Sensitive): {best_offer_price_sensitive}")

print("\n" + "=" * 80)
print("TEST 8: Dynamic CTA Selection")
print("=" * 80)

# High engagement, early turn
cta = select_dynamic_cta({"engagement_level": 0.85, "personality_type": "enthusiastic"}, 1, 85)
print(f"CTA for High Engagement (Early Turn): {cta}")

# Cautious merchant
cta = select_dynamic_cta({"engagement_level": 0.5, "personality_type": "cautious"}, 2, 50)
print(f"CTA for Cautious Merchant: {cta}")

# Low engagement, multiple turns
cta = select_dynamic_cta({"engagement_level": 0.3, "personality_type": "neutral"}, 4, 30)
print(f"CTA for Low Engagement: {cta}")

print("\n" + "=" * 80)
print("✅ All tests completed successfully!")
print("=" * 80)
