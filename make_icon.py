"""Generuje icon.png (ikona napismaker): klatka filmowa z paskami napisów."""
from PIL import Image, ImageDraw

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((0, 0, S, S), 200, fill=(24, 32, 56))
# perforacje taśmy filmowej
for x in range(88, 88 + 8 * 112, 112):
    for y in (36, S - 36 - 52):
        d.rounded_rectangle((x, y, x + 64, y + 52), 14, fill=(70, 84, 120))
# ekran
d.rounded_rectangle((70, 130, S - 70, S - 130), 60, fill=(48, 110, 190))
d.polygon([(70, S - 130 - 330), (S - 70, S - 130 - 450), (S - 70, S - 130), (70, S - 130)], fill=(36, 88, 160))
d.ellipse((650, 190, 800, 340), fill=(255, 214, 80))  # słońce
# trójkąt "play" w tle
d.polygon([(400, 270), (400, 500), (600, 385)], fill=(255, 255, 255, 70))
# napisy
d.rounded_rectangle((170, 600, S - 170, 690), 30, fill=(0, 0, 0, 200))
d.rounded_rectangle((220, 622, S - 220, 668), 22, fill=(255, 255, 255))
d.rounded_rectangle((300, 715, S - 300, 790), 30, fill=(0, 0, 0, 200))
d.rounded_rectangle((345, 735, S - 345, 770), 18, fill=(255, 214, 80))
img.resize((512, 512), Image.LANCZOS).save("icon.png")
