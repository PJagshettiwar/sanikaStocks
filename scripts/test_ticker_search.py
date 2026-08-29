import asyncio
import httpx
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
from brokers.indstocks import INDstocksBroker
from risk_engine import _resolve_symbol

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

SYMBOLS_FROM_LLM = ["AMBER", "HINDCOPPER", "POLICYBZR", "NH", "HDFCAMC", "KMSUGAR", "MEESHO", "BSE", "IFCI"]


async def main():
    async with httpx.AsyncClient() as http:
        broker = INDstocksBroker(os.environ["INDSTOCKS_TOKEN"], http)
        instruments = await broker.get_instruments()
        print(f"Loaded {len(instruments)} instruments from INDstocks\n")

        print(f"{'LLM Symbol':<15} {'Resolved':<20} {'Security ID':<15} {'Status'}")
        print("-" * 65)

        for sym in SYMBOLS_FROM_LLM:
            resolved = _resolve_symbol(sym, instruments)
            if resolved:
                sec_id = instruments[resolved]
                status = "EXACT" if resolved == sym else "FUZZY"
                print(f"{sym:<15} {resolved:<20} {sec_id:<15} {status}")
            else:
                print(f"{sym:<15} {'---':<20} {'---':<15} NOT FOUND")


asyncio.run(main())
