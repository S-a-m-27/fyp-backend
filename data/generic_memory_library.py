"""Curated generic memory library for reminiscence-style quizzes.

These images are **not** used for DeepFace / embedding training — only personal
uploads of relatives are. Generic quizzes use *titles* (and optional manifest
text) as answers, not face recognition.

Eight shelves (topics), ~10 image cards each. Each card has a *title* (what
to recognise) and *location* (place context).

**Images on disk (offline):** one **main folder per category (topic)** only.
Under that single folder you add **subfolders** — each subfolder is one bundle
(collection the caretaker may buy separately):

  static/memory/generic/{topic_slug}/{bundle_slug}/{index}.jpg

Optional **manifest.json** in the same folder as the images maps each filename to
quiz/UI metadata (restart the API after edits). Example::

    {
      "0.jpg": {
        "title": "Short name (used as the quiz correct answer)",
        "location": "Place or context line under the photo",
        "description": "Longer hint — what the photo is about (shown during training)."
      }
    }

There is **only one** ``war_history`` directory for all war-related packs:

  static/memory/generic/war_history/
    included/          ← starter seed (10 images: 0.jpg … 9.jpg)
    ww2_europe/         ← optional extra bundle
    cold_war_era/       ← another bundle, same topic

All three bundles share ``library_topic = war_history``; each bundle has its
own ``library_collection_slug`` matching the subfolder name.

The seed uses ``bundle_slug = "included"`` by default. Add rows + files for
other bundles under the **same** ``war_history`` folder as shown above.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

# Default bundle folder under each topic (caretaker “starter” set; more bundles = sibling folders).
DEFAULT_GENERIC_BUNDLE_SLUG = "included"

# --- API metadata (no DB required) -----------------------------------------

GENERIC_TOPIC_CATALOG: List[Dict[str, str]] = [
    {
        "slug": "war_history",
        "label": "War & peace in history",
        "blurb": "Memorials, independence moments, and peace landmarks.",
    },
    {
        "slug": "cricket",
        "label": "Cricket",
        "blurb": "Grounds, trophies, and famous matches.",
    },
    {
        "slug": "music_singers",
        "label": "Music & singers",
        "blurb": "Concerts, halls, and beloved voices.",
    },
    {
        "slug": "cinema_tv",
        "label": "Cinema & TV",
        "blurb": "Studios, festivals, and classic screens.",
    },
    {
        "slug": "landmarks",
        "label": "Landmarks & places",
        "blurb": "Bridges, towers, and cities everyone recognises.",
    },
    {
        "slug": "sports_olympics",
        "label": "Sports beyond cricket",
        "blurb": "Olympics, football, tennis, and athletics.",
    },
    {
        "slug": "literature_poetry",
        "label": "Literature & poetry",
        "blurb": "Writers, libraries, and prize moments.",
    },
    {
        "slug": "science_space",
        "label": "Science & space",
        "blurb": "Museums, rockets, and discoveries.",
    },
]

GENERIC_TOPIC_SLUGS = frozenset(t["slug"] for t in GENERIC_TOPIC_CATALOG)

# (slug, title, location) — 10 per slug, 80 total
_RAW: List[Tuple[str, str, str]] = [
    # war_history
    ("war_history", "Armistice signing railway carriage", "Compiègne, France"),
    ("war_history", "Independence Day at Red Fort", "New Delhi, India"),
    ("war_history", "Hiroshima Peace Memorial Park", "Hiroshima, Japan"),
    ("war_history", "Berlin Wall opening celebrations", "Berlin, Germany"),
    ("war_history", "D-Day landing beaches memorial", "Normandy, France"),
    ("war_history", "UN General Assembly first sessions", "New York, USA"),
    ("war_history", "Treaty of Versailles Hall of Mirrors", "Versailles, France"),
    ("war_history", "Commonwealth war graves ceremony", "Ypres, Belgium"),
    ("war_history", "Nobel Peace Prize institute", "Oslo, Norway"),
    ("war_history", "Anne Frank House museum", "Amsterdam, Netherlands"),
    # cricket
    ("cricket", "Lord's Cricket Ground — Home of Cricket", "London, UK"),
    ("cricket", "Eden Gardens — historic Test venue", "Kolkata, India"),
    ("cricket", "Melbourne Cricket Ground — Boxing Day", "Melbourne, Australia"),
    ("cricket", "Galle Stadium beside the fort", "Galle, Sri Lanka"),
    ("cricket", "ICC Men's Cricket World Cup trophy", "Dubai, UAE"),
    ("cricket", "Sydney Cricket Ground members pavilion", "Sydney, Australia"),
    ("cricket", "Old Trafford cricket ground", "Manchester, UK"),
    ("cricket", "Wanderers Club — first Test ground", "Johannesburg, South Africa"),
    ("cricket", "Sabina Park — Caribbean cricket heart", "Kingston, Jamaica"),
    ("cricket", "Sharjah Cricket Stadium floodlights", "Sharjah, UAE"),
    # music_singers
    ("music_singers", "Royal Albert Hall classical season", "London, UK"),
    ("music_singers", "Carnegie Hall marquee", "New York, USA"),
    ("music_singers", "La Scala opera house stage", "Milan, Italy"),
    ("music_singers", "Vienna Musikverein golden hall", "Vienna, Austria"),
    ("music_singers", "Abbey Road zebra crossing", "London, UK"),
    ("music_singers", "Nashville Grand Ole Opry", "Tennessee, USA"),
    ("music_singers", "Sydney Opera House sails", "Sydney, Australia"),
    ("music_singers", "Montreux Jazz Festival lakeside", "Montreux, Switzerland"),
    ("music_singers", "Bollywood film song recording studio era", "Mumbai, India"),
    ("music_singers", "Glastonbury Pyramid Stage crowd", "Somerset, UK"),
    # cinema_tv
    ("cinema_tv", "Cannes Film Festival red carpet", "Cannes, France"),
    ("cinema_tv", "Hollywood Walk of Fame stars", "Los Angeles, USA"),
    ("cinema_tv", "Bollywood Film City studio gates", "Mumbai, India"),
    ("cinema_tv", "Bafta mask award ceremony", "London, UK"),
    ("cinema_tv", "Oscars Dolby Theatre", "Los Angeles, USA"),
    ("cinema_tv", "Venice Lido film festival palace", "Venice, Italy"),
    ("cinema_tv", "BBC Television Centre doughnut", "London, UK"),
    ("cinema_tv", "Tokyo International Film Festival", "Tokyo, Japan"),
    ("cinema_tv", "Locarno Piazza Grande open-air screen", "Locarno, Switzerland"),
    ("cinema_tv", "National Film Archive screening", "Pune, India"),
    # landmarks
    ("landmarks", "Eiffel Tower from Champ de Mars", "Paris, France"),
    ("landmarks", "Taj Mahal marble mausoleum", "Agra, India"),
    ("landmarks", "Great Wall winding ridge", "Beijing, China"),
    ("landmarks", "Statue of Liberty harbour view", "New York, USA"),
    ("landmarks", "Big Ben and Houses of Parliament", "London, UK"),
    ("landmarks", "Pyramids of Giza plateau", "Giza, Egypt"),
    ("landmarks", "Christ the Redeemer mountain", "Rio de Janeiro, Brazil"),
    ("landmarks", "Sydney Harbour Bridge climb", "Sydney, Australia"),
    ("landmarks", "Burj Khalifa skyline", "Dubai, UAE"),
    ("landmarks", "Golden Gate Bridge fog", "San Francisco, USA"),
    # sports_olympics
    ("sports_olympics", "Olympic rings at stadium", "Athens, Greece"),
    ("sports_olympics", "FIFA World Cup final trophy", "Lusail, Qatar"),
    ("sports_olympics", "Wimbledon Centre Court grass", "London, UK"),
    ("sports_olympics", "Tour de France Champs-Élysées finish", "Paris, France"),
    ("sports_olympics", "Maracanã football cathedral", "Rio de Janeiro, Brazil"),
    ("sports_olympics", "NBA Finals arena lights", "Los Angeles, USA"),
    ("sports_olympics", "Barcelona 1992 Olympic stadium", "Barcelona, Spain"),
    ("sports_olympics", "Tokyo Olympic National Stadium", "Tokyo, Japan"),
    ("sports_olympics", "All England Badminton championships", "Birmingham, UK"),
    ("sports_olympics", "Hockey World Cup pitch", "Bhubaneswar, India"),
    # literature_poetry
    ("literature_poetry", "Shakespeare's Globe theatre", "London, UK"),
    ("literature_poetry", "British Library King's Library tower", "London, UK"),
    ("literature_poetry", "Stratford-upon-Avon birthplace", "Stratford, UK"),
    ("literature_poetry", "National Library of India reading room", "Kolkata, India"),
    ("literature_poetry", "Hay-on-Wye festival tents", "Hay-on-Wye, UK"),
    ("literature_poetry", "Jaipur literature festival lawns", "Jaipur, India"),
    ("literature_poetry", "Nobel Prize in Literature banquet", "Stockholm, Sweden"),
    ("literature_poetry", "New York Public Library lions", "New York, USA"),
    ("literature_poetry", "Café de Flore writers' corner", "Paris, France"),
    ("literature_poetry", "Vienna State Library baroque hall", "Vienna, Austria"),
    # science_space
    ("science_space", "NASA Apollo Saturn V rocket", "Houston, USA"),
    ("science_space", "Kennedy Space Center launch pad", "Florida, USA"),
    ("science_space", "CERN Globe of Science", "Geneva, Switzerland"),
    ("science_space", "ISRO mission control screens", "Bengaluru, India"),
    ("science_space", "Natural History Museum dinosaur hall", "London, UK"),
    ("science_space", "Smithsonian Air and Space Museum", "Washington DC, USA"),
    ("science_space", "Hubble Space Telescope model", "Baltimore, USA"),
    ("science_space", "Jodrell Bank radio telescope", "Cheshire, UK"),
    ("science_space", "Science Museum energy hall", "London, UK"),
    ("science_space", "Neutron star artwork — Griffith Observatory", "Los Angeles, USA"),
]


def iter_generic_image_jobs() -> List[Dict[str, Any]]:
    """Each generic card: on-disk path under topic/bundle, title, place (for checklists)."""
    counts: Dict[str, int] = {}
    jobs: List[Dict[str, Any]] = []
    bundle = DEFAULT_GENERIC_BUNDLE_SLUG
    for slug, title, location in _RAW:
        idx = counts.get(slug, 0)
        counts[slug] = idx + 1
        filename = f"{idx}.jpg"
        rel_path = f"static/memory/generic/{slug}/{bundle}/{filename}".replace(
            "\\", "/",
        )
        manifest_key = f"{slug}/{bundle}/{filename}".replace("\\", "/")
        jobs.append(
            {
                "filename": filename,
                "file_path": rel_path,
                "manifest_key": manifest_key,
                "library_topic": slug,
                "library_collection_slug": bundle,
                "index": idx,
                "title": title,
                "location": location,
            },
        )
    return jobs


def build_seed_memory_dicts() -> List[Dict[str, Any]]:
    """Rows ready for ``MemoryItem`` construction (no ORM here)."""
    rows: List[Dict[str, Any]] = []
    for job in iter_generic_image_jobs():
        rows.append(
            {
                "patient_id": None,
                "title": job["title"],
                "description": None,
                "related_person_name": None,
                "related_person_relation": None,
                "category": "image",
                "library_type": "generic",
                "library_topic": job["library_topic"],
                "library_collection_slug": job["library_collection_slug"],
                "memory_type": "general",
                "year": None,
                "location": job["location"],
                "caretaker_email": None,
                "file_path": job["file_path"],
                "extra_file_paths": None,
            },
        )
    return rows
