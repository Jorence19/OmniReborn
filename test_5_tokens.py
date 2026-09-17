import asyncio
import httpx
from database import ForensicDatabase
from branding_scraper import BrandingScraper
from cluster import calculate_pair_similarity

SAMPLE_CAS = [
    "0x5317C0d077D2eEB639448939b930D49c4984B63B",  # COPPERINU (Peak ATH $20.1M)
    "0x85BD531bA0787D14fF20aa5a3d49E0E44Ac9650C",  # HABITS (team astro)
    "0x7190Cb95dead29359eB9050f03Ce6bffBf2915c0",  # gigalon (team gigalon)
    "0x73Fd518b4998Df42FBa3AE740865F374522baE2a",  # NCHIP (team NCHIP)
    "0x1b8A598A3cBc9EA2F256ff139DB839f9A70C97b9",  # WASH (ATH $1.96M, 1st boost)
]

async def fetch_dexscreener_profile(ca: str) -> dict:
    url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                pairs = data.get("pairs") or []
                if not pairs:
                    return {}
                # Take highest liquidity pair
                best_pair = max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
                info = best_pair.get("info") or {}
                websites = info.get("websites") or []
                socials = info.get("socials") or []

                website_url = websites[0].get("url") if websites else None
                tg_url = next((s.get("url") for s in socials if s.get("type") == "telegram"), None)
                x_url = next((s.get("url") for s in socials if s.get("type") == "twitter"), None)

                return {
                    "dex_id": best_pair.get("dexId"),
                    "pair_address": best_pair.get("pairAddress"),
                    "market_cap": float(best_pair.get("marketCap") or best_pair.get("fdv") or 0.0),
                    "liquidity_usd": float(best_pair.get("liquidity", {}).get("usd", 0) or 0.0),
                    "website_url": website_url,
                    "tg_url": tg_url,
                    "x_url": x_url
                }
    except Exception as e:
        print(f"Dexscreener lookup failed for {ca}: {e}")
    return {}

def resolve_team_flagship_name(db: ForensicDatabase, candidate_team_name: str, fallback_symbol: str) -> str:
    """
    Directive 3: Name the team after the token with the highest ATH / Market Cap in its list.
    """
    if not candidate_team_name or candidate_team_name.lower() == "unclustered":
        return f"Team ${fallback_symbol}"

    tokens = db.get_team_tokens(candidate_team_name)
    if not tokens:
        return f"Team {candidate_team_name.replace('team ', '').upper()}"

    # Sort by ATH USD descending
    tokens_sorted = sorted(tokens, key=lambda t: float(t.get("ath_usd") or 0.0), reverse=True)
    highest_token = tokens_sorted[0]
    flagship_symbol = highest_token.get("symbol") or fallback_symbol
    peak_ath = float(highest_token.get("ath_usd") or 0.0)

    if peak_ath > 0:
        return f"Team ${flagship_symbol} (Peak: ${peak_ath:,.0f})"
    return f"Team ${flagship_symbol}"

async def run_test():
    db = ForensicDatabase()
    scraper = BrandingScraper()

    print("=" * 90)
    print(" [*] FORENSIC ENRICHMENT & TEAM IDENTIFICATION TEST (5 SAMPLE TOKENS)")
    print("=" * 90)

    for i, ca in enumerate(SAMPLE_CAS, 1):
        token_row = db.get_token(ca)
        if not token_row:
            print(f"[{i}/5] CA: {ca} not found in database. Skipping.")
            continue

        symbol = token_row.get("symbol") or "UNKNOWN"
        name = token_row.get("name") or symbol
        stored_ath = float(token_row.get("ath_usd") or 0.0)

        # 1. Fetch live Dexscreener
        dex_data = await fetch_dexscreener_profile(ca)
        live_mcap = dex_data.get("market_cap", 0.0)
        website = dex_data.get("website_url") or token_row.get("website")
        tg = dex_data.get("tg_url")
        x_acc = dex_data.get("x_url") or token_row.get("x_handle")

        # 2. Deep Branding Scraping
        web_info = await scraper.analyze_website(website)
        tg_info = scraper.analyze_telegram(tg)
        x_info = scraper.analyze_x(x_acc)

        # Persist Branding Profile
        db.upsert_branding_profile({
            "ca": ca,
            "website_url": website,
            "website_domain": web_info.get("website_domain"),
            "website_tld": web_info.get("website_tld"),
            "website_host_type": web_info.get("website_host_type", "NONE"),
            "favicon_hash": web_info.get("favicon_hash"),
            "website_title_hash": web_info.get("website_title_hash"),
            "tg_url": tg,
            "tg_handle": tg_info.get("tg_handle"),
            "tg_naming_pattern": tg_info.get("tg_naming_pattern", "NONE"),
            "x_handle": x_info.get("x_handle"),
            "x_naming_pattern": x_info.get("x_naming_pattern", "NONE")
        })

        # Update ATH if live market cap is higher
        if live_mcap > stored_ath:
            db.upsert_token({"ca": ca, "ath_usd": live_mcap})
            stored_ath = live_mcap

        # 3. On-chain Execution DNA
        funder = token_row.get("funder_1hop") or "N/A"
        funder_label = token_row.get("funder_label") or "Unknown"
        snipe_val = float(token_row.get("value_eth") or 0.0)
        is_005 = "YES" if (str(round(snipe_val, 4)).endswith("005") or str(round(snipe_val, 4)).endswith("05")) else "NO"
        template = (token_row.get("template_hash") or "")[:12] + "..." if token_row.get("template_hash") else "N/A"
        method = token_row.get("method_selector") or "N/A"

        # 4. Resolve Team Name by Highest Market Cap Token (Directive 3)
        raw_team = token_row.get("candidate_team") or "Unclustered"
        flagship_team_name = resolve_team_flagship_name(db, raw_team, symbol)

        # Print Forensic Card
        print(f"\n[{i}/5] {name} (${symbol})")
        print(f" - Contract Address   : {ca}")
        print(f" - Peak ATH Recorded  : ${stored_ath:,.2f}" + (f" | Live MCap: ${live_mcap:,.2f}" if live_mcap > 0 else ""))
        print(f" - Flagship Team Name : {flagship_team_name} (Raw: {raw_team})")
        print(f" - 1-Hop Funder       : {funder} ({funder_label})")
        print(f" - Launch Method      : {method} | Template Hash: {template}")
        print(f" - Dev Snipe Size     : {snipe_val} ETH (.0005 Pattern: {is_005})")
        print(f" - Website Analysis   : {web_info.get('website_domain') or 'None'} (Host: {web_info['website_host_type']} | Favicon: {web_info['favicon_hash'] or 'None'})")
        print(f" - Socials Pattern    : TG Pattern: {tg_info['tg_naming_pattern']} ({tg_info['tg_handle'] or 'None'}) | X Pattern: {x_info['x_naming_pattern']} ({x_info['x_handle'] or 'None'})")
        print("-" * 90)

    print("\n[SUCCESS] Test Run Completed Successfully.")

if __name__ == "__main__":
    asyncio.run(run_test())
