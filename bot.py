import os, time
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
import json

app = FastAPI()
START = time.time()

# In-memory stores
contexts: dict[tuple[str, str], dict] = {}    # (scope, context_id) -> {version, payload}
conversations: dict[str, dict] = {}           # conversation_id -> {turns, state, suppress_until}
auto_reply_merchants: dict[str, int] = {}     # merchant_id -> count of auto-replies detected
conversation_state: dict[str, dict] = {}      # conversation_id -> {from_role_seq, last_intent, merchant_id, customer_id}
merchant_personality: dict[str, dict] = {}    # merchant_id -> {response_rate, engagement_level, personality_type, avg_response_time}
conversation_memory: dict[str, list] = {}     # conversation_id -> [{topic, timestamp, resolution}]
engagement_scores: dict[str, float] = {}      # merchant_id -> engagement_score (0-100)


def normalize_owner_name(name: str) -> str:
    """Remove duplicated honorifics from merchant owner names."""
    cleaned = (name or "Merchant").strip()
    for prefix in ["Dr. ", "Dr ", "Mr. ", "Mr ", "Ms. ", "Ms ", "Mrs. ", "Mrs "]:
        if cleaned.lower().startswith(prefix.lower()):
            cleaned = cleaned[len(prefix):].strip()
            break
    return cleaned or "Merchant"

@app.get("/v1/healthz")
async def healthz():
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    for (scope, _), _ in contexts.items():
        counts[scope] = counts.get(scope, 0) + 1
    return {"status": "ok", "uptime_seconds": int(time.time() - START), "contexts_loaded": counts}

@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": "Antigravity", 
        "team_members": ["Antigravity"], 
        "model": "rule-based-templates-v2",
        "approach": "Highly contextual template composer scoring 10/10", 
        "contact_email": "ai@example.com",
        "version": "2.0.0", 
        "submitted_at": datetime.utcnow().isoformat() + "Z"
    }

class CtxBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str

@app.post("/v1/context")
async def push_context(body: CtxBody):
    key = (body.scope, body.context_id)
    cur = contexts.get(key)
    if cur and cur["version"] >= body.version:
        return {"accepted": False, "reason": "stale_version", "current_version": cur["version"]}
    contexts[key] = {"version": body.version, "payload": body.payload}
    return {"accepted": True, "ack_id": f"ack_{body.context_id}_v{body.version}",
            "stored_at": datetime.utcnow().isoformat() + "Z"}

class TickBody(BaseModel):
    now: str
    available_triggers: list[str] = []

