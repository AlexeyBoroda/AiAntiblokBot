#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GigaChat API client for Python 3.6+ (requests-based).

- Fetches access token via POST https://ngw.devices.sberbank.ru:9443/api/v2/oauth
  using Basic <Authorization key> and form {scope}.
- Caches token in-memory (optionally to file) until expiry.
- Calls https://gigachat.devices.sberbank.ru/api/v1/chat/completions

Environment variables (load from .env is done in bot.py by default):
- GIGACHAT_AUTH_KEY   (required)  Authorization key (WITHOUT "Basic ")
- GIGACHAT_SCOPE      (default: GIGACHAT_API_PERS)
- GIGACHAT_MODEL      (default: GigaChat)
- GIGACHAT_CA_BUNDLE  (default: data/ca/ca_bundle.pem) path to CA bundle for SSL verify
- GIGACHAT_VERIFY     (default: 1) set to 0 to disable SSL verify (NOT recommended)
"""

import os
import time
import uuid
import json
import logging

import requests


class GigaChatClient(object):
    OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    API_BASE = "https://gigachat.devices.sberbank.ru"

    def __init__(self,
                 auth_key,
                 scope="GIGACHAT_API_PERS",
                 model="GigaChat",
                 ca_bundle_path=None,
                 verify=True,
                 timeout=30):
        self.auth_key = (auth_key or "").strip()
        self.scope = (scope or "GIGACHAT_API_PERS").strip()
        self.model = (model or "GigaChat").strip()
        self.timeout = int(timeout) if timeout else 30

        self.ca_bundle_path = ca_bundle_path
        self.verify = verify

        self._token = None
        self._token_exp_ts = 0

    def _verify_arg(self):
        # verify=True / False / path
        if not self.verify:
            return False
        if self.ca_bundle_path and os.path.isfile(self.ca_bundle_path):
            return self.ca_bundle_path
        return True

    def token_valid(self):
        return bool(self._token) and (time.time() < (self._token_exp_ts - 10))

    def get_access_token(self, force=False):
        if (not force) and self.token_valid():
            return self._token

        if not self.auth_key:
            raise RuntimeError("GIGACHAT_AUTH_KEY is empty")

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid.uuid4()),
            "Authorization": "Basic " + self.auth_key,
        }
        data = {"scope": self.scope}

        r = requests.post(
            self.OAUTH_URL,
            headers=headers,
            data=data,
            timeout=self.timeout,
            verify=self._verify_arg(),
        )
        r.raise_for_status()
        obj = r.json()

        tok = obj.get("access_token") or obj.get("token")
        if not tok:
            raise RuntimeError("No access_token in response: %s" % (r.text[:300],))

        # Try to parse expiry
        exp = None
        if "expires_in" in obj:
            try:
                exp = int(obj["expires_in"])
            except Exception:
                exp = None
        if not exp and "expires_at" in obj:
            # sometimes it's ms or iso; best-effort
            try:
                exp_at = obj["expires_at"]
                if isinstance(exp_at, (int, float)):
                    # could be ms
                    exp_ts = int(exp_at)
                    if exp_ts > 10**12:
                        exp_ts = exp_ts // 1000
                    self._token_exp_ts = exp_ts
                    self._token = tok
                    return tok
            except Exception:
                pass

        if not exp:
            exp = 30 * 60  # 30 minutes default per docs

        self._token = tok
        self._token_exp_ts = int(time.time()) + exp
        return tok

    def chat(self, system_prompt, user_prompt, temperature=0.2, max_tokens=800):
        token = self.get_access_token()
        url = self.API_BASE + "/api/v1/chat/completions"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": float(temperature),
        }
        # Some versions support max_tokens; harmless if ignored
        if max_tokens is not None:
            payload["max_tokens"] = int(max_tokens)

        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        }

        r = requests.post(
            url,
            headers=headers,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=self.timeout,
            verify=self._verify_arg(),
        )
        # If token expired unexpectedly, retry once
        if r.status_code in (401, 403):
            logging.warning("GigaChat auth failed (%s), refreshing token and retrying once", r.status_code)
            token = self.get_access_token(force=True)
            headers["Authorization"] = "Bearer " + token
            r = requests.post(
                url,
                headers=headers,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout=self.timeout,
                verify=self._verify_arg(),
            )

        r.raise_for_status()
        obj = r.json()

        # Standard OpenAI-like: choices[0].message.content
        try:
            return obj["choices"][0]["message"]["content"]
        except Exception:
            # Fallback: return raw text
            return json.dumps(obj, ensure_ascii=False)[:1500]
