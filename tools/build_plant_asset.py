import sys
import os
import json
import time
from PIL import Image

def build_asset(input_path, plant_name, variant_id, output_dir, project_root, display_name=None):
    img = Image.open(input_path).convert("RGBA")
    width, height = img.size
    
    print(f"Original size: {width}x{height}. Finding bounding box...")
    
    # Auto-detect background type by checking corners
    corners = [(0,0), (width-1,0), (0,height-1), (width-1,height-1)]
    white_corners = sum(1 for cx, cy in corners if min(img.getpixel((cx, cy))[:3]) > 200)
    is_white_bg = white_corners >= 2
    print(f"Background detected as: {'WHITE' if is_white_bg else 'BLACK'}")
    
    # 1. Find bounding box of non-background pixels
    left, top, right, bottom = width, height, 0, 0
    data = img.getdata()
    
    for y in range(height):
        for x in range(width):
            r, g, b, a = data[y * width + x]
            
            if is_white_bg:
                bg_dist = 255 - min(r, g, b)
            else:
                bg_dist = max(r, g, b)
                
            if bg_dist > 15: # Not pure background
                if x < left: left = x
                if x > right: right = x
                if y < top: top = y
                if y > bottom: bottom = y
                
    if left > right or top > bottom:
        print("Error: Image is completely blank or empty.")
        return False
        
    # Crop the image
    box = (left, top, right + 1, bottom + 1)
    cropped_img = img.crop(box)
    print(f"Cropped to: {cropped_img.size}")
    
    # Scale down if too large (game isometric assets usually < 300px height)
    max_height = 250
    if cropped_img.height > max_height:
        ratio = max_height / cropped_img.height
        new_width = int(cropped_img.width * ratio)
        cropped_img = cropped_img.resize((new_width, max_height), Image.Resampling.LANCZOS)
        print(f"Scaled to: {cropped_img.size}")
        
    c_width, c_height = cropped_img.size
    
    # 2. Split layers
    print("Splitting channels...")
    red_layer = []
    green_layer = []
    blue_layer = []
    
    has_blue = False
    
    c_data = list(cropped_img.getdata())
    for r, g, b, a in c_data:
        if is_white_bg:
            v_alpha = 255 - min(r, g, b)
        else:
            v_alpha = max(r, g, b)
        
        # Smooth alpha blending for edges
        if v_alpha < 15:
            alpha = 0
        elif v_alpha < 40:
            alpha = int((v_alpha - 15) / 25.0 * 255)
        else:
            alpha = 255
            
        if alpha == 0:
            red_layer.append((0, 0, 0, 0))
            green_layer.append((0, 0, 0, 0))
            blue_layer.append((0, 0, 0, 0))
            continue
            
        # Preserve original true color with edge de-matting
        if is_white_bg and 0 < alpha < 255:
            a_norm = alpha / 255.0
            r_un = max(0, min(255, int((r - 255 * (1 - a_norm)) / a_norm)))
            g_un = max(0, min(255, int((g - 255 * (1 - a_norm)) / a_norm)))
            b_un = max(0, min(255, int((b - 255 * (1 - a_norm)) / a_norm)))
            pixel = (r_un, g_un, b_un, alpha)
        elif (not is_white_bg) and 0 < alpha < 255:
            a_norm = alpha / 255.0
            r_un = max(0, min(255, int(r / a_norm)))
            g_un = max(0, min(255, int(g / a_norm)))
            b_un = max(0, min(255, int(b / a_norm)))
            pixel = (r_un, g_un, b_un, alpha)
        else:
            pixel = (r, g, b, alpha)
        
        # Determine dominant channel based on original RGB hue
        if r > g + 15 and r > b + 15:
            red_layer.append(pixel)
            green_layer.append((0, 0, 0, 0))
            blue_layer.append((0, 0, 0, 0))
        elif g > r + 15 and g > b + 15:
            red_layer.append((0, 0, 0, 0))
            green_layer.append(pixel)
            blue_layer.append((0, 0, 0, 0))
        elif b > r + 15 and b > g + 15:
            red_layer.append((0, 0, 0, 0))
            green_layer.append((0, 0, 0, 0))
            blue_layer.append(pixel)
            has_blue = True
        else:
            if r >= g and r >= b:
                red_layer.append(pixel)
                green_layer.append((0, 0, 0, 0))
                blue_layer.append((0, 0, 0, 0))
            elif g >= r and g >= b:
                red_layer.append((0, 0, 0, 0))
                green_layer.append(pixel)
                blue_layer.append((0, 0, 0, 0))
            else:
                red_layer.append((0, 0, 0, 0))
                green_layer.append((0, 0, 0, 0))
                blue_layer.append(pixel)
                has_blue = True
                
    # Save layers
    os.makedirs(output_dir, exist_ok=True)
    
    img_red = Image.new("RGBA", cropped_img.size)
    img_red.putdata(red_layer)
    out_red = os.path.join(output_dir, "layer_red.png")
    img_red.save(out_red)
    
    img_green = Image.new("RGBA", cropped_img.size)
    img_green.putdata(green_layer)
    out_green = os.path.join(output_dir, "layer_green.png")
    img_green.save(out_green)
    
    layers_created = ["layer-red", "layer-green"]
    
    if has_blue:
        img_blue = Image.new("RGBA", cropped_img.size)
        img_blue.putdata(blue_layer)
        out_blue = os.path.join(output_dir, "layer_blue.png")
        img_blue.save(out_blue)
        layers_created.append("layer-blue")
        
    # Write metadata
    metadata = {
        "name": plant_name,
        "display_name": display_name or plant_name,
        "variant_id": variant_id,
        "width": c_width,
        "height": c_height,
        "layers": layers_created,
        "source_image": os.path.basename(input_path),
        "true_color": True,
        "created_at": int(time.time())
    }
    
    with open(os.path.join(output_dir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=4)
        
    # Generate CSS & auto-inject
    css_w = c_width // 2
    css_h = c_height // 2
    
    css_class = f"plant-{plant_name}-v{variant_id}"
    
    css_code = f"\n/* CSS Output for {plant_name} ({display_name or plant_name}, Variant: {variant_id}) */\n"
    css_code += f".plant-sprite.{css_class} {{ width: {css_w}px; height: {css_h}px; }}\n"
    css_code += f".plant-sprite.{css_class} .layer-red {{ background-image: url('assets/plants/{plant_name}/{variant_id}/layer_red.png'); }}\n"
    css_code += f".plant-sprite.{css_class} .layer-green {{ background-image: url('assets/plants/{plant_name}/{variant_id}/layer_green.png'); }}\n"
    if has_blue:
        css_code += f".plant-sprite.{css_class} .layer-blue {{ background-image: url('assets/plants/{plant_name}/{variant_id}/layer_blue.png'); }}\n"
        
    print("Auto-injecting CSS into src/styles.css...")
    css_path = os.path.join(project_root, "src", "styles.css")
    if os.path.exists(css_path):
        import re
        with open(css_path, "r", encoding="utf-8") as f:
            css_content = f.read()
        pattern = r"\n*/\* CSS Output for " + re.escape(plant_name) + r" [^\n]*Variant: " + re.escape(variant_id) + r"\)[^\n]*\n(?:\.plant-sprite\.plant-" + re.escape(plant_name) + r"-v" + re.escape(variant_id) + r"[^\n]*\n*)+"
        if re.search(pattern, css_content):
            new_css = re.sub(pattern, css_code, css_content)
        else:
            new_css = css_content + css_code
        with open(css_path, "w", encoding="utf-8") as f:
            f.write(new_css)
        print("CSS injected successfully!")
    else:
        print(f"Warning: styles.css not found at {css_path}")
        
    return True

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: uv run python build_plant_asset.py <input_img> <plant_name> [variant_id] [display_name]")
        sys.exit(1)
        
    input_file = sys.argv[1]
    name = sys.argv[2]
    
    # Generate variant ID if not provided
    if len(sys.argv) >= 4 and sys.argv[3] and sys.argv[3].lower() != "auto":
        variant_id = sys.argv[3]
    else:
        variant_id = str(int(time.time()))
        
    display_name = sys.argv[4] if len(sys.argv) >= 5 and sys.argv[4] else name

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(project_root, "src", "assets", "plants", name, variant_id)
    
    if build_asset(input_file, name, variant_id, out_dir, project_root, display_name):
        print(f"Asset '{name}' (Display: '{display_name}', Variant {variant_id}) built successfully in {out_dir}!")
    else:
        print(f"Failed to build asset '{name}'.")
        sys.exit(1)
