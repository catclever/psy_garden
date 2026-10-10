import sys
from PIL import Image
import os

def split_rgb(input_path, output_red, output_green, output_blue):
    img = Image.open(input_path).convert("RGBA")
    data = list(img.getdata())
    
    red_layer = []
    green_layer = []
    blue_layer = []
    
    for r, g, b, a in data:
        v = max(r, g, b)
        
        # 更平滑的抠图边缘，保留内部的绝对不透明度
        if v < 15:
            alpha = 0
        elif v < 40:
            alpha = int((v - 15) / 25.0 * 255)
        else:
            alpha = 255
            
        if alpha == 0:
            red_layer.append((0, 0, 0, 0))
            green_layer.append((0, 0, 0, 0))
            blue_layer.append((0, 0, 0, 0))
            continue
            
        # Determine dominant channel
        if r > g + 15 and r > b + 15:
            red_layer.append((v, v, v, alpha))
            green_layer.append((0, 0, 0, 0))
            blue_layer.append((0, 0, 0, 0))
        elif g > r + 15 and g > b + 15:
            red_layer.append((0, 0, 0, 0))
            green_layer.append((v, v, v, alpha))
            blue_layer.append((0, 0, 0, 0))
        elif b > r + 15 and b > g + 15:
            red_layer.append((0, 0, 0, 0))
            green_layer.append((0, 0, 0, 0))
            blue_layer.append((v, v, v, alpha))
        else:
            # Tie breaker
            if r >= g and r >= b:
                red_layer.append((v, v, v, alpha))
                green_layer.append((0, 0, 0, 0))
                blue_layer.append((0, 0, 0, 0))
            elif g >= r and g >= b:
                red_layer.append((0, 0, 0, 0))
                green_layer.append((v, v, v, alpha))
                blue_layer.append((0, 0, 0, 0))
            else:
                red_layer.append((0, 0, 0, 0))
                green_layer.append((0, 0, 0, 0))
                blue_layer.append((v, v, v, alpha))
                
    img_red = Image.new("RGBA", img.size)
    img_red.putdata(red_layer)
    img_red.save(output_red)
    
    img_green = Image.new("RGBA", img.size)
    img_green.putdata(green_layer)
    img_green.save(output_green)

    img_blue = Image.new("RGBA", img.size)
    img_blue.putdata(blue_layer)
    img_blue.save(output_blue)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python split_rgb_layers.py <input_img>")
        sys.exit(1)
        
    input_file = sys.argv[1]
    base = os.path.splitext(input_file)[0]
    out_red = f"{base}_red.png"
    out_green = f"{base}_green.png"
    out_blue = f"{base}_blue.png"
    
    split_rgb(input_file, out_red, out_green, out_blue)
    print(f"Split completed! Saved {out_red}, {out_green}, {out_blue}")
