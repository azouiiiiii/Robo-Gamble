import pyautogui
import time

while True:
    print("把鼠标移到目标位置，3秒后输出坐标...")
    time.sleep(3)
    print(f"当前坐标: {pyautogui.position()}")