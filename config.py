"""تنظیمات ربات پیام‌رسان خصوصی."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_id: int
    user_x_id: int
    user_x_name: str
    user_y_id: int
    user_y_name: str
    database_path: Path

    @property
    def allowed_user_ids(self) -> set[int]:
        return {self.admin_id, self.user_x_id, self.user_y_id}

    def name_for(self, user_id: int) -> str:
        if user_id == self.user_x_id:
            return self.user_x_name
        if user_id == self.user_y_id:
            return self.user_y_name
        if user_id == self.admin_id:
            return "ادمین"
        return "کاربر ناشناس"

    def other_user_id(self, user_id: int) -> int:
        if user_id == self.user_x_id:
            return self.user_y_id
        if user_id == self.user_y_id:
            return self.user_x_id
        raise ValueError("این شناسه متعلق به یکی از دو طرف گفتگو نیست.")


def _required_text(key: str) -> str:
    value = os.getenv(key, "").strip()
    if not value:
        raise RuntimeError(
            f"مقدار {key} تنظیم نشده است. فایل .env را از روی .env.example بسازید و آن را تکمیل کنید."
        )
    return value


def _required_id(key: str) -> int:
    raw = _required_text(key)
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"مقدار {key} باید یک عدد صحیح باشد.") from exc
    if value <= 0:
        raise RuntimeError(f"مقدار {key} باید بزرگ‌تر از صفر باشد.")
    return value


def load_settings() -> Settings:
    token = _required_text("BOT_TOKEN")
    admin_id = _required_id("ADMIN_ID")
    user_x_id = _required_id("USER_X_ID")
    user_y_id = _required_id("USER_Y_ID")

    if len({admin_id, user_x_id, user_y_id}) != 3:
        raise RuntimeError("شناسه ادمین، کاربر X و کاربر Y باید با یکدیگر متفاوت باشند.")

    user_x_name = os.getenv("USER_X_NAME", "کاربر X").strip() or "کاربر X"
    user_y_name = os.getenv("USER_Y_NAME", "کاربر Y").strip() or "کاربر Y"

    return Settings(
        bot_token=token,
        admin_id=admin_id,
        user_x_id=user_x_id,
        user_x_name=user_x_name,
        user_y_id=user_y_id,
        user_y_name=user_y_name,
        database_path=BASE_DIR / "data" / "bot.sqlite3",
    )
