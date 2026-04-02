import cv2
import numpy as np
from mss import mss

# 强制清理可能残留在内存中的旧窗口
cv2.destroyAllWindows() 

# 显式指定窗口模式
cv2.namedWindow("Detection Test", cv2.WINDOW_NORMAL) 
# 把它挪到一个固定位置，防止它乱跳
cv2.moveWindow("Detection Test", 100, 100) 

with mss() as sct:
    monitor = {"top": 0, "left": 0, "width": 400, "height": 400}
    
    while True:
        img = np.array(sct.grab(monitor))
        frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        
        cv2.imshow("Detection Test", frame)
        
        # 增加等待时间到 20ms，给系统 UI 线程足够的喘息时间
        if cv2.waitKey(20) & 0xFF == ord('q'):
            break

cv2.destroyAllWindows()