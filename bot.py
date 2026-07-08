import asyncio
import logging
import re

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    CallbackQuery,
    Message,
)

from config import BOT_TOKEN, RECENT_MATCHES_COUNT
import sofascore_client as sc
import predict_football
import predict_tennis
import fbref_client
import tennis_extra_stats
import browser_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class Flow(StatesGroup):
    choosing_sport = State()
    entering_match = State()


DISCLAIMER = (
    "\n\n⚠️ Это статистическая оценка на основе истории матчей, "
    "а не гарантия результата. Играйте ответственно."
)


def sport_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⚽ Футбол", callback_data="sport:football"),
                InlineKeyboardButton(text="🎾 Теннис", callback_data="sport:tennis"),
            ]
        ]
    )


def split_names(text: str) -> tuple[str, str] | None:
    """Разбирает 'Команда1 - Команда2' / 'Игрок1 vs Игрок2' и т.п."""
    parts = re.split(r"\s*-\s*|\s+vs\.?\s+|\s+—\s+", text.strip(), maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0].strip(), parts[1].strip()


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.set_state(Flow.choosing_sport)
    await message.answer(
        "Привет! Я прогнозирую футбольные и теннисные матчи на основе статистики.\n\n"
        "Выбери вид спорта:",
        reply_markup=sport_keyboard(),
    )


@dp.callback_query(F.data.startswith("sport:"))
async def choose_sport(callback: CallbackQuery, state: FSMContext):
    sport = callback.data.split(":", 1)[1]
    await state.update_data(sport=sport)
    await state.set_state(Flow.entering_match)

    if sport == "football":
        example = "Реал Мадрид - Барселона"
    else:
        example = "Синнер - Алькарас"

    await callback.message.edit_text(
        f"Введи матч в формате:\n<b>{example}</b>",
        parse_mode="HTML",
    )
    await callback.answer()


@dp.message(Flow.entering_match)
async def handle_match(message: Message, state: FSMContext):
    data = await state.get_data()
    sport = data.get("sport")

    names = split_names(message.text or "")
    if not names:
        await message.answer(
            "Не разобрал названия. Введи в формате 'Команда1 - Команда2' "
            "(или 'Игрок1 - Игрок2')."
        )
        return

    name_a, name_b = names
    status_msg = await message.answer("Ищу данные, считаю прогноз… ⏳")

    try:
        if sport == "football":
            result_text = await build_football_prediction(name_a, name_b)
        else:
            result_text = await build_tennis_prediction(name_a, name_b)
    except sc.SofascoreError as e:
        logger.warning(f"Ошибка источника данных: {e}")
        result_text = (
            "Не удалось получить статистику по одной из команд/игроков. "
            "Проверь написание названия или попробуй позже — источник данных "
            "может быть временно недоступен."
        )
    except Exception:
        logger.exception("Неожиданная ошибка при построении прогноза")
        result_text = "Произошла ошибка при расчёте прогноза. Попробуй ещё раз позже."

    await status_msg.edit_text(result_text)
    await state.set_state(Flow.choosing_sport)
    await message.answer("Хочешь разобрать ещё один матч?", reply_markup=sport_keyboard())


async def build_football_prediction(name_home: str, name_away: str) -> str:
    home_entity = await sc.search_entity(name_home, entity_type="team")
    away_entity = await sc.search_entity(name_away, entity_type="team")

    if not home_entity or not away_entity:
        return (
            "Не нашёл одну из команд по названию.\n\n"
            "Попробуй ввести название на английском (например, Real Madrid "
            "вместо Реал Мадрид) — источник статистики иногда не находит "
            "команды по русскому написанию."
        )

    home_events = await sc.get_team_recent_events(home_entity["id"], RECENT_MATCHES_COUNT)
    away_events = await sc.get_team_recent_events(away_entity["id"], RECENT_MATCHES_COUNT)

    home_stats = predict_football.extract_goals_for_team(home_events, home_entity["id"])
    away_stats = predict_football.extract_goals_for_team(away_events, away_entity["id"])

    # Доп. статистика с FBref (xG) — опциональна, при недоступности не
    # блокирует прогноз (см. дисклеймер про отзыв доступа Opta в fbref_client.py)
    home_advanced, away_advanced = await asyncio.gather(
        fbref_client.get_team_advanced_stats(home_entity["name"]),
        fbref_client.get_team_advanced_stats(away_entity["name"]),
        return_exceptions=False,
    )

    pred = predict_football.predict_match(home_stats, away_stats, home_advanced, away_advanced)

    xg_line = ""
    if pred["used_xg"]:
        xg_line = (
            f"\n📊 Учтён xG с FBref: "
            f"{home_advanced['avg_xg_for']} — {away_advanced['avg_xg_for']}"
        )

    return (
        f"⚽ <b>{home_entity['name']} — {away_entity['name']}</b>\n\n"
        f"Ожидаемые голы: {pred['expected_home_goals']} — {pred['expected_away_goals']}\n"
        f"Наиболее вероятный счёт: {pred['most_likely_score']}"
        f"{xg_line}\n\n"
        f"П1 (победа {home_entity['name']}): {pred['home_win_pct']}%\n"
        f"Ничья: {pred['draw_pct']}%\n"
        f"П2 (победа {away_entity['name']}): {pred['away_win_pct']}%\n\n"
        f"Тотал больше 2.5: {pred['over_2_5_pct']}%\n"
        f"Тотал меньше 2.5: {pred['under_2_5_pct']}%"
        f"{DISCLAIMER}"
    )


