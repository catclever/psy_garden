import urllib.request
import urllib.parse
import json
import os
import random

categories = [
    "Category:观赏植物",
    "Category:药用植物",
    "Category:香草",
    "Category:有毒植物",
    "Category:温室植物",
    "Category:多肉植物"
]

all_plants = set()

def fetch_category(cat_name):
    url = f"https://zh.wikipedia.org/w/api.php?action=query&list=categorymembers&cmtitle={urllib.parse.quote(cat_name)}&cmlimit=500&format=json"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        response = urllib.request.urlopen(req)
        data = json.loads(response.read())
        members = data.get('query', {}).get('categorymembers', [])
        for m in members:
            title = m['title']
            if not title.startswith("Category:") and not title.startswith("List of") and not title.startswith("Template:"):
                all_plants.add(title)
    except Exception as e:
        print(f"Error fetching {cat_name}: {e}")

for cat in categories:
    print(f"Fetching {cat}...")
    fetch_category(cat)

print(f"Total unique plants fetched: {len(all_plants)}")

def assign_theme(name):
    # simple keyword-based theme assignment
    if any(x in name for x in ['毒', '曼陀罗', '罂粟', '夜来香', '夹竹桃']):
        return {"theme": "执念与虚妄", "emotion": "obsession, illusion"}
    elif any(x in name for x in ['草', '薄荷', '迷迭香', '马鞭草', '茶', '参', '百合']):
        return {"theme": "净化与自我和解", "emotion": "healing, clarity"}
    elif any(x in name for x in ['刺', '松', '柏', '仙人掌', '杉', '铁树', '树']):
        return {"theme": "边界与防御", "emotion": "protection, isolation"}
    elif any(x in name for x in ['藓', '蕨', '莲', '睡', '竹']):
        return {"theme": "静默与时光", "emotion": "silence, time"}
    elif any(x in name for x in ['菊', '兰', '梅', '雪', '冰']):
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
for idx, name in enumerate(all_plants):
    cat = assign_theme(name)
    final_db.append({
        "id": f"plant_{idx:04d}",
        "zh_name": name,
        "theme": cat['theme'],
        "emotion_tags": cat['emotion']
    })

out_path = os.path.expanduser("~/.gemini/Projects/psy_garden/kb/plant_database.json")
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(final_db, f, ensure_ascii=False, indent=2)

print(f"Saved plant database with {len(final_db)} entries to {out_path}")
