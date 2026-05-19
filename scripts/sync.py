#!/usr/bin/env python3
"""
nycevents — daily data sync.

Runs once a day (via GitHub Actions cron). Pulls from configured sources,
normalizes into a common event shape, deduplicates, writes web/data/events.json.
On completion, optionally sends an email digest.

Stdlib only — no pip dependencies.
"""

from __future__ import annotations
import calendar
import datetime as dt
import email.mime.text
import hashlib
import html as html_lib
import json
import os
import re
import smtplib
import sys
import traceback
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "data" / "events.json"
LOG = ROOT / "scripts" / "last_sync.log"
TENTPOLES = ROOT / "scripts" / "tentpole_annuals.json"
EXHIBITS  = ROOT / "scripts" / "museum_exhibits.json"

ET_OFFSET = "-04:00"   # EDT; switches to -05:00 in winter — refine if needed

# ---------- Common data shape ----------
@dataclass
class Event:
    id: str
    title: str
    category: str           # outabout | stagesound | mindeye
    subcategory: str
    tags: list[str]
    neighborhood: str
    borough: str            # manhattan | outer
    venue: str
    start: str              # ISO 8601 with offset
    end: str
    price: str              # free | $ | $$ | $$$
    tentpole: bool
    sources: list[dict]     # [{name, url}]
    description: str = ""

    @staticmethod
    def make_id(title: str, start: str, venue: str) -> str:
        key = f"{title.lower().strip()}|{start[:10]}|{venue.lower().strip()}"
        return "e-" + hashlib.sha1(key.encode()).hexdigest()[:10]


# ---------- HTTP ----------
UA = "Mozilla/5.0 (compatible; nycevents-sync/0.2; +github.com)"

