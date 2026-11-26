import asyncio
import logging
import os
import random
import re
import secrets
import sqlite3
import urllib.parse
from datetime import datetime, timedelta, timezone
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, CommandObject, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BotCommand,
)

# ==========================================
# НАСТРОЙКИ БОТА (@Maestro_vpn_official_bot)
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬ_СЮДА_ТОКЕН_ОТ_BOTFATHER")
MINI_APP_URL = os.getenv("MINI_APP_URL", "https://rkk77777.github.io/MaestroVPN/")
BOT_USERNAME = os.getenv("BOT_USERNAME", "Maestro_vpn_official_bot")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "MaestroVPNnews")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/MaestroVPNnews")
ADMIN_ID = int(os.getenv("ADMIN_ID", "5922226163"))
PORT = int(os.getenv("PORT", "10000"))
DB_PATH = os.getenv("DB_PATH", "maestro_bot.db")

PLANS = {
    "month": {"title": "1 месяц", "price": 69, "devices": 3, "days": 30},
    "q3": {"title": "3 месяца", "price": 199, "devices": 5, "days": 90},
    "half": {"title": "6 месяцев", "price": 399, "devices": 7, "days": 180},
    "year": {"title": "12 месяцев (1 год)", "price": 799, "devices": 10, "days": 365},
}

AVAILABLE_SERVERS = [
    "🇫🇷 Франция",
    "🇩🇪 Германия",
    "🇭🇺 Венгрия",
    "🇺🇸 США",
    "🇫🇮 Хельсинки",
    "🇷🇺 Россия",
]

PROMO_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

AGREEMENT_TEXT = (
    "📄 <b>Пользовательское соглашение и политика конфиденциальности Maestro VPN</b>\n\n"
    "1. <b>Предмет сервиса:</b> Maestro VPN предоставляет доступ к серверам (Франция, Германия, Венгрия, США, Хельсинки, Россия) по оформленной подписке.\n"
    "2. <b>Оплата и тарифы:</b> подписка оформляется на выбранный срок (30, 90, 180 или 365 дней) с лимитом устройств от 3 до 10. Скрытые списания не производятся.\n"
    "3. <b>Промокоды и рефералы:</b> персональный промокод из 7 символов (проходится через <code>/minigame</code>) даёт +3 дня подписки и активируется только после покупки подписки или вложения в сервис. Реферальный учёт засчитывается после покупки приглашённым.\n"
    "4. <b>Устройства и доступ:</b> добавление дополнительных устройств происходит по персональной ссылке приглашения в рамках лимита тарифа. Запрещены DDoS, спам, сканирование сетей и противоправные действия.\n"
    "5. <b>Персональные данные:</b> обрабатываются только технические данные аккаунта (Telegram ID, срок подписки, статус оплаты, промокод, список устройств и обращения в поддержку) для работы сервиса.\n\n"
    "Нажимая <b>«Принимаю условия»</b>, вы подтверждаете согласие с правилами сервиса и политикой конфиденциальности."
)


class UserFlowStates(StatesGroup):
    waiting_support_message = State()
    waiting_promo_input = State()
    playing_minigame = State()


logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def generate_promo_code() -> str:
    return "".join(secrets.choice(PROMO_ALPHABET) for _ in range(7))


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                expires_at TEXT,
                devices INTEGER NOT NULL DEFAULT 3,
                referrals INTEGER NOT NULL DEFAULT 0,
                has_paid INTEGER NOT NULL DEFAULT 0,
                invited_by INTEGER,
                referral_counted INTEGER NOT NULL DEFAULT 0,
                accepted_terms INTEGER NOT NULL DEFAULT 0,
                promo_code TEXT UNIQUE,
                promo_progress INTEGER NOT NULL DEFAULT 0,
                promo_used INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        _ensure_column(conn, "users", "has_paid", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "users", "invited_by", "INTEGER")
        _ensure_column(conn, "users", "referral_counted", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "users", "accepted_terms", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "users", "promo_code", "TEXT")
        _ensure_column(conn, "users", "promo_progress", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "users", "promo_used", "INTEGER NOT NULL DEFAULT 0")

        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            UPDATE users
            SET has_paid = 1
            WHERE has_paid = 0 AND expires_at IS NOT NULL AND expires_at > ?
            """,
            (now_iso,),
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                device_name TEXT NOT NULL,
                added_at TEXT NOT NULL
            )
            """
        )

        rows = conn.execute("SELECT user_id, promo_code FROM users").fetchall()
        for uid, pcode in rows:
            if not pcode or not re.fullmatch(r"[A-Z0-9]{7}", str(pcode)):
                while True:
                    new_promo = generate_promo_code()
                    exists = conn.execute(
                        "SELECT 1 FROM users WHERE promo_code = ?", (new_promo,)
                    ).fetchone()
                    if not exists:
                        conn.execute(
                            "UPDATE users SET promo_code = ? WHERE user_id = ?",
                            (new_promo, uid),
                        )
                        break

        conn.commit()


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def get_user_record(user_id: int) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT expires_at, devices, referrals, has_paid, invited_by,
                   referral_counted, accepted_terms, promo_code, promo_progress, promo_used
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            while True:
                promo_code = generate_promo_code()
                if not conn.execute("SELECT 1 FROM users WHERE promo_code = ?", (promo_code,)).fetchone():
                    break
            conn.execute(
                """
                INSERT INTO users (
                    user_id, expires_at, devices, referrals, has_paid,
                    invited_by, referral_counted, accepted_terms, promo_code,
                    promo_progress, promo_used
                )
                VALUES (?, NULL, 3, 0, 0, NULL, 0, 0, ?, 0, 0)
                """,
                (user_id, promo_code),
            )
            conn.commit()
            return {
                "expires_at": None,
                "devices": 3,
                "referrals": 0,
                "has_paid": 0,
                "invited_by": None,
                "referral_counted": 0,
                "accepted_terms": 0,
                "promo_code": promo_code,
                "promo_progress": 0,
                "promo_used": 0,
            }

        promo_code = row[7]
        if not promo_code or not re.fullmatch(r"[A-Z0-9]{7}", str(promo_code)):
            while True:
                promo_code = generate_promo_code()
                if not conn.execute("SELECT 1 FROM users WHERE promo_code = ?", (promo_code,)).fetchone():
                    break
            conn.execute("UPDATE users SET promo_code = ? WHERE user_id = ?", (promo_code, user_id))
            conn.commit()

        return {
            "expires_at": _parse_dt(row[0]),
            "devices": int(row[1] if row[1] is not None else 3),
            "referrals": int(row[2] if row[2] is not None else 0),
            "has_paid": int(row[3] if row[3] is not None else 0),
            "invited_by": int(row[4]) if row[4] is not None else None,
            "referral_counted": int(row[5] if row[5] is not None else 0),
            "accepted_terms": int(row[6] if row[6] is not None else 0),
            "promo_code": promo_code,
            "promo_progress": int(row[8] if row[8] is not None else 0),
            "promo_used": int(row[9] if row[9] is not None else 0),
        }