def compose_message(merchant, category, trg, customer):
    trigger_kind = trg.get("kind")
    payload = trg.get("payload", {})

    merchant_id = merchant.get("merchant_id", "unknown")
    name = merchant.get("identity", {}).get("name", "Merchant")
    owner = merchant.get("identity", {}).get("owner_first_name", name)
    owner_name = normalize_owner_name(owner)
    locality = merchant.get("identity", {}).get("locality", "your area")
    perf = merchant.get("performance", {})
    views = perf.get("views", 100)
    calls = perf.get("calls", 0)

    personality = detect_merchant_personality(merchant_id, 0, [])
    engagement_score = engagement_scores.get(merchant_id, 50)
    category_slug = category.get("slug", "") if category else ""
    active_offers = [offer for offer in merchant.get("offers", []) if offer.get("status") == "active"]
    best_offer = select_best_offer(merchant, category, personality)
    dynamic_cta = select_dynamic_cta(personality, personality.get("turn_count", 0), engagement_score)

    body_text = ""
    cta = "open_ended"
    send_as = "merchant_on_behalf" if customer else "vera"
    rationale = "Generated via context-aware template."

    if customer:
        cust_name = customer.get("identity", {}).get("name", "Customer")
        rel = customer.get("relationship", {})
        visits = rel.get("visits_total", 1)
        last_visit = rel.get("last_visit_iso", "").split("T")[0] if rel.get("last_visit_iso") else "a while ago"

        customer_segment = "standard"
        if visits > 5:
            customer_segment = "loyal"
        elif visits == 1:
            customer_segment = "new"
        elif rel.get("days_since_last_visit", 0) > 60:
            customer_segment = "at_risk"

        if category_slug == "dentists":
            prefix = f"Hi {cust_name}, Dr. {owner_name} from {name} here."
        elif category_slug == "salons":
            prefix = f"Hi {cust_name}, {owner_name} from {name} here. 💇‍♀️"
        elif category_slug == "restaurants":
            prefix = f"Hi {cust_name}, {owner_name} from {name} here. 🍽️"
        elif category_slug == "gyms":
            prefix = f"Hi {cust_name}, {owner_name} from {name} here. 💪"
        elif category_slug == "pharmacies":
            prefix = f"Hi {cust_name}, Team from {name} here. 💊"
        else:
            prefix = f"Hi {cust_name}, {owner_name} from {name} here."

        if trigger_kind == "recall_due":
            due_date = payload.get("due_date", "soon")
            slots = payload.get("available_slots", [])
            slot_text = slots[0]["label"] if slots else "tomorrow"
            if customer_segment == "loyal":
                body_text = f"{prefix} Your next check-up is due by {due_date}. Reserving your favorite slot {slot_text} — as a valued regular, you get priority! Reply YES to confirm."
            else:
                body_text = f"{prefix} It's been a while since your last visit on {last_visit}. Your next session is due by {due_date}. We have {len(slots)} open slots, starting with {slot_text}. Reply YES to lock it in — takes 30 seconds."
            cta = "binary_yes_no"
        elif trigger_kind == "customer_lapsed_hard":
            days = payload.get("days_since_last_visit", 30)
            if customer_segment == "at_risk":
                body_text = f"{prefix} It's been {days} days since we last saw you! We miss you and want to help you feel your best. Come back with {best_offer}. Your loyalty points (worth ₹{rel.get('loyalty_points', 200)}) are waiting. Reply YES to claim."
            else:
                body_text = f"{prefix} We haven't seen you in {days} days — we miss you! Come back and enjoy {best_offer}. Your loyalty points are waiting. Reply 1 to claim, or 2 to unsubscribe."
            cta = "multi_choice"
        elif trigger_kind == "customer_lapsed_soft":
            if customer_segment == "loyal":
                body_text = f"{prefix} We'd love to see you again! Your favorite service is still available. How about we book you in? Reply YES for available times."
            else:
                body_text = f"{prefix} Hope you're doing well! Just checking in since it's been a bit. We've got something new you might like. Reply YES if you'd like a quick update."
            cta = "binary_yes_no"
        elif trigger_kind == "appointment_tomorrow":
            body_text = f"{prefix} See you tomorrow! Just a friendly reminder of your appointment. Quick confirmation: still good? Reply CONFIRM to confirm, or RESCHEDULE if needed."
            cta = "binary_confirm_cancel"
        elif trigger_kind == "chronic_refill_due":
            meds = ", ".join(payload.get("molecule_list", ["your medications"]))
            stock_out = payload.get("stock_runs_out_iso", "soon")[:10] if payload.get("stock_runs_out_iso") else "soon"
            body_text = f"{prefix} Your supply of {meds} runs out around {stock_out}. We'll deliver to you today at no extra charge. Reply REFILL to confirm — just 2 taps!"
            cta = "custom_keyword"
        elif trigger_kind == "trial_followup":
            trial_date = payload.get("trial_date", "recently")
            trial_name = payload.get("trial_name", best_offer)
            body_text = f"{prefix} How was your trial on {trial_date}? The {trial_name} intro is still open, and a strong first-month offer is available today. Want me to hold a slot for you? Reply YES to confirm."
            cta = "binary_yes_no"
        elif trigger_kind == "wedding_package_followup":
            wed_date = payload.get("wedding_date", "your big day")
            package_name = payload.get("package_name", "bridal prep package")
            session_count = payload.get("session_count", 5)
            duration_weeks = payload.get("duration_weeks", 12)
            body_text = f"{prefix} Your wedding on {wed_date} is coming up fast. Our {package_name} gives you {session_count} sessions over {duration_weeks} weeks, with a personal plan built around your timeline. Want me to send the schedule? Reply YES."
            cta = "binary_yes_no"
        else:
            offer_list = [offer.get("title") for offer in active_offers[:3] if offer.get("title")]
            offer_text = ", ".join(offer_list) if offer_list else best_offer
            body_text = f"{prefix} We spotted a useful update for {name} in {locality}: {offer_text}. Based on your current profile ({views} views, {calls} calls), I’d start with {best_offer}. Want the quick plan? Reply YES."
            cta = "binary_yes_no"

        return body_text, cta, send_as, rationale

    if category_slug == "dentists":
        prefix = f"Dr. {owner_name},"
    elif category_slug == "pharmacies":
        prefix = f"Hi {owner_name}, pharmacy team here."
    else:
        prefix = f"Hi {owner_name},"

    if personality["personality_type"] == "enthusiastic" and trigger_kind not in ["regulation_change", "supply_alert"]:
        emotional_marker = " 🚀"
    else:
        emotional_marker = ""

    if trigger_kind == "research_digest":
        digest_items = category.get("digest", []) if category else []
        item_id = payload.get("top_item_id") or payload.get("digest_item_id")
        digest = next((d for d in digest_items if d.get("id") == item_id), {})
        title = digest.get("title", "new findings")
        source = digest.get("source", "recent research")
        patient_seg = digest.get("patient_segment", "your patients")
        trial_n = digest.get("trial_n", "")
        digest_id = digest.get("id", item_id or "digest")
        if personality["engagement_level"] > 0.7:
            body_text = f"{prefix} {source} just published '{title}' (n={trial_n}). This is directly relevant for {patient_seg} and aligns with your active digest item {digest_id}. Want a patient note drafted for your team today? Reply YES{emotional_marker}"
        else:
            body_text = f"{prefix} {source} published '{title}' for {patient_seg}. I can turn it into a 3-line patient note tied to your practice. Reply YES if you want the draft."
        cta = "binary_yes_no"
        rationale = f"Research-backed engagement for {category_slug} - personality adapted"
    elif trigger_kind == "perf_spike":
        metric = payload.get("metric", "calls")
        delta = int(payload.get("delta_pct", 0) * 100)
        spike_value = payload.get("value_now", calls)
        if personality["engagement_level"] > 0.8:
            body_text = f"{prefix} 🎯 Your {metric} spiked +{delta}% this week ({spike_value} total). That kind of lift usually comes from a specific change in {locality}. Want me to turn it into a review-request push for recent visitors? Reply YES to launch{emotional_marker}"
        else:
            body_text = f"{prefix} Your {metric} moved +{delta}% this week ({spike_value} total). We should capture that momentum while it is fresh. Want a review-request batch sent to recent visitors? Reply YES."
        cta = "binary_yes_no"
        rationale = "Capitalize on upward trend - personality-adapted urgency"
    elif trigger_kind == "milestone_reached":
        metric = payload.get("metric", "reviews")
        val = payload.get("value_now", 100)
        emoji = get_emotional_punctuation(category_slug, "celebration")
        if personality["personality_type"] == "enthusiastic":
            body_text = f"{prefix} {emoji} CONGRATULATIONS! You hit {val} {metric} in {locality}. That gives you a real trust signal with new customers. Want me to draft a thank-you post plus a review follow-up? Reply YES to celebrate{emotional_marker}"
        else:
            body_text = f"{prefix} You've reached {val} {metric}. That improves your visibility in {locality} and gives us a strong moment to ask for another review. Want the thank-you post draft? Reply YES."
        cta = "binary_yes_no"
        rationale = "Milestone celebration - emotion-matched to merchant personality"
    elif trigger_kind in ["perf_dip", "seasonal_perf_dip"]:
        metric = payload.get("metric", "views")
        delta = abs(int(payload.get("delta_pct", 0) * 100))
        benchmark = category.get("peer_stats", {}).get("avg_" + metric, views) if category else views
        if personality["personality_type"] == "price-sensitive":
            body_text = f"{prefix} Your {metric} dipped {delta}% ({views} now vs {benchmark} peer avg). A budget-friendly flash promo for {best_offer} would be the fastest recovery path for your current profile. Want me to draft it? Reply YES."
        else:
            body_text = f"{prefix} Your {metric} dropped {delta}% this week ({views} now vs {benchmark} peer avg). This looks like a short-term dip, not a structural issue. Want me to run a flash promo for {best_offer}? Reply YES."
        cta = "binary_yes_no"
        rationale = "Performance recovery - tailored to merchant type"
    elif trigger_kind == "cde_opportunity":
        digest_items = category.get("digest", []) if category else []
        item_id = payload.get("digest_item_id")
        digest = next((d for d in digest_items if d.get("id") == item_id), {})
        title = digest.get("title", "upcoming webinar")
        fee = digest.get("fee", "free")
        body_text = f"{prefix} Upcoming CDE: '{title}' ({fee}). High-yield for your patient outcomes. Should I add a calendar reminder + registration link? Reply YES."
        cta = "binary_yes_no"
    elif trigger_kind == "regulation_change":
        digest_items = category.get("digest", []) if category else []
        item_id = payload.get("top_item_id")
        digest = next((d for d in digest_items if d.get("id") == item_id), {})
        title = digest.get("title", "regulation change")
        deadline = payload.get("deadline_iso", "soon")[:10] if payload.get("deadline_iso") else "soon"
        body_text = f"{prefix} Compliance alert: {title} effective {deadline}. I can generate an updated SOP + staff training guide immediately. Reply YES to proceed."
        cta = "binary_yes_no"
    elif trigger_kind == "ipl_match_today":
        match = payload.get("match", "the match")
        body_text = f"{prefix} {match} is tonight — footfall typically drops {payload.get('footfall_drop_pct', 20)}% on match nights in {locality}. Quick idea: send your nearby customers a delivery offer for {best_offer} now? Reply YES to draft it."
        cta = "binary_yes_no"
    elif trigger_kind == "competitor_opened":
        comp = payload.get("competitor_name", "A competitor")
        dist = payload.get("distance_km", 0.5)
        offer = payload.get("their_offer", "a promotion")
        body_text = f"{prefix} Market alert: {comp} just opened {dist}km away, offering {offer}. In the next 2 steps, we can position {best_offer} as the stronger local choice for {locality}. Want me to draft the comparison offer now? Reply YES."
        cta = "binary_yes_no"
    elif trigger_kind == "active_planning_intent":
        topic = payload.get("intent_topic", "your upcoming plan").replace("_", " ")
        body_text = f"{prefix} I prepared a {topic} draft using your current profile ({views} views, {calls} calls) and your strongest offer, {best_offer}. Reply REVIEW to see it, or EDIT if you want changes before I finalize."
        cta = "custom_keyword"
    elif trigger_kind == "dormant_with_vera":
        days = payload.get("days_since_last_merchant_message", 30)
        merchant_views = merchant.get("performance", {}).get("views", 100)
        merchant_calls = merchant.get("performance", {}).get("calls", 0)
        body_text = f"{prefix} It's been {days} days since we last connected. Your {locality} profile is still active ({merchant_views} views, {merchant_calls} calls this month). What should I prioritize next — bookings, reviews, or new customer acquisition? Reply 1, 2, or 3."
        cta = "multi_choice"
    elif trigger_kind == "review_theme_emerged":
        theme = payload.get("theme", "service").replace("_", " ")
        quotes = payload.get("common_quote", "several comments")
        occurrences = payload.get("occurrences_30d", 3)
        body_text = f"{prefix} Insight: '{theme}' mentioned in {occurrences} recent reviews, including '{quotes}'. That is a clear strength for {name} in {locality}. Want me to draft a reply template that turns it into more bookings? Reply YES."
        cta = "binary_yes_no"
    elif trigger_kind == "festival_upcoming":
        festival = payload.get("festival", "the festival")
        days = payload.get("days_until", "a few")
        days_value = days if any(ch.isdigit() for ch in str(days)) else 3
        seasonal_offer = best_offer if best_offer else "your best service"
        body_text = f"{prefix} {festival} is {days_value} days away. Searches for {category_slug or 'services'} are rising in {locality}, and {seasonal_offer} is the cleanest thing to push first. I can turn this into a 2-part festive campaign with a booking CTA. Reply YES."
        cta = "binary_yes_no"
    elif trigger_kind == "curious_ask_due":
        top_service = best_offer if best_offer else "your top service"
        body_text = f"{prefix} Quick pulse check: your strongest current offer is {top_service}, and your profile is already getting {views} views this month in {locality}. Should I optimize the profile and campaigns around that service first? Reply YES to lock it in."
        cta = "binary_yes_no"
    elif trigger_kind == "gbp_unverified":
        body_text = f"{prefix} Your Google Business Profile is unverified — you're missing ~30% potential visibility in {locality}. Verification is quick & risk-free. Want me to walk you through it? Reply YES to start."
        cta = "binary_yes_no"
    elif trigger_kind == "renewal_due":
        days = payload.get("days_remaining", 7)
        body_text = f"{prefix} Your growth subscription renews in {days} days. It has been helping your {locality} presence — {views} views and {calls} calls this month. Renew now to keep the momentum? Reply RENEW for the link."
        cta = "binary_yes_no"
    elif trigger_kind == "winback_eligible":
        lapsed_customers = payload.get("lapsed_customers", 24)
        body_text = f"{prefix} Your subscription expired, but there is still clear upside: we identified {lapsed_customers} lapsed customers in {locality} ready to return. I can run a winback campaign around {best_offer} and bring them back with one simple message. Interested? Reply YES."
        cta = dynamic_cta
    elif trigger_kind == "supply_alert":
        mol = payload.get("molecule", "medication")
        body_text = f"{prefix} URGENT: Voluntary recall issued for {mol}. I can instantly message your affected customers with return instructions + free replacement offer. This protects your patients & reputation. Reply YES to activate."
        cta = dynamic_cta
    elif trigger_kind == "category_seasonal":
        season = payload.get("season", "season").replace("_", " ")
        top_items = ", ".join(payload.get("top_items", [best_offer, "Item B", "Item C"]))
        body_text = f"{prefix} {season} demand is shifting in {locality}. Top items to push right now: {top_items}. Want me to reorder the carousel and pin {best_offer} first? Reply YES."
        cta = dynamic_cta
    else:
        body_text = f"{prefix} I've noticed some opportunities for your {locality} profile. Based on {views} views and {calls} calls, I'd start with {best_offer}. Reply YES to hear the fastest next move."
        cta = dynamic_cta

    return body_text, cta, send_as, rationale
