from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run(
        "smartworkmate.web_gui:app",
        host="127.0.0.1",
        port=8550,
        reload=False,
        http="h11",
        loop="asyncio",
        log_level="warning",
    )


if __name__ == "__main__":
    main()
