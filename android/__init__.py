"""汽水音乐 Android AScript 小程序。"""

import re
import time
import traceback

from ascript.android import action, system
from ascript.android.node import Selector
from ascript.android.screen import Ocr
from ascript.android.system import Device


APP_PACKAGE = "com.luna.music"
MAX_ROUNDS = 50
FIRST_AD_TIMEOUT = 120
NEXT_AD_TIMEOUT = 75
REWARD_DIALOG_TIMEOUT = 15
TRANSITION_TIMEOUT = 10
POLL_INTERVAL = 0.8
LIVE_CLOSE_RETRY_DELAY = 3
LIVE_CLOSE_ATTEMPTS = 2

TOP = (0, 0, 1, 0.24)
TOP_RIGHT = (0.48, 0, 1, 0.24)
CLOSE_CORNER = (0.73, 0, 1, 0.24)
DIALOG = (0.07, 0.24, 0.93, 0.80)
TOMORROW_HEADER = (0.07, 0.46, 0.93, 0.66)
TOMORROW_ACTION = (0.07, 0.73, 0.93, 0.92)

SPLASH_SKIP = r"(?:\d+[秒sS]?)?跳过(?:广告)?(?:\d+[秒sS]?)?"
COUNTDOWN = (
    r"(?:再看|再观看|观看|还需|剩余)?\d+(?:秒|[sS])"
    r"(?:后)?(?:即可|可)?领(?:取)?(?:奖励|听歌时长)"
)
CLAIM_READY = r"领取成功[×✕✖Xx]?"
CLAIM_BUTTON = r"领取奖励"
LIVE_ROOM = r"更多直播"
LIVE_CLOSE = r"(?:关闭(?:直播(?:间)?)?|退出直播(?:间)?|[×✕✖Xx])"
TODAY_CLAIMED = r"恭喜获得今日免费时长畅听"
TOMORROW_PROMPT = rf"(?:再看[1１一]个提前享明日畅听|{TODAY_CLAIMED})"
TOMORROW_BUTTON = r"立即解锁明日畅听"


class StopRun(Exception):
    """当前界面不宜继续操作。"""


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}")


def foreground_package():
    info = Device.current_appinfo()
    return getattr(info, "packageName", None)


def require_player():
    if foreground_package() != APP_PACKAGE:
        raise StopRun("汽水音乐已离开前台，停止点击。")


