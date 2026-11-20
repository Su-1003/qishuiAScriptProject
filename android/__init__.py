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

TOP = (0, 0, 1, 0.24)
TOP_RIGHT = (0.48, 0, 1, 0.24)
CLOSE_CORNER = (0.73, 0, 1, 0.24)
DIALOG = (0.07, 0.24, 0.93, 0.80)

SPLASH_SKIP = r"(?:\d+[秒sS]?)?跳过(?:广告)?(?:\d+[秒sS]?)?"
COUNTDOWN = (
    r"(?:再看|再观看|观看|还需|剩余)?\d+(?:秒|[sS])"
    r"(?:后)?(?:即可|可)?领(?:取)?(?:奖励|听歌时长)"
)
CLAIM_READY = r"领取成功[×✕✖Xx]?"
CLAIM_BUTTON = r"领取奖励"
LIVE_CLOSE = r"(?:关闭(?:直播(?:间)?)?|退出直播(?:间)?|[×✕✖Xx])"


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


def wait_for_claim(timeout):
    live_closed = False
    continue_clicked = False
    for frame in wait_frames(timeout):
        resume = frame.find(r"继续观看", DIALOG)
        exit_prompt = frame.find(r"(?:坚持退出|确认退出|放弃奖励)", DIALOG)
        if resume and exit_prompt and not continue_clicked:
            tap(resume, "继续观看")
            continue_clicked = True
            continue

        if frame.find(COUNTDOWN, TOP):
            continue
        ready = frame.find(CLAIM_READY, TOP_RIGHT)
        if ready:
            return ready

        if not live_closed and frame.find(r"更多直播", TOP_RIGHT):
            close = find_live_close(frame)
            if close:
                tap(close, "关闭直播间")
                live_closed = True
    return None


def claim_reward(ready):
    tap(ready, "领取成功")
    for frame in wait_frames(REWARD_DIALOG_TIMEOUT):
        button = frame.find(CLAIM_BUTTON, DIALOG)
        if button:
            tap(button, "领取奖励")
            break
    else:
        raise StopRun("点了「领取成功」后未找到「领取奖励」。")

    # 等待旧领取界面退出，避免把残留按钮当作下一轮。
    clear_frames = 0
    for frame in wait_frames(TRANSITION_TIMEOUT):
        if frame.find(CLAIM_BUTTON, DIALOG) or frame.find(CLAIM_READY, TOP_RIGHT):
            clear_frames = 0
            continue
        clear_frames += 1
        if clear_frames >= 2 or frame.find(COUNTDOWN, TOP):
            return
    raise StopRun("点击领取后界面未切换，本次领取结果需在汽水音乐中确认。")


def main():
    completed = 0
    try:
        open_player()
        log(f"请手动打开第一条奖励广告，等待上限 {FIRST_AD_TIMEOUT} 秒。")
        for index in range(MAX_ROUNDS):
            timeout = FIRST_AD_TIMEOUT if index == 0 else NEXT_AD_TIMEOUT
            ready = wait_for_claim(timeout)
            if not ready:
                raise StopRun(f"{timeout} 秒内未识别到可领取状态，结束运行。")
            claim_reward(ready)
            completed += 1
            log(f"已完成第 {completed} 次领取操作。")
        log(f"已达到本次设置的 {MAX_ROUNDS} 轮上限。")
    except StopRun as error:
        log(str(error))
    except Exception:
        log("运行出错。请检查 AScript 的无障碍和截屏/OCR 权限，再查看下方日志。")
        traceback.print_exc()
    finally:
        log(f"脚本结束，共完成 {completed} 次领取操作，实际到账以汽水音乐为准。")


main()
