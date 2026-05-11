import httpx

from app.config import get_settings


settings = get_settings()


class MarquezClient:
    def __init__(self) -> None:
        self.base_url = settings.MARQUEZ_URL.rstrip("/")
        self.lineage_endpoint = settings.MARQUEZ_LINEAGE_ENDPOINT

    async def health(self) -> bool:
        urls = [
            f"{self.base_url}/api/v1/namespaces",
            f"{self.base_url}/healthcheck",
            f"{self.base_url}/",
        ]

        async with httpx.AsyncClient(timeout=10) as client:
            for url in urls:
                try:
                    response = await client.get(url)
                    if response.status_code < 500:
                        return True
                except Exception:
                    continue

        return False

    async def emit_openlineage_event(self, event: dict) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                response = await client.post(
                    self.lineage_endpoint,
                    json=event,
                    headers={"Content-Type": "application/json"},
                )

                return {
                    "success": 200 <= response.status_code < 300,
                    "status_code": response.status_code,
                    "response_text": response.text[:1000],
                    "error": None,
                }

            except Exception as exc:
                return {
                    "success": False,
                    "status_code": None,
                    "response_text": None,
                    "error": str(exc),
                }