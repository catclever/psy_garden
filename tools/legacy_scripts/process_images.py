from PIL import Image
import glob
import os

for img_path in glob.glob("plant_*.png"):
    img = Image.open(img_path).convert("RGBA")
    data = img.getdata()
    new_data = []
    for item in data:
        r, g, b, a = item
        # Calculate max intensity
        alpha = max(r, g, b)
        if alpha == 0:
            new_data.append((0, 0, 0, 0))
        else:
            # Unmultiply
            # To simulate additive blending (screen) as a normal alpha blend
            new_data.append((r, g, b, alpha))
    
    img.putdata(new_data)
    img.save(img_path)
    print("Processed", img_path)