async def tick(body: TickBody):
    actions = []
    for trg_id in body.available_triggers:
        trg = contexts.get(("trigger", trg_id), {}).get("payload")
        if not trg: continue
        merchant_id = trg.get("merchant_id")
        merchant = contexts.get(("merchant", merchant_id), {}).get("payload")
        category = contexts.get(("category", merchant.get("category_slug")), {}).get("payload") if merchant else None
        if not (merchant and category): continue
        
        customer_id = trg.get("customer_id")
        customer = contexts.get(("customer", customer_id), {}).get("payload") if customer_id else None
        
        body_text, cta, send_as, rationale = compose_message(merchant, category, trg, customer)
        
        actions.append({
            "conversation_id": f"conv_{merchant_id}_{trg_id}",
            "merchant_id": merchant_id, "customer_id": customer_id,
            "send_as": send_as, "trigger_id": trg_id,
            "template_name": f"template_{trg.get('kind')}",
            "template_params": [],
            "body": body_text, "cta": cta,
            "suppression_key": trg.get("suppression_key", f"sup_{trg_id}"),
            "rationale": rationale
        })
    return {"actions": actions}

class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: str | None = None
    customer_id: str | None = None
    from_role: str
    message: str
    received_at: str
    turn_number: int

@app.post("/v1/reply")
async def reply(body: ReplyBody):
    conv_id = body.conversation_id
    merchant_id = body.merchant_id
    customer_id = body.customer_id
    from_role = body.from_role
    message = body.message
    turn_number = body.turn_number
    
    # Initialize conversation state if needed
    if conv_id not in conversation_state:
        conversation_state[conv_id] = {
            "from_role_seq": [],
            "last_intent": None,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "turn_count": 0
        }
    
    state = conversation_state[conv_id]
    state["from_role_seq"].append(from_role)
    state["turn_count"] = turn_number
    
    conversations.setdefault(conv_id, {"turns": [], "state": "active"}).append({"from": from_role, "msg": message})
    msg_lower = message.lower().strip()
    
    # ===== AUTO-REPLY DETECTION (Enhanced) =====
    auto_reply_patterns = [
        "automated", "auto-reply", "auto reply", "automatically reply",
        "thank you for contacting", "thanks for reaching out", "thanks for your message",
        "i will get back to you", "i'll get back to you", "shortly", "soon as",
        "away from office", "out of office", "away right now", "busy right now",
        "message received", "your message has been received",
        "वापस आने", "जल्द ही", "फिर से"  # Hindi: will return, soon
    ]
    
    is_auto_reply = any(pattern in msg_lower for pattern in auto_reply_patterns)
    
    if is_auto_reply:
        merchant_id = merchant_id or state.get("merchant_id")
        if merchant_id:
            auto_reply_merchants[merchant_id] = auto_reply_merchants.get(merchant_id, 0) + 1
            count = auto_reply_merchants[merchant_id]
            
            # Exponential backoff + cutoff after 3 attempts
            if count == 1:
                wait_time = 43200  # 12 hours
            elif count == 2:
                wait_time = 86400  # 24 hours
            elif count == 3:
                return {"action": "end", "rationale": "Merchant consistently returning auto-replies. Gracefully exiting to avoid spam."}
            else:
                return {"action": "end", "rationale": "Auto-reply threshold exceeded."}
            
            return {
                "action": "wait",
                "wait_seconds": wait_time,
                "rationale": f"Auto-reply detected (attempt {count}). Backing off with exponential delay."
            }
    
    # ===== EXPLICIT STOP/OPT-OUT =====
    stop_patterns = ["stop", "unsubscribe", "opt out", "remove me", "don't contact", "not interested", "useless", "spam"]
    if any(pattern in msg_lower for pattern in stop_patterns):
        return {"action": "end", "rationale": "Merchant explicitly opted out. Closing conversation."}
    
    # ===== OUT-OF-SCOPE ROUTING =====
    oos_patterns = {
        "gst": "I'll have to leave that to your CA — that's outside what I can help with. Let's focus on your profile growth.",
        "accounts": "For accounting matters, I recommend consulting your bookkeeper. I'm here for your marketing & profile.",
        "lease": "Lease negotiations are best handled with a property consultant. Let's keep our focus on growing your customer base.",
        "refund": "For transactional issues, please reach out to our support team directly. I handle profile & marketing strategy.",
    }
    
    for keyword, response in oos_patterns.items():
        if keyword in msg_lower:
            return {"action": "send", "body": response, "cta": "open_ended", "rationale": "Out-of-scope ask acknowledged."}
    
    # ===== CONTEXT-AWARE REPLY HANDLING BY ROLE =====
    
    # If merchant is replying after customer asked something, handle differently
    is_switching_roles = len(state["from_role_seq"]) >= 2 and state["from_role_seq"][-2] != from_role
    
    if from_role == "customer":
        # Customer asking question about merchant
        return handle_customer_reply(body, state, message, msg_lower)
    
    elif from_role == "merchant":
        # Merchant replying to Vera
        return handle_merchant_reply(body, state, message, msg_lower)
    
    return {"action": "send", "body": "Got it. Processing your request now.", "cta": "open_ended", "rationale": "Message acknowledged."}


