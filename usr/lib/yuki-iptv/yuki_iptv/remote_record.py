#
# Copyright (c) 2023, 2024 Liya Astrova <liyaastrova@proton.me>
#
# This file is part of yuki-iptv.
#
# yuki-iptv is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# yuki-iptv is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with yuki-iptv. If not, see <https://www.gnu.org/licenses/>.
#
# The Font Awesome pictograms are licensed under the CC BY 4.0 License.
# Font Awesome Free 5.15.4 by @fontawesome - https://fontawesome.com
# License - https://creativecommons.org/licenses/by/4.0/
#
#
# Server-side recording through the Streams Manager API.
#
# Streams Manager (https://github.com/christheradioguy/stream-manager) records
# a channel on the server with
#     POST /api/channels/{id}/record        start
#     POST /api/channels/{id}/record/stop   stop
#     GET  /api/channels/{id}/record        state of the current recording
# These helpers wrap those calls. They block on the network, so they are meant
# to be called from a background thread, never from the Qt main thread.
#
import re
import logging
import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10

_STREAM_PATH = re.compile(r"/stream/([^/?#]+)")


class RemoteRecordError(Exception):
    """A request that reached the server but was refused, or could not be made."""

    def __init__(self, message, status=None, state=None):
        super().__init__(message)
        self.status = status
        # Set when the server answered 409: the channel is already recording,
        # and this is that recording's state.
        self.state = state


def channel_id_for(channel_item, playing_url):
    """The Streams Manager channel id for what is playing.

    The playlist served by Streams Manager puts the channel id in tvg-id, so
    that is the first choice. Failing that, the id is taken from the stream
    URL itself, which is always /stream/{id}.
    """
    if channel_item:
        tvg_id = str(channel_item.get("tvg-ID", "") or "").strip()
        if tvg_id:
            return tvg_id
    if playing_url:
        match = _STREAM_PATH.search(str(playing_url))
        if match:
            return match.group(1)
    return ""


def normalize_base_url(url):
    url = str(url or "").strip().rstrip("/")
    if url and not re.match(r"^https?://", url, re.IGNORECASE):
        url = "http://" + url
    return url


def _headers(token):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + str(token).strip()
    return headers


def _request(method, base_url, path, token, json_body=None):
    base_url = normalize_base_url(base_url)
    if not base_url:
        raise RemoteRecordError("Streams Manager URL is not set")
    url = base_url + path
    try:
        response = requests.request(
            method,
            url,
            json=json_body,
            headers=_headers(token),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.warning(f"Streams Manager request failed: {method} {url}: {exc}")
        raise RemoteRecordError(f"Cannot reach Streams Manager: {exc}") from exc

    try:
        body = response.json()
    except ValueError:
        body = {}

    if response.status_code >= 400:
        detail = body.get("detail") if isinstance(body, dict) else None
        if isinstance(detail, list):
            # FastAPI validation errors come as a list of {"msg": ...}
            detail = "; ".join(str(d.get("msg", d)) for d in detail)
        message = str(detail or f"HTTP {response.status_code}")
        logger.warning(f"Streams Manager refused {method} {url}: {message}")
        raise RemoteRecordError(message, status=response.status_code)
    return body


def remote_record_start(base_url, channel_id, token=""):
    """Start recording. Returns the recording state as the server reports it.

    If the channel is already being recorded, the error carries that
    recording's state so the caller can adopt it instead of failing.
    """
    try:
        return _request(
            "POST", base_url, f"/api/channels/{channel_id}/record", token, {}
        )
    except RemoteRecordError as exc:
        if exc.status == 409:
            try:
                exc.state = remote_record_status(base_url, channel_id, token)
            except RemoteRecordError:
                pass
        raise


def remote_record_stop(base_url, channel_id, token=""):
    """Stop recording. Returns the final state. 404 means nothing was recording."""
    return _request("POST", base_url, f"/api/channels/{channel_id}/record/stop", token)


def remote_record_status(base_url, channel_id, token=""):
    """Current (or most recent) recording of a channel, or None if there is none."""
    try:
        return _request("GET", base_url, f"/api/channels/{channel_id}/record", token)
    except RemoteRecordError as exc:
        if exc.status == 404:
            return None
        raise


def is_active(state):
    return bool(state) and state.get("status") == "recording"
