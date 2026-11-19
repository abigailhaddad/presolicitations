"""The only module that talks to SAM.gov. Tests swap in a fake with the same three methods."""
from __future__ import annotations

import time

import requests

from . import config


class SamError(Exception):
    """An HTTP error from SAM.gov. `status` is the HTTP code, 0 for a network failure."""

    def __init__(self, status: int, body: str = ""):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


class Client:
    def __init__(self, sleep: float = config.SLEEP_SECONDS):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = config.USER_AGENT
        self.sleep = sleep

    def _get(self, url: str, tries: int = 3) -> requests.Response:
        last = SamError(0, "no attempt")
        for i in range(tries):
            time.sleep(self.sleep)
            try:
                r = self.s.get(url, timeout=60)
            except requests.RequestException as e:  # network trouble: retry
                last = SamError(0, str(e))
                time.sleep(2**i)
                continue
            if r.status_code == 200:
                return r
            last = SamError(r.status_code, r.text[:300])
            if r.status_code < 500:  # 4xx will not change on retry (404 gone, 400 deleted, 401 controlled)
                raise last
            time.sleep(2**i)
        raise last

    def resources(self, notice_id: str) -> list[dict]:
        """Every attachment record for a notice, deleted ones included, no cap."""
        d = self._get(f"{config.API}/v3/opportunities/{notice_id}/resources").json()
        lists = d.get("_embedded", {}).get("opportunityAttachmentList", [])
        return [a for item in lists for a in item.get("attachments", [])]

    def live(self, notice_id: str) -> dict:
        """The live notice record (has `latest`, `deleted`, `archived`, `cancelled`, `status`, `data2`)."""
        return self._get(f"{config.API}/v2/opportunities/{notice_id}").json()

    def download(self, resource_id: str) -> bytes:
        url = f"{config.API}/v3/opportunities/resources/files/{resource_id}/download?api_key=null&token="
        return self._get(url).content