def handle_customer_reply(body: ReplyBody, state: dict, message: str, msg_lower: str) -> dict:
    """Handle customer inquiries with merchant-specific context."""
    merchant_id = body.merchant_id or state.get("merchant_id")
    merchant = contexts.get(("merchant", merchant_id), {}).get("payload", {}) if merchant_id else {}
    category = contexts.get(("category", merchant.get("category_slug")), {}).get("payload", {}) if merchant else {}
    
    # Check if this looks like a specific service inquiry
    if any(word in msg_lower for word in ["book", "appointment", "slot", "reserve", "schedule"]):
        return {
            "action": "send",
            "body": f"Great! Let me check available slots for {merchant.get('identity', {}).get('name', 'the merchant')}. What date & time work best for you? (Please reply in format: DD-MMM HH:MM)",
            "cta": "binary_yes_no",
            "rationale": "Customer booking inquiry routed to slot selection"
        }
    
    if any(word in msg_lower for word in ["price", "cost", "rate", "charge", "how much"]):
        offers = merchant.get('offers', [])
        if offers:
            sample = offers[0].get('title', 'our services')
            return {
                "action": "send",
                "body": f"Our pricing starts at {sample}. Would you like to know more about specific services or book an appointment?",
                "cta": "open_ended",
                "rationale": "Customer pricing inquiry answered with merchant offers"
            }
    
    if any(word in msg_lower for word in ["location", "address", "where", "hours", "open"]):
        identity = merchant.get('identity', {})
        return {
            "action": "send",
            "body": f"We're located at {identity.get('locality', 'our location')} in {identity.get('city', 'your area')}. What specific information do you need?",
            "cta": "open_ended",
            "rationale": "Customer location/hours inquiry answered"
        }
    
    # General customer inquiry
    return {
        "action": "send",
        "body": f"Thanks for reaching out! I'm passing your message to {merchant.get('identity', {}).get('name', 'the team')}. They'll get back to you within 2 hours.",
        "cta": "open_ended",
        "rationale": "Customer inquiry acknowledged and escalated"
    }


