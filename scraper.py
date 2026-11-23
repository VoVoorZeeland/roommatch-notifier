import json
import os
import re
import requests
from playwright.sync_api import sync_playwright

URL = "https://www.roommatch.nl/aanbod/studentenwoningen#?gesorteerd-op=prijs%2B&locatie=Regio%2BHaaglanden%252F%2BLeiden"
SEEN_FILE = "seen_listings.json"
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")


def load_seen() -> set:
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return set(json.load(f))


def save_seen(seen_ids: set) -> None:
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen_ids)), f, indent=2)


def parse_card_info(text: str) -> tuple[str, str]:
    lower_text = text.lower()

    # Check "onzelfstandig" first since it contains "zelfstandig"
    if "onzelfstandig" in lower_text or "deel je" in lower_text:
        housing_type = "Shared (Onzelfstandig)"
    elif "zelfstandig" in lower_text:
        housing_type = "Zelfstandig"
    else:
        housing_type = "Unknown"

    # Match "Online tot: di 24-11-2026 om 00:00"
    deadline_match = re.search(r"Online tot:\s*([^\n]+)", text, re.IGNORECASE)
    closes_at = deadline_match.group(1).strip() if deadline_match else "Unknown"

    return housing_type, closes_at


def fetch_listings() -> list[dict]:
    listings = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        page.goto(URL, wait_until="networkidle", timeout=60000)

        try:
            page.wait_for_selector("a[href*='details']", timeout=20000)
        except Exception:
            print("Geen woningen gevonden of pagina laadde te langzaam.")
            browser.close()
            return []

        page.wait_for_timeout(3000)

        links = page.query_selector_all("a[href*='details']")
        for link in links:
            href = link.get_attribute("href")
            if not href:
                continue

            full_url = href if href.startswith("http") else f"https://www.roommatch.nl{href}"

            match = re.search(r"(\d{4,})", full_url)
            listing_id = match.group(1) if match else full_url

            if listing_id in listings:
                continue

            # Grab text from the closest card container (fallback to the link itself)
            card_text = link.evaluate(
                """el => {
                    const card = el.closest('article, .woning-item, .list-item, li') || el;
                    return card.innerText;
                }"""
            ).strip()

            lines = [line.strip() for line in card_text.splitlines() if line.strip()]
            title = lines[0] if lines else "Nieuwe studentenwoning"
            details = " | ".join(lines[1:4]) if len(lines) > 1 else "Klik op de link voor details."

            housing_type, closes_at = parse_card_info(card_text)

            listings[listing_id] = {
                "id": listing_id,
                "title": title,
                "details": details,
                "housing_type": housing_type,
                "closes_at": closes_at,
                "url": full_url,
            }

        browser.close()

    return list(listings.values())


def send_discord_notification(listing: dict) -> None:
    if not WEBHOOK_URL:
        raise ValueError("DISCORD_WEBHOOK_URL ontbreekt in de environment variables.")

    payload = {
        "embeds": [
            {
                "title": f"🏠 {listing['title']}",
                "url": listing["url"],
                "description": listing["details"],
                "color": 3447003,
                "fields": [
                    {"name": "Type", "value": listing["housing_type"], "inline": True},
                    {"name": "Sign-up closes", "value": listing["closes_at"], "inline": True},
                ],
                "footer": {"text": "Roommatch.nl • Regio Haaglanden / Leiden"},
            }
        ]
    }
    response = requests.post(WEBHOOK_URL, json=payload, timeout=15)
    response.raise_for_status()


def main():
    seen = load_seen()
    first_run = len(seen) == 0

    current_listings = fetch_listings()
    print(f"Aantal gevonden woningen: {len(current_listings)}")

    if not current_listings:
        return

    new_listings = [item for item in current_listings if item["id"] not in seen]

    if first_run:
        print(f"Eerste run: {len(current_listings)} bestaande woningen opgeslagen zonder meldingen.")
        for item in current_listings:
            seen.add(item["id"])
        save_seen(seen)
        return

    for item in new_listings:
        print(f"Nieuwe woning gevonden: {item['title']} | {item['housing_type']} | Sluit: {item['closes_at']}")
        send_discord_notification(item)
        seen.add(item["id"])

    if new_listings:
        save_seen(seen)
        print(f"{len(new_listings)} nieuwe melding(en) verstuurd.")
    else:
        print("Geen nieuwe woningen gevonden.")


if __name__ == "__main__":
    main()