def wait_frames(timeout):
    """每次轮询只做一次 OCR，所有判断使用同一帧。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        require_player()
        yield ScreenText()
        time.sleep(POLL_INTERVAL)


class ScreenText:
    def __init__(self):
        display = Device.display()
        self.width = display.widthPixels
        self.height = display.heightPixels
        if not 0 < self.width < self.height:
            raise StopRun("请保持手机竖屏后重新运行。")
        self.results = Ocr.paddleocr() or []

    def contains(self, region, x, y):
        left, top, right, bottom = region
        return (
            left * self.width <= x < right * self.width
            and top * self.height <= y < bottom * self.height
        )

    def find(self, pattern, region):
        for result in self.results:
            x, y = int(result.center_x), int(result.center_y)
            if self.contains(region, x, y):
                text = re.sub(r"\s+", "", result.text or "")
                if re.fullmatch(pattern, text):
                    return x, y
        return None


def tap(point, label):
    require_player()
    if action.click(*point) is False:
        raise StopRun(f"点击「{label}」失败，请检查 AScript 的无障碍权限。")
    log(f"已点击「{label}」")


def open_player():
    if foreground_package() == APP_PACKAGE:
        return
    system.open(APP_PACKAGE)
    if not system.wait_for_package(APP_PACKAGE, 12000):
        raise StopRun("汽水音乐未进入前台，请检查是否已安装及是否允许关联启动。")
    for frame in wait_frames(5):
        skip = frame.find(SPLASH_SKIP, TOP_RIGHT)
        if skip:
            tap(skip, "跳过开屏广告")
            return


def find_live_close(frame):
    selector = (
        Selector()
        .packageName("^" + re.escape(APP_PACKAGE) + "$")
        .visible(True)
        .enabled(True)
    )
    nodes = selector.desc("^" + LIVE_CLOSE + "$").find_all()
    for node in nodes or []:
        point = node.rect.centerX(), node.rect.centerY()
        if frame.contains(CLOSE_CORNER, *point):
            return point
    return frame.find(LIVE_CLOSE, CLOSE_CORNER)


def find_resume_button(frame):
    if frame.find(r"(?:坚持退出|确认退出|放弃奖励)", DIALOG):
        return frame.find(r"继续观看", DIALOG)
    return None


def ad_in_progress(frame):
    return bool(
        frame.find(COUNTDOWN, TOP)
        or frame.find(LIVE_ROOM, TOP_RIGHT)
        or find_resume_button(frame)
    )


def find_tomorrow_button(frame):
    button = frame.find(TOMORROW_BUTTON, TOMORROW_ACTION)
    if button:
        return button

    # OCR 有时会把同一按钮的两段文字分开返回。
    unlock = frame.find(r"立即解锁", (0.07, 0.73, 0.50, 0.92))
    tomorrow = frame.find(r"明日畅听", (0.50, 0.73, 0.93, 0.92))
    if unlock and tomorrow and abs(unlock[1] - tomorrow[1]) <= frame.height * 0.025:
        return unlock
    return None


def read_tomorrow_offer(frame):
    """返回是否看到弹窗内容，以及证据齐全时的按钮坐标。"""
    heading = frame.find(TOMORROW_PROMPT, TOMORROW_HEADER)
    button = find_tomorrow_button(frame)
    return bool(heading or button), button if heading else None


def open_tomorrow_ad(point):
    tap(point, "立即解锁 明日畅听")
    for frame in wait_frames(TRANSITION_TIMEOUT):
        offer_visible, _ = read_tomorrow_offer(frame)
        if not offer_visible and ad_in_progress(frame):
            log("已识别到明日畅听广告界面。")
            return
    raise StopRun("已点击「立即解锁 明日畅听」，等待广告打开超时。")


def wait_for_claim(timeout, allow_tomorrow):
    live_close_attempts = 0
    next_live_close_at = 0
    continue_clicked = False
    for frame in wait_frames(timeout):
        resume = find_resume_button(frame)
        if resume:
            if not continue_clicked:
                tap(resume, "继续观看")
                continue_clicked = True
            continue

        if frame.find(COUNTDOWN, TOP):
            continue
        offer_visible, unlock = read_tomorrow_offer(frame)
        if allow_tomorrow and unlock:
            return None, unlock
        if offer_visible:
            continue
        ready = frame.find(CLAIM_READY, TOP_RIGHT)
        if ready:
            return ready, None

        if time.monotonic() < next_live_close_at or not frame.find(LIVE_ROOM, TOP_RIGHT):
            continue
        if live_close_attempts >= LIVE_CLOSE_ATTEMPTS:
            raise StopRun("已尝试点击两次关闭按钮，仍识别到直播间。")
        close = find_live_close(frame)
        if close:
            label = "重试关闭直播间" if live_close_attempts else "关闭直播间"
            tap(close, label)
            live_close_attempts += 1
            next_live_close_at = time.monotonic() + LIVE_CLOSE_RETRY_DELAY
    return None, None


def claim_reward(ready, allow_tomorrow):
    """返回本轮是否完成常规领取，以及是否出现明日畅听入口。"""
    tap(ready, "领取成功")
    for frame in wait_frames(REWARD_DIALOG_TIMEOUT):
        offer_visible, unlock = read_tomorrow_offer(frame)
        if allow_tomorrow and unlock:
            return False, unlock
        if offer_visible:
            continue
        button = frame.find(CLAIM_BUTTON, DIALOG)
        if button:
            tap(button, "领取奖励")
            break
    else:
        raise StopRun("点了「领取成功」后未找到「领取奖励」。")

    # OCR 没读到旧按钮时，仍需要下一条广告或领取结果来确认页面变化。
    for frame in wait_frames(TRANSITION_TIMEOUT):
        offer_visible, unlock = read_tomorrow_offer(frame)
        if allow_tomorrow and unlock:
            return True, unlock
        if frame.find(CLAIM_BUTTON, DIALOG) or frame.find(CLAIM_READY, TOP_RIGHT):
            continue
        if frame.find(TODAY_CLAIMED, TOMORROW_HEADER) or (
            not offer_visible and ad_in_progress(frame)
        ):
            return True, None
    raise StopRun("已点击「领取奖励」，未识别到下一条广告或领取结果，请在汽水音乐中确认。")


def main():
    completed = 0
    tomorrow_clicked = False
    try:
        open_player()
        log(f"请手动打开第一条奖励广告，或回到「明日畅听」弹窗，等待上限 {FIRST_AD_TIMEOUT} 秒。")
        timeout = FIRST_AD_TIMEOUT
        while completed < MAX_ROUNDS:
            allow_tomorrow = not tomorrow_clicked
            ready, unlock = wait_for_claim(timeout, allow_tomorrow)
            if not ready and not unlock:
                raise StopRun(f"{timeout} 秒内未识别到可领取状态，结束运行。")
            if ready:
                claimed, unlock = claim_reward(ready, allow_tomorrow)
                if claimed:
                    completed += 1
                    log(f"已完成第 {completed} 次领取操作。")
            if unlock and completed < MAX_ROUNDS:
                open_tomorrow_ad(unlock)
                tomorrow_clicked = True
            timeout = NEXT_AD_TIMEOUT
        log(f"已达到本次设置的 {MAX_ROUNDS} 轮上限。")
    except StopRun as error:
        log(str(error))
    except Exception:
        log("运行出错。请检查 AScript 的无障碍和截屏/OCR 权限，再查看下方日志。")
        traceback.print_exc()
    finally:
        log(f"脚本结束，共完成 {completed} 次领取操作，实际到账以汽水音乐为准。")


main()