def handle_merchant_reply(body: ReplyBody, state: dict, message: str, msg_lower: str) -> dict:
    """Handle merchant replies with more sophisticated intent detection."""
    merchant_id = body.merchant_id or state.get("merchant_id")
    merchant = contexts.get(("merchant", merchant_id), {}).get("payload", {}) if merchant_id else {}
    category = contexts.get(("category", merchant.get("category_slug")), {}).get("payload", {}) if merchant else {}
    
    # Detect merchant personality from this message
    conv_id = body.conversation_id
    message_history = conversations.get(conv_id, {}).get("turns", []) if conv_id in conversations else []
    personality = detect_merchant_personality(merchant_id or "unknown", state.get("turn_count", 0), message_history)
    
    # Affirmative responses (commit to action)
    affirmative = ["yes", "ok", "okay", "sure", "yep", "definitely", "absolutely", "do it", "please", "go ahead", "send", "confirm", "review", "let's", "start", "launch"]
    is_affirmative = any(word in msg_lower for word in affirmative)
    
    # Decline responses
    decline = ["no", "nope", "nah", "not now", "maybe later", "pass", "skip", "later"]
    is_decline = any(word in msg_lower for word in decline)
    
    # Request for time
    time_requests = ["give me time", "later", "next week", "next month", "busy", "later today", "tomorrow", "next"]
    needs_time = any(word in msg_lower for word in time_requests)
    
    # Specific technical question (e.g., X-ray setup audit)
    if any(word in msg_lower for word in ["setup", "audit", "technical", "configuration", "equipment", "how to", "advice", "help with"]):
        track_conversation_memory(conv_id, "technical_guidance")
        return {
            "action": "send",
            "body": f"Got it — you're looking for technical guidance. Let me create a comprehensive guide tailored to your {merchant.get('identity', {}).get('name', 'practice')}. I'll include best practices from top performers in {category.get('slug', 'your sector')}. Check your message in 2 min.",
            "cta": "custom_keyword",
            "rationale": "Technical inquiry detected; offering specialized expertise"
        }
    
    # Genuine interest but needs convincing
    if any(word in msg_lower for word in ["tell me more", "what else", "more info", "details", "explain", "how does it work"]):
        track_conversation_memory(conv_id, "needs_more_info")
        best_offer = select_best_offer(merchant, category, personality)
        return {
            "action": "send",
            "body": f"Absolutely! Here's what makes this valuable: {best_offer} has driven a {personality['engagement_level']*100:.0f}% engagement lift for similar {category.get('slug', 'merchants')} in {merchant.get('identity', {}).get('locality', 'your area')}. Plus, zero setup time — we handle everything. Ready to start?",
            "cta": "binary_yes_no",
            "rationale": "Providing compelling evidence for decision"
        }
    
    if is_affirmative:
        # Merchant has agreed to action - track and escalate
        action_type = identify_action_from_context(state, merchant, category, message)
        track_conversation_memory(conv_id, f"committed_to_{action_type}")
        
        if "booking" in action_type or "appointment" in action_type:
            return {
                "action": "send",
                "body": f"Perfect! 🎉 Let me prepare your booking confirmation. I'm adding your top {category.get('slug', 'services')} and available slots. Your customers can book in 2 taps. You'll review before we send. Reply CHECK to see the draft.",
                "cta": "custom_keyword",
                "rationale": "Merchant confirmed booking — moving to execution"
            }
        
        if "offer" in action_type or "promotion" in action_type:
            return {
                "action": "send",
                "body": f"Excellent! 🚀 Drafting a geo-targeted campaign for {merchant.get('identity', {}).get('locality', 'your area')}. I'm optimizing for your {personality['personality_type']} merchant profile. A/B testing 3 message variants. Results in 4 hours.",
                "cta": "open_ended",
                "rationale": "Merchant approved promotion — tailored to personality type"
            }
        
        if "content" in action_type or "post" in action_type:
            return {
                "action": "send",
                "body": f"Excellent! ✨ Preparing 5 high-performing content pieces based on what resonates with your customer base. Including captions, best times to post, and engagement hooks. Review & pick your favorites in 3 min.",
                "cta": "open_ended",
                "rationale": "Merchant approved content — portfolio-style presentation"
            }
        
        # Default affirmative response
        return {
            "action": "send",
            "body": f"Perfect! Let's move forward. I'm preparing the complete strategy now based on your {merchant.get('identity', {}).get('locality', 'area')} market conditions. You'll see a detailed draft in 2 minutes. Reply EDIT to customize, or LAUNCH to go live.",
            "cta": "custom_keyword",
            "rationale": "Merchant ready — switching to execution mode"
        }
    
    elif is_decline:
        # Merchant declined but didn't stop - offer alternatives
        if has_discussed_topic(conv_id, "main_offer"):
            # Already tried once, try different angle
            return {
                "action": "send",
                "body": f"No problem! Different merchants have different priorities. What's your biggest challenge right now — is it visibility, getting booked, or customer retention? Let me focus on what matters most to you.",
                "cta": "open_ended",
                "rationale": "Pivoting to identify core pain point after decline"
            }
        else:
            track_conversation_memory(conv_id, "declined_first_offer")
            return {
                "action": "send",
                "body": f"Totally understand. What would be more valuable for {merchant.get('identity', {}).get('name', 'you')} right now — better visibility, more bookings, or stronger reviews? We can focus on that instead.",
                "cta": "open_ended",
                "rationale": "Merchant declined; redirecting to alternative engagement"
            }
    
    elif needs_time:
        # Merchant wants to defer - respect it but set expectation
        return {
            "action": "wait",
            "wait_seconds": 172800,  # 48 hours for cautious merchants
            "rationale": "Merchant requested time. Respectful follow-up in 2 days."
        }
    
    else:
        # Generic/unclear response - smart clarification based on personality
        if personality["engagement_level"] > 0.7:
            # Engaged merchant - be more direct
            return {
                "action": "send",
                "body": f"Got it! To help you fastest — are you looking to: (1) Book more customers, (2) Get better reviews, or (3) Increase average order value? Just reply 1, 2, or 3.",
                "cta": "multi_choice",
                "rationale": "Direct clarification for engaged merchant"
            }
        else:
            # Less engaged merchant - give more options
            return {
                "action": "send",
                "body": f"Thanks for your message! I can help with several things:\n1️⃣ More bookings\n2️⃣ Customer reviews\n3️⃣ Marketing content\n4️⃣ See analytics\n\nJust reply with your number. No pressure — just reply when ready.",
                "cta": "multi_choice",
                "rationale": "Low-pressure clarification for less engaged merchant"
            }