def save_user_record(
    user_id: int,
    expires_at: datetime | None,
    devices: int,
    referrals: int,
    has_paid: int | None = None,
    invited_by: int | None = None,
    referral_counted: int | None = None,
    accepted_terms: int | None = None,
    promo_code: str | None = None,
    promo_progress: int | None = None,
    promo_used: int | None = None,
) -> None:
    current = get_user_record(user_id)
    final_paid = has_paid if has_paid is not None else current["has_paid"]
    final_invited_by = invited_by if invited_by is not None else current["invited_by"]
    final_ref_counted = referral_counted if referral_counted is not None else current["referral_counted"]
    final_terms = accepted_terms if accepted_terms is not None else current["accepted_terms"]
    final_promo = current["promo_code"]
    final_progress = promo_progress if promo_progress is not None else current["promo_progress"]
    final_used = promo_used if promo_used is not None else current["promo_used"]
    iso_val = expires_at.astimezone(timezone.utc).isoformat() if expires_at else None

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE users
            SET expires_at = ?,
                devices = ?,
                referrals = ?,
                has_paid = ?,
                invited_by = ?,
                referral_counted = ?,
                accepted_terms = ?,
                promo_code = ?,
                promo_progress = ?,
                promo_used = ?
            WHERE user_id = ?
            """,
            (
                iso_val,
                devices,
                referrals,
                final_paid,
                final_invited_by,
                final_ref_counted,
                final_terms,
                final_promo,
                final_progress,
                final_used,
                user_id,
            ),
        )
        conn.commit()


def attach_inviter_if_empty(user_id: int, inviter_id: int) -> None:
    if inviter_id == user_id:
        return
    rec = get_user_record(user_id)
    if rec["invited_by"] is None:
        save_user_record(
            user_id=user_id,
            expires_at=rec["expires_at"],
            devices=rec["devices"],
            referrals=rec["referrals"],
            invited_by=inviter_id,
        )


def mark_paid_and_credit_referral(user_id: int) -> None:
    rec = get_user_record(user_id)
    if not rec["has_paid"]:
        save_user_record(
            user_id=user_id,
            expires_at=rec["expires_at"],
            devices=rec["devices"],
            referrals=rec["referrals"],
            has_paid=1,
        )
        rec = get_user_record(user_id)

    inviter_id = rec["invited_by"]
    if inviter_id and inviter_id != user_id and not rec["referral_counted"]:
        inviter_rec = get_user_record(inviter_id)
        save_user_record(
            user_id=inviter_id,
            expires_at=inviter_rec["expires_at"],
            devices=inviter_rec["devices"],
            referrals=inviter_rec["referrals"] + 1,
        )
        save_user_record(
            user_id=user_id,
            expires_at=rec["expires_at"],
            devices=rec["devices"],
            referrals=rec["referrals"],
            referral_counted=1,
        )


def get_user_devices(user_id: int) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, device_name, added_at FROM user_devices WHERE user_id = ? ORDER BY id ASC",
            (user_id,),
        ).fetchall()
    return [{"id": r[0], "name": r[1], "added_at": r[2]} for r in rows]


def add_user_device(user_id: int, device_name: str) -> None:
    now_str = datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m.%Y %H:%M")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO user_devices (user_id, device_name, added_at) VALUES (?, ?, ?)",
            (user_id, device_name.strip(), now_str),
        )
        conn.commit()


def remove_user_device(user_id: int, device_id: int) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM user_devices WHERE id = ? AND user_id = ?",
            (device_id, user_id),
        )
        conn.commit()


def add_subscription_days(user_id: int, days: int, devices: int | None = None, mark_paid: bool = True) -> None:
    rec = get_user_record(user_id)
    now = datetime.now(timezone.utc)
    base = rec["expires_at"] if (rec["expires_at"] and rec["expires_at"] > now) else now
    new_expires = base + timedelta(days=days)
    target_devices = devices if devices is not None else rec["devices"]
    paid_flag = 1 if mark_paid else rec["has_paid"]
    save_user_record(
        user_id=user_id,
        expires_at=new_expires,
        devices=target_devices,
        referrals=rec["referrals"],
        has_paid=paid_flag,
    )
    if paid_flag:
        mark_paid_and_credit_referral(user_id)


def format_subscription_status(user_id: int) -> str:
    rec = get_user_record(user_id)
    expires_at: datetime | None = rec.get("expires_at")
    now = datetime.now(timezone.utc)
    if not expires_at or expires_at <= now:
        return "❌ Подписка не активна (0 дн. осталось)"
    delta = expires_at - now
    days_left = max(1, (delta.days + (1 if delta.seconds > 0 else 0)))
    date_str = expires_at.astimezone(timezone(timedelta(hours=3))).strftime("%d.%m.%Y %H:%M МСК")
    return f"✅ Активна до <b>{date_str}</b> (осталось ~<b>{days_left} дн.</b>)"


def build_referral_link(user_id: int) -> str:
    clean_bot = BOT_USERNAME.lstrip("@")
    return f"https://t.me/{clean_bot}?start=ref_{user_id}"


def build_device_invite_link(user_id: int) -> str:
    clean_bot = BOT_USERNAME.lstrip("@")
    return f"https://t.me/{clean_bot}?start=adddev_{user_id}"


async def is_user_subscribed(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    clean_channel = CHANNEL_USERNAME.lstrip("@")
    if not clean_channel:
        return True
    try:
        member = await bot.get_chat_member(chat_id=f"@{clean_channel}", user_id=user_id)
        return member.status in {"member", "administrator", "creator"}
    except Exception as exc:
        logging.warning(f"Subscription check error for user {user_id}: {exc}")
        return False


def subscription_gate_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Подписаться на @MaestroVPNnews", url=CHANNEL_URL)],
            [InlineKeyboardButton(text="✅ Я подписался", callback_data="gate:check_sub")],
        ]
    )


def agreement_gate_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Принимаю условия", callback_data="gate:accept_terms")],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data="gate:decline_terms")],
        ]
    )


def back_button(target: str = "nav:start") -> InlineKeyboardButton:
    return InlineKeyboardButton(text="⬅️ Вернуться назад", callback_data=target)


def reply_hints_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏠 Меню (/start)"), KeyboardButton(text="👤 Кабинет")],
            [KeyboardButton(text="💳 Купить подписку"), KeyboardButton(text="🎟 Промокод")],
            [KeyboardButton(text="👥 Добавить друзей"), KeyboardButton(text="🆘 Поддержка")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите подсказку или введите /minigame...",
    )


def start_keyboard() -> InlineKeyboardMarkup:
    app_btn = InlineKeyboardButton(
        text="📱 Открыть приложение",
        web_app=WebAppInfo(url=MINI_APP_URL),
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [app_btn],
            [
                InlineKeyboardButton(text="👤 Кабинет", callback_data="nav:cabinet"),
                InlineKeyboardButton(text="💳 Купить подписку", callback_data="nav:tariffs"),
            ],
            [
                InlineKeyboardButton(text="⚙️ Управление подпиской", callback_data="nav:manage_sub"),
                InlineKeyboardButton(text="🎟 Промокод", callback_data="nav:promo"),
            ],
            [
                InlineKeyboardButton(text="📱 Устройства", callback_data="nav:devices"),
                InlineKeyboardButton(text="👥 Добавить друзей", callback_data="nav:referral"),
            ],
            [
                InlineKeyboardButton(text="🆘 Поддержка", callback_data="nav:support"),
            ],
        ]
    )


def cabinet_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Купить подписку", callback_data="nav:tariffs")],
            [InlineKeyboardButton(text="⚙️ Управление подпиской", callback_data="nav:manage_sub")],
            [InlineKeyboardButton(text="📱 Устройства", callback_data="nav:devices")],
            [back_button("nav:start")],
        ]
    )


def manage_sub_keyboard(user_id: int) -> InlineKeyboardMarkup:
    invite_link = build_device_invite_link(user_id)
    share_text = urllib.parse.quote("Подключи устройство к моей подписке Maestro VPN:")
    share_url = urllib.parse.quote(invite_link)
    telegram_share_url = f"https://t.me/share/url?url={share_url}&text={share_text}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📱 Устройства", callback_data="nav:devices")],
            [InlineKeyboardButton(text="🔗 Скопировать ссылку добавления", callback_data="dev:copy_link")],
            [InlineKeyboardButton(text="📤 Переслать ссылку в Telegram", url=telegram_share_url)],
            [InlineKeyboardButton(text="💳 Купить подписку", callback_data="nav:tariffs")],
            [back_button("nav:cabinet")],
        ]
    )


def devices_keyboard(user_id: int) -> InlineKeyboardMarkup:
    devices = get_user_devices(user_id)
    invite_link = build_device_invite_link(user_id)
    share_text = urllib.parse.quote("Подключи устройство к моей подписке Maestro VPN:")
    share_url = urllib.parse.quote(invite_link)
    telegram_share_url = f"https://t.me/share/url?url={share_url}&text={share_text}"

    rows: list[list[InlineKeyboardButton]] = []
    for idx, dev in enumerate(devices, start=1):
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🗑 Отключить #{idx}: {dev['name']}",
                    callback_data=f"dev:del:{dev['id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="🔗 Скопировать ссылку добавления", callback_data="dev:copy_link")])
    rows.append([InlineKeyboardButton(text="📤 Переслать ссылку в Telegram", url=telegram_share_url)])
    rows.append([back_button("nav:manage_sub")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tariffs_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1 мес • 69 ₽", callback_data="buy:month"),
                InlineKeyboardButton(text="3 мес • 199 ₽", callback_data="buy:q3"),
            ],
            [
                InlineKeyboardButton(text="6 мес • 399 ₽", callback_data="buy:half"),
                InlineKeyboardButton(text="12 мес • 799 ₽", callback_data="buy:year"),
            ],
            [back_button("nav:cabinet")],
        ]
    )


def build_plan_invoice_text(plan_key: str, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    plan = PLANS[plan_key]
    text = (
        f"💳 <b>Maestro VPN — Оформление подписки</b>\n\n"
        f"• Тариф: <b>{plan['title']}</b> ({plan['days']} дн.)\n"
        f"• Лимит устройств: <b>до {plan['devices']}</b>\n"
        f"• К оплате: <b>{plan['price']} руб.</b>\n"
        f"• Ваш Telegram ID: <code>{user_id}</code>\n\n"
        f"Нажмите кнопку ниже для оформления или вернитесь назад."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💳 Оплатить {plan['price']} руб.",
                    callback_data=f"confirm_pay:{plan_key}",
                )
            ],
            [back_button("nav:tariffs")],
        ]
    )
    return text, kb


def referral_keyboard(user_id: int) -> InlineKeyboardMarkup:
    ref_link = build_referral_link(user_id)
    share_text = urllib.parse.quote("Попробуй Maestro VPN — быстрый доступ и удобный кабинет:")
    share_url = urllib.parse.quote(ref_link)
    telegram_share_url = f"https://t.me/share/url?url={share_url}&text={share_text}"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Поделиться в Telegram", url=telegram_share_url)],
            [InlineKeyboardButton(text="📋 Скопировать вашу ссылку", callback_data="ref:copy")],
            [back_button("nav:start")],
        ]
    )


def support_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [back_button("nav:start")],
        ]
    )


def build_start_message(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    rec = get_user_record(user_id)
    text = (
        f"🌐 Сервера в разных странах: Франция, Германия, Венгрия, США, Хельсинки, Россия\n"
        f"🔐 Подключение по подписке и управление устройствами по ссылке\n"
        f"⚡ Стабильное соединение и удобный личный кабинет в боте и Mini App\n"
        f"🎟 Персональный промокод на +3 дня через мини-игру /minigame\n"
        f"👥 Реферальная система и поддержка 24/7\n\n"
        f"🆔 Ваш Telegram ID: <code>{user_id}</code>\n"
        f"📊 Статус: {format_subscription_status(user_id)}"
    )
    return text, start_keyboard()


def build_cabinet_message(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    rec = get_user_record(user_id)
    devs = get_user_devices(user_id)
    text = (
        f"👤 <b>Личный кабинет</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"📅 Когда кончится подписка: {format_subscription_status(user_id)}\n"
        f"📱 Подключено устройств: <b>{len(devs)} / {rec['devices']}</b>\n"
        f"👥 Приглашено друзей: <b>{rec['referrals']}</b>"
    )
    return text, cabinet_keyboard()


def build_manage_sub_message(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    rec = get_user_record(user_id)
    devs = get_user_devices(user_id)
    invite_link = build_device_invite_link(user_id)
    text = (
        f"⚙️ <b>Управление подпиской</b>\n\n"
        f"📊 Статус: {format_subscription_status(user_id)}\n"
        f"📱 Устройств: <b>{len(devs)} из {rec['devices']}</b>\n"
        f"🔗 Ссылка добавления устройства:\n"
        f"<code>{invite_link}</code>"
    )
    return text, manage_sub_keyboard(user_id)


def build_devices_message(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    rec = get_user_record(user_id)
    devs = get_user_devices(user_id)
    invite_link = build_device_invite_link(user_id)
    if not devs:
        list_block = "Пока нет подключённых устройств."
    else:
        lines = []
        for i, d in enumerate(devs, start=1):
            lines.append(f"{i}. <b>{d['name']}</b> <i>(добавлено {d['added_at']})</i>")
        list_block = "\n".join(lines)

    text = (
        f"📱 <b>Ваши устройства ({len(devs)} / {rec['devices']})</b>\n\n"
        f"{list_block}\n\n"
        f"Добавление устройств происходит только по ссылке:\n"
        f"<code>{invite_link}</code>"
    )
    return text, devices_keyboard(user_id)


def build_tariffs_message(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    servers_lines = "\n".join(f"• {srv}" for srv in AVAILABLE_SERVERS)
    text = (
        f"🌐 <b>Имеющиеся сервера в Maestro VPN:</b>\n"
        f"{servers_lines}\n\n"
        f"💳 <b>Купить подписку Maestro VPN</b>\n\n"
        f"• <b>1 месяц</b> — <b>69 ₽</b> (до 3 устройств, 30 дн.)\n"
        f"• <b>3 месяца</b> — <b>199 ₽</b> (до 5 устройств, 90 дн.)\n"
        f"• <b>6 месяцев</b> — <b>399 ₽</b> (до 7 устройств, 180 дн.)\n"
        f"• <b>12 месяцев</b> — <b>799 ₽</b> (до 10 устройств, 365 дн.)\n\n"
        f"Выберите подходящий тариф:"
    )
    return text, tariffs_keyboard()


def build_referral_message(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    rec = get_user_record(user_id)
    if not rec["has_paid"]:
        text = (
            f"👥 <b>Реферальная система</b>\n\n"
            f"🔒 Реферальная ссылка и учёт приглашений доступны только после того, как вы <b>хотя бы один раз купите или оплатите подписку</b>.\n\n"
            f"Перейдите в раздел «Купить подписку», оформите подписку, и после этого вам откроется приглашение друзей."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💳 Купить подписку", callback_data="nav:tariffs")],
                [back_button("nav:start")],
            ]
        )
        return text, kb

    ref_link = build_referral_link(user_id)
    text = (
        f"👥 <b>Добавить друзей</b>\n\n"
        f"Приглашено друзей (оплативших): <b>{rec['referrals']}</b>\n"
        f"Ваша реферальная ссылка:\n"
        f"<code>{ref_link}</code>\n\n"
        f"Нажмите кнопку ниже, чтобы выбрать кому отправить ссылку в Telegram, или скопируйте её."
    )
    return text, referral_keyboard(user_id)


def build_promo_message(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    rec = get_user_record(user_id)
    progress = min(7, max(0, rec["promo_progress"]))
    revealed_chars = rec["promo_code"][:progress] if progress > 0 else "—"
    status_line = (
        f"🔑 Ваш персональный промокод: <code>{rec['promo_code']}</code> (открыто {progress}/7 символов)"
        if progress == 7
        else f"🧩 Открыто символов: <b>{progress} / 7</b> (текущие: <code>{revealed_chars}</code>)"
    )
    used_note = "\n✅ Вы уже активировали промокод (+3 дня)." if rec["promo_used"] else ""
    paid_note = (
        "🔓 Активация промокода доступна (покупка/вложение в сервис засчитаны)."
        if rec["has_paid"]
        else "🔒 <b>Важно:</b> промокод работает только после того, как вы <b>хотя бы один раз купите подписку или сделаете любое вложение в сервис</b>."
    )

    text = (
        f"🎟 <b>Промокод (+3 дня подписки)</b>\n\n"
        f"Введите команду <code>/minigame</code> и пройдите мини-игру, чтобы получить символы своего промокода (7 символов — буквы и цифры).\n"
        f"У каждого пользователя свой уникальный промокод!\n\n"
        f"{status_line}\n"
        f"{paid_note}{used_note}\n\n"
        f"Нажмите <b>«Ввести промокод»</b> и отправьте ваш промокод сообщением:"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Ввести промокод", callback_data="promo:enter")],
            [InlineKeyboardButton(text="💳 Купить подписку", callback_data="nav:tariffs")],
            [back_button("nav:start")],
        ]
    )
    return text, kb


def _generate_minigame_round(user_id: int, step_index: int) -> tuple[str, InlineKeyboardMarkup, int]:
    """Генерирует раунд мини-игры для получения очередного (step_index-го, 1..7) символа промокода."""
    # Используем детерминированный seed от user_id и step_index, чтобы у каждого игрока была своя последовательность
    rng = random.Random(f"{user_id}:minigame:{step_index}")
    code_target = rng.choice(["A", "B", "C", "D", "E", "F"])
    options = ["A", "B", "C", "D", "E", "F"]
    rng.shuffle(options)

    text = (
        f"🎮 <b>Мини-игра: символ промокода #{step_index} из 7</b>\n\n"
        f"Найдите нужный код доступа: <b>{code_target}</b>\n"
        f"Выберите правильную кнопку ниже, чтобы открыть следующий символ вашего промокода:"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=options[0], callback_data=f"mg:{step_index}:{options[0]}:{code_target}"),
                InlineKeyboardButton(text=options[1], callback_data=f"mg:{step_index}:{options[1]}:{code_target}"),
                InlineKeyboardButton(text=options[2], callback_data=f"mg:{step_index}:{options[2]}:{code_target}"),
            ],
            [
                InlineKeyboardButton(text=options[3], callback_data=f"mg:{step_index}:{options[3]}:{code_target}"),
                InlineKeyboardButton(text=options[4], callback_data=f"mg:{step_index}:{options[4]}:{code_target}"),
                InlineKeyboardButton(text=options[5], callback_data=f"mg:{step_index}:{options[5]}:{code_target}"),
            ],
            [back_button("nav:promo")],
        ]
    )
    return text, kb, step_index


async def _ensure_access_gates(event: Message | CallbackQuery, user_id: int, state: FSMContext | None = None) -> bool:
    """Проверяет подписку на @MaestroVPNnews и принятие пользовательского соглашения."""
    get_user_record(user_id)

    if not await is_user_subscribed(user_id):
        msg_text = (
            f"📢 Для работы с ботом необходимо подписаться на наш Telegram-канал:\n"
            f"<b>{CHANNEL_URL}</b>\n\n"
            f"Подпишитесь на канал и нажмите <b>«Я подписался»</b>."
        )
        if isinstance(event, CallbackQuery):
            await event.answer("Сначала подпишитесь на @MaestroVPNnews", show_alert=True)
            try:
                await event.message.edit_text(msg_text, parse_mode="HTML", reply_markup=subscription_gate_keyboard())
            except Exception:
                await event.message.answer(msg_text, parse_mode="HTML", reply_markup=subscription_gate_keyboard())
        else:
            await event.answer(msg_text, parse_mode="HTML", reply_markup=subscription_gate_keyboard())
        return False

    rec = get_user_record(user_id)
    if not rec["accepted_terms"]:
        if isinstance(event, CallbackQuery):
            await event.answer()
            try:
                await event.message.edit_text(AGREEMENT_TEXT, parse_mode="HTML", reply_markup=agreement_gate_keyboard())
            except Exception:
                await event.message.answer(AGREEMENT_TEXT, parse_mode="HTML", reply_markup=agreement_gate_keyboard())
        else:
            await event.answer(AGREEMENT_TEXT, parse_mode="HTML", reply_markup=agreement_gate_keyboard())
        return False

    return True


async def send_or_edit_callback(callback: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup):
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=reply_markup)
    await callback.answer()


@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    args = (command.args or "").strip()
    user_id = message.from_user.id
    get_user_record(user_id)

    if args.startswith("ref_"):
        inviter_raw = args.removeprefix("ref_")
        if inviter_raw.isdigit():
            inviter_id = int(inviter_raw)
            if inviter_id != user_id:
                attach_inviter_if_empty(user_id, inviter_id)

    if args.startswith("adddev_"):
        owner_raw = args.removeprefix("adddev_")
        if owner_raw.isdigit():
            owner_id = int(owner_raw)
            owner_rec = get_user_record(owner_id)
            devs = get_user_devices(owner_id)
            if len(devs) >= owner_rec["devices"]:
                await message.answer(
                    f"❌ Достигнут лимит устройств по подписке пользователя <code>{owner_id}</code> (<b>{len(devs)} / {owner_rec['devices']}</b>).",
                    parse_mode="HTML",
                )
            else:
                device_label = f"Устройство #{len(devs) + 1} (ID {user_id})"
                add_user_device(owner_id, device_label)
                await message.answer(
                    f"✅ Устройство подключено к подписке по ссылке!\n"
                    f"Статус подписки: {format_subscription_status(owner_id)}",
                    parse_mode="HTML",
                )

    if args.startswith("pay_"):
        raw = args.removeprefix("pay_")
        plan_key = raw.split("_uid_")[0]
        if plan_key in PLANS:
            if not await _ensure_access_gates(message, user_id, state):
                return
            text, kb = build_plan_invoice_text(plan_key, user_id)
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            return

    if not await _ensure_access_gates(message, user_id, state):
        return

    start_text, start_kb = build_start_message(user_id)
    await message.answer("⌨️ Подсказки клавиатуры активированы.", reply_markup=reply_hints_keyboard())
    await message.answer(start_text, parse_mode="HTML", reply_markup=start_kb)


@dp.callback_query(F.data == "gate:check_sub")
async def cb_gate_check_sub(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if not await is_user_subscribed(user_id):
        await callback.answer("Подписка на @MaestroVPNnews пока не найдена", show_alert=True)
        return

    rec = get_user_record(user_id)
    if not rec["accepted_terms"]:
        await callback.answer("Подписка подтверждена! Теперь примите условия")
        await send_or_edit_callback(callback, AGREEMENT_TEXT, agreement_gate_keyboard())
        return

    start_text, start_kb = build_start_message(user_id)
    await callback.message.answer("⌨️ Подсказки клавиатуры активированы.", reply_markup=reply_hints_keyboard())
    await send_or_edit_callback(callback, start_text, start_kb)


@dp.callback_query(F.data == "gate:accept_terms")
async def cb_gate_accept_terms(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if not await is_user_subscribed(user_id):
        await callback.answer("Сначала подпишитесь на @MaestroVPNnews", show_alert=True)
        return

    rec = get_user_record(user_id)
    save_user_record(
        user_id=user_id,
        expires_at=rec["expires_at"],
        devices=rec["devices"],
        referrals=rec["referrals"],
        accepted_terms=1,
    )
    start_text, start_kb = build_start_message(user_id)
    await callback.message.answer("⌨️ Подсказки клавиатуры активированы.", reply_markup=reply_hints_keyboard())
    await send_or_edit_callback(callback, start_text, start_kb)


@dp.callback_query(F.data == "gate:decline_terms")
async def cb_gate_decline_terms(callback: CallbackQuery):
    await callback.answer("Без принятия условий доступ к боту закрыт", show_alert=True)
    try:
        await callback.message.edit_text(
            "❌ Вы отклонили пользовательское соглашение и политику конфиденциальности.\n"
            "Чтобы начать работу с ботом, нажмите <b>«Принимаю условия»</b>.",
            parse_mode="HTML",
            reply_markup=agreement_gate_keyboard(),
        )
    except Exception:
        pass


@dp.message(Command("minigame"))
async def cmd_minigame(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not await _ensure_access_gates(message, user_id, state):
        return

    rec = get_user_record(user_id)
    if rec["promo_progress"] >= 7:
        await message.answer(
            f"🎉 Вы уже собрали все 7 символов своего промокода: <code>{rec['promo_code']}</code>\n"
            f"Откройте раздел <b>«Промокод»</b>, чтобы активировать его (+3 дня после покупки/вложения).",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button("nav:promo")]]),
        )
        return

    next_step = rec["promo_progress"] + 1
    await state.set_state(UserFlowStates.playing_minigame)
    text, kb, _ = _generate_minigame_round(user_id, next_step)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.callback_query(F.data.startswith("mg:"))
async def cb_minigame_choice(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if not await _ensure_access_gates(callback, user_id, state):
        return

    parts = callback.data.split(":")
    if len(parts) != 4 or not parts[1].isdigit():
        await callback.answer("Ошибка раунда", show_alert=True)
        return

    step = int(parts[1])
    picked = parts[2]
    target = parts[3]

    if picked != target:
        await callback.answer(f"Мимо! Нужен был код {target}, пробуем снова", show_alert=False)
        text, kb, _ = _generate_minigame_round(user_id, step)
        await send_or_edit_callback(callback, text, kb)
        return

    rec = get_user_record(user_id)
    new_progress = max(rec["promo_progress"], min(7, step))
    save_user_record(
        user_id=user_id,
        expires_at=rec["expires_at"],
        devices=rec["devices"],
        referrals=rec["referrals"],
        promo_progress=new_progress,
    )
    updated_rec = get_user_record(user_id)
    unlocked_char = updated_rec["promo_code"][new_progress - 1]
    opened_part = updated_rec["promo_code"][:new_progress]

    if new_progress < 7:
        await callback.answer(f"Открыт символ #{new_progress}: {unlocked_char}", show_alert=False)
        next_text, next_kb, _ = _generate_minigame_round(user_id, new_progress + 1)
        header = (
            f"✅ Символ <b>#{new_progress}</b> получен: <code>{unlocked_char}</code>\n"
            f"Открыто символов: <b>{new_progress}/7</b> (<code>{opened_part}</code>)\n\n"
        )
        await send_or_edit_callback(callback, header + next_text, next_kb)
    else:
        await state.clear()
        await callback.answer("Все 7 символов промокода собраны!", show_alert=True)
        done_text = (
            f"🏆 <b>Мини-игра пройдена!</b>\n\n"
            f"Открыт последний символ: <code>{unlocked_char}</code>\n"
            f"Ваш персональный промокод (7 символов): <code>{updated_rec['promo_code']}</code>\n\n"
            f"Активируйте его в разделе <b>«Промокод»</b> и получите <b>+3 дня</b> подписки (работает после покупки подписки или вложения в сервис)."
        )
        done_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🎟 Перейти в Промокод", callback_data="nav:promo")],
                [back_button("nav:start")],
            ]
        )
        await send_or_edit_callback(callback, done_text, done_kb)


@dp.message(Command("sub"))
async def cmd_sub(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return
    parts = (command.args or "").split()
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        await message.answer(
            "Формат: <code>/sub USER_ID ДНИ [УСТРОЙСТВА]</code>\n"
            "Пример: <code>/sub 5922226163 30 5</code>",
            parse_mode="HTML",
        )
        return
    target_id = int(parts[0])
    days = int(parts[1])
    devices = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 10
    add_subscription_days(target_id, days, devices, mark_paid=True)
    await message.answer(
        f"✅ Выдана подписка пользователю <code>{target_id}</code> на <b>{days} дн.</b> (устройств: <b>{devices}</b>).\n"
        f"Статус: {format_subscription_status(target_id)}",
        parse_mode="HTML",
    )


@dp.message(Command("unsub"))
async def cmd_unsub(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return
    target_raw = (command.args or "").strip()
    if not target_raw.isdigit():
        await message.answer(
            "Формат: <code>/unsub USER_ID</code>\n"
            "Пример: <code>/unsub 5922226163</code>",
            parse_mode="HTML",
        )
        return
    target_id = int(target_raw)
    rec = get_user_record(target_id)
    save_user_record(target_id, None, 3, rec["referrals"])
    await message.answer(
        f"🗑 Подписка пользователя <code>{target_id}</code> отозвана.\n"
        f"Статус: {format_subscription_status(target_id)}",
        parse_mode="HTML",
    )


# Обработка быстрых подсказок из нижней клавиатуры чата
@dp.message(F.text == "🏠 Меню (/start)")
async def hint_start(message: Message, state: FSMContext):
    await state.clear()
    if not await _ensure_access_gates(message, message.from_user.id, state):
        return
    text, kb = build_start_message(message.from_user.id)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.message(F.text == "👤 Кабинет")
async def hint_cabinet(message: Message, state: FSMContext):
    await state.clear()
    if not await _ensure_access_gates(message, message.from_user.id, state):
        return
    text, kb = build_cabinet_message(message.from_user.id)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.message(F.text == "💳 Купить подписку")
async def hint_tariffs(message: Message, state: FSMContext):
    await state.clear()
    if not await _ensure_access_gates(message, message.from_user.id, state):
        return
    text, kb = build_tariffs_message(message.from_user.id)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.message(F.text == "🎟 Промокод")
async def hint_promo(message: Message, state: FSMContext):
    await state.clear()
    if not await _ensure_access_gates(message, message.from_user.id, state):
        return
    text, kb = build_promo_message(message.from_user.id)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.message(F.text == "👥 Добавить друзей")
async def hint_referral(message: Message, state: FSMContext):
    await state.clear()
    if not await _ensure_access_gates(message, message.from_user.id, state):
        return
    text, kb = build_referral_message(message.from_user.id)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.message(F.text == "🆘 Поддержка")
async def hint_support(message: Message, state: FSMContext):
    if not await _ensure_access_gates(message, message.from_user.id, state):
        return
    await state.set_state(UserFlowStates.waiting_support_message)
    await message.answer(
        "🆘 <b>Поддержка</b>\n\nНапишите ваше сообщение следующим сообщением — я передам его администратору.",
        parse_mode="HTML",
        reply_markup=support_keyboard(),
    )


# Навигация по inline-кнопкам
@dp.callback_query(F.data == "nav:start")
async def cb_nav_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await _ensure_access_gates(callback, callback.from_user.id, state):
        return
    text, kb = build_start_message(callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "nav:cabinet")
async def cb_nav_cabinet(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await _ensure_access_gates(callback, callback.from_user.id, state):
        return
    text, kb = build_cabinet_message(callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "nav:manage_sub")
async def cb_nav_manage_sub(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await _ensure_access_gates(callback, callback.from_user.id, state):
        return
    text, kb = build_manage_sub_message(callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "nav:devices")
async def cb_nav_devices(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await _ensure_access_gates(callback, callback.from_user.id, state):
        return
    text, kb = build_devices_message(callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "dev:copy_link")
async def cb_dev_copy_link(callback: CallbackQuery, state: FSMContext):
    if not await _ensure_access_gates(callback, callback.from_user.id, state):
        return
    invite_link = build_device_invite_link(callback.from_user.id)
    await callback.answer("Ссылка для добавления устройства отправлена ниже", show_alert=False)
    await callback.message.answer(
        f"🔗 <b>Ссылка для добавления устройства:</b>\n<code>{invite_link}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button("nav:devices")]]),
    )


@dp.callback_query(F.data.startswith("dev:del:"))
async def cb_dev_del(callback: CallbackQuery, state: FSMContext):
    if not await _ensure_access_gates(callback, callback.from_user.id, state):
        return
    raw_id = callback.data.split(":", 2)[2]
    if raw_id.isdigit():
        remove_user_device(callback.from_user.id, int(raw_id))
    text, kb = build_devices_message(callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "nav:tariffs")
async def cb_nav_tariffs(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await _ensure_access_gates(callback, callback.from_user.id, state):
        return
    text, kb = build_tariffs_message(callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "nav:promo")
async def cb_nav_promo(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await _ensure_access_gates(callback, callback.from_user.id, state):
        return
    text, kb = build_promo_message(callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "promo:enter")
async def cb_promo_enter(callback: CallbackQuery, state: FSMContext):
    if not await _ensure_access_gates(callback, callback.from_user.id, state):
        return
    rec = get_user_record(callback.from_user.id)
    if not rec["has_paid"]:
        await callback.answer("Сначала купите подписку или сделайте вложение в сервис", show_alert=True)
        return
    if rec["promo_used"]:
        await callback.answer("Вы уже использовали промокод", show_alert=True)
        return
    await state.set_state(UserFlowStates.waiting_promo_input)
    await callback.answer()
    await callback.message.answer(
        "✍️ Отправьте ваш промокод из 7 символов сообщением:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button("nav:promo")]]),
    )


@dp.message(UserFlowStates.waiting_promo_input)
async def handle_promo_input(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not await _ensure_access_gates(message, user_id, state):
        return

    rec = get_user_record(user_id)
    if not rec["has_paid"]:
        await state.clear()
        await message.answer(
            "🔒 Промокод работает только после того, как вы <b>хотя бы один раз купите подписку или сделаете любое вложение в сервис</b>.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="💳 Купить подписку", callback_data="nav:tariffs")],
                    [back_button("nav:promo")],
                ]
            ),
        )
        return

    if rec["promo_used"]:
        await state.clear()
        await message.answer(
            "✅ Ваш промокод уже был активирован ранее.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button("nav:promo")]]),
        )
        return

    entered = (message.text or "").strip().upper()
    if entered != rec["promo_code"] or rec["promo_progress"] < 7:
        await message.answer(
            "❌ Неверный промокод или открыты не все 7 символов через <code>/minigame</code>. Проверьте код и попробуйте снова.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button("nav:promo")]]),
        )
        return

    save_user_record(
        user_id=user_id,
        expires_at=rec["expires_at"],
        devices=rec["devices"],
        referrals=rec["referrals"],
        promo_used=1,
    )
    add_subscription_days(user_id, 3, mark_paid=True)
    await state.clear()
    await message.answer(
        f"✅ Промокод <code>{entered}</code> активирован! Вам начислено <b>+3 дня</b> подписки.\n"
        f"Статус: {format_subscription_status(user_id)}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button("nav:start")]]),
    )


@dp.callback_query(F.data == "nav:referral")
async def cb_nav_referral(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if not await _ensure_access_gates(callback, callback.from_user.id, state):
        return
    text, kb = build_referral_message(callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "ref:copy")
async def cb_ref_copy(callback: CallbackQuery, state: FSMContext):
    if not await _ensure_access_gates(callback, callback.from_user.id, state):
        return
    rec = get_user_record(callback.from_user.id)
    if not rec["has_paid"]:
        await callback.answer("Доступно только после покупки подписки", show_alert=True)
        return
    ref_link = build_referral_link(callback.from_user.id)
    await callback.answer("Ссылка отправлена ниже для удобного копирования", show_alert=False)
    await callback.message.answer(
        f"📋 <b>Ваша ссылка для приглашения друзей:</b>\n<code>{ref_link}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button("nav:referral")]]),
    )


@dp.callback_query(F.data == "nav:support")
async def cb_nav_support(callback: CallbackQuery, state: FSMContext):
    if not await _ensure_access_gates(callback, callback.from_user.id, state):
        return
    await state.set_state(UserFlowStates.waiting_support_message)
    text = (
        "🆘 <b>Поддержка</b>\n\n"
        "Напишите ваш вопрос или описание проблемы следующим сообщением — оно придёт напрямую администратору."
    )
    await send_or_edit_callback(callback, text, support_keyboard())


@dp.callback_query(F.data.startswith("buy:"))
async def cb_buy_plan(callback: CallbackQuery, state: FSMContext):
    if not await _ensure_access_gates(callback, callback.from_user.id, state):
        return
    plan_key = callback.data.split(":", 1)[1]
    if plan_key not in PLANS:
        await callback.answer("Тариф не найден", show_alert=True)
        return
    text, kb = build_plan_invoice_text(plan_key, callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data.startswith("confirm_pay:"))
async def cb_confirm_pay(callback: CallbackQuery, state: FSMContext):
    if not await _ensure_access_gates(callback, callback.from_user.id, state):
        return
    plan_key = callback.data.split(":", 1)[1]
    plan = PLANS.get(plan_key)
    if not plan:
        await callback.answer("Ошибка тарифа", show_alert=True)
        return

    user_id = callback.from_user.id
    add_subscription_days(user_id, plan["days"], plan["devices"], mark_paid=True)

    await callback.message.answer(
        f"✅ Заявка на тариф <b>{plan['title']} ({plan['price']} руб.)</b> оформлена для ID <code>{user_id}</code>.\n"
        f"Статус подписки: {format_subscription_status(user_id)}\n"
        f"Подключите сюда свой платёжный шлюз (ЮKassa / Stars / CryptoBot) для реального списания средств.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="👤 Перейти в кабинет", callback_data="nav:cabinet")],
                [back_button("nav:tariffs")],
            ]
        ),
    )
    await callback.answer()


# Приём сообщения пользователя в поддержку и пересылка администратору (ADMIN_ID = 5922226163)
@dp.message(UserFlowStates.waiting_support_message)
async def handle_support_message(message: Message, state: FSMContext):
    user = message.from_user
    username_str = f"@{user.username}" if user.username else "без username"
    admin_text = (
        f"📩 <b>Новое обращение в поддержку</b>\n\n"
        f"👤 От: <b>{user.full_name}</b> ({username_str})\n"
        f"🆔 Telegram ID: <code>{user.id}</code>\n\n"
        f"💬 Сообщение:\n{message.html_text or message.text or '[медиа/вложение]'}"
    )

    try:
        await bot.send_message(chat_id=ADMIN_ID, text=admin_text, parse_mode="HTML")
        if message.text is None:
            await message.copy_to(chat_id=ADMIN_ID)
    except Exception as exc:
        logging.error(f"Failed to forward support message to ADMIN_ID={ADMIN_ID}: {exc}")

    await state.clear()
    await message.answer(
        "✅ Ваше сообщение отправлено в поддержку! Ожидайте ответ администратора.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button("nav:start")]]),
    )


async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="OK — Maestro VPN bot is running")


async def start_http_server():
    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"HTTP health server listening on port {PORT}")


async def on_startup():
    init_db()
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню и кабинет"),
            BotCommand(command="minigame", description="Мини-игра за символы промокода"),
        ]
    )


async def main():
    dp.startup.register(on_startup)
    await start_http_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
