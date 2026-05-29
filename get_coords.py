"""截图取色 + 取坐标工具。鼠标悬停实时显示 RGB，点击记录。"""
import cv2
import os

SCREENSHOTS = {
    "2": {"file": "action.png",     "label": "操作"},
    "3": {"file": "settlement.png", "label": "结算"},
    "4": {"file": "peek.png",       "label": "看牌"},
}

TASKS = [
    # # --- 点数精确框选（已完成）---
    # {"label": "regions.hand_rank_1",     "type": "box", "state": "看牌"},
    # {"label": "regions.hand_rank_2",     "type": "box", "state": "看牌"},
    # {"label": "regions.public_rank_1",   "type": "box", "state": "操作"},
    # {"label": "regions.public_rank_2",   "type": "box", "state": "操作"},
    # {"label": "regions.public_rank_3",   "type": "box", "state": "操作"},
    # {"label": "regions.public_rank_4",   "type": "box", "state": "操作"},
    # {"label": "regions.public_rank_5",   "type": "box", "state": "操作"},
    # --- 筹码区域重框 ---
    # {"label": "regions.my_chips",        "type": "box", "state": "操作"},
    # {"label": "regions.call_amount",     "type": "box", "state": "操作"},
    # {"label": "regions.pot",             "type": "box", "state": "操作"},
    # --- 滑块终点 ---
    {"label": "signals.raise_slider_end","type": "point","state": "操作"},
]

images = {}
for key, info in SCREENSHOTS.items():
    path = info["file"]
    if os.path.exists(path):
        images[key] = cv2.imread(path)
        print(f"[{key}] 已加载: {path} ({info['label']})")
    else:
        print(f"[{key}] 未找到: {path}，跳过")

if not images:
    print("\n请把截图放到项目目录。默认文件名：")
    for k, v in SCREENSHOTS.items():
        print(f"  {v['file']} — {v['label']}")
    exit(1)

results = {}
current_task = 0
box_start = None
mouse_x, mouse_y = 0, 0

def find_state_key(label):
    for k, v in SCREENSHOTS.items():
        if v["label"] == label:
            return k
    return None

# 检查任务所需截图是否齐全
missing = set()
for t in TASKS:
    key = find_state_key(t["state"])
    if key and key not in images:
        missing.add(t["state"])
if missing:
    print(f"\n⚠ 缺少截图，以下状态的图片未找到: {missing}")
    print("  请补上对应截图后重新运行")

def switch_to_task(idx):
    global current_state
    if idx >= len(TASKS):
        return
    task = TASKS[idx]
    key = find_state_key(task["state"])
    if key and key in images:
        current_state = key

current_state = find_state_key(TASKS[0]["state"])
if current_state not in images:
    current_state = list(images.keys())[0]

