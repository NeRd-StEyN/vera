import json
import sys
import os

# Add the current directory to the path so we can import bot
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from bot import compose_message

def generate_submission():
    dataset_dir = "expanded"
    categories = {}
    for cat_name in ["dentists", "salons", "restaurants", "gyms", "pharmacies"]:
        with open(f"{dataset_dir}/categories/{cat_name}.json", encoding='utf-8') as f:
            categories[cat_name] = json.load(f)
            
    merchants = {}
    for f_name in os.listdir(f"{dataset_dir}/merchants"):
        with open(f"{dataset_dir}/merchants/{f_name}", encoding='utf-8') as f:
            m = json.load(f)
            merchants[m["merchant_id"]] = m
            
    customers = {}
    for f_name in os.listdir(f"{dataset_dir}/customers"):
        with open(f"{dataset_dir}/customers/{f_name}", encoding='utf-8') as f:
            c = json.load(f)
            customers[c["customer_id"]] = c
            
    triggers = {}
    for f_name in os.listdir(f"{dataset_dir}/triggers"):
        with open(f"{dataset_dir}/triggers/{f_name}", encoding='utf-8') as f:
            t = json.load(f)
            triggers[t["id"]] = t
            
    with open(f"{dataset_dir}/test_pairs.json", encoding='utf-8') as f:
        test_pairs = json.load(f).get("pairs", [])
        
    submission_lines = []
    
    for pair in test_pairs:
        trg_id = pair["trigger_id"]
        trg = triggers.get(trg_id)
        if not trg:
            print(f"Trigger {trg_id} not found!")
            continue
            
        merchant_id = pair["merchant_id"]
        merchant = merchants.get(merchant_id, {})
        category = categories.get(merchant.get("category_slug", ""))
        
        customer_id = pair.get("customer_id")
        customer = customers.get(customer_id) if customer_id else None
        
        body_text, cta, send_as, rationale = compose_message(merchant, category, trg, customer)
        
        test_id = pair["test_id"]
        suppression_key = trg.get("suppression_key", f"sup_{trg_id}")
        
        line = {
            "test_id": test_id,
            "body": body_text,
            "cta": cta,
            "send_as": send_as,
            "suppression_key": suppression_key,
            "rationale": rationale
        }
        submission_lines.append(line)
        
    with open("submission.jsonl", "w", encoding='utf-8') as f:
        for line in submission_lines:
            f.write(json.dumps(line) + "\n")
            
    print(f"Generated submission.jsonl with {len(submission_lines)} lines")

if __name__ == "__main__":
    generate_submission()
