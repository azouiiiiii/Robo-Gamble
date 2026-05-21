"""从 settlement.png 截取 round_winner 区域保存为模板，只跑一次。"""
import cv2
import os

img = cv2.imread("settlement.png")
if img is None:
    print("找不到 settlement.png，请确保截图在项目目录")
    exit(1)

# regions.round_winner = [37, 81, 374, 43]
x, y, w, h = 37, 81, 374, 43
template = img[y:y+h, x:x+w]
cv2.imwrite("templates/round_winner.png", template)
os.makedirs("templates", exist_ok=True)
cv2.imwrite("templates/round_winner.png", template)
print("templates/round_winner.png saved")
