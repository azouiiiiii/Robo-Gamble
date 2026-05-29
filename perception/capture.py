import os
import cv2
import pyautogui
import numpy as np
import easyocr
from PIL import Image

_reader = None

def _get_reader():
    global _reader
    if _reader is None:
        
        _reader = easyocr.Reader(['en'], gpu=False, verbose=False)
    return _reader


class CardCapturer:
    def __init__(self, config, executor):
        self.cfg = config
        self.executor = executor
        self.reg_hand = "regions.hand"
        self.reg_public = "regions.public"
        self.reg_pot = "regions.pot"
        self.reg_raise = "regions.raise_amount"
        self.reg_chips = "regions.my_chips"
        self.reg_call = "regions.call_amount"

        # 加载筹码图标模板（用于从数字区域中抹除图标）
        self._icon_tmpl = None
        if os.path.exists("icon.png"):
            self._icon_tmpl = cv2.imread("icon.png", 0)

        # call 区域图标通常更大更清晰，用独立模板
        self._icon_call_tmpl = None
        if os.path.exists("icon_call.png"):
            self._icon_call_tmpl = cv2.imread("icon_call.png", 0)

        # 加载点数模板
        self._rank_templates = {}
        _rank_map = {"a": "A", "k": "K", "q": "Q", "j": "J", "10": "T",
                     "9": "9", "8": "8", "7": "7", "6": "6", "5": "5",
                     "4": "4", "3": "3", "2": "2"}
        _tmpl_dir = "rank_template"
        if os.path.isdir(_tmpl_dir):
            for fname in os.listdir(_tmpl_dir):
                if fname.endswith(".png"):
                    name = fname.rsplit(".", 1)[0]
                    if name in _rank_map:
                        tmpl = cv2.imread(os.path.join(_tmpl_dir, fname), 0)
                        if tmpl is not None:
                            self._rank_templates[_rank_map[name]] = tmpl

    # ── 截图入口 ────────────────────────────────

    def capture_hand(self):
        region = self.cfg.get_coord(self.reg_hand)
        pre_suits = {}
        def _capture():
            img = pyautogui.screenshot(region=region)
            for i in range(1, 3):
                x, y = self.cfg.get_coord(f"signals.hand_suit_{i}")
                r, g, b = pyautogui.pixel(int(x), int(y))
                pre_suits[i] = self._match_suit(r, g, b)
            return img
        screenshot = self.executor.hold_c_for_capture(_capture)
        return self._process_to_cards(screenshot, card_region=region, num_cards=2,
                                      suit_prefix="hand_suit", pre_captured_suits=pre_suits)

    def capture_public(self):
        visible = self.count_public_cards()
        if visible == 0:
            return []
        region = self.cfg.get_coord(self.reg_public)
        screenshot = pyautogui.screenshot(region=region)
        active = set(range(visible))
        return self._process_to_cards(screenshot, card_region=region, num_cards=5,
                                      suit_prefix="public_suit", active_slots=active)

    # ── 数字 OCR ──────────────────────────────

    def capture_pot(self):
        region = self.cfg.get_coord(self.reg_pot)
        img = pyautogui.screenshot(region=region)
        img = self._remove_icon(img)
        return self._ocr_to_int(img)

    def capture_raise_amount(self):
        region = self.cfg.get_coord(self.reg_raise)
        img = pyautogui.screenshot(region=region)
        img = self._remove_icon(img, tmpl=self._icon_call_tmpl, precise=True)
        self._debug_save(img, "raise")
        return self._ocr_digits(img)

    def capture_my_chips(self):
        region = self.cfg.get_coord(self.reg_chips)
        img = pyautogui.screenshot(region=region)
        img = self._remove_icon(img)
        return self._ocr_to_int(img)

    def _debug_save(self, img, tag):
        import time
        _dir = "debug"
        os.makedirs(_dir, exist_ok=True)
        ts = int(time.time() * 1000)
        img.save(os.path.join(_dir, f"{tag}_{ts}.png"))

    def capture_call_amount(self):
        region = self.cfg.get_coord(self.reg_call)
        img = pyautogui.screenshot(region=region)
        img = self._remove_icon(img, tmpl=self._icon_call_tmpl)
        return self._ocr_to_int(img)

    def _remove_icon(self, pil_img, tmpl=None, precise=False):
        """抹除筹码图标：模板匹配定位 → 失败时 precise 模式不抹黑"""
        if tmpl is None:
            tmpl = self._icon_tmpl
        if tmpl is None:
            return pil_img

        gray = np.array(pil_img.convert("L"))
        th, tw = tmpl.shape

        if th <= gray.shape[0] and tw <= gray.shape[1]:
            result = cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if max_val >= 0.5:
                x, y = max_loc
                if precise:
                    # 只涂图标区域，留 2px 边距
                    y1, y2 = max(0, y - 2), min(gray.shape[0], y + th + 2)
                    x1, x2 = max(0, x - 2), min(gray.shape[1], x + tw + 2)
                    gray[y1:y2, x1:x2] = 0
                else:
                    gray[0:gray.shape[0], 0:x + tw + 2] = 0
                return Image.fromarray(gray)

        # precise 模式不盲目抹黑（digit-only OCR 能忽略图标）
        if precise:
            return pil_img

        # 非 precise：按图标宽度抹黑左侧
        crop_x = min(tw + 2, gray.shape[1] - 1)
        gray[:, 0:crop_x] = 0
        return Image.fromarray(gray)

    def _ocr_digits(self, pil_img):
        """针对大区域（弹窗等）的纯数字识别：提取所有数字子串，抗图标K噪音"""
        import re
        arr = np.array(pil_img.convert("L"))
        arr = cv2.resize(arr, (arr.shape[1] * 4, arr.shape[0] * 4))
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(arr)
        reader = _get_reader()
        # allowlist 含数字、点、k，图标常被误读成 K 字母，通过正则过滤
        results = reader.readtext(enhanced, detail=0, allowlist="0123456789.kK")
        text = "".join(results).strip().lower()
        # 提取所有合法数字子串，\b 确保 k 后缀后没有紧跟数字（排除 8k020 误读为 8k）
        matches = re.findall(r'\d+(?:\.\d+)?k?\b', text)
        best = 0
        for m in matches:
            try:
                if m.endswith("k"):
                    val = int(float(m[:-1]) * 1000)
                elif "." in m:
                    val = int(float(m))
                else:
                    val = int(m)
                if val > best:
                    best = val
            except ValueError:
                continue
        if best > 0:
            return best
        # 回退到通用 OCR
        return self._ocr_to_int(pil_img)

    def _ocr_to_int(self, pil_img):
        # 两次 OCR：CLAHE 增强 + 二值化，取结果更长的（对抗逗号打断）
        text1 = self._ocr_text(pil_img)           # CLAHE
        text2 = self._ocr_text(pil_img, binarize=True)  # OTSU 二值化
        text = text1 if len(text1) >= len(text2) else text2
        # 只保留数字、点、k
        clean = "".join(c for c in text if c in "0123456789.kK")
        clean = clean.strip().lower()
        try:
            if clean.endswith("k"):
                return int(float(clean[:-1]) * 1000)
            if "." in clean:
                return int(float(clean))
            return int(clean) if clean else 0
        except ValueError:
            return 0

    def _ocr_text(self, pil_img, allow_chars=None, binarize=False):
        arr = np.array(pil_img)
        h, w = arr.shape[:2]
        if h < 60 or w < 200:
            arr = cv2.resize(arr, (w * 4, h * 4), interpolation=cv2.INTER_CUBIC)
        if len(arr.shape) == 3:
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        else:
            gray = arr
        if binarize:
            _, arr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        else:
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            arr = clahe.apply(gray)
        results = _get_reader().readtext(arr, detail=0)
        text = "".join(results).strip()
        if allow_chars:
            text = "".join(c for c in text if c in allow_chars)
        return text

    # ── 牌面识别 ──────────────────────────────

    def _process_to_cards(self, pil_img, card_region, num_cards, suit_prefix="",
                          active_slots=None, pre_captured_suits=None):
        """
        card_region: (x, y, w, h) 屏幕坐标，用于定位精确 rank 区域。
        active_slots: set of slot indices to include. None = all.
        pre_captured_suits: {index: suit_char} 手牌在按C期间预抓的花色。
        """
        if pil_img is None or num_cards == 0:
            return []

        rx0, ry0 = card_region[0], card_region[1]
        base = suit_prefix.replace("_suit", "")  # "hand" or "public"
        cards = []

        for i in range(num_cards):
            if active_slots is not None and i not in active_slots:
                continue

            # 花色：手牌用按C期间预抓结果，公共牌实时读像素
            idx = i + 1
            if pre_captured_suits and idx in pre_captured_suits:
                suit = pre_captured_suits[idx]
            else:
                suit = self._get_suit_from_pixel(f"{suit_prefix}_{idx}")

            # 点数：用用户框选的精确 rank 区域，从截图中裁剪
            rank_abs = self.cfg.get_coord(f"regions.{base}_rank_{i+1}")
            rx = rank_abs[0] - rx0
            ry = rank_abs[1] - ry0
            rw, rh = rank_abs[2], rank_abs[3]
            rank_crop = pil_img.crop((rx, ry, rx + rw, ry + rh))
            rank = self._ocr_rank(rank_crop)

            result = f"{rank}{suit}" if rank else f"?{suit}"
            cards.append(result)

        return cards

    def count_public_cards(self):
        """用 5 个固定点色匹配统计公共牌数量"""
        ref = self.cfg.get_color("card_face_rgb")
        count = 0
        for i in range(1, 6):
            x, y = self.cfg.get_coord(f"signals.public_card_{i}")
            pixel = pyautogui.pixel(int(x), int(y))
            dist = sum((pixel[j] - ref[j]) ** 2 for j in range(3))
            if dist < 2500:
                count += 1
        return count

    def _get_suit_from_pixel(self, signal_key):
        """读取绝对坐标的像素，匹配最近花色"""
        x, y = self.cfg.get_coord(f"signals.{signal_key}")
        r, g, b = pyautogui.pixel(int(x), int(y))
        return self._match_suit(r, g, b)

    def _match_suit(self, r, g, b):
        suits = {
            "h": self.cfg.get_color("heart_rgb"),
            "d": self.cfg.get_color("diamond_rgb"),
            "s": self.cfg.get_color("spade_rgb"),
            "c": self.cfg.get_color("club_rgb"),
        }
        best, best_dist = "?", float("inf")
        for suit, (sr, sg, sb) in suits.items():
            dist = (r - sr) ** 2 + (g - sg) ** 2 + (b - sb) ** 2
            if dist < best_dist:
                best_dist = dist
                best = suit
        return best

    def _ocr_rank(self, pil_img):
        """EasyOCR 为主（CLAHE + allowlist）→ 模板匹配 fallback"""
        gray = np.array(pil_img.convert("L"))

        # 尝试 1: EasyOCR + CLAHE + allowlist（1098765432 兼容 "10"）
        gray4 = cv2.resize(gray, (gray.shape[1] * 4, gray.shape[0] * 4))
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray4)
        reader = _get_reader()
        results = reader.readtext(enhanced, detail=0, allowlist="AKQJ1098765432")
        text = "".join(results).strip().upper()
        rank = self._extract_rank(text)
        if rank:
            return rank

        # 尝试 2: EasyOCR 无 allowlist（应对字体差异）
        results = reader.readtext(enhanced, detail=0)
        text = "".join(results).strip().upper()
        rank = self._extract_rank(text)
        if rank:
            return rank

        # 尝试 3: 模板滑动匹配（截图不够大时补白边，不跳过）
        if self._rank_templates:
            best_rank = "?"
            best_score = 0
            gh, gw = gray.shape
            for rank_char, tmpl in self._rank_templates.items():
                th, tw = tmpl.shape
                pad_h, pad_w = max(0, th - gh), max(0, tw - gw)
                canvas = cv2.copyMakeBorder(gray, 0, pad_h, 0, pad_w,
                                            cv2.BORDER_CONSTANT, value=255) if pad_h or pad_w else gray
                result = cv2.matchTemplate(canvas, tmpl, cv2.TM_CCOEFF_NORMED)
                _, score, _, _ = cv2.minMaxLoc(result)
                if score > best_score:
                    best_score = score
                    best_rank = rank_char
            if best_score >= 0.8 and best_rank != "?":
                return best_rank
            self._debug_save(pil_img, f"rank_{best_rank}_{best_score:.2f}")

        return ""

    def _extract_rank(self, text):
        valid = set("AKQJT98765432")
        for ch in text:
            if ch in valid:
                return ch
        # EasyOCR 常见误读映射（Q 的圆形 → 0/O，T 的直笔 → 1，10 → T）
        if "10" in text:
            return "T"
        if "0" in text or "O" in text:
            return "Q"
        if "1" in text:
            return "T"
        return ""

    def get_readable_cards(self, cards_list):
        if not cards_list:
            return "未知"
        return ", ".join(cards_list)