def http_get(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


# ---------- Neighborhood/borough mapping ----------
# Neighborhoods we don't surface — events that resolve here get dropped.
DROP_HOODS = {"harlem", "morningside"}

# Manhattan community board → neighborhood id (matches web/data/neighborhoods.json)
# CB 9-12 (Morningside through Inwood) intentionally omitted — user excluded those.
MN_CB_HOOD = {
    "1": "fidi", "2": "westvillage", "3": "eastvillage", "4": "chelsea",
    "5": "midtownW", "6": "gramercy", "7": "uws", "8": "ues",
}
# Outer borough community board → neighborhood
BK_CB_HOOD = {
    "1": "williamsburg", "2": "dumbo", "3": "bushwick", "4": "bushwick",
    "6": "parkslope", "7": "redhook", "8": "crownhts", "9": "crownhts",
}
QN_CB_HOOD = { "1": "lic", "2": "lic", "7": "flushing", "8": "flushing" }

# Major NYC venue → neighborhood id. Used by Ticketmaster adapter & keyword fallback.
VENUE_HOOD = {
    # UWS
    "beacon theatre": "uws", "lincoln center": "uws", "david geffen hall": "uws",
    "alice tully hall": "uws", "metropolitan opera house": "uws", "symphony space": "uws",
    # UES
    "92ny": "ues", "92nd street y": "ues", "park avenue armory": "ues", "frick collection": "ues",
    "guggenheim": "ues", "metropolitan museum": "ues",
    # Midtown West
    "madison square garden": "midtownW", "msg": "midtownW", "radio city": "midtownW",
    "carnegie hall": "midtownW", "town hall": "midtownW", "javits center": "midtownW",
    "bryant park": "midtownW", "the shed": "midtownW", "hudson yards": "midtownW",
    # Midtown East
    "rockefeller": "midtownE", "ed sullivan theater": "midtownW",
    # Chelsea
    "chelsea": "chelsea", "joyce theater": "chelsea", "highline ballroom": "chelsea",
    # Gramercy / Flatiron / Union Sq
    "gramercy theatre": "gramercy", "irving plaza": "gramercy", "union square": "gramercy",
    "madison square park": "gramercy",
    # West Village
    "comedy cellar": "westvillage", "village vanguard": "westvillage", "blue note": "westvillage",
    "(le) poisson rouge": "westvillage", "the public theater": "eastvillage",
    "joe's pub": "eastvillage", "webster hall": "eastvillage",
    # SoHo / Tribeca
    "city winery": "soho", "soho house": "soho",
    # LES
    "bowery ballroom": "les", "mercury lounge": "les", "rockwood music hall": "les",
    # FiDi
    "the oculus": "fidi", "battery park": "fidi", "south street seaport": "fidi", "pier 17": "fidi",
    # Outer
    "barclays center": "dumbo", "bam": "dumbo", "brooklyn academy": "dumbo",
    "brooklyn steel": "bushwick", "house of yes": "bushwick", "elsewhere": "bushwick",
    "music hall of williamsburg": "williamsburg", "warsaw": "williamsburg",
    "pioneer works": "redhook",
    "kings theatre": "crownhts",
    "forest hills stadium": "flushing",
}

# Keyword fallback when CB not available
HOOD_KEYWORDS = [
    ("uws",          ["upper west", "lincoln center", "amsterdam ave", "columbus ave", "west 7", "west 8", "west 9"]),
    ("ues",          ["upper east", "lexington ave", "park ave", "madison ave", "east 7", "east 8", "east 9"]),
    ("midtownW",     ["times square", "hell's kitchen", "8th ave", "9th ave", "10th ave", "broadway"]),
    ("midtownE",     ["midtown east", "grand central", "rockefeller", "5th ave"]),
    ("chelsea",      ["chelsea", "high line", "meatpacking"]),
    ("gramercy",     ["gramercy", "flatiron", "union square", "murray hill"]),
    ("westvillage",  ["west village", "greenwich village", "bleecker", "christopher st"]),
    ("eastvillage",  ["east village", "tompkins", "alphabet city", "avenue a", "avenue b"]),
    ("soho",         ["soho", "tribeca", "nolita", "little italy"]),
    ("les",          ["lower east side", "delancey", "essex", "rivington", "orchard"]),
    ("fidi",         ["financial district", "battery park", "wall street", "world trade"]),
    ("williamsburg", ["williamsburg", "greenpoint", "bedford ave"]),
    ("bushwick",     ["bushwick", "ridgewood"]),
    ("dumbo",        ["dumbo", "brooklyn heights", "fort greene", "downtown brooklyn"]),
    ("parkslope",    ["park slope", "prospect heights"]),
    ("redhook",      ["red hook", "gowanus", "carroll gardens"]),
    ("crownhts",     ["crown heights", "bed-stuy", "bedford-stuy"]),
    ("lic",          ["long island city", "lic", "astoria", "sunnyside"]),
    ("flushing",     ["flushing", "jackson heights", "corona", "forest hills"]),
]

def map_hood(borough: str, location: str, community_board: str | None) -> tuple[str, str]:
    """Return (neighborhood_id, borough_id: 'manhattan' or 'outer')."""
    loc = (location or "").lower()
    b = (borough or "").lower()
    is_mn = "manhattan" in b
    boro_out = "manhattan" if is_mn else "outer"

    # Venue-name match (highest priority — most accurate)
    for venue_key, hood_id in VENUE_HOOD.items():
        if venue_key in loc:
            # Determine borough from the hood
            outer_hoods = {"williamsburg", "lic", "bushwick", "flushing", "dumbo",
                           "parkslope", "redhook", "crownhts"}
            return hood_id, ("outer" if hood_id in outer_hoods else "manhattan")

    # Community board lookup
    if community_board:
        cb = str(community_board).strip().lstrip("0")
        if is_mn and cb in MN_CB_HOOD: return MN_CB_HOOD[cb], boro_out
        if "brooklyn" in b and cb in BK_CB_HOOD: return BK_CB_HOOD[cb], boro_out
        if "queens" in b and cb in QN_CB_HOOD: return QN_CB_HOOD[cb], boro_out

    # Keyword fallback
    for hood_id, kws in HOOD_KEYWORDS:
        if any(kw in loc for kw in kws):
            return hood_id, boro_out

    # Defaults by borough
    if is_mn: return "midtownE", "manhattan"
    if "brooklyn" in b: return "williamsburg", "outer"
    if "queens"   in b: return "lic", "outer"
    return "midtownE", "manhattan"


# ---------- Categorizer & filter ----------

# Event names that are admin permits, not public events.
EXCLUDE_NAME_PATTERNS = re.compile(
    r"\b(soccer|baseball|softball|football|tennis|handball|basketball|lacrosse|"
    r"volleyball|cricket|rugby|kickball|bocce|pickleball|practice|"
    r"sport|league|tournament|scrimmage|miscellaneous|after.?school|aftercare|"
    r"outdoor\s+learning|field\s+experience|tabling|shuttle|cruise|parking|"
    r"lawn\s+closure|lawn\s+maintenance|production|model\s+aircraft|"
    r"loading|load\s*in|load\s*out|"
    r"sss\s+cp[ew]|"            # school program codes
    r"^\d+-?lv\s)",             # "2-LV", "3-LV" hotel valet permits
    re.I,
)
EXCLUDE_TYPE_PATTERNS = re.compile(
    r"\b(sport|youth\s+sport|adult\s+sport|production\s+event|"
    r"theater\s+load|press\s+conference|filming)\b",
    re.I,
)

# Strong INCLUDE signals — event_type and event_name patterns that indicate
# a clearly public event worth surfacing.
INCLUDE_TYPE_PATTERNS = re.compile(
    r"\b(street\s+activity|plaza\s+event|plaza\s+partner|"
    r"single\s+block|multi\s+block|farmers\s*market|"
    r"festival|parade|run|walk|race|concert|"
    r"open\s+street\s+partner)\b",
    re.I,
)
INCLUDE_NAME_PATTERNS = re.compile(
    r"\b(parade|festival|fair|market|run|walk|race|"
    r"concert|series|fest|block\s+party|open\s+street|"
    r"performance|exhibit|theater\s+co|theatre\s+co)\b",
    re.I,
)

def is_public_event(name: str, event_type: str) -> bool:
    """Return True only for genuinely public/curated events.
    Requires BOTH: doesn't match exclusion patterns, AND matches an inclusion signal.
    """
    if not name: return False
    if EXCLUDE_TYPE_PATTERNS.search(event_type or ""):
        return False
    if EXCLUDE_NAME_PATTERNS.search(name):
        return False
    if INCLUDE_TYPE_PATTERNS.search(event_type or ""):
        return True
    if INCLUDE_NAME_PATTERNS.search(name):
        return True
    return False

def classify_open_data(event_type: str, event_agency: str, name: str) -> tuple[str, str, list[str]]:
    """Map NYC Open Data event_type → (category, subcategory, tags)."""
    et = (event_type or "").lower()
    nm = (name or "").lower()
    if "parade" in et or "parade" in nm:
        return "outabout", "parades", ["free", "outdoor", "parade"]
    if "street fair" in et or "street activity" in et or "block party" in et:
        return "outabout", "street-fairs", ["free", "outdoor"]
    if "farmers market" in et or "farmers market" in nm:
        return "outabout", "street-fairs", ["free", "outdoor", "food"]
    if "festival" in et or "festival" in nm:
        return "outabout", "festival", ["festival"]
    if "concert" in et or "music" in et or "concert" in nm:
        return "stagesound", "concerts", ["music"]
    if "film" in et or "film" in nm:
        return "outabout", "film-festival", ["film"]
    if "race" in nm or "run" in nm or "marathon" in nm or "bike" in nm:
        return "outabout", "festival", ["outdoor"]
    if "plaza" in et:
        return "outabout", "street-fairs", ["free", "outdoor"]
    # default: outdoor permitted event
    return "outabout", "street-fairs", ["free", "outdoor"]


# ---------- Adapters ----------

def fetch_nyc_permits() -> Iterable[Event]:
    """NYC Permitted Event Information — public, no auth, structured JSON."""
    today = dt.date.today().isoformat()
    end_window = (dt.date.today() + dt.timedelta(days=240)).isoformat()
    # SODA query: events from today through ~8 months out, up to 1000 rows
    where = f"start_date_time >= '{today}T00:00:00' AND start_date_time <= '{end_window}T23:59:59'"
    url = (
        "https://data.cityofnewyork.us/resource/tvpp-9vvx.json"
        f"?$where={urllib.parse.quote(where)}"
        "&$order=start_date_time"
        "&$limit=5000"
    )
    body = http_get(url)
    rows = json.loads(body)
    for r in rows:
        name = (r.get("event_name") or "").strip()
        event_type = (r.get("event_type") or "").strip()
        if not is_public_event(name, event_type):
            continue
        start = r.get("start_date_time")
        end   = r.get("end_date_time") or start
        if not start: continue
        # Open Data returns naive ET datetimes — append offset
        if "T" in start and not (start.endswith("Z") or "+" in start[10:] or "-" in start[10:]):
            start = start + ET_OFFSET
        if end and "T" in end and not (end.endswith("Z") or "+" in end[10:] or "-" in end[10:]):
            end = end + ET_OFFSET
        loc = r.get("event_location") or r.get("event_street_side") or ""
        borough_raw = r.get("event_borough") or ""
        # community_board can be like "04," with trailing commas/multiple values
        cb_raw = r.get("community_board") or ""
        cb = re.findall(r"\d+", str(cb_raw))
        cb = cb[0] if cb else None
        hood, boro = map_hood(borough_raw, loc, cb)
        if hood in DROP_HOODS:
            continue
        category, sub, tags = classify_open_data(
            r.get("event_type"), r.get("event_agency"), name
        )
        venue = loc or (r.get("event_agency") or "Citywide")
        # Build a 2-sentence summary from the structured fields.
        agency = (r.get("event_agency") or "NYC").strip()
        et_label = (r.get("event_type") or "permitted event").strip()
        # Friendly time string
        try:
            s_dt = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
            e_dt = dt.datetime.fromisoformat(end.replace("Z", "+00:00"))
            same_day = s_dt.date() == e_dt.date()
            day_str = s_dt.strftime("%A, %b %-d")
            t_str = f"{s_dt.strftime('%-I:%M %p')}–{e_dt.strftime('%-I:%M %p')}"
            when_str = f"{day_str}, {t_str}" if same_day else f"{s_dt.strftime('%b %-d')} – {e_dt.strftime('%b %-d')}"
        except Exception:
            when_str = ""
        sent1 = f"A {et_label.lower()} hosted by {agency}."
        sent2 = f"Takes place {when_str} at {loc}." if when_str else f"Location: {loc}."
        description = f"{sent1} {sent2}".strip()

        yield Event(
            id=Event.make_id(name, start, venue),
            title=name,
            category=category, subcategory=sub, tags=tags,
            neighborhood=hood, borough=boro,
            venue=venue,
            start=start, end=end,
            price="free", tentpole=False,
            sources=[{
                "name": "NYC Open Data",
                "url": f"https://data.cityofnewyork.us/d/tvpp-9vvx"
            }],
            description=description,
        )


def _nth_weekday(year: int, month: int, weekday: int, ordinal) -> dt.date:
    """Return the date of the Nth weekday of month. ordinal can be 1..5 or 'last'."""
    cal = calendar.Calendar()
    days = [d for d in cal.itermonthdates(year, month) if d.month == month and d.weekday() == weekday]
    if ordinal == "last":
        return days[-1]
    return days[int(ordinal) - 1]


def fetch_tentpoles() -> Iterable[Event]:
    """Hand-curated annuals — emit for current year and next year."""
    data = json.loads(TENTPOLES.read_text())
    this_year = dt.date.today().year
    for year in (this_year, this_year + 1):
        for a in data["annuals"]:
            rule = a["date_rule"]
            try:
                if "weekday" in rule:
                    d = _nth_weekday(year, rule["month"], rule["weekday"], rule["ordinal"])
                else:
                    d = dt.date(year, rule["month"], rule["day"])
            except Exception:
                continue
            t_start = rule.get("time", a.get("time", {})).get("start") or a.get("time", {}).get("start", "10:00")
            t_end   = rule.get("time", a.get("time", {})).get("end")   or a.get("time", {}).get("end",   "18:00")
            duration = a.get("duration_days", 0)
            end_date = d + dt.timedelta(days=duration)
            start_iso = f"{d.isoformat()}T{t_start}:00{ET_OFFSET}"
            end_iso   = f"{end_date.isoformat()}T{t_end}:00{ET_OFFSET}"
            yield Event(
                id=Event.make_id(a["title"], start_iso, a["venue"]),
                title=a["title"],
                category=a["category"], subcategory=a["subcategory"],
                tags=list(a.get("tags", [])),
                neighborhood=a["neighborhood"], borough=a["borough"],
                venue=a["venue"],
                start=start_iso, end=end_iso,
                price=a["price"], tentpole=bool(a.get("tentpole", True)),
                sources=list(a.get("sources", [])),
                description=a.get("description", ""),
            )


def fetch_museum_exhibits() -> Iterable[Event]:
    """Hand-curated currently-running museum special exhibits."""
    data = json.loads(EXHIBITS.read_text())
    today = dt.date.today()
    for ex in data["exhibits"]:
        try:
            sd = dt.date.fromisoformat(ex["start_date"])
            ed = dt.date.fromisoformat(ex["end_date"])
        except Exception:
            continue
        if ed < today:           # exhibit already closed
            continue
        start_iso = f"{sd.isoformat()}T10:00:00{ET_OFFSET}"
        end_iso   = f"{ed.isoformat()}T17:00:00{ET_OFFSET}"
        venue = ex["museum"]
        yield Event(
            id=Event.make_id(ex["title"], start_iso, venue),
            title=ex["title"],
            category="mindeye", subcategory="museum-art",
            tags=list(ex.get("tags", ["m-art"])),
            neighborhood=ex["neighborhood"],
            borough="manhattan" if ex["neighborhood"] not in {"williamsburg","lic","bushwick","flushing","dumbo","parkslope","redhook","crownhts"} else "outer",
            venue=venue,
            start=start_iso, end=end_iso,
            price=ex.get("price", "$$"),
            tentpole=False,
            sources=[{"name": venue, "url": ex.get("url", "")}],
            description=ex.get("description", ""),
        )


# RSS adapters: stored as "leads" — short cards linking out, because
# news/aggregator RSS items don't always carry structured event dates.
def _rss_items(url: str) -> list[dict]:
    try:
        body = http_get(url)
    except Exception:
        return []
    out = []
    try:
        root = ET.fromstring(body)
        # RSS 2.0
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link  = (it.findtext("link") or "").strip()
            pub   = (it.findtext("pubDate") or "").strip()
            desc  = (it.findtext("description") or "").strip()
            out.append({"title": title, "link": link, "pub": pub, "desc": desc})
    except ET.ParseError:
        pass
    return out


def _looks_event(title: str) -> bool:
    t = title.lower()
    keywords = [
        "tonight", "this week", "weekend", "today in", "things to do",
        "free", "concert", "festival", "parade", "fair", "exhibit",
        "opening", "premiere", "screening", "talk", "reading",
    ]
    return any(k in t for k in keywords)


def fetch_timeout_rss() -> Iterable[Event]:
    """Time Out NY RSS — emit items that look like event roundups, with a 7-day display window."""
    for it in _rss_items("https://www.timeout.com/newyork/feed.rss"):
        if not _looks_event(it["title"]): continue
        title = html_lib.unescape(it["title"])
        try:
            pub = dt.datetime.strptime(it["pub"], "%a, %d %b %Y %H:%M:%S %z")
        except Exception:
            pub = dt.datetime.now(dt.timezone.utc)
        start_iso = pub.isoformat()
        end_iso = (pub + dt.timedelta(days=7)).isoformat()
        yield Event(
            id=Event.make_id(title, start_iso, "Time Out"),
            title=title,
            category="outabout", subcategory="festival",
            tags=["roundup"],
            neighborhood="midtownE", borough="manhattan",
            venue="See article",
            start=start_iso, end=end_iso,
            price="free", tentpole=False,
            sources=[{"name": "Time Out NY", "url": it["link"]}],
            description=html_lib.unescape(re.sub(r"<[^>]+>", " ", it["desc"]))[:240],
        )


def fetch_skint_rss() -> Iterable[Event]:
    """The Skint RSS — daily NYC roundups of free/cheap things."""
    for it in _rss_items("https://theskint.com/feed/"):
        title = html_lib.unescape(it["title"])
        try:
            pub = dt.datetime.strptime(it["pub"], "%a, %d %b %Y %H:%M:%S %z")
        except Exception:
            pub = dt.datetime.now(dt.timezone.utc)
        start_iso = pub.isoformat()
        end_iso = (pub + dt.timedelta(days=1)).isoformat()
        yield Event(
            id=Event.make_id(title, start_iso, "Skint"),
            title=title,
            category="outabout", subcategory="street-fairs",
            tags=["roundup", "free"],
            neighborhood="midtownE", borough="manhattan",
            venue="See article",
            start=start_iso, end=end_iso,
            price="free", tentpole=False,
            sources=[{"name": "The Skint", "url": it["link"]}],
            description=html_lib.unescape(re.sub(r"<[^>]+>", " ", it["desc"]))[:240],
        )


def fetch_gothamist_rss() -> Iterable[Event]:
    """Gothamist RSS — general NYC news; filter for event-shaped posts."""
    for it in _rss_items("https://gothamist.com/feed"):
        if not _looks_event(it["title"]): continue
        title = html_lib.unescape(it["title"])
        try:
            pub = dt.datetime.strptime(it["pub"], "%a, %d %b %Y %H:%M:%S %z")
        except Exception:
            pub = dt.datetime.now(dt.timezone.utc)
        start_iso = pub.isoformat()
        end_iso = (pub + dt.timedelta(days=7)).isoformat()
        yield Event(
            id=Event.make_id(title, start_iso, "Gothamist"),
            title=title,
            category="outabout", subcategory="festival",
            tags=["roundup"],
            neighborhood="midtownE", borough="manhattan",
            venue="See article",
            start=start_iso, end=end_iso,
            price="free", tentpole=False,
            sources=[{"name": "Gothamist", "url": it["link"]}],
            description=html_lib.unescape(re.sub(r"<[^>]+>", " ", it["desc"]))[:240],
        )


def _tm_price_bucket(prng: dict | None) -> str:
    if not prng: return "$$"
    try:
        avg = (float(prng.get("min", 0)) + float(prng.get("max", 0))) / 2
    except Exception:
        return "$$"
    if avg <= 25: return "$"
    if avg <= 75: return "$$"
    return "$$$"

def _tm_classify(classifications: list) -> tuple[str, str, list[str]]:
    """Map Ticketmaster classifications to (category, subcategory, tags)."""
    if not classifications: return "stagesound", "concerts", ["music"]
    c = classifications[0] or {}
    seg = ((c.get("segment") or {}).get("name") or "").lower()
    genre = ((c.get("genre") or {}).get("name") or "").lower()
    if seg == "music":
        tag_map = {
            "rock": "g-indie", "alternative": "g-indie", "indie": "g-indie",
            "jazz": "g-jazz", "classical": "g-classical",
            "hip-hop": "g-hiphop", "rap": "g-hiphop", "r&b": "g-hiphop",
            "electronic": "g-electronic", "dance": "g-electronic",
            "folk": "g-folk", "world": "g-world",
        }
        tag = next((t for k, t in tag_map.items() if k in genre), "music")
        return "stagesound", "concerts", [tag]
    if seg == "arts & theatre":
        if "comedy" in genre: return "stagesound", "comedy-clubs", ["comedy"]
        return "stagesound", "concerts", ["theater"]
    if seg == "comedy" or "comedy" in genre:
        return "stagesound", "comedy-theater", ["comedy"]
    return "stagesound", "concerts", ["music"]


def fetch_ticketmaster() -> Iterable[Event]:
    """Ticketmaster Discovery API — covers Beacon, MSG, Radio City, Carnegie, etc.

    Requires TM_API_KEY env var (free signup at developer.ticketmaster.com).
    No-op if key not set.
    """
    key = os.environ.get("TM_API_KEY")
    if not key:
        return

    today = dt.date.today()
    end_window = today + dt.timedelta(days=180)
    base = "https://app.ticketmaster.com/discovery/v2/events.json"

    # Paginate up to 5 pages of 200 (TM hard caps at ~1000 results per query)
    for page in range(5):
        params = {
            "apikey": key,
            "city": "New York",
            "stateCode": "NY",
            "countryCode": "US",
            "classificationName": "music,arts & theatre,comedy",
            "startDateTime": f"{today.isoformat()}T00:00:00Z",
            "endDateTime":   f"{end_window.isoformat()}T23:59:59Z",
            "size": 200,
            "page": page,
            "sort": "date,asc",
        }
        url = base + "?" + urllib.parse.urlencode(params)
        try:
            body = http_get(url, timeout=30)
        except urllib.error.HTTPError as e:
            print(f"  ticketmaster page {page} failed: {e}", file=sys.stderr)
            break
        data = json.loads(body)
        events_arr = (data.get("_embedded") or {}).get("events") or []
        if not events_arr:
            break

        for r in events_arr:
            name = (r.get("name") or "").strip()
            if not name: continue
            dates = (r.get("dates") or {}).get("start") or {}
            start_iso = dates.get("dateTime") or (dates.get("localDate") + "T20:00:00Z" if dates.get("localDate") else None)
            if not start_iso: continue
            # End: ~3h after start (TM rarely provides end time)
            try:
                end_dt = dt.datetime.fromisoformat(start_iso.replace("Z", "+00:00")) + dt.timedelta(hours=3)
                end_iso = end_dt.isoformat()
            except Exception:
                end_iso = start_iso

            venues = ((r.get("_embedded") or {}).get("venues") or [])
            v = venues[0] if venues else {}
            venue_name = (v.get("name") or "Unknown venue").strip()
            v_city = ((v.get("city") or {}).get("name") or "").lower()
            # Skip events that aren't actually in NYC (TM "New York" returns surrounding metro)
            if "new york" not in v_city and "brooklyn" not in v_city and "queens" not in v_city:
                continue
            v_state = ((v.get("state") or {}).get("stateCode") or "").upper()
            if v_state and v_state != "NY":
                continue

            hood, boro = map_hood("Manhattan", venue_name, None)
            if hood in DROP_HOODS:
                continue

            category, sub, tags = _tm_classify(r.get("classifications") or [])
            prng_list = r.get("priceRanges") or []
            price = _tm_price_bucket(prng_list[0] if prng_list else None)

            # 2-sentence description
            try:
                s_dt = dt.datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
                when_str = s_dt.astimezone(dt.timezone(dt.timedelta(hours=-4))).strftime("%A, %b %-d at %-I:%M %p")
            except Exception:
                when_str = ""
            sent1 = f"Live show at {venue_name}" + (f" on {when_str}." if when_str else ".")
            if prng_list:
                p = prng_list[0]
                pmin = p.get("min"); pmax = p.get("max")
                sent2 = f"Tickets via Ticketmaster, from ${pmin:.0f}–${pmax:.0f}." if pmin is not None and pmax is not None else "Tickets via Ticketmaster."
            else:
                sent2 = "Tickets via Ticketmaster."

            yield Event(
                id=Event.make_id(name, start_iso, venue_name),
                title=name,
                category=category, subcategory=sub, tags=tags,
                neighborhood=hood, borough=boro,
                venue=venue_name,
                start=start_iso, end=end_iso,
                price=price, tentpole=False,
                sources=[{"name": "Ticketmaster", "url": r.get("url", "https://www.ticketmaster.com")}],
                description=f"{sent1} {sent2}".strip(),
            )

        # Stop if last page
        pageinfo = data.get("page") or {}
        if pageinfo.get("number", page) + 1 >= pageinfo.get("totalPages", 1):
            break


# Register adapters in run order.
# News RSS adapters (timeout / skint / gothamist) intentionally disabled —
# they return articles, not scheduled events. Functions kept for reference.
ADAPTERS: list[tuple[str, Callable[[], Iterable[Event]]]] = [
    ("tentpoles",        fetch_tentpoles),
    ("museum_exhibits",  fetch_museum_exhibits),
    ("nyc_permits",      fetch_nyc_permits),
    ("ticketmaster",     fetch_ticketmaster),
]


# ---------- Dedup ----------
def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()

def dedupe(events: list[Event]) -> list[Event]:
    bucket: dict[tuple, Event] = {}
    for ev in events:
        key = (_norm_title(ev.title), ev.start[:10], _norm_title(ev.venue))
        if key in bucket:
            existing = bucket[key]
            seen = {s["url"] for s in existing.sources}
            for s in ev.sources:
                if s["url"] not in seen:
                    existing.sources.append(s); seen.add(s["url"])
            if len(ev.description) > len(existing.description):
                existing.description = ev.description
        else:
            bucket[key] = ev
    return list(bucket.values())


# ---------- Email digest ----------
def send_digest(events: list[Event], failures: list[tuple[str, str]]) -> None:
    """Send a morning briefing + (if failures) a failure alert.

    Configuration via env vars (set as GitHub Actions secrets):
      SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_TO
    All optional — if SMTP_HOST is unset, this is a no-op.
    """
    host = os.environ.get("SMTP_HOST")
    if not host: return
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    pw   = os.environ.get("SMTP_PASS", "")
    to   = os.environ.get("EMAIL_TO", user)
    if not (user and pw and to): return

    today = dt.date.today()
    # Top picks: anything starting in next 36h
    soon = sorted(
        [e for e in events if e.start[:10] in (today.isoformat(),
                                               (today + dt.timedelta(days=1)).isoformat())],
        key=lambda e: e.start
    )[:8]

    lines = [f"NYC Events digest — {today.isoformat()}", ""]
    if soon:
        lines.append("Top picks for the next 36 hours:")
        for e in soon:
            lines.append(f"  • {e.title} — {e.start[11:16]} · {e.venue}")
    else:
        lines.append("(No events flagged in the next 36 hours.)")
    lines.append("")
    lines.append(f"Total events in DB: {len(events)}")
    if failures:
        lines.append("")
        lines.append("Adapter failures:")
        for n, err in failures:
            lines.append(f"  - {n}: {err}")
    body = "\n".join(lines)

    msg = email.mime.text.MIMEText(body)
    msg["Subject"] = f"NYC Events — {today.isoformat()}" + (" — adapter failures" if failures else "")
    msg["From"] = user
    msg["To"]   = to
    try:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            s.login(user, pw)
            s.sendmail(user, [to], msg.as_string())
    except Exception as e:
        # Don't crash the run on email failure — log to stderr
        print(f"email send failed: {e}", file=sys.stderr)


# ---------- Driver ----------
def run() -> int:
    all_events: list[Event] = []
    failures: list[tuple[str, str]] = []

    for name, fn in ADAPTERS:
        try:
            count = 0
            for ev in fn():
                all_events.append(ev); count += 1
            print(f"  {name}: {count} events")
        except Exception as e:
            failures.append((name, f"{type(e).__name__}: {e}"))
            traceback.print_exc()

    merged = dedupe(all_events)

    payload = {
        "generated_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=-4))).isoformat(),
        "events": [asdict(e) for e in merged],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2))

    summary = (
        f"sync complete @ {payload['generated_at']}\n"
        f"  events written: {len(merged)}\n"
        f"  adapters ok:    {len(ADAPTERS) - len(failures)}\n"
        f"  adapters fail:  {len(failures)}\n"
    )
    for n, err in failures:
        summary += f"    - {n}: {err}\n"
    LOG.write_text(summary)
    print(summary)

    # Email digest (and failure alert)
    send_digest(merged, failures)

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(run())
