import os
from PIL import Image

def check_background(image_path):
    try:
        img = Image.open(image_path).convert('RGB')
        width, height = img.size
        # Check the 4 corners
        corners = [
            (0, 0),
            (width - 1, 0),
            (0, height - 1),
            (width - 1, height - 1)
        ]
        
        white_count = 0
        not_black_count = 0
        
        for x, y in corners:
            r, g, b = img.getpixel((x, y))
            # If a corner is very bright, it's a white/light background
            if r > 200 and g > 200 and b > 200:
                white_count += 1
            # If a corner is not very dark, it's not a pure black background
            if max(r, g, b) > 30:
                not_black_count += 1
                
        # If at least 2 corners are white, it's a "White Background" hallucination
        if white_count >= 2:
            return "WHITE"
            
        # If 3 or more corners aren't black, the AI drew a scene/gradient instead of pure black
        if not_black_count >= 3:
            return "NOT_BLACK"
            
        return "OK"
    except Exception as e:
        print(f"Error reading {image_path}: {e}")
        return "ERROR"

def main():
    # Setup directories
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates_dir = os.path.join(project_root, 'tools', 'candidates')
    bad_dir = os.path.join(candidates_dir, 'bad_backgrounds')
    
    if not os.path.exists(bad_dir):
        os.makedirs(bad_dir)
        
    # Get all current candidate JPGs
    files = [f for f in os.listdir(candidates_dir) if f.startswith('candidate_plant_') and f.endswith('.jpg')]
    
    bad_files = []
    
    for f in files:
        file_path = os.path.join(candidates_dir, f)
        status = check_background(file_path)
        
        if status != "OK":
            bad_files.append(f)
            # Move the bad file to the bad_backgrounds directory
            os.rename(file_path, os.path.join(bad_dir, f))
            print(f"[REJECTED - {status}] {f}")
            
    print("-" * 40)
    print(f"Scanned: {len(files)} images.")
    print(f"Rejected: {len(bad_files)} images (Moved to tools/candidates/bad_backgrounds/).")
    print("-" * 40)
    
    if bad_files:
        print("To reprocess, extract the following IDs:")
        ids = []
        for f in bad_files:
            try:
                parts = f.split('_')
                plant_id = int(parts[2])
                ids.append(plant_id)
            except:
                pass
        print(f"Missing IDs: {sorted(ids)}")
        
if __name__ == "__main__":
    main()
