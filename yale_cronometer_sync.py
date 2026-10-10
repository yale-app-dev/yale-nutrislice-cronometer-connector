#!/usr/bin/env python3
"""Sync Yale Hospitality (Nutrislice) menu items into Cronometer as custom foods.

Each dish becomes a Cronometer custom food named "<prefix><dish name>" with two
servings: "oz" (default, so you can log by weight) and Yale's posted portion
("serving (4.42 oz)"). Already-synced dishes are tracked in a state file so
repeated runs only create new or changed foods.

Cronometer has no official API; this talks to the same JSON API the Cronometer
mobile app uses (mobile.cronometer.com/api/v2), the approach used by
github.com/rwestergren/cronometer-api-mcp. It may break if Cronometer changes it.

Stdlib only (Python 3.9+).
"""

import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
NUTRISLICE_API = "https://yalehospitality.api.nutrislice.com/menu/api"
CRONOMETER_API = "https://mobile.cronometer.com"
OZ_GRAMS = 28.3495231
FL_OZ_GRAMS = 29.5735  # assumes water-like density

log = logging.getLogger("yale-cronometer")

# Nutrislice rounded_nutrition_info field -> Cronometer (USDA) nutrient id.
# Values from Nutrislice are per Yale serving.
NUTRIENT_MAP = {
    "calories": 208,
    "g_protein": 203,
    "g_fat": 204,
    "g_carbs": 205,
    "g_fiber": 291,
    "g_sugar": 269,
    "mg_sodium": 307,
    "g_saturated_fat": 606,
    "g_trans_fat": 605,
    "mg_cholesterol": 601,
    "mg_potassium": 306,
    "mg_calcium": 301,
    "mg_iron": 303,
    "mg_vitamin_c": 401,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def http_json(url, payload=None, headers=None, timeout=30):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def load_env(path):
    """Minimal .env loader: KEY=value lines; real env vars win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Nutrislice
# ---------------------------------------------------------------------------

def list_menus(halls):
    """Return [(school_slug, menu_type_slug)] for the configured halls."""
    schools = http_json(f"{NUTRISLICE_API}/schools/?format=json")
    wanted = None if halls in ("all", ["all"]) else set(halls)
    menus = []
    for school in schools:
        if wanted is not None and school["slug"] not in wanted:
            continue
        for menu_type in school.get("active_menu_types", []):
            menus.append((school["slug"], menu_type["slug"]))
    if wanted:
        known = {s["slug"] for s in schools}
        for slug in sorted(wanted - known):
            log.warning("Unknown hall %r. Run with --list-halls to see valid ones.", slug)
    return menus


def fetch_foods(menus, dates):
    """Return Nutrislice food dicts served at the given menus on the given dates."""
    weeks = {}
    for d in dates:
        # Nutrislice weeks start on Sunday.
        weeks.setdefault(d - dt.timedelta(days=(d.weekday() + 1) % 7), set()).add(d.isoformat())

    def fetch(job):
        (school, menu_type), (week_start, day_strs) = job
        url = (f"{NUTRISLICE_API}/weeks/school/{school}/menu-type/{menu_type}/"
               f"{week_start:%Y/%m/%d}/?format=json")
        try:
            week = http_json(url)
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
            log.warning("Could not fetch %s/%s: %s", school, menu_type, exc)
            return []
        return [item["food"] for day in week.get("days", []) if day.get("date") in day_strs
                for item in day.get("menu_items", []) if item.get("food")]

    jobs = [(m, w) for m in menus for w in weeks.items()]
    with ThreadPoolExecutor(max_workers=8) as pool:
        return [food for foods in pool.map(fetch, jobs) for food in foods]


def serving_grams(food):
    info = food.get("serving_size_info") or {}
    try:
        amount = float(info.get("serving_size_amount"))
    except (TypeError, ValueError):
        return None
    unit = (info.get("serving_size_unit") or "").strip().lower()
    factor = {"oz": OZ_GRAMS, "ounce": OZ_GRAMS, "ounces": OZ_GRAMS,
              "g": 1.0, "gram": 1.0, "grams": 1.0,
              "fl oz": FL_OZ_GRAMS, "floz": FL_OZ_GRAMS}.get(unit)
    if factor is None or amount <= 0:
        return None
    return amount * factor


def serving_label(food):
    info = food["serving_size_info"]
    amount = float(info["serving_size_amount"])
    return f"Yale serving ({amount:g} {info['serving_size_unit'].strip()})"


def build_food(food, prefix):
    """Translate a Nutrislice food into a Cronometer add_food `data` object.

    Returns None if the food lacks nutrition or a weight-based serving size.
    """
    if not food.get("has_nutrition_info"):
        return None
    grams = serving_grams(food)
    if not grams:
        return None
    info = food.get("rounded_nutrition_info") or {}
    scale = 100.0 / grams  # Cronometer stores nutrients per 100 g

    per100 = {}
    for field, nid in NUTRIENT_MAP.items():
        value = info.get(field)
        if value is not None:
            per100[nid] = round(float(value) * scale, 3)
    if 208 not in per100:
        return None

    # Derived fields the Cronometer app sends alongside the macros.
    for src, mirror in ((203, -203), (204, -204), (205, -205)):
        per100[mirror] = per100.get(src, 0.0)
    per100[-221] = 0.0  # alcohol
    per100[-1205] = round(max(0.0, per100.get(205, 0.0) - per100.get(291, 0.0)), 3)

    return {
        "id": 0,
        "name": f"{prefix}{food['name'].strip()}",
        "category": 0,
        "owner": None,
        "retired": None,
        "source": None,
        "defaultMeasureId": 0,
        "comments": f"Imported from Yale Nutrislice (food id {food['id']}).",
        "alternateId": None,
        "measures": [
            {"id": 0, "name": "oz", "value": OZ_GRAMS, "amount": 1.0, "type": "Weight"},
            {"id": 0, "name": serving_label(food), "value": round(grams, 2),
             "amount": 1.0, "type": "Atomic"},
        ],
        "labelType": "AMERICAN_2016",
        "nutrients": [{"id": nid, "amount": amt} for nid, amt in sorted(per100.items())],
        "properties": {},
        "foodTags": [],
    }


def fingerprint(data):
    """Hash of the parts of a food that matter for change detection."""
    key = {"name": data["name"], "nutrients": data["nutrients"],
           "measures": [(m["name"], m["value"]) for m in data["measures"]]}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Cronometer (unofficial mobile API)
# ---------------------------------------------------------------------------

class CronometerError(Exception):
    pass


class Cronometer:
    HEADERS = {"user-agent": "Dart/3.9 (dart:io)",
               "content-type": "text/plain; charset=utf-8"}
    APP_AUTH = {"api": 3, "os": "Android", "build": "2807", "flavour": "free"}

    def __init__(self, email, password, session_path):
        self.email = email
        self.password = password
        self.session_path = session_path
        cached = read_json(session_path, {})
        if cached.get("email") == email:
            self.user_id, self.token = cached.get("user_id"), cached.get("token")
        else:
            self.user_id = self.token = None

    def login(self):
        payload = {
            "email": self.email,
            "password": self.password,
            # Must stay null: a non-null value overwrites the account's timezone.
            "timezone": None,
            "userCode": os.environ.get("CRONOMETER_2FA_CODE") or None,
            "build": "4.48.2 b2807-a",
            "device": "Android 14 (SDK 34), Google Pixel 6 Pro",
            "firebaseToken": "",
            "features": {},
            "auth": {"userId": None, "token": None, **self.APP_AUTH},
            "lastSeen": 0,
            "config": {"call_version": 2},
        }
        data = http_json(f"{CRONOMETER_API}/api/v2/login", payload, self.HEADERS)
        if "sessionKey" not in data:
            if data.get("error") == "TOTP_CODE_REQUIRED":
                raise CronometerError(
                    "Your Cronometer account has two-factor auth enabled. Run once "
                    "manually with CRONOMETER_2FA_CODE=<code> to cache a session.")
            raise CronometerError(f"Cronometer login failed: {data.get('error') or data}")
        self.user_id, self.token = data["id"], data["sessionKey"]
        write_json(self.session_path, {"email": self.email, "user_id": self.user_id,
                                       "token": self.token})
        os.chmod(self.session_path, 0o600)
        log.info("Logged in to Cronometer as %s", self.email)

    def request(self, endpoint, payload, retried=False):
        if not self.token:
            self.login()
        body = dict(payload, auth={"userId": self.user_id, "token": self.token, **self.APP_AUTH},
                    lastSeen=0)
        try:
            data = http_json(f"{CRONOMETER_API}{endpoint}", body, self.HEADERS)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403) and not retried:
                self.token = None
                return self.request(endpoint, payload, retried=True)
            raise
        if isinstance(data, dict) and data.get("result") in ("FAIL", "FAILURE"):
            if not retried:
                self.token = None
                return self.request(endpoint, payload, retried=True)
            raise CronometerError(f"Cronometer {endpoint} failed: {data}")
        return data

    def save_food(self, data):
        """Create (id 0) or overwrite (existing id) a custom food; returns its id."""
        resp = self.request("/api/v2/add_food", {"data": data, "config": {"call_version": 1}})
        food_id = resp.get("id")
        if not food_id:
            raise CronometerError(f"add_food returned no id: {resp}")
        return food_id

    def get_food(self, food_id):
        return self.request("/api/v2/get_food", {"id": food_id, "config": {"call_version": 1}})

    def update_food(self, food_id, data):
        """Update an existing custom food's name, nutrients and serving weights."""
        food = self.get_food(food_id)
        food.pop("messages", None)
        food["name"] = data["name"]
        food["nutrients"] = data["nutrients"]
        new_values = {m["name"]: m["value"] for m in data["measures"]}
        for m in food.get("measures", []):
            if m.get("name") in new_values:
                m["value"] = new_values.pop(m["name"])
        for name, value in new_values.items():  # serving size label changed
            food.setdefault("measures", []).append(
                {"id": 0, "name": name, "value": value, "amount": 1.0, "type": "Atomic"})
        return self.save_food(food)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(args):
    config = read_json(args.config, None)
    if config is None:
        sys.exit(f"Missing config file {args.config}.")

    menus = list_menus(config.get("halls", "all"))
    if config.get("menus"):
        menus = [m for m in menus if m[1] in set(config["menus"])]
    today = dt.date.today()
    dates = [today + dt.timedelta(days=i) for i in range(int(config.get("days_ahead", 1)) + 1)]
    prefix = config.get("name_prefix", "Yale - ")

    foods = {}
    seen = set()
    skipped = 0
    for food in fetch_foods(menus, dates):
        if food["id"] in seen:
            continue
        seen.add(food["id"])
        data = build_food(food, prefix)
        if data is None:
            skipped += 1
            continue
        foods[str(food["id"])] = data
    log.info("Found %d dishes with nutrition across %d menus (%d skipped: no nutrition/weight)",
             len(foods), len(menus), skipped)

    state_path = args.state_dir / "synced.json"
    state = read_json(state_path, {})
    todo = [(nid, data) for nid, data in foods.items()
            if state.get(nid, {}).get("fingerprint") != fingerprint(data)]
    if args.limit:
        todo = todo[:args.limit]

    if args.dry_run:
        for nid, data in todo:
            action = "update" if nid in state else "create"
            serving = data["measures"][1]["name"]
            kcal = next(n["amount"] for n in data["nutrients"] if n["id"] == 208)
            print(f"[{action}] {data['name']}  —  {serving}, {kcal * OZ_GRAMS / 100:.0f} kcal/oz")
        print(f"\n{len(todo)} foods would be written to Cronometer.")
        return

    email, password = os.environ.get("CRONOMETER_EMAIL"), os.environ.get("CRONOMETER_PASSWORD")
    if not email or not password:
        sys.exit("Set CRONOMETER_EMAIL and CRONOMETER_PASSWORD in .env")
    crono = Cronometer(email, password, args.state_dir / "session.json")

    created = updated = failed = 0
    for nid, data in todo:
        try:
            if nid in state:
                crono_id = crono.update_food(state[nid]["cronometer_id"], data)
                updated += 1
            else:
                crono_id = crono.save_food(data)
                created += 1
        except (CronometerError, urllib.error.URLError, json.JSONDecodeError) as exc:
            log.error("Failed to sync %r: %s", data["name"], exc)
            failed += 1
            if isinstance(exc, CronometerError) and "login failed" in str(exc).lower():
                break
            continue
        state[nid] = {"cronometer_id": crono_id, "name": data["name"],
                      "fingerprint": fingerprint(data),
                      "synced_at": dt.datetime.now().isoformat(timespec="seconds")}
        write_json(state_path, state)  # save as we go so a crash never duplicates
    log.info("Done: %d created, %d updated, %d failed, %d already up to date",
             created, updated, failed, len(foods) - len(todo))
    if failed:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--state-dir", type=Path, default=HERE / "state")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be synced without touching Cronometer")
    parser.add_argument("--limit", type=int, default=0, help="sync at most N foods this run")
    parser.add_argument("--list-halls", action="store_true", help="print hall and menu slugs")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env(HERE / ".env")

    if args.list_halls:
        for school in http_json(f"{NUTRISLICE_API}/schools/?format=json"):
            menus = ", ".join(m["slug"] for m in school.get("active_menu_types", []))
            print(f"{school['slug']:28} {school['name']}  [{menus}]")
        return
    run(args)


if __name__ == "__main__":
    main()
