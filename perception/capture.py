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

    # ── 截图入口 ────────────────────────────────

    def capture_hand(self):
        def _get_screenshot():
            region = self.cfg.get_coord(self.reg_hand)
            return pyautogui.screenshot(region=region)

        screenshot = self.executor.hold_c_for_capture(_get_screenshot)
        self._counter += 1
        _save_debug(f"hand_raw_{self._counter}.png", screenshot)
        return self._process_to_cards(screenshot, num_cards=2, tag=f"hand_{self._counter}", suit_prefix="hand_suit")

    def capture_public(self):
        region = self.cfg.get_coord(self.reg_public)
        screenshot = pyautogui.screenshot(region=region)
        _save_debug(f"public_raw_{self._counter}.png", screenshot)
        return self._process_to_cards(screenshot, num_cards=5, tag=f"public_{self._counter}", suit_prefix="public_suit")

    # ── 数字 OCR ──────────────────────────────

    def capture_pot(self):
        region = self.cfg.get_coord(self.reg_pot)
        img = pyautogui.screenshot(region=region)
        return self._ocr_to_int(img)

    def capture_raise_amount(self):
        region = self.cfg.get_coord(self.reg_raise)
        img = pyautogui.screenshot(region=region)
        return self._ocr_to_int(img)

    def capture_my_chips(self):
        region = self.cfg.get_coord(self.reg_chips)
        img = pyautogui.screenshot(region=region)
        return self._ocr_to_int(img)

    def capture_call_amount(self):
        region = self.cfg.get_coord(self.reg_call)
        img = pyautogui.screenshot(region=region)
        return self._ocr_to_int(img)

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

    def _process_to_cards(self, pil_img, num_cards, tag="", suit_prefix=""):
        if pil_img is None or num_cards == 0:
            return []

        w, h = pil_img.size
        card_w = w // num_cards
        cards = []

        for i in range(num_cards):
            # 花色：绝对像素点匹配
            suit = self._get_suit_from_pixel(f"{suit_prefix}_{i+1}")

            # 点数：裁剪左上角 OCR
            left = i * card_w
            margin = card_w // 10
            crop = pil_img.crop((left + margin, 0, min(left + card_w - margin, w), h))
            rank_crop = crop.crop((0, 0, card_w // 2, h // 2))
            _save_debug(f"{tag}_slot{i}.png", crop)
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
        gray = pil_img.convert("L")
        # 放大 4 倍
        gray = gray.resize((gray.width * 4, gray.height * 4), Image.LANCZOS)
        arr = np.array(gray)

        # 自适应二值化
        thresh = cv2.adaptiveThreshold(
            arr, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 15, 4
        )

        _save_debug(f"rank_thresh_{self._counter}.png", thresh)

        results = _get_reader().readtext(thresh, detail=0)
        text = "".join(results).strip().upper()

        valid = set("AKQJT9876543210")
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
