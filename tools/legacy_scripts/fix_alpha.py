from PIL import Image
import glob

for img_path in glob.glob("plant_*.png"):
    img = Image.open(img_path).convert("RGBA")
    data = list(img.getdata())
    new_data = []
    for r, g, b, a in data:
        alpha = max(r, g, b)
        # 增加阈值：过滤掉 AI 产出图中的暗部噪点。否则极其微弱的噪点会被解算成纯白像素，在 CSS saturate 时爆仓成实心方块
        if alpha < 25:
            new_data.append((0, 0, 0, 0))
        else:
            # properly unmultiply the color by the calculated alpha
            r_un = min(255, int((r / alpha) * 255))
            g_un = min(255, int((g / alpha) * 255))
            b_un = min(255, int((b / alpha) * 255))
            new_data.append((r_un, g_un, b_un, alpha))
            
    img.putdata(new_data)
    img.save(img_path)
    print("Processed", img_path)
