import asyncio
import logging

from backend.app.main import app
from backend.app.serve import serve

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(serve(app))
