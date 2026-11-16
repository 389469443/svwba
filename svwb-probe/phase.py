"""
阶段判定：battle / operate / other

判据（三张真实截图标定，全部 2560x1600）：

  ① 是否在「影之幻境」对局内 —— 看右上角「兵种」按钮区域的亮度
       主界面  (153,152,174) 亮  → 不在对局
       运营    ( 71, 69, 94) 暗  → 对局中
       战斗    ( 59, 33, 49) 暗  → 对局中

  ② 对局内是运营还是战斗 —— 看右侧圆形徽章的颜色
       运营  蓝占比 0.746 / 红 0.021  （蓝色「准备」）
       战斗  红占比 0.522 / 蓝 0.008  （红色「战斗中」）

  ③ 兔子模板（只有运营阶段才出现）作为运营的补充确认

历史教训：
  · 早期用「左侧 8 人栏红色血条比例 ≥ 0.025」判断是否在对局内：
    花园主题背景下只有 0.018，直接把运营阶段判成 other。
  · 后来改用「右侧徽章颜色」判断运营/战斗，但**没做"是否在对局内"的前置判断**：
    主界面右上角的红宝石被当成「战斗中」，悬浮层永远卡在"读取中"。
  · 内存也不能作准：退出对局后内存里仍有上一局的残留数据。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), "dsh-shadowverse-huanjing", "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

# 右上角「兵种」按钮区域（相对宽高）
TROOP_X0, TROOP_X1 = 0.90, 0.99
TROOP_Y0, TROOP_Y1 = 0.045, 0.075
TROOP_DARK_MAX = 110      # 均值亮度低于它 → 在对局内

# 右侧圆形徽章
BADGE_CX, BADGE_CY, BADGE_R = 0.935, 0.50, 0.035
BLUE_MIN = 0.30
RED_MIN = 0.20


def _troop_brightness(img):
    """「兵种」按钮区域的平均亮度（0~255）"""
    W, H = img.size
    px = img.convert("RGB").load()
    n = 0
    total = 0
    for y in range(int(H * TROOP_Y0), int(H * TROOP_Y1)):
        for x in range(int(W * TROOP_X0), int(W * TROOP_X1)):
            if not (0 <= x < W and 0 <= y < H):
                continue
            r, g, b = px[x, y]
            total += (r + g + b) / 3.0
            n += 1
    return total / n if n else 255.0


def _badge_ratio(img):
    """右侧徽章区域的「红占比 / 蓝占比」"""
    W, H = img.size
    cx, cy = int(W * BADGE_CX), int(H * BADGE_CY)
    r = int(min(W, H) * BADGE_R)
    px = img.convert("RGB").load()
    red = blue = tot = 0
    for y in range(cy - r, cy + r, 2):
        for x in range(cx - r, cx + r, 2):
            if not (0 <= x < W and 0 <= y < H):
                continue
            rr, gg, bb = px[x, y]
            tot += 1
            if rr > 120 and rr > gg + 40 and rr > bb + 30:
                red += 1
            if bb > 110 and bb > rr + 30:
                blue += 1
    if not tot:
        return 0.0, 0.0
    return red / tot, blue / tot


def in_match(img):
    """是否在「影之幻境」对局内（运营或战斗都算）"""
    if img is None:
        return False
    return _troop_brightness(img) < TROOP_DARK_MAX


def phase_of(img):
    """返回 'battle' | 'operate' | 'other'"""
    if img is None:
        return "other"

    # ① 不在对局内 → 直接 other（避免主界面的红宝石被当成"战斗中"）
    if not in_match(img):
        return "other"

    # ② 对局内：蓝色「准备」= 运营；红色「战斗中」= 战斗
    red, blue = _badge_ratio(img)
    if blue >= BLUE_MIN and blue > red:
        return "operate"
    if red >= RED_MIN:
        return "battle"

    # ③ 徽章不明显时用兔子模板兜底
    try:
        import pet_detect as PET
        if PET.score(img) >= PET.THRESHOLD:
            return "operate"
    except Exception:
        pass

    return "other"