def draw_ui(img):
    out = img.copy()
    h, w = out.shape[:2]


    # ── 顶部状态栏 ──
    overlay = out.copy()
    cv2.rectangle(overlay, (0, 0), (w, 55), (0, 0, 0), -1)
    out = cv2.addWeighted(overlay, 0.5, out, 0.5, 0)

    if current_task < len(TASKS):
        t = TASKS[current_task]
        type_names = {"point": "点选", "box": "框选(点两下)", "color": "取色(点击记录RGB)"}
        info = f"第{current_task+1}/{len(TASKS)}: {t['label']} [{type_names[t['type']]}] @ {t['state']}"
    else:
        info = "全部完成! 按 Q 退出"

    cv2.putText(out, info, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)

    done = sum(1 for r in results.values() if r is not None)
    cv2.putText(out, f"完成: {done}/{len(TASKS)}", (w - 200, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

    # ── RGB 实时显示 ──
    if 0 <= mouse_y < h and 0 <= mouse_x < w:
        b, g, r = img[mouse_y, mouse_x].tolist()
        rgb_text = f"RGB: ({r}, {g}, {b})"

        # 鼠标旁色块
        block_size = 20
        bx, by = mouse_x + 15, mouse_y + 15
        if bx + 180 > w:
            bx = mouse_x - 180
        if by + block_size > h:
            by = mouse_y - 25

        cv2.rectangle(out, (bx, by), (bx + block_size, by + block_size), (r, g, b), -1)
        cv2.rectangle(out, (bx, by), (bx + block_size, by + block_size), (255, 255, 255), 1)
        cv2.putText(out, rgb_text, (bx + 28, by + 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 2)

        # 十字准星
        cv2.line(out, (mouse_x - 8, mouse_y), (mouse_x + 8, mouse_y), (0, 255, 255), 1)
        cv2.line(out, (mouse_x, mouse_y - 8), (mouse_x, mouse_y + 8), (0, 255, 255), 1)

    # ── 框选起点 ──
    if box_start is not None and current_task < len(TASKS) and TASKS[current_task]["type"] == "box":
        cv2.circle(out, box_start, 4, (0, 0, 255), -1)

    return out

def on_mouse(event, x, y, flags, param):
    global mouse_x, mouse_y, current_task, box_start

    mouse_x, mouse_y = x, y

    if event != cv2.EVENT_LBUTTONDOWN:
        return
    if current_task >= len(TASKS):
        return

    task = TASKS[current_task]
    label = task["label"]
    ttype = task["type"]

    if ttype == "box":
        if box_start is None:
            box_start = (x, y)
            print(f"  [{label}] 左上: ({x}, {y})，请点右下角...")
        else:
            x1, y1 = box_start
            bw, bh = x - x1, y - y1
            if bw <= 0 or bh <= 0:
                print(f"  ! 宽高必须为正 (w={bw}, h={bh})，重新框选")
                box_start = None
                return
            results[label] = [x1, y1, bw, bh]
            print(f"  [{label}] = [{x1}, {y1}, {bw}, {bh}]  ✓")
            box_start = None
            current_task += 1
            switch_to_task(current_task)

    elif ttype == "color":
        b, g, r = images[current_state][y, x].tolist()
        results[label] = [r, g, b]
        print(f"  [{label}] RGB = [{r}, {g}, {b}]  ✓")
        current_task += 1
        switch_to_task(current_task)

    else:  # point
        results[label] = [x, y]
        print(f"  [{label}] = [{x}, {y}]  ✓")
        current_task += 1
        switch_to_task(current_task)

cv2.namedWindow("get_coords", cv2.WINDOW_NORMAL)
cv2.setMouseCallback("get_coords", on_mouse)

print(f"\n一共 {len(TASKS)} 个框选任务：")
for i, t in enumerate(TASKS):
    print(f"  {i+1}. [{t['state']}] {t['label']}")
print("\n操作: 鼠标悬停看RGB | 点两下框选区域 | Z=回退 | Q=退出")
print("注意: 前2个任务需要 peek.png (按住C键看手牌的截图)")
print(f"\n>>> 当前: {TASKS[0]['label']} @ {TASKS[0]['state']} <<<")

while True:
    frame = draw_ui(images[current_state])
    cv2.imshow("get_coords", frame)
    key = cv2.waitKey(20) & 0xFF

    if key == ord('q'):
        break
    elif key == ord('z'):
        if current_task > 0:
            current_task -= 1
            prev_label = TASKS[current_task]["label"]
            if prev_label in results:
                del results[prev_label]
            box_start = None
            switch_to_task(current_task)
            print(f"<- 回退: {TASKS[current_task]['label']}")
    try:
        k = chr(key)
        if k in images:
            current_state = k
            print(f"-> 手动切换: {SCREENSHOTS[current_state]['label']}")
    except ValueError:
        pass

cv2.destroyAllWindows()

# 输出汇总
print("\n" + "=" * 60)
print("框选结果（填入 config.json → coords → regions）")
print("=" * 60)

for task in TASKS:
    label = task["label"]
    val = results.get(label)
    if val:
        print(f'  "{label}": {val},')
    else:
        print(f'  "{label}": ✗ 未获取')