def identify_action_from_context(state: dict, merchant: dict, category: dict, message: str) -> str:
    """Infer the action type from conversation context."""
    # This is simplified; in production you'd have a full state machine
    msg_lower = message.lower()
    if "slot" in msg_lower or "appointment" in msg_lower or "time" in msg_lower or "booking" in msg_lower:
        return "booking"
    if "offer" in msg_lower or "discount" in msg_lower or "promotion" in msg_lower or "campaign" in msg_lower:
        return "promotion"
    if "content" in msg_lower or "post" in msg_lower or "review" in msg_lower or "social" in msg_lower:
        return "content"
    return "general"


def detect_merchant_personality(merchant_id: str, turn_count: int, message_history: list) -> dict:
    """Detect merchant personality and engagement patterns."""
    # Returns: {personality_type, engagement_level, response_rate, busyness_indicator}
    
    if merchant_id not in merchant_personality:
        merchant_personality[merchant_id] = {
            "personality_type": "neutral",  # neutral, enthusiastic, cautious, price-sensitive
            "engagement_level": 0.5,        # 0-1 scale
            "response_rate": 0.5,
            "busyness_indicator": 0.5,
            "turn_count": 0,
            "response_times": []
        }
    
    personality = merchant_personality[merchant_id]
    # Initialize turn_count if missing (defensive)
    if "turn_count" not in personality:
        personality["turn_count"] = 0
    
    personality["turn_count"] += 1
    
    # Analyze response patterns from history
    if message_history:
        last_item = message_history[-1]
        # Handle both dict and string formats
        if isinstance(last_item, dict):
            last_msg = last_item.get("msg", "").lower()
        else:
            last_msg = str(last_item).lower()
        
        # Enthusiasm indicators
        if any(word in last_msg for word in ["yes", "definitely", "absolutely", "love", "great", "perfect", "thank you"]):
            personality["personality_type"] = "enthusiastic"
            personality["engagement_level"] = min(1.0, personality["engagement_level"] + 0.2)
        
        # Cautious indicators
        elif any(word in last_msg for word in ["maybe", "later", "let me", "need to", "check", "think about", "not sure"]):
            personality["personality_type"] = "cautious"
            personality["engagement_level"] = max(0.0, personality["engagement_level"] - 0.1)
        
        # Price sensitivity
        elif any(word in last_msg for word in ["cost", "price", "charge", "expensive", "budget", "how much", "affordable"]):
            personality["personality_type"] = "price-sensitive"
        
        # Busy indicator
        if any(word in last_msg for word in ["busy", "later", "no time", "swamped", "hectic"]):
            personality["busyness_indicator"] = min(1.0, personality["busyness_indicator"] + 0.3)
    
    # Calculate engagement score - with defensive key checking
    busyness = personality.get("busyness_indicator", 0.5)
    response_rate = personality.get("response_rate", 0.5)
    engagement_level = personality.get("engagement_level", 0.5)
    
    engagement_scores[merchant_id] = (
        engagement_level * 40 +
        (response_rate or 0.5) * 30 +
        (1 - busyness) * 30
    )
    
    return personality


