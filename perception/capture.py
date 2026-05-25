import os
import cv2
import pyautogui
import numpy as np
import easyocr
from PIL import Image

_reader = None
_debug = True  # 设为 False 关闭调试截图
_debug_dir = "debug"


def _get_reader():
    global _reader
    if _reader is None:
        
        _reader = easyocr.Reader(['en'], gpu=False, verbose=False)
    return _reader


def _save_debug(name, img):
    if not _debug:
        return
    os.makedirs(_debug_dir, exist_ok=True)
    if isinstance(img, Image.Image):
        img.save(os.path.join(_debug_dir, name))
    else:
        Image.fromarray(img).save(os.path.join(_debug_dir, name))


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
        self._counter = 0  # 调试用

        # 加载筹码图标模板（用于从数字区域中抹除图标）
        self._icon_tmpl = None
        if os.path.exists("icon.png"):
            self._icon_tmpl = cv2.imread("icon.png", 0)

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
        screenshot = self.executor.hold_c_for_capture(lambda: pyautogui.screenshot(region=region))
        self._counter += 1
        _save_debug(f"hand_raw_{self._counter}.png", screenshot)
        return self._process_to_cards(screenshot, card_region=region, num_cards=2,
                                      tag=f"hand_{self._counter}", suit_prefix="hand_suit")

    def capture_public(self):
        visible = self.count_public_cards()
        if visible == 0:
            self._counter += 1
            return []
        region = self.cfg.get_coord(self.reg_public)
        screenshot = pyautogui.screenshot(region=region)
        self._counter += 1
        _save_debug(f"public_raw_{self._counter}.png", screenshot)
        active = set(range(visible))
        return self._process_to_cards(screenshot, card_region=region, num_cards=5,
                                      tag=f"public_{self._counter}", suit_prefix="public_suit",
                                      active_slots=active)

    # ── 数字 OCR ──────────────────────────────

    def capture_pot(self):
        region = self.cfg.get_coord(self.reg_pot)
        img = pyautogui.screenshot(region=region)
        img = self._remove_icon(img)
        return self._ocr_to_int(img)

    def capture_raise_amount(self):
        region = self.cfg.get_coord(self.reg_raise)
        img = pyautogui.screenshot(region=region)
        img = self._remove_icon(img)
        return self._ocr_to_int(img)

    def capture_my_chips(self):
        region = self.cfg.get_coord(self.reg_chips)
        img = pyautogui.screenshot(region=region)
        img = self._remove_icon(img)
        return self._ocr_to_int(img)

    def capture_call_amount(self):
        region = self.cfg.get_coord(self.reg_call)
        img = pyautogui.screenshot(region=region)
        img = self._remove_icon(img)
        return self._ocr_to_int(img)

    def _remove_icon(self, pil_img):
        """抹除左侧筹码图标：模板匹配定位 → 失败则按图标宽度固定裁切"""
        if self._icon_tmpl is None:
            return pil_img

        gray = np.array(pil_img.convert("L"))
        th, tw = self._icon_tmpl.shape

        if th <= gray.shape[0] and tw <= gray.shape[1]:
            result = cv2.matchTemplate(gray, self._icon_tmpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if max_val >= 0.6:
                x, y = max_loc
                # 抹除图标及其左侧区域
                bg_x = min(x + tw + 2, gray.shape[1] - 1)
                fill_val = int(gray[y + th // 2, bg_x])
                gray[0:gray.shape[0], 0:x + tw + 2] = fill_val
                return Image.fromarray(gray)

        # 模板匹配失败：按图标宽度固定裁掉左侧
        crop_x = min(tw + 2, gray.shape[1] - 1)
        fill_val = int(gray[0, -1])
        gray[:, 0:crop_x] = fill_val
        return Image.fromarray(gray)

    def _ocr_to_int(self, pil_img):
        text = self._ocr_text(pil_img, allow_chars="0123456789.kK")
        text = text.strip().replace(",", "").replace(" ", "").lower()
        try:
            if text.endswith("k"):
                return int(float(text[:-1]) * 1000)
            if "." in text:
                return int(float(text))
            return int(text)
        except ValueError:
            return 0

    def _ocr_text(self, pil_img, allow_chars=None):
        arr = np.array(pil_img)
        results = _get_reader().readtext(arr, detail=0)
        text = "".join(results).strip()
        if allow_chars:
            text = "".join(c for c in text if c in allow_chars)
        return text

    # ── 牌面识别 ──────────────────────────────

    def _process_to_cards(self, pil_img, card_region, num_cards, tag="", suit_prefix="",
                          active_slots=None):
        """
        card_region: (x, y, w, h) 屏幕坐标，用于定位精确 rank 区域。
        active_slots: set of slot indices to include. None = all.
        """
        if pil_img is None or num_cards == 0:
            return []

        rx0, ry0 = card_region[0], card_region[1]
        base = suit_prefix.replace("_suit", "")  # "hand" or "public"
        cards = []

        for i in range(num_cards):
            if active_slots is not None and i not in active_slots:
                continue

            # 花色：绝对像素点匹配（不变）
            suit = self._get_suit_from_pixel(f"{suit_prefix}_{i+1}")

            # 点数：用用户框选的精确 rank 区域，从截图中裁剪
            rank_abs = self.cfg.get_coord(f"regions.{base}_rank_{i+1}")
            rx = rank_abs[0] - rx0
            ry = rank_abs[1] - ry0
            rw, rh = rank_abs[2], rank_abs[3]
            rank_crop = pil_img.crop((rx, ry, rx + rw, ry + rh))
            _save_debug(f"{tag}_slot{i}_rank.png", rank_crop)
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
        """模板匹配（不缩放，滑动找最佳位置）→ 失败回退 EasyOCR"""
        gray = np.array(pil_img.convert("L"))

        # 尝试 1: 模板滑动匹配
        if self._rank_templates:
            best_rank = "?"
            best_score = 0
            for rank_char, tmpl in self._rank_templates.items():
                th, tw = tmpl.shape
                gh, gw = gray.shape
                if th > gh or tw > gw:
                    continue  # 模板比截图大，跳过
                result = cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED)
                _, score, _, _ = cv2.minMaxLoc(result)
                if score > best_score:
                    best_score = score
                    best_rank = rank_char
            if best_score >= 0.7 and best_rank != "?":
                return best_rank

        # 尝试 2: EasyOCR fallback
        gray4 = cv2.resize(gray, (gray.shape[1] * 4, gray.shape[0] * 4))
        reader = _get_reader()
        _, thresh = cv2.threshold(gray4, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        _save_debug(f"rank_thresh_{self._counter}.png", thresh)
        results = reader.readtext(thresh, detail=0)
        text = "".join(results).strip().upper()
        rank = self._extract_rank(text)
        if rank:
            return rank
        results = reader.readtext(gray4, detail=0)
        text = "".join(results).strip().upper()
        return self._extract_rank(text)

    def _extract_rank(self, text):
        valid = set("AKQJT98765432")
        for ch in text:
            if ch in valid:
                return ch
        if "1" in text or "0" in text:
            return "T"
        return ""

    def get_readable_cards(self, cards_list):
        if not cards_list:
            return "未知"
        return ", ".join(cards_list)
