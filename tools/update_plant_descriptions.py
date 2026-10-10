import urllib.request
import urllib.parse
import json
import os
import time

db_path = os.path.expanduser("~/.gemini/Projects/psy_garden/kb/plant_database.json")
with open(db_path, 'r', encoding='utf-8') as f:
    plants = json.load(f)

print(f"Loaded {len(plants)} plants. Fetching descriptions from Wikipedia...")

def fetch_descriptions(titles):
    titles_str = urllib.parse.quote('|'.join(titles))
    url = f"https://zh.wikipedia.org/w/api.php?action=query&prop=extracts&exintro=1&explaintext=1&exchars=150&titles={titles_str}&format=json"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        response = urllib.request.urlopen(req)
        data = json.loads(response.read())
        pages = data.get('query', {}).get('pages', {})
        results = {}
        for page_id, page_info in pages.items():
            if 'title' in page_info and 'extract' in page_info:
                results[page_info['title']] = page_info['extract'].replace('\n', ' ').strip()
        return results
    except Exception as e:
        print(f"Error fetching batch: {e}")
        return {}

batch_size = 50
for i in range(0, len(plants), batch_size):
    batch = plants[i:i+batch_size]
    titles = [p['zh_name'] for p in batch]
    print(f"Fetching batch {i//batch_size + 1}...")
    descriptions = fetch_descriptions(titles)
    
    for p in batch:
        desc = descriptions.get(p['zh_name'], "")
        if not desc:
            desc = f"{p['zh_name']}，属于《心灵花园》植物档案。"
        p['description'] = desc
    time.sleep(0.5)

with open(db_path, 'w', encoding='utf-8') as f:
    json.dump(plants, f, ensure_ascii=False, indent=2)

print(f"Successfully updated {len(plants)} plants with descriptions.")
