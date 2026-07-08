import asyncio
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from dotenv import load_dotenv
import httpx

load_dotenv()

bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher()

# ================= НАСТРОЙКИ =================
# Вариант 1: Groq (быстрый и бесплатный)
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
API_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.1-70b-versatile"   # очень сильная и относительно uncensored

# Вариант 2 (если Groq лимиты кончатся): OpenRouter
# API_URL = "https://openrouter.ai/api/v1/chat/completions"
# MODEL = "cognitivecomputations/dolphin-2.9.1-llama-3-70b"  # сильно uncensored

async def ask_ai(prompt: str):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.8,
        "max_tokens": 1500
    }
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(API_URL, json=data, headers=headers)
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
        else:
            return f"Ошибка API: {resp.status_code} - {resp.text[:300]}"

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("✅ DarkGPT-style бот запущен.\nБез цензуры. Пиши что угодно.")

@dp.message()
async def handle(message: types.Message):
    await message.answer("⏳ Думаю...")
    try:
        response = await ask_ai(message.text)
        await message.answer(response)
    except Exception as e:
        await message.answer(f"Ошибка: {str(e)}")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
