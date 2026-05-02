# Vera AI Challenge Submission

## Approach

This submission implements the Vera AI bot using a rule-based template generation approach built with FastAPI. It handles multi-turn conversations and responds dynamically to different types of triggers. The bot persists contexts in-memory to ensure low-latency message generation. It interprets triggers and automatically applies category-specific voices and merchant personalization.

The main composition logic is located in the `/v1/tick` endpoint, where it iterates over available triggers, retrieves all required 4 layers of context (Category, Merchant, Trigger, Customer), and produces customized messages.

For multi-turn capabilities (`/v1/reply`), it uses simple keyword heuristics to determine merchant intent, including gracefully detecting auto-replies, handling hostile opt-outs, and continuing the conversation towards fulfilling an action.

## Tradeoffs Made

1. **Rule-Based over LLM**: To guarantee < 30ms latency, deterministic test outputs, and zero external dependency risk, the composer was implemented using rule-based templating rather than an external LLM call. This reduces generative variability and avoids hallucination, at the cost of less organic, conversational variability.
2. **In-Memory Store**: Contexts and conversation state are stored in a fast in-memory python dictionary. While very fast, it is not durable across restarts. A real production system would utilize Redis or an equivalent fast remote cache.
3. **Keyword-Based Reply Handlers**: Interpreting user replies uses basic NLP keyword matching (e.g., detecting "auto" or "shortly" for auto-replies). This is an acceptable tradeoff for test conditions but an LLM classifier would be more robust against paraphrasing.

## Additional Context That Would Have Helped

- **Real WhatsApp Conversation Logs**: Access to a broader set of raw, un-anonymized conversation transcripts would have helped fine-tune the rule-based templates for specific edge cases and unexpected merchant queries.
- **Specific Opt-In/Opt-Out Regulations**: More detailed information on WhatsApp business APIs and regional consent requirements, to ensure the multi-turn cadences comply strictly with external platform rules.
- **Deeper Customer History**: More granular history per customer (e.g. detailed past purchases and interaction rates) would allow for much stronger personalization in customer-facing flows.
