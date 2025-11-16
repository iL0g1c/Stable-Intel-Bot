# src/shared/multiplayerAPI.py
import time
import logging
import traceback
from urllib.parse import unquote_plus
from .http_client import safe_post


class MultiplayerAPI:
    def __init__(self, sessionID, accountID):
        self.sessionID = sessionID
        self.accountID = accountID
        self.myID = None
        self.lastMsgID = 0
        self.log = logging.getLogger("eventhorizon.multiplayer")
        self.headers = {
            "Origin": "https://www.geo-fs.com",
            "Referer": "https://www.geo-fs.com/geofs.php",
            "User-Agent": "Mozilla/5.0"
        }

    def handshake(self):
        """Initialize connection; populate self.myID and self.lastMsgID."""
        self.log.info("[mp] handshake: begin")
        while True:
            body = {
                "origin": "https://www.geo-fs.com",
                "acid": self.accountID,
                "sid": self.sessionID,
                "id": "",
                "ac": "1",
                "co": [9999999999999999]*6,
                "ve": [0.0]*6,
                "st": {"gr": True, "as": 0},
                "ti": int(time.time() * 1000),
                "ac": 1,
                "co": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                "ve": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                "st": {"gr": 1, "as": 0},
                "ro": {"ad": 0},
                "ti": int(time.time() * 1000),
                "m": "",
                "ci": 0
            }

            t0 = time.time()
            resp = safe_post(
                "https://mps.geo-fs.com/update",
                body,
                timeout=(5, 15),
                max_json_retries=2,
                cookies={"PHPSESSID": self.sessionID},
                headers=self.headers,
            )
            if not resp:
                self.log.warning("[mp] handshake step2 failed in %.2fs; retrying…", time.time() - t0)
                traceback.print_exc()
                time.sleep(5)
                continue

            # first call gives us myID
            self.myID = resp.get("myId")

            # second call to pick up lastMsgId
            body["id"] = self.myID
            body["ci"] = 0
            body["ti"] = int(time.time() * 1000)

            t1 = time.time()
            resp2 = safe_post(
                "https://mps.geo-fs.com/update",
                body,
                timeout=(5, 15),
                max_json_retries=2,
                cookies={"PHPSESSID": self.sessionID},
                headers=self.headers,            )
            if not resp2:
                print("Second handshake call failed, retrying in 5s…")
                traceback.print_exc()
                time.sleep(5)
                continue

            self.myID = resp2.get("myId")
            self.lastMsgID = resp2.get("lastMsgId") or 0
            self.log.info("[mp] handshake: success myId=%s lastMsgId=%s total=%.2fs", self.myID, self.lastMsgID, time.time() - t0)
            return

    def sendMsg(self, msg: str):
        """Post a chat message into Geo‑FS."""
        while True:
            body = {
                "origin": "https://www.geo-fs.com",
                "acid": self.accountID,
                "sid": self.sessionID,
                "id": self.myID,
                "ac": "1",
                "co": [9999999999999999]*6,
                "ve": [0.0]*6,
                "st": {"gr": True, "as": 0},
                "ti": None,
                "ac": 1,
                "co": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                "ve": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                "st": {"gr": 1, "as": 0},
                "ro": {"ad": 0},
                "ti": int(time.time() * 1000),
                "m": msg,
                "ci": self.lastMsgID
            }
            start_time = time.time()
            resp = safe_post(
                "https://mps.geo-fs.com/update",
                body,
                timeout=(5, 15),
                max_json_retries=2,
                cookies={"PHPSESSID": self.sessionID},
                headers=self.headers,
            )
            end_time = time.time()
            print(f"The last request took {end_time - start_time} seconds.")
            if resp:
                self.myID = resp.get("myId")
                return

            print("sendMsg failed, retrying in 5s…")
            traceback.print_exc()
            time.sleep(5)

    def getMessages(self, max_duration: float = 20.0) -> list[dict]:
        """Fetch latest chat messages and update self.lastMsgID."""
        start = time.time()
        self.log.debug("[mp] getMessages: begin myId=%s lastMsgId=%s", self.myID, self.lastMsgID)
        while True:
            body = {
                "origin": "https://www.geo-fs.com",
                "acid": self.accountID,
                "sid": self.sessionID,
                "id": self.myID,
                "ac": "1",
                "co": [9999999999999999]*6,
                "ve": [0.0]*6,
                "st": {"gr": True, "as": 0},
                "ti": None,
                "ac": 1,
                "co": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                "ve": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                "st": {"gr": 1, "as": 0},
                "ro": {"ad": 0},
                "ti": int(time.time() * 1000),
                "m": "",
                "ci": self.lastMsgID
            }
            t0 = time.time()
            resp = safe_post(
                "https://mps.geo-fs.com/update",
                body,
                timeout=(5, 15),
                max_json_retries=2,
                cookies={"PHPSESSID": self.sessionID},
                headers=self.headers,
            )
            if resp:
                self.myID = resp.get("myId")
                self.lastMsgID = resp.get("lastMsgId") or self.lastMsgID
                msgs = resp.get("chatMessages", [])
                for m in msgs:
                    if "msg" in m and m["msg"]:
                        m["msg"] = unquote_plus(m["msg"])
                self.log.debug("[mp] getMessages: ok in %.2fs; msgs=%d lastMsgId=%s", time.time() - t0, len(msgs), self.lastMsgID)
                return msgs

            self.log.warning("[mp] getMessages: request failed in %.2fs (elapsed %.2fs); retrying…", time.time() - t0, time.time() - start)
            traceback.print_exc()
            if time.time() - start >=max_duration:
                raise TimeoutError("getMessages: soft deadline exceeded")
            time.sleep(2)