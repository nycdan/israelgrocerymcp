"""Translate English grocery queries to Hebrew for better Israeli store search results."""
from __future__ import annotations

import re

# English → Hebrew mappings. Israeli store APIs index products in Hebrew.
_EN_TO_HE: dict[str, str] = {
    "apple": "תפוח",
    "apples": "תפוחים",
    "lady apple": "תפוח ליידי",
    "lady apples": "תפוחים ליידי",
    "pink lady": "תפוח פינק",
    "granny smith": "תפוח גרנד סמיט",
    "fuji": "תפוח פוג'י",
    "gala": "תפוח גלה",
    "golden delicious": "תפוח מוזהב",
    "eggs": "ביצים",
    "egg": "ביצה",
    "milk": "חלב",
    "chicken": "עוף",
    "chicken breast": "חזה עוף",
    "beef": "בקר",
    "fish": "דג",
    "salmon": "סלמון",
    "tuna": "טונה",
    "bread": "לחם",
    "cheese": "גבינה",
    "butter": "חמאה",
    "yogurt": "יוגורט",
    "cream": "שמנת",
    "rice": "אורז",
    "pasta": "פסטה",
    "tomato": "עגבנייה",
    "tomatoes": "עגבניות",
    "potato": "תפוח אדמה",
    "potatoes": "תפוחי אדמה",
    "onion": "בצל",
    "onions": "בצלים",
    "garlic": "שום",
    "carrot": "גזר",
    "carrots": "גזרים",
    "cucumber": "מלפפון",
    "cucumbers": "מלפפונים",
    "pepper": "פלפל",
    "peppers": "פלפלים",
    "zucchini": "קישוא",
    "zucchinis": "קישואים",
    "broccoli": "ברוקולי",
    "cauliflower": "כרובית",
    "spinach": "תרד",
    "lettuce": "חסה",
    "lemon": "לימון",
    "lemons": "לימונים",
    "orange": "תפוז",
    "oranges": "תפוזים",
    "banana": "בננה",
    "bananas": "בננות",
    "avocado": "אבוקדו",
    "avocados": "אבוקדו",
    "olive oil": "שמן זית",
    "salt": "מלח",
    "sugar": "סוכר",
    "flour": "קמח",
    "honey": "דבש",
    "water": "מים",
    "juice": "מיץ",
    "coffee": "קפה",
    "tea": "תה",
    "wine": "יין",
    "beer": "בירה",
    "chocolate": "שוקולד",
    "cereal": "דגנים",
    "cookies": "עוגיות",
    "crackers": "קרקרים",
    "nuts": "אגוזים",
    "beans": "שעועית",
    "lentils": "עדשים",
    "chickpeas": "חומוס",
    "hummus": "חומוס",
    "tahini": "טחינה",
    "pita": "פיתה",
    "pitas": "פיתות",
    "hummus": "חומוס",
    "falafel": "פלאפל",
    "yogurt": "יוגורט",
    "cottage cheese": "גבינת קוטג'",
    "cream cheese": "גבינת שמנת",
    "mozzarella": "מוצרלה",
    "parmesan": "פרמזן",
    "feta": "פטה",
    "green beans": "שעועית ירוקה",
    "bell pepper": "פלפל צבעוני",
    "red pepper": "פלפל אדום",
    "yellow pepper": "פלפל צהוב",
    "sweet potato": "בטטה",
    "sweet potatoes": "בטטות",
    "mushroom": "פטריה",
    "mushrooms": "פטריות",
    "eggplant": "חציל",
    "eggplants": "חצילים",
    "celery": "סלרי",
    "parsley": "פטרוזיליה",
    "cilantro": "כוסברה",
    "dill": "שמיר",
    "basil": "בזיליקום",
    "oregano": "אורגנו",
    "thyme": "טימין",
    "rosemary": "רוזמרין",
    "cumin": "כמון",
    "paprika": "פפריקה",
    "turmeric": "כורכום",
    "cinnamon": "קינמון",
    "ginger": "ג'ינג'ר",
    "coriander": "כוסברה",
    "nutmeg": "אגוז מוסקט",
    "vanilla": "וניל",
    "oil": "שמן",
    "vinegar": "חומץ",
    "soy sauce": "רוטב סויה",
    "ketchup": "קטשופ",
    "mustard": "חרדל",
    "mayonnaise": "מיונז",
    "salsa": "סלסה",
    "hot sauce": "רוטב חריף",
}


def _is_mostly_hebrew(text: str) -> bool:
    """True if the string is mostly Hebrew (or other non-ASCII)."""
    if not text.strip():
        return False
    hebrew_chars = sum(1 for c in text if "\u0590" <= c <= "\u05FF" or c in "׳״")
    return hebrew_chars >= len(text.replace(" ", "")) * 0.3


def query_to_hebrew(query: str) -> str:
    """
    Convert an English grocery query to Hebrew for better Israeli store search results.
    If the query is already mostly Hebrew, return as-is.
    Otherwise look up mappings (case-insensitive, whole-word) and return the Hebrew term.
    """
    q = query.strip()
    if not q:
        return q
    if _is_mostly_hebrew(q):
        return q

    lower = q.lower()
    # Exact match first
    if lower in _EN_TO_HE:
        return _EN_TO_HE[lower]

    # Try multi-word phrases (longest first)
    words = re.split(r"\s+", lower)
    for n in range(min(len(words), 3), 0, -1):
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i : i + n])
            if phrase in _EN_TO_HE:
                # Replace the phrase with Hebrew, keep rest
                before = " ".join(words[:i])
                after = " ".join(words[i + n :])
                hebrew = _EN_TO_HE[phrase]
                parts = [p for p in (before, hebrew, after) if p]
                return " ".join(parts)

    # Single-word lookup
    result_parts = []
    for w in words:
        if w in _EN_TO_HE:
            result_parts.append(_EN_TO_HE[w])
        else:
            result_parts.append(w)
    return " ".join(result_parts)
