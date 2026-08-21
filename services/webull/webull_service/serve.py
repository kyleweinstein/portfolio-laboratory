from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "webull_service.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"),
    )


if __name__ == "__main__":
    main()
