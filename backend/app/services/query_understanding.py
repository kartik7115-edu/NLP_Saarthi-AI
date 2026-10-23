"""Structured query understanding.

Turns a free-text question into intent + entities, using only the question itself
(the frontend farmer profile is an optional, lower-priority fallback):

    "What documents are required for PM-KISAN?"
        -> intent=document_requirements, scheme=[pm-kisan]
    "What schemes are available for cotton farmers in Maharashtra?"
        -> intent=scheme_search, state=Maharashtra, crop=cotton, beneficiary=farmer

Scheme resolution reuses the canonical alias index (scheme_query.resolve_schemes) - there is no
second alias table. Matching is on whole tokens (stemmed for English); there is no substring matching.
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from app.services.intent_detection import detect_intent, score_intents, stem, tokenize
from app.services.scheme_query import resolve_schemes

# ----------------------------------------------------------------------------- gazetteers
STATE_NAMES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana",
    "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur",
    "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana",
    "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Andaman and Nicobar Islands", "Chandigarh",
    "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
]
STATE_ALIASES = {
    "orissa": "Odisha", "telengana": "Telangana", "uttaranchal": "Uttarakhand", "pondicherry": "Puducherry",
    "chattisgarh": "Chhattisgarh", "tamilnadu": "Tamil Nadu", "jammu kashmir": "Jammu and Kashmir",
    "j&k": "Jammu and Kashmir", "andaman nicobar": "Andaman and Nicobar Islands", "new delhi": "Delhi",
    "महाराष्ट्र": "Maharashtra", "आंध्र प्रदेश": "Andhra Pradesh", "आन्ध्र प्रदेश": "Andhra Pradesh",
    "तेलंगाना": "Telangana", "उत्तर प्रदेश": "Uttar Pradesh", "मध्य प्रदेश": "Madhya Pradesh", "बिहार": "Bihar",
    "ओडिशा": "Odisha", "उड़ीसा": "Odisha", "पंजाब": "Punjab", "हरियाणा": "Haryana", "राजस्थान": "Rajasthan",
    "गुजरात": "Gujarat", "कर्नाटक": "Karnataka", "केरल": "Kerala", "तमिलनाडु": "Tamil Nadu",
    "पश्चिम बंगाल": "West Bengal", "असम": "Assam", "झारखंड": "Jharkhand", "छत्तीसगढ़": "Chhattisgarh",
    "हिमाचल प्रदेश": "Himachal Pradesh", "उत्तराखंड": "Uttarakhand", "दिल्ली": "Delhi",
}

# canonical crop -> spellings. Horticultural crops also map to the category used in the dataset.
CROPS: Dict[str, List[str]] = {
    "cotton": ["cotton", "kapas", "कपास"], "paddy": ["paddy", "rice", "धान", "चावल"],
    "wheat": ["wheat", "gehu", "गेहूं", "गेहूँ"], "maize": ["maize", "corn", "makka"],
    "sugarcane": ["sugarcane", "sugar cane", "ganna", "गन्ना"], "soybean": ["soybean", "soyabean", "soya"],
    "groundnut": ["groundnut", "peanut"], "mustard": ["mustard", "rapeseed"], "sunflower": ["sunflower"],
    "pulses": ["pulses", "pulse", "dal", "tur", "arhar", "chana", "moong", "urad", "masoor", "lentil"],
    "millets": ["millet", "millets", "bajra", "jowar", "ragi", "sorghum"], "jute": ["jute"],
    "tea": ["tea"], "coffee": ["coffee"], "rubber": ["rubber"],
}
HORTICULTURE_CROPS = [
    "mango", "banana", "apple", "grapes", "grape", "pomegranate", "orange", "citrus", "guava", "papaya", "litchi",
    "vegetable", "vegetables", "tomato", "onion", "potato", "chilli", "chili", "turmeric", "ginger", "brinjal",
    "okra", "cabbage", "cauliflower", "flower", "flowers", "rose", "spices", "spice", "coconut", "cashew",
    "arecanut", "fruit", "fruits", "horticulture crops", "horticultural crops",
]
HORTICULTURE_CATEGORY = "horticultural_crops"

# phrase -> (activities, domains). Activities/domains use the dataset vocabulary.
TOPICS: Dict[str, Tuple[Set[str], Set[str]]] = {
    "irrigation": ({"irrigation"}, {"irrigation", "water_management"}),
    "micro irrigation": ({"irrigation"}, {"irrigation", "water_management"}),
    "drip": ({"irrigation"}, {"irrigation", "water_management"}),
    "sprinkler": ({"irrigation"}, {"irrigation", "water_management"}),
    "borewell": ({"irrigation"}, {"irrigation", "water_management"}),
    "pump": ({"irrigation", "solar_agriculture"}, {"irrigation", "solar_energy"}),
    "solar": ({"solar_agriculture"}, {"solar_energy"}),
    "tractor": ({"farm_mechanisation"}, {"farm_mechanisation"}),
    "machinery": ({"farm_mechanisation"}, {"farm_mechanisation"}),
    "machine": ({"farm_mechanisation"}, {"farm_mechanisation"}),
    "harvester": ({"farm_mechanisation"}, {"farm_mechanisation"}),
    "mechanization": ({"farm_mechanisation"}, {"farm_mechanisation"}),
    "mechanisation": ({"farm_mechanisation"}, {"farm_mechanisation"}),
    "drone": ({"farm_mechanisation"}, {"farm_mechanisation"}),
    "soil": (set(), {"soil_health"}),
    "storage": ({"storage"}, {"agri_infrastructure", "post_harvest_infrastructure"}),
    "cold storage": ({"storage"}, {"agri_infrastructure", "post_harvest_infrastructure"}),
    "warehouse": ({"storage"}, {"agri_infrastructure", "post_harvest_infrastructure"}),
    "godown": ({"storage"}, {"agri_infrastructure", "post_harvest_infrastructure"}),
    "infrastructure": (set(), {"agri_infrastructure"}),
    "marketing": ({"marketing"}, {"agricultural_marketing"}),
    "market": ({"marketing"}, {"agricultural_marketing"}),
    "sell": ({"marketing"}, {"agricultural_marketing"}),
    "selling": ({"marketing"}, {"agricultural_marketing"}),
    "horticulture": ({"horticulture"}, {"horticulture"}),
    "orchard": ({"horticulture"}, {"horticulture"}),
    "nursery": ({"horticulture"}, {"horticulture"}),
    "greenhouse": ({"horticulture"}, {"horticulture"}),
    "polyhouse": ({"horticulture"}, {"horticulture"}),
    "fpo": ({"farmer_producer_organization"}, {"farmer_collectives"}),
    "farmer producer organisation": ({"farmer_producer_organization"}, {"farmer_collectives"}),
    "farmer producer organization": ({"farmer_producer_organization"}, {"farmer_collectives"}),
    "insurance": (set(), {"crop_insurance", "life_insurance", "health_insurance", "accident_insurance"}),
    "crop insurance": (set(), {"crop_insurance"}),
    "loan": (set(), {"agri_credit", "enterprise_credit"}),
    "credit": (set(), {"agri_credit", "enterprise_credit"}),
    "pension": (set(), {"pension", "farmer_pension", "social_pension"}),
    "income support": (set(), {"income_support"}),
    "financial assistance": (set(), {"income_support"}),
    "financial support": (set(), {"income_support"}),
    "cash": (set(), {"income_support"}),
    "organic": ({"natural_farming"}, set()),
    "natural farming": ({"natural_farming"}, set()),
    "housing": (set(), {"housing"}),
    "scholarship": (set(), {"scholarship", "education"}),
    "employment": (set(), {"employment"}),
    "skill": (set(), {"skill_development"}),
}

FARM_WORDS = {"farmer", "kisan", "kisaan", "cultivator", "agriculturist", "grower", "farming", "agriculture",
              "agricultural", "किसान", "खेती", "कृषि"}
OCCUPATION_WORDS: Dict[str, Tuple[str, Set[str]]] = {   # token -> (occupation, activities)
    "fisherman": ("fisherman", {"fisheries"}), "fishermen": ("fisherman", {"fisheries"}),
    "fisher": ("fisherman", {"fisheries"}), "fisherfolk": ("fisherman", {"fisheries"}),
    "fishing": ("fisherman", {"fisheries"}), "fishery": ("fisherman", {"fisheries"}),
    "fisheries": ("fisherman", {"fisheries"}), "aquaculture": ("fisherman", {"fisheries"}),
    "dairy": ("dairy_farmer", {"dairy"}), "milk": ("dairy_farmer", {"dairy"}), "milch": ("dairy_farmer", {"dairy"}),
    "livestock": ("livestock_farmer", {"livestock"}), "cattle": ("livestock_farmer", {"livestock"}),
    "cow": ("livestock_farmer", {"livestock"}), "buffalo": ("livestock_farmer", {"livestock"}),
    "goat": ("livestock_farmer", {"livestock"}), "sheep": ("livestock_farmer", {"livestock"}),
    "poultry": ("poultry_farmer", {"poultry"}), "hen": ("poultry_farmer", {"poultry"}),
    "chicken": ("poultry_farmer", {"poultry"}),
}
FARMER_TYPE_WORDS = {"small": "small", "marginal": "marginal", "tenant": "tenant", "sharecropper": "sharecropper",
                     "landless": "landless", "large": "large", "young": "young", "youth": "young"}
WOMEN_WORDS = {"women", "woman", "female", "ladies", "lady", "girl", "girls", "mahila", "महिला", "महिलाओं"}
AG_CONTEXT_WORDS = {"crop", "acre", "hectare", "cultivate", "cultivating", "cultivation", "harvest", "sowing", "sow"}
HISTORICAL_WORDS = {"old", "previous", "earlier", "formerly", "replaced", "replacement", "discontinued", "repealed",
                    "scrapped", "happened", "status", "renamed", "successor"}
ACRE_TO_HA = 0.404686
_LAND_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(acre|acres|hectare|hectares|ha)\b", re.I)

# ----------------------------------------------------------------------------- phrase matching
Table = List[Tuple[Tuple[str, ...], bool, Any]]


def _build_table(items: Iterable[Tuple[str, Any]]) -> Table:
    table: Table = []
    for phrase, value in items:
        toks = tokenize(phrase)
        if not toks:
            continue
        ascii_ = all(t.isascii() for t in toks)
        table.append((tuple(stem(t) if ascii_ else t for t in toks), ascii_, value))
    table.sort(key=lambda e: -len(e[0]))
    return table


def _match(tokens: List[str], stems: List[str], table: Table) -> List[Tuple[int, int, Any]]:
    claimed = [False] * len(tokens)
    spans = []
    for seq, ascii_, value in table:
        n = len(seq)
        for i in range(len(tokens) - n + 1):
            if any(claimed[i:i + n]):
                continue
            src = stems if ascii_ else tokens
            if all(src[i + j] == seq[j] for j in range(n)):
                for j in range(i, i + n):
                    claimed[j] = True
                spans.append((i, i + n, value))
    spans.sort()
    return spans


_STATE_TABLE = _build_table([(s, s) for s in STATE_NAMES] + [(a, s) for a, s in STATE_ALIASES.items()])
_CROP_TABLE = _build_table(
    [(a, (c, None)) for c, aliases in CROPS.items() for a in aliases]
    + [(a, (a.replace(" ", "_"), HORTICULTURE_CATEGORY)) for a in HORTICULTURE_CROPS]
)
_TOPIC_TABLE = _build_table(TOPICS.items())


# ----------------------------------------------------------------------------- result object
@dataclass
class QueryUnderstanding:
    query: str
    intent: str = "general_information"
    intent_scores: Dict[str, int] = field(default_factory=dict)
    schemes: List[Dict[str, Any]] = field(default_factory=list)     # [{"id","matched","name"}]
    state: Optional[str] = None
    state_source: Optional[str] = None                               # "query" | "profile"
    crop: Optional[str] = None
    crop_category: Optional[str] = None
    crop_source: Optional[str] = None
    occupations: List[str] = field(default_factory=list)            # farm occupations only
    occupation_source: Optional[str] = None
    beneficiary: Optional[str] = None                                # primary occupation
    farmer_types: List[str] = field(default_factory=list)
    farmer_types_inferred: List[str] = field(default_factory=list)
    gender: Optional[str] = None
    land_ha: Optional[float] = None
    land_source: Optional[str] = None
    occupation_inferred_from: List[str] = field(default_factory=list)
    activities: List[str] = field(default_factory=list)
    domains: List[str] = field(default_factory=list)
    historical: bool = False
    consumed: Set[int] = field(default_factory=set)                  # token positions used by entities
    tokens: List[str] = field(default_factory=list)

    @property
    def has_topic(self) -> bool:
        return bool(self.activities or self.domains)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query, "intent": self.intent,
            "scheme": [s["id"] for s in self.schemes] or None,
            "state": self.state, "state_source": self.state_source,
            "crop": self.crop, "crop_category": self.crop_category,
            "beneficiary": self.beneficiary, "occupations": self.occupations,
            "farmer_types": self.farmer_types, "farmer_types_inferred": self.farmer_types_inferred,
            "gender": self.gender, "land_ha": self.land_ha,
            "activities": self.activities, "domains": self.domains, "historical": self.historical,
        }


def _find_span(tokens: List[str], phrase: str, used: Set[int]) -> Optional[Tuple[int, int]]:
    seq = tokenize(phrase)
    n = len(seq)
    for i in range(len(tokens) - n + 1):
        if tokens[i:i + n] == seq and not (set(range(i, i + n)) & used):
            return i, i + n
    return None


def understand_query(query: str, records: List[Dict[str, Any]], profile: Optional[Dict[str, Any]] = None) -> QueryUnderstanding:
    """records: scheme records (adapted or canonical) including inactive ones, so old names resolve."""
    profile = profile or {}
    tokens = tokenize(query)
    stems = [stem(t) for t in tokens]
    u = QueryUnderstanding(query=query, tokens=tokens)

    # 1. schemes named in the question (canonical aliases)
    for m in resolve_schemes(query, records):
        u.schemes.append({"id": m["id"], "matched": m["matched"], "name": m["record"].get("name")})
        span = _find_span(tokens, m["matched"], u.consumed)
        if span:
            u.consumed.update(range(*span))

    # 2. intent (knowing whether a scheme was named)
    u.intent_scores = score_intents(query, has_named_scheme=bool(u.schemes))
    u.intent = detect_intent(query, has_named_scheme=bool(u.schemes))

    # 3. state
    for i, j, state in _match(tokens, stems, _STATE_TABLE):
        if not (set(range(i, j)) & u.consumed):
            u.state, u.state_source = state, "query"
            u.consumed.update(range(i, j))
            break
    if not u.state and profile.get("state"):
        for _, _, state in _match(tokenize(str(profile["state"])), [stem(t) for t in tokenize(str(profile["state"]))], _STATE_TABLE):
            u.state, u.state_source = state, "profile"
            break

    # 4. crop
    for i, j, (crop, category) in _match(tokens, stems, _CROP_TABLE):
        if not (set(range(i, j)) & u.consumed):
            u.crop, u.crop_category, u.crop_source = crop, category, "query"
            u.consumed.update(range(i, j))
            break
    if not u.crop and profile.get("crop"):
        for _, _, (crop, category) in _match(tokenize(str(profile["crop"])), [stem(t) for t in tokenize(str(profile["crop"]))], _CROP_TABLE):
            u.crop, u.crop_category, u.crop_source = crop, category, "profile"
            break

    # 5a. land size (acres/hectares written in the question)
    m = _LAND_RE.search(query)
    if m:
        val = float(m.group(1))
        u.land_ha = round(val * ACRE_TO_HA if m.group(2).lower().startswith("acre") else val, 3)
        u.land_source = "query"
    elif profile.get("land_size"):
        try:
            u.land_ha, u.land_source = round(float(profile["land_size"]) * ACRE_TO_HA, 3), "profile"  # profile is in acres
        except (TypeError, ValueError):
            pass

    # 5. beneficiary / occupation / farmer type / gender
    occ: List[str] = []
    acts: Set[str] = set()
    for idx, t in enumerate(tokens):
        if idx in u.consumed:
            continue
        prev = tokens[idx - 1] if idx else ""
        if (stems[idx] in FARM_WORDS or t in FARM_WORDS) and prev != "non":
            if "farmer" not in occ:
                occ.append("farmer")
            u.consumed.add(idx)
        elif t in OCCUPATION_WORDS or stems[idx] in OCCUPATION_WORDS:
            o, a = OCCUPATION_WORDS.get(t) or OCCUPATION_WORDS[stems[idx]]
            if o not in occ:
                occ.append(o)
            acts |= a
            u.consumed.add(idx)
        elif t in WOMEN_WORDS:
            u.gender = "women"
            u.consumed.add(idx)
    if not occ:
        # No explicit "farmer": a crop name, a land size or agricultural words are unambiguous farming context.
        why = []
        if u.crop and u.crop_source == "query":
            why.append("crop")
        if u.land_source == "query":
            why.append("land_size")
        if set(stems) & AG_CONTEXT_WORDS:
            why.append("agricultural_word")
        if why:
            occ.append("farmer")
            u.occupation_inferred_from = why
    if occ:
        for idx, t in enumerate(tokens):
            if t in FARMER_TYPE_WORDS and FARMER_TYPE_WORDS[t] not in u.farmer_types:
                u.farmer_types.append(FARMER_TYPE_WORDS[t])
                u.consumed.add(idx)
        if u.gender == "women" and "women" not in u.farmer_types:
            u.farmer_types.append("women")
    if occ:
        u.occupations, u.occupation_source = occ, "query"
    elif profile.get("occupation") and any(w in str(profile["occupation"]).lower() for w in ("farmer", "kisan", "cultivator")):
        u.occupations, u.occupation_source = ["farmer"], "profile"
    u.beneficiary = u.occupations[0] if u.occupations else None

    if u.land_ha is not None and u.occupations:
        # Indian agricultural-census bands: marginal < 1 ha, small 1-2 ha
        if u.land_ha < 1:
            u.farmer_types_inferred.append("marginal")
        elif u.land_ha <= 2:
            u.farmer_types_inferred.append("small")


    # 7. topics (activities / domains)
    acts_all, domains = set(acts), set()
    for i, j, (a, d) in _match(tokens, stems, _TOPIC_TABLE):
        acts_all |= a
        domains |= d
    u.activities, u.domains = sorted(acts_all), sorted(domains)

    # 8. historical cues
    u.historical = bool(set(tokens) & HISTORICAL_WORDS) or "what happened" in " ".join(tokens)
    return u