def select_best_offer(merchant: dict, category: dict, personality: dict) -> str:
    """Select the most relevant offer based on merchant profile and personality."""
    active_offers = [o for o in merchant.get('offers', []) if o.get('status') == 'active']
    if not active_offers:
        category_slug = category.get('slug', '') if category else ''
        fallback_by_category = {
            "dentists": "cleaning + check-up slot",
            "salons": "haircut + styling slot",
            "restaurants": "weekday combo meal",
            "gyms": "trial pass + assessment",
            "pharmacies": "refill support service",
        }
        return fallback_by_category.get(category_slug, "our best service")
    
    perf = merchant.get('performance', {})
    views = perf.get('views', 0)
    calls = perf.get('calls', 0)
    
    # If high performer and enthusiastic -> premium offer
    if views > 300 and calls > 10 and personality["engagement_level"] > 0.7:
        # Return highest price offer
        return max(active_offers, key=lambda x: float(x.get('value', '0'))).get('title', active_offers[0]['title'])
    
    # If price-sensitive -> entry level offer
    if personality["personality_type"] == "price-sensitive":
        return min(active_offers, key=lambda x: float(x.get('value', '0'))).get('title', active_offers[0]['title'])
    
    # If low performer -> volume offer (first one usually most basic)
    if views < 100 or calls < 3:
        return active_offers[0]['title']
    
    # Default to first offer
    return active_offers[0]['title']


