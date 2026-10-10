import urllib.request
import urllib.parse
import json
import random
import os

query = """
SELECT ?taxon ?taxonLabel ?scientificName ?familyLabel WHERE {
  ?taxon wdt:P31 wd:Q16521;
         wdt:P171* wd:Q756;
         wdt:P225 ?scientificName;
         wdt:P171 ?family.
  ?taxon wikibase:sitelinks ?sitelinks .
  FILTER(?sitelinks > 30)
  SERVICE wikibase:label { bd:serviceParam wikibase:language "zh,en". }
} LIMIT 1500
"""

url = "https://query.wikidata.org/sparql?query=" + urllib.parse.quote(query)
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)', 'Accept': 'application/json'})

print("Fetching plant data from Wikidata SPARQL...")
try:
    response = urllib.request.urlopen(req)
    data = json.loads(response.read())
except Exception as e:
    print("Error fetching:", e)
    exit(1)

plants = []
seen = set()
for result in data['results']['bindings']:
    zh_name = result.get('taxonLabel', {}).get('value', '')
    if any('\u4e00' <= c <= '\u9fff' for c in zh_name):
        sci_name = result.get('scientificName', {}).get('value', '')
        if sci_name in seen: continue
        seen.add(sci_name)
        
        family = result.get('familyLabel', {}).get('value', '')
        plants.append({
            "name": zh_name,
            "scientific_name": sci_name,
            "family": family
        })

print(f"Fetched {len(plants)} valid plant entries.")

def assign_category(family):
    family_str = family.lower()
    if any(x in family_str for x in ['rosaceae', '蔷薇', 'solanaceae', '茄']):
        return {"theme": "执念与虚妄", "emotion": "obsession, illusion"}
    elif any(x in family_str for x in ['lamiaceae', '唇形', 'verbenaceae', '马鞭草']):
        return {"theme": "净化与自我和解", "emotion": "healing, clarity"}
    elif any(x in family_str for x in ['pinaceae', '松', 'cupressaceae', '柏', 'cactaceae', '仙人掌']):
        return {"theme": "边界与防御", "emotion": "protection, isolation"}
    elif any(x in family_str for x in ['polypodiaceae', '蕨', 'bryophyte', '藓', 'nymphaeaceae', '睡莲']):
        return {"theme": "静默与时光", "emotion": "silence, time"}
    elif any(x in family_str for x in ['asteraceae', '菊', 'orchidaceae', '兰']):
        return {"theme": "沉思与清冷", "emotion": "detachment, coldness"}
    else:
        cats = [
            {"theme": "净化与自我和解", "emotion": "peace, relief"},
            {"theme": "边界与防御", "emotion": "guarded, sharp"},
            {"theme": "沉思与清冷", "emotion": "solitude, philosophical"},
            {"theme": "执念与虚妄", "emotion": "unreachable, looping"},
            {"theme": "静默与时光", "emotion": "patience, ancient"}
        ]
        return random.choice(cats)

final_db = []
for p in plants:
    cat = assign_category(p['family'])
    final_db.append({
        "zh_name": p['name'],
        "scientific_name": p['scientific_name'],
        "family": p['family'],
        "theme": cat['theme'],
        "emotion_tags": cat['emotion']
    })

out_path = os.path.expanduser("~/.gemini/Projects/psy_garden/kb/plant_database.json")
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(final_db, f, ensure_ascii=False, indent=2)

print(f"Saved plant database with {len(final_db)} entries to {out_path}")
