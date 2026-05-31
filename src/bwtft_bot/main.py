import asyncio

from bwtft_bot.bot import run_bot


def main() -> None:
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
