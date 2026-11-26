import asyncio
import logging
import os
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

# 8 символов: латинские буквы и цифры 1-9 (без 0)
SUB_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz123456789"


class UserFlowStates(StatesGroup):
    waiting_support_message = State()
    waiting_join_code = State()
    waiting_email = State()


logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def generate_sub_code() -> str:
    return "".join(secrets.choice(SUB_CODE_ALPHABET) for _ in range(12))


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
                sub_code TEXT UNIQUE,
                expires_at TEXT,
                devices INTEGER NOT NULL DEFAULT 3,
                referrals INTEGER NOT NULL DEFAULT 0,
                email TEXT,
                email_bonus_claimed INTEGER NOT NULL DEFAULT 0,
                allow_add_devices INTEGER NOT NULL DEFAULT 1,
                auto_renew INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        _ensure_column(conn, "users", "sub_code", "TEXT")
        _ensure_column(conn, "users", "email", "TEXT")
        _ensure_column(conn, "users", "email_bonus_claimed", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "users", "allow_add_devices", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "users", "auto_renew", "INTEGER NOT NULL DEFAULT 1")

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

        # Выдаём уникальный код всем пользователям, у которых его ещё нет
        rows = conn.execute("SELECT user_id, sub_code FROM users").fetchall()
        for uid, code in rows:
            if not code or not re.fullmatch(r"[A-Za-z1-9]{12}", str(code)):
                while True:
                    new_code = generate_sub_code()
                    exists = conn.execute(
                        "SELECT 1 FROM users WHERE sub_code = ?", (new_code,)
                    ).fetchone()
                    if not exists:
                        conn.execute(
                            "UPDATE users SET sub_code = ? WHERE user_id = ?",
                            (new_code, uid),
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
            SELECT sub_code, expires_at, devices, referrals, email, email_bonus_claimed,
                   allow_add_devices, auto_renew
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            while True:
                sub_code = generate_sub_code()
                if not conn.execute("SELECT 1 FROM users WHERE sub_code = ?", (sub_code,)).fetchone():
                    break
            conn.execute(
                """
                INSERT INTO users (
                    user_id, sub_code, expires_at, devices, referrals,
                    email, email_bonus_claimed, allow_add_devices, auto_renew
                )
                VALUES (?, ?, NULL, 3, 0, NULL, 0, 1, 1)
                """,
                (user_id, sub_code),
            )
            conn.commit()
            return {
                "sub_code": sub_code,
                "expires_at": None,
                "devices": 3,
                "referrals": 0,
                "email": None,
                "email_bonus_claimed": 0,
                "allow_add_devices": 1,
                "auto_renew": 1,
            }

        sub_code = row[0]
        if not sub_code or not re.fullmatch(r"[A-Za-z1-9]{8}", str(sub_code)):
            while True:
                sub_code = generate_sub_code()
                if not conn.execute("SELECT 1 FROM users WHERE sub_code = ?", (sub_code,)).fetchone():
                    break
            conn.execute("UPDATE users SET sub_code = ? WHERE user_id = ?", (sub_code, user_id))
            conn.commit()

        return {
            "sub_code": sub_code,
            "expires_at": _parse_dt(row[1]),
            "devices": int(row[2] if row[2] is not None else 3),
            "referrals": int(row[3] if row[3] is not None else 0),
            "email": row[4],
            "email_bonus_claimed": int(row[5] if row[5] is not None else 0),
            "allow_add_devices": int(row[6] if row[6] is not None else 1),
            "auto_renew": int(row[7] if row[7] is not None else 1),
        }


def find_user_by_code(code: str) -> int | None:
    clean = code.strip()
    if not re.fullmatch(r"[A-Za-z1-9]{12}", clean):
        return None
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT user_id FROM users WHERE sub_code = ?",
            (clean,),
        ).fetchone()
    return int(row[0]) if row else None


