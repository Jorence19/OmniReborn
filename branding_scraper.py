import re
import hashlib
import httpx
from typing import Dict, Any, Optional
from urllib.parse import urlparse, urljoin

class BrandingScraper:
    def __init__(self, timeout: float = 6.0):
        self.timeout = timeout
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }

    def analyze_telegram(self, tg_url_or_handle: Optional[str]) -> Dict[str, Any]:
        if not tg_url_or_handle:
            return {"tg_handle": None, "tg_naming_pattern": "NONE"}
        
        raw = str(tg_url_or_handle).strip()
        # Extract handle from https://t.me/handle or @handle
        handle = raw.replace("https://t.me/", "").replace("http://t.me/", "").replace("t.me/", "").replace("@", "").split("/")[0].strip()
        if not handle:
            return {"tg_handle": None, "tg_naming_pattern": "NONE"}

        handle_lower = handle.lower()
        if handle_lower.endswith("_portal") or handle_lower.endswith("portal"):
            pattern = "PORTAL_SUFFIX"
        elif handle_lower.endswith("_rbh") or handle_lower.endswith("rbh"):
            pattern = "RBH_SUFFIX"
        elif handle_lower.endswith("_erc") or handle_lower.endswith("erc"):
            pattern = "ERC_SUFFIX"
        elif "official" in handle_lower:
            pattern = "OFFICIAL_SUFFIX"
        elif handle_lower.endswith("_coin") or handle_lower.endswith("coin"):
            pattern = "COIN_SUFFIX"
        elif handle_lower.endswith("_community") or handle_lower.endswith("community"):
            pattern = "COMMUNITY_SUFFIX"
        else:
            pattern = "CUSTOM_NAME"

        return {
            "tg_handle": handle,
            "tg_naming_pattern": pattern
        }

    def analyze_x(self, x_url_or_handle: Optional[str]) -> Dict[str, Any]:
        if not x_url_or_handle:
            return {"x_handle": None, "x_naming_pattern": "NONE"}
        
        raw = str(x_url_or_handle).strip()
        handle = raw.replace("https://x.com/", "").replace("https://twitter.com/", "").replace("@", "").split("/")[0].split("?")[0].strip()
        if not handle:
            return {"x_handle": None, "x_naming_pattern": "NONE"}

        h_lower = handle.lower()
        if h_lower.endswith("app") or h_lower.endswith("_app"):
            pattern = "APP_SUFFIX"
        elif h_lower.endswith("coin") or h_lower.endswith("_coin"):
            pattern = "COIN_SUFFIX"
        elif h_lower.endswith("token") or h_lower.endswith("_token"):
            pattern = "TOKEN_SUFFIX"
        elif h_lower.endswith("rbh") or h_lower.endswith("_rbh"):
            pattern = "RBH_SUFFIX"
        elif "official" in h_lower:
            pattern = "OFFICIAL_SUFFIX"
        else:
            pattern = "CUSTOM_HANDLE"

        return {
            "x_handle": handle,
            "x_naming_pattern": pattern
        }

    async def analyze_website(self, url: Optional[str]) -> Dict[str, Any]:
        if not url or not str(url).startswith("http"):
            return {
                "website_url": url,
                "website_domain": None,
                "website_tld": None,
                "website_host_type": "NONE",
                "favicon_hash": None,
                "website_title_hash": None
            }

        url_str = str(url).strip()
        parsed = urlparse(url_str)
        domain = parsed.netloc.lower()
        tld = domain.split(".")[-1] if "." in domain else None

        # Base structure
        result = {
            "website_url": url_str,
            "website_domain": domain,
            "website_tld": f".{tld}" if tld else None,
            "website_host_type": "CUSTOM_OR_UNKNOWN",
            "favicon_hash": None,
            "website_title_hash": None
        }

        # Check domain based host signature
        if "vercel.app" in domain:
            result["website_host_type"] = "VERCEL"
        elif "carrd.co" in domain:
            result["website_host_type"] = "CARRD"
        elif "netlify.app" in domain:
            result["website_host_type"] = "NETLIFY"
        elif "github.io" in domain:
            result["website_host_type"] = "GITHUB_PAGES"

        try:
            async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout, follow_redirects=True) as client:
                resp = await client.get(url_str)
                if resp.status_code == 200:
                    headers_lower = {k.lower(): v.lower() for k, v in resp.headers.items()}
                    
                    # Detect host from HTTP response headers
                    if "x-vercel-id" in headers_lower:
                        result["website_host_type"] = "VERCEL"
                    elif "x-carrd-site" in headers_lower or "carrd" in headers_lower.get("server", ""):
                        result["website_host_type"] = "CARRD"
                    elif "x-nf-request-id" in headers_lower or "netlify" in headers_lower.get("server", ""):
                        result["website_host_type"] = "NETLIFY"
                    elif "cf-ray" in headers_lower:
                        if result["website_host_type"] == "CUSTOM_OR_UNKNOWN":
                            result["website_host_type"] = "CLOUDFLARE_PROXY"

                    html_text = resp.text

                    # Extract Title & Meta
                    title_match = re.search(r"<title>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
                    title = title_match.group(1).strip() if title_match else ""
                    if title:
                        result["website_title_hash"] = hashlib.sha256(title.encode("utf-8")).hexdigest()[:16]

                    # Extract Favicon
                    icon_match = re.search(r'<link[^>]*rel=[\'"][^\'"]*icon[^\'"]*[\'"][^>]*href=[\'"]([^\'"]+)[\'"]', html_text, re.IGNORECASE)
                    favicon_url = None
                    if icon_match:
                        favicon_url = urljoin(url_str, icon_match.group(1).strip())
                    else:
                        favicon_url = urljoin(url_str, "/favicon.ico")

                    # Fetch favicon and compute hash
                    try:
                        fav_resp = await client.get(favicon_url)
                        if fav_resp.status_code == 200 and len(fav_resp.content) > 0:
                            # Use SHA256 hex prefix as standard robust hash
                            result["favicon_hash"] = hashlib.sha256(fav_resp.content).hexdigest()[:16]
                    except Exception:
                        pass
        except Exception:
            pass

        return result