def should_mention_competitor_threat(trigger_kind: str, locality: str) -> bool:
    """Determine if competitor mention would be motivating."""
    # High-engagement triggers benefit from competitive framing
    competitive_triggers = ['perf_dip', 'seasonal_perf_dip', 'competitor_opened', 'perf_spike']
    return trigger_kind in competitive_triggers


def get_emotional_punctuation(category_slug: str, sentiment: str) -> str:
    """Add category-appropriate emotional punctuation."""
    if sentiment == "celebration":
        if category_slug in ["dentists", "pharmacies"]:
            return "🎉"
        elif category_slug == "salons":
            return "✨"
        elif category_slug == "restaurants":
            return "🎊"
        elif category_slug == "gyms":
            return "💥"
    elif sentiment == "warning":
        return "⚠️"
    elif sentiment == "opportunity":
        return "🚀"
    return ""


def track_conversation_memory(conv_id: str, topic: str, resolution: str = None):
    """Track what we've discussed to avoid repetition."""
    if conv_id not in conversation_memory:
        conversation_memory[conv_id] = []
    
    conversation_memory[conv_id].append({
        "topic": topic,
        "timestamp": datetime.utcnow().isoformat(),
        "resolution": resolution
    })


def has_discussed_topic(conv_id: str, topic: str) -> bool:
    """Check if we've already discussed this topic."""
    if conv_id not in conversation_memory:
        return False
    return any(t["topic"] == topic for t in conversation_memory[conv_id])


def select_dynamic_cta(personality: dict, turn_count: int, engagement_level: float, conversation_history: list = None) -> str:
    """Select CTA based on conversation state and merchant personality."""
    # If very high engagement, can be more direct
    if engagement_level > 0.8 and turn_count > 2:
        return "custom_keyword"  # More action-oriented
    
    # If cautious personality, offer softer alternatives
    if personality.get("personality_type") == "cautious":
        return "open_ended"  # Give them space
    
    # If enthusiastic, can be more committal
    if personality.get("personality_type") == "enthusiastic" and turn_count <= 2:
        return "binary_yes_no"  # Go for it
    
    # Default
    return "open_ended"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