def save_user_record(
    user_id: int,
    expires_at: datetime | None,
    devices: int,
    referrals: int,
    sub_code: str | None = None,
    email: str | None = None,
    email_bonus_claimed: int | None = None,
    allow_add_devices: int | None = None,
    auto_renew: int | None = None,
) -> None:
    current = get_user_record(user_id)
    final_code = current["sub_code"]  # Код менять нельзя
    final_email = email if email is not None else current["email"]
    final_bonus = email_bonus_claimed if email_bonus_claimed is not None else current["email_bonus_claimed"]
    final_allow = allow_add_devices if allow_add_devices is not None else current["allow_add_devices"]
    final_auto = auto_renew if auto_renew is not None else current["auto_renew"]
    iso_val = expires_at.astimezone(timezone.utc).isoformat() if expires_at else None

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE users
            SET sub_code = ?,
                expires_at = ?,
                devices = ?,
                referrals = ?,
                email = ?,
                email_bonus_claimed = ?,
                allow_add_devices = ?,
                auto_renew = ?
            WHERE user_id = ?
            """,
            (
                final_code,
                iso_val,
                devices,
                referrals,
                final_email,
                final_bonus,
                final_allow,
                final_auto,
                user_id,
            ),
        )
        conn.commit()


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


def increment_referral(inviter_id: int) -> None:
    rec = get_user_record(inviter_id)
    save_user_record(
        user_id=inviter_id,
        expires_at=rec["expires_at"],
        devices=rec["devices"],
        referrals=rec["referrals"] + 1,
    )


def add_subscription_days(user_id: int, days: int, devices: int | None = None) -> None:
    rec = get_user_record(user_id)
    now = datetime.now(timezone.utc)
    base = rec["expires_at"] if (rec["expires_at"] and rec["expires_at"] > now) else now
    new_expires = base + timedelta(days=days)
    target_devices = devices if devices is not None else rec["devices"]
    save_user_record(
        user_id=user_id,
        expires_at=new_expires,
        devices=target_devices,
        referrals=rec["referrals"],
    )


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


def back_button(target: str = "nav:start") -> InlineKeyboardButton:
    return InlineKeyboardButton(text="⬅️ Вернуться назад", callback_data=target)


def reply_hints_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏠 Меню (/start)"), KeyboardButton(text="👤 Кабинет")],
            [KeyboardButton(text="💎 Тарифы"), KeyboardButton(text="👥 Добавить друзей")],
            [KeyboardButton(text="🆘 Поддержка"), KeyboardButton(text="❓ Помощь (/help)")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите подсказку или напишите сообщение...",
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
                InlineKeyboardButton(text="💎 Тарифы", callback_data="nav:tariffs"),
            ],
            [
                InlineKeyboardButton(text="⚙️ Управление подпиской", callback_data="nav:manage_sub"),
            ],
            [
                InlineKeyboardButton(text="📱 Устройства", callback_data="nav:devices"),
                InlineKeyboardButton(text="📲 Зайти с устройства", callback_data="nav:join_device"),
            ],
            [
                InlineKeyboardButton(text="✉️ Привязать почту (+2 дн.)", callback_data="nav:email"),
                InlineKeyboardButton(text="🛠 Настройки", callback_data="nav:settings"),
            ],
            [
                InlineKeyboardButton(text="👥 Добавить друзей", callback_data="nav:referral"),
                InlineKeyboardButton(text="🆘 Поддержка", callback_data="nav:support"),
            ],
            [
                InlineKeyboardButton(text="❓ Справка (/help)", callback_data="nav:help"),
            ],
        ]
    )


def cabinet_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Продлить подписку", callback_data="nav:tariffs")],
            [InlineKeyboardButton(text="⚙️ Управление подпиской", callback_data="nav:manage_sub")],
            [InlineKeyboardButton(text="📱 Устройства", callback_data="nav:devices")],
            [InlineKeyboardButton(text="✉️ Привязать почту (+2 дн.)", callback_data="nav:email")],
            [InlineKeyboardButton(text="🛠 Настройки", callback_data="nav:settings")],
            [back_button("nav:start")],
        ]
    )


def manage_sub_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📱 Устройства", callback_data="nav:devices")],
            [InlineKeyboardButton(text="📲 Зайти с устройства", callback_data="nav:join_device")],
            [InlineKeyboardButton(text="🛠 Настройки", callback_data="nav:settings")],
            [InlineKeyboardButton(text="🔄 Продлить подписку", callback_data="nav:tariffs")],
            [back_button("nav:cabinet")],
        ]
    )


def devices_keyboard(user_id: int) -> InlineKeyboardMarkup:
    devices = get_user_devices(user_id)
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
    rows.append([InlineKeyboardButton(text="➕ Добавить устройство", callback_data="dev:add_show_code")])
    rows.append([InlineKeyboardButton(text="📲 Зайти с устройства", callback_data="nav:join_device")])
    rows.append([InlineKeyboardButton(text="🛠 Настройки", callback_data="nav:settings")])
    rows.append([back_button("nav:manage_sub")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rec = get_user_record(user_id)
    add_dev_label = (
        "🔓 Добавление устройств: ВКЛ (нажмите, чтобы отключить)"
        if rec["allow_add_devices"]
        else "🔒 Добавление устройств: ВЫКЛ (нажмите, чтобы включить)"
    )
    auto_renew_label = (
        "🔁 Автопродление: ВКЛ (рекомендовано выключить)"
        if rec["auto_renew"]
        else "⏹ Автопродление: ВЫКЛ (нажмите, чтобы включить)"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=add_dev_label, callback_data="toggle:allow_add")],
            [InlineKeyboardButton(text=auto_renew_label, callback_data="toggle:auto_renew")],
            [back_button("nav:manage_sub")],
        ]
    )


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
        f"💎 <b>Maestro VPN — Оформление тарифа</b>\n\n"
        f"• Тариф: <b>{plan['title']}</b> ({plan['days']} дн.)\n"
        f"• Лимит устройств: <b>до {plan['devices']}</b>\n"
        f"• К оплате: <b>{plan['price']} руб.</b>\n"
        f"• Ваш Telegram ID: <code>{user_id}</code>\n\n"
        f"Нажмите кнопку ниже для оформления или вернитесь назад к тарифам."
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


def help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👤 Посмотреть статус подписки", callback_data="nav:cabinet")],
            [InlineKeyboardButton(text="⚙️ Управление подпиской", callback_data="nav:manage_sub")],
            [InlineKeyboardButton(text="💎 Перейти к тарифам", callback_data="nav:tariffs")],
            [InlineKeyboardButton(text="👥 Пригласить друзей", callback_data="nav:referral")],
            [InlineKeyboardButton(text="🆘 Написать в поддержку", callback_data="nav:support")],
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
        f"✦ <b>Maestro VPN — Главное меню</b>\n\n"
        f"🆔 Ваш Telegram ID: <code>{user_id}</code>\n"
        f"🔑 Код вашей подписки (сменить нельзя): <code>{rec['sub_code']}</code>\n"
        f"📊 Статус: {format_subscription_status(user_id)}\n\n"
        f"Выберите нужный раздел на кнопках ниже или воспользуйтесь подсказками клавиатуры:"
    )
    return text, start_keyboard()


def build_cabinet_message(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    rec = get_user_record(user_id)
    devs = get_user_devices(user_id)
    email_line = f"<code>{rec['email']}</code>" if rec["email"] else "не привязана (+2 дня при привязке)"
    text = (
        f"👤 <b>Личный кабинет</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"🔑 Код подписки: <code>{rec['sub_code']}</code>\n"
        f"📅 Когда кончится подписка: {format_subscription_status(user_id)}\n"
        f"📱 Подключено устройств: <b>{len(devs)} / {rec['devices']}</b>\n"
        f"✉️ Почта: {email_line}\n"
        f"👥 Приглашено друзей: <b>{rec['referrals']}</b>"
    )
    return text, cabinet_keyboard()


def build_manage_sub_message(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    rec = get_user_record(user_id)
    devs = get_user_devices(user_id)
    add_status = "разрешено" if rec["allow_add_devices"] else "заблокировано"
    renew_status = "включено" if rec["auto_renew"] else "выключено"
    text = (
        f"⚙️ <b>Управление подпиской</b>\n\n"
        f"🔑 Код подписки: <code>{rec['sub_code']}</code>\n"
        f"📊 Статус: {format_subscription_status(user_id)}\n"
        f"📱 Устройств: <b>{len(devs)} из {rec['devices']}</b>\n"
        f"🔒 Добавление устройств: <b>{add_status}</b>\n"
        f"🔁 Автопродление: <b>{renew_status}</b>"
    )
    return text, manage_sub_keyboard()


def build_devices_message(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    rec = get_user_record(user_id)
    devs = get_user_devices(user_id)
    if not devs:
        list_block = "Пока нет подключённых устройств."
    else:
        lines = []
        for i, d in enumerate(devs, start=1):
            lines.append(f"{i}. <b>{d['name']}</b> <i>(добавлено {d['added_at']})</i>")
        list_block = "\n".join(lines)

    text = (
        f"📱 <b>Ваши устройства ({len(devs)} / {rec['devices']})</b>\n"
        f"🔑 Код подписки: <code>{rec['sub_code']}</code>\n\n"
        f"{list_block}\n\n"
        f"Нажмите «Добавить устройство», чтобы показать код для подключения другого устройства."
    )
    return text, devices_keyboard(user_id)


def build_settings_message(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    rec = get_user_record(user_id)
    add_state = "✅ Разрешено" if rec["allow_add_devices"] else "🔒 Заблокировано"
    renew_state = "✅ Включено (рекомендовано выключить)" if rec["auto_renew"] else "❌ Выключено"
    text = (
        f"🛠 <b>Настройки подписки и безопасности</b>\n\n"
        f"1. <b>Добавление устройств по коду:</b> {add_state}\n"
        f"<i>Если кто-то увидел ваш код в эфире, отключите эту функцию — по коду никто не сможет зайти.</i>\n\n"
        f"2. <b>Автопродление подписки:</b> {renew_state}\n"
        f"<i>Система автоматически списывает средства и продлевает подписку. Если автопродление не нужно — выключите функцию (рекомендовано выключить).</i>"
    )
    return text, settings_keyboard(user_id)


def build_email_message(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    rec = get_user_record(user_id)
    if rec["email"]:
        status_block = f"Привязанная почта: <b>{rec['email']}</b>\nБонус +2 дня уже начислен."
        kb = InlineKeyboardMarkup(inline_keyboard=[[back_button("nav:cabinet")]])
    else:
        status_block = "Почта ещё не привязана. Привяжите почту и получите <b>+2 дня</b> к подписке!"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✉️ Ввести почту", callback_data="email:enter")],
                [back_button("nav:cabinet")],
            ]
        )
    text = f"✉️ <b>Привязка почты</b>\n\n{status_block}"
    return text, kb


def build_tariffs_message(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    servers_lines = "\n".join(f"• {srv}" for srv in AVAILABLE_SERVERS)
    text = (
        f"🌐 <b>Имеющиеся сервера в Maestro VPN:</b>\n"
        f"{servers_lines}\n\n"
        f"💎 <b>Тарифы Maestro VPN</b>\n\n"
        f"• <b>1 месяц</b> — <b>69 ₽</b> (до 3 устройств, 30 дн.)\n"
        f"• <b>3 месяца</b> — <b>199 ₽</b> (до 5 устройств, 90 дн.)\n"
        f"• <b>6 месяцев</b> — <b>399 ₽</b> (до 7 устройств, 180 дн.)\n"
        f"• <b>12 месяцев</b> — <b>799 ₽</b> (до 10 устройств, 365 дн.)\n\n"
        f"Выберите подходящий вариант:"
    )
    return text, tariffs_keyboard()


def build_referral_message(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    ref_link = build_referral_link(user_id)
    rec = get_user_record(user_id)
    text = (
        f"👥 <b>Добавить друзей</b>\n\n"
        f"Приглашено друзей: <b>{rec['referrals']}</b>\n"
        f"Ваша реферальная ссылка:\n"
        f"<code>{ref_link}</code>\n\n"
        f"Нажмите кнопку ниже, чтобы выбрать кому отправить ссылку в Telegram, или скопируйте её."
    )
    return text, referral_keyboard(user_id)


def build_help_message() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"❓ <b>Справка — что делать в разных ситуациях</b>\n\n"
        f"1. <b>Посмотреть статус подписки</b> — откройте «Кабинет», чтобы узнать дату окончания и остаток дней.\n"
        f"2. <b>Продлить или купить подписку</b> — нажмите «Продлить подписку» в кабинете или «Тарифы» в меню.\n"
        f"3. <b>Закончилась подписка</b> — перейдите в «Тарифы» и выберите подходящий срок действия.\n"
        f"4. <b>Нужен личный кабинет Mini App</b> — нажмите кнопку «Открыть приложение».\n"
        f"5. <b>Хотите пригласить друзей</b> — откройте «Добавить друзей» и отправьте свою ссылку.\n"
        f"6. <b>Не знаете, кому отправить ссылку</b> — нажмите «Поделиться в Telegram» и выберите чат или контакт.\n"
        f"7. <b>Нужно скопировать ссылку</b> — нажмите «Скопировать вашу ссылку» в разделе друзей.\n"
        f"8. <b>Возникла проблема с подключением</b> — напишите в «Поддержка», обращение уйдёт администратору.\n"
        f"9. <b>Не приходит ответ от поддержки</b> — проверьте, что в профиле Telegram открыты входящие сообщения.\n"
        f"10. <b>Нужно подключить несколько устройств</b> — используйте код подписки в «Зайти с устройства».\n"
        f"11. <b>Хотите сэкономить на сроке</b> — сравните тарифы на 3, 6 и 12 месяцев в разделе «Тарифы».\n"
        f"12. <b>Потерялись в меню</b> — нажмите «Вернуться назад» в любом разделе или введите /start.\n"
        f"13. <b>Нужны быстрые подсказки</b> — откройте клавиатуру внизу поля ввода сообщения.\n"
        f"14. <b>Нужен ваш Telegram ID</b> — он отображается на старте и в разделе «Кабинет».\n"
        f"15. <b>Остался вопрос по работе сервиса</b> — опишите ситуацию в разделе «Поддержка»."
    )
    return text, help_keyboard()


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
                increment_referral(inviter_id)

    if args.startswith("pay_"):
        raw = args.removeprefix("pay_")
        plan_key = raw.split("_uid_")[0]
        if plan_key in PLANS:
            text, kb = build_plan_invoice_text(plan_key, user_id)
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            return

    start_text, start_kb = build_start_message(user_id)
    await message.answer("⌨️ Подсказки клавиатуры активированы.", reply_markup=reply_hints_keyboard())
    await message.answer(start_text, parse_mode="HTML", reply_markup=start_kb)


@dp.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    text, kb = build_help_message()
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


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
    rec = get_user_record(target_id)
    now = datetime.now(timezone.utc)
    base = rec["expires_at"] if (rec["expires_at"] and rec["expires_at"] > now) else now
    new_expires = base + timedelta(days=days)
    save_user_record(target_id, new_expires, devices, rec["referrals"])
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
    text, kb = build_start_message(message.from_user.id)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.message(F.text == "👤 Кабинет")
async def hint_cabinet(message: Message, state: FSMContext):
    await state.clear()
    text, kb = build_cabinet_message(message.from_user.id)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.message(F.text == "💎 Тарифы")
async def hint_tariffs(message: Message, state: FSMContext):
    await state.clear()
    text, kb = build_tariffs_message(message.from_user.id)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.message(F.text == "👥 Добавить друзей")
async def hint_referral(message: Message, state: FSMContext):
    await state.clear()
    text, kb = build_referral_message(message.from_user.id)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.message(F.text == "🆘 Поддержка")
async def hint_support(message: Message, state: FSMContext):
    await state.set_state(UserFlowStates.waiting_support_message)
    await message.answer(
        "🆘 <b>Поддержка</b>\n\nНапишите ваше сообщение следующим сообщением — я передам его администратору.",
        parse_mode="HTML",
        reply_markup=support_keyboard(),
    )


@dp.message(F.text == "❓ Помощь (/help)")
async def hint_help(message: Message, state: FSMContext):
    await state.clear()
    text, kb = build_help_message()
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


# Навигация по inline-кнопкам
@dp.callback_query(F.data == "nav:start")
async def cb_nav_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = build_start_message(callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "nav:cabinet")
async def cb_nav_cabinet(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = build_cabinet_message(callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "nav:manage_sub")
async def cb_nav_manage_sub(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = build_manage_sub_message(callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "nav:devices")
async def cb_nav_devices(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = build_devices_message(callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "dev:add_show_code")
async def cb_dev_add_show_code(callback: CallbackQuery):
    user_id = callback.from_user.id
    rec = get_user_record(user_id)
    if not rec["allow_add_devices"]:
        await callback.answer("Добавление устройств отключено", show_alert=False)
        await callback.message.answer(
            "🔒 <b>Добавление устройств отключено</b>\n\n"
            "Вам нужно зайти в <b>Настройки</b> и включить эту функцию, чтобы можно было добавлять устройства.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🛠 Открыть настройки", callback_data="nav:settings")],
                    [back_button("nav:devices")],
                ]
            ),
        )
        return

    await callback.answer()
    await callback.message.answer(
        f"🔑 Ваш код подписки: <code>{rec['sub_code']}</code>\n\n"
        f"Введите этот код на другом устройстве в разделе <b>«Зайти с устройства»</b>.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button("nav:devices")]]),
    )


@dp.callback_query(F.data == "nav:join_device")
async def cb_nav_join_device(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserFlowStates.waiting_join_code)
    text = (
        "📲 <b>Зайти с устройства</b>\n\n"
        "Напишите в чат с ботом код подписки из 12 символов (буквы и цифры без нуля), чтобы подключить это устройство."
    )
    await send_or_edit_callback(
        callback,
        text,
        InlineKeyboardMarkup(inline_keyboard=[[back_button("nav:start")]]),
    )


@dp.message(UserFlowStates.waiting_join_code)
async def handle_join_device_code(message: Message, state: FSMContext):
    code = (message.text or "").strip()
    if not re.fullmatch(r"[A-Za-z1-9]{12}", code):
        await message.answer(
            "❌ Код должен состоять ровно из 12 символов (буквы и цифры, кроме 0). Попробуйте ещё раз или нажмите «Вернуться назад».",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button("nav:start")]]),
        )
        return

    owner_id = find_user_by_code(code)
    if owner_id is None:
        await message.answer(
            "❌ Подписка с таким кодом не найдена. Проверьте код и отправьте снова.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button("nav:start")]]),
        )
        return

    owner_rec = get_user_record(owner_id)
    if not owner_rec["allow_add_devices"]:
        await message.answer(
            "🔒 <b>Подключение по коду заблокировано</b>\n\n"
            "Владелец подписки отключил добавление устройств. Чтобы зайти, нужно зайти в <b>Настройки</b> и включить эту функцию.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button("nav:start")]]),
        )
        return

    devs = get_user_devices(owner_id)
    if len(devs) >= owner_rec["devices"]:
        await message.answer(
            f"❌ Достигнут лимит устройств по этой подписке (<b>{len(devs)} / {owner_rec['devices']}</b>).",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button("nav:start")]]),
        )
        return

    device_label = f"Устройство #{len(devs) + 1} (ID {message.from_user.id})"
    add_user_device(owner_id, device_label)
    await state.clear()
    await message.answer(
        f"✅ Устройство успешно подключено к подписке по коду <code>{code}</code>!\n"
        f"Статус подписки: {format_subscription_status(owner_id)}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📱 Посмотреть устройства", callback_data="nav:devices")],
                [back_button("nav:start")],
            ]
        ),
    )


@dp.callback_query(F.data == "nav:email")
async def cb_nav_email(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = build_email_message(callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "email:enter")
async def cb_email_enter(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserFlowStates.waiting_email)
    await send_or_edit_callback(
        callback,
        "✉️ <b>Привязка почты</b>\n\nВведите ваш e-mail следующим сообщением и получите <b>+2 дня</b> подписки.",
        InlineKeyboardMarkup(inline_keyboard=[[back_button("nav:email")]]),
    )


@dp.message(UserFlowStates.waiting_email)
async def handle_email_input(message: Message, state: FSMContext):
    raw_email = (message.text or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", raw_email):
        await message.answer(
            "❌ Введите корректный адрес электронной почты (например, <code>name@example.com</code>).",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button("nav:email")]]),
        )
        return

    user_id = message.from_user.id
    rec = get_user_record(user_id)
    bonus_days = 0
    if not rec["email_bonus_claimed"]:
        add_subscription_days(user_id, 2)
        bonus_days = 2

    save_user_record(
        user_id=user_id,
        expires_at=get_user_record(user_id)["expires_at"],
        devices=rec["devices"],
        referrals=rec["referrals"],
        email=raw_email,
        email_bonus_claimed=1,
    )
    await state.clear()

    bonus_text = "\n🎁 Вам начислено <b>+2 дня</b> подписки!" if bonus_days else ""
    await message.answer(
        f"✅ Почта <b>{raw_email}</b> привязана к аккаунту.{bonus_text}\n"
        f"Статус: {format_subscription_status(user_id)}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button("nav:cabinet")]]),
    )


@dp.callback_query(F.data == "nav:settings")
async def cb_nav_settings(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = build_settings_message(callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "toggle:allow_add")
async def cb_toggle_allow_add(callback: CallbackQuery):
    user_id = callback.from_user.id
    rec = get_user_record(user_id)
    new_val = 0 if rec["allow_add_devices"] else 1
    save_user_record(
        user_id=user_id,
        expires_at=rec["expires_at"],
        devices=rec["devices"],
        referrals=rec["referrals"],
        allow_add_devices=new_val,
    )
    text, kb = build_settings_message(user_id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "toggle:auto_renew")
async def cb_toggle_auto_renew(callback: CallbackQuery):
    user_id = callback.from_user.id
    rec = get_user_record(user_id)
    new_val = 0 if rec["auto_renew"] else 1
    save_user_record(
        user_id=user_id,
        expires_at=rec["expires_at"],
        devices=rec["devices"],
        referrals=rec["referrals"],
        auto_renew=new_val,
    )
    text, kb = build_settings_message(user_id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data.startswith("dev:del:"))
async def cb_dev_del(callback: CallbackQuery):
    raw_id = callback.data.split(":", 2)[2]
    if raw_id.isdigit():
        remove_user_device(callback.from_user.id, int(raw_id))
    text, kb = build_devices_message(callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "nav:tariffs")
async def cb_nav_tariffs(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = build_tariffs_message(callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "nav:referral")
async def cb_nav_referral(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = build_referral_message(callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "ref:copy")
async def cb_ref_copy(callback: CallbackQuery):
    ref_link = build_referral_link(callback.from_user.id)
    await callback.answer("Ссылка отправлена ниже для удобного копирования", show_alert=False)
    await callback.message.answer(
        f"📋 <b>Ваша ссылка для приглашения друзей:</b>\n<code>{ref_link}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button("nav:referral")]]),
    )


@dp.callback_query(F.data == "nav:help")
async def cb_nav_help(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = build_help_message()
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "nav:support")
async def cb_nav_support(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserFlowStates.waiting_support_message)
    text = (
        "🆘 <b>Поддержка</b>\n\n"
        "Напишите ваш вопрос или описание проблемы следующим сообщением — оно придёт напрямую администратору."
    )
    await send_or_edit_callback(callback, text, support_keyboard())


@dp.callback_query(F.data.startswith("buy:"))
async def cb_buy_plan(callback: CallbackQuery):
    plan_key = callback.data.split(":", 1)[1]
    if plan_key not in PLANS:
        await callback.answer("Тариф не найден", show_alert=True)
        return
    text, kb = build_plan_invoice_text(plan_key, callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data.startswith("confirm_pay:"))
async def cb_confirm_pay(callback: CallbackQuery):
    plan_key = callback.data.split(":", 1)[1]
    plan = PLANS.get(plan_key)
    if not plan:
        await callback.answer("Ошибка тарифа", show_alert=True)
        return

    user_id = callback.from_user.id
    add_subscription_days(user_id, plan["days"], plan["devices"])

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
            BotCommand(command="help", description="Справка по всем ситуациям"),
        ]
    )


async def main():
    dp.startup.register(on_startup)
    await start_http_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