async def build_tennis_prediction(name_a: str, name_b: str) -> str:
    player_a = await sc.search_entity(name_a, entity_type="player")
    player_b = await sc.search_entity(name_b, entity_type="player")

    if not player_a or not player_b:
        return (
            "Не нашёл одного из игроков по имени.\n\n"
            "Попробуй ввести имя латиницей, как оно пишется в ATP/WTA "
            "(например, Sinner, Alcaraz, Djokovic) — база данных источника "
            "статистики может не находить кириллические варианты написания."
        )

    events_a = await sc.get_player_recent_events(player_a["id"], RECENT_MATCHES_COUNT)
    events_b = await sc.get_player_recent_events(player_b["id"], RECENT_MATCHES_COUNT)

    form_a = predict_tennis.win_ratio_from_events(events_a, player_a["id"])
    form_b = predict_tennis.win_ratio_from_events(events_b, player_b["id"])

    rank_a = await sc.get_player_ranking(player_a["id"])
    rank_b = await sc.get_player_ranking(player_b["id"])

    # Доп. данные — предстоящий матч, чтобы узнать покрытие корта и H2H.
    # Всё опционально: если что-то не нашлось, просто не участвует в расчёте.
    surface = None
    h2h_share_a = None
    upcoming_event = await sc.find_upcoming_event_between(player_a["id"], player_b["id"])
    if upcoming_event:
        event_id = upcoming_event.get("id")
        surface = await sc.get_event_surface(event_id)
        h2h_events = await sc.get_event_h2h(event_id)
        h2h_share_a = predict_tennis.h2h_win_share(h2h_events, player_a["id"])

    # Статистика по покрытию корта (требует Playwright — деградирует до
    # None, если браузер недоступен на хостинге, см. browser_manager.py)
    surface_rate_a = surface_rate_b = None
    if surface and player_a.get("slug") and player_b.get("slug"):
        rates_a, rates_b = await asyncio.gather(
            tennis_extra_stats.get_surface_win_rates(player_a["slug"], player_a["id"]),
            tennis_extra_stats.get_surface_win_rates(player_b["slug"], player_b["id"]),
        )
        if rates_a:
            surface_rate_a = rates_a.get(surface)
        if rates_b:
            surface_rate_b = rates_b.get(surface)

    pred = predict_tennis.predict_match(
        form_a, form_b, rank_a, rank_b,
        surface=surface,
        surface_rate_a=surface_rate_a,
        surface_rate_b=surface_rate_b,
        h2h_share_a=h2h_share_a,
    )

    rank_line = ""
    if rank_a is not None and rank_b is not None:
        rank_line = f"Рейтинг: {rank_a} vs {rank_b}\n"

    extra_lines = ""
    if pred["surface"]:
        extra_lines += f"Покрытие: {pred['surface']}\n"
    if pred["surface_rate_a"] is not None and pred["surface_rate_b"] is not None:
        extra_lines += (
            f"Винрейт на этом покрытии: "
            f"{round(pred['surface_rate_a']*100)}% — {round(pred['surface_rate_b']*100)}%\n"
        )
    if pred["h2h_share_a"] is not None:
        extra_lines += f"Личные встречи в пользу {player_a['name']}: {round(pred['h2h_share_a']*100)}%\n"

    return (
        f"🎾 <b>{player_a['name']} — {player_b['name']}</b>\n\n"
        f"{rank_line}"
        f"Форма (посл. {RECENT_MATCHES_COUNT} матчей): "
        f"{pred['form_a_pct']}% побед — {pred['form_b_pct']}% побед\n"
        f"{extra_lines}\n"
        f"Вероятность победы {player_a['name']}: {pred['player_a_win_pct']}%\n"
        f"Вероятность победы {player_b['name']}: {pred['player_b_win_pct']}%"
        f"{DISCLAIMER}"
    )


async def main():
    try:
        await dp.start_polling(bot)
    finally:
        await browser_manager.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
