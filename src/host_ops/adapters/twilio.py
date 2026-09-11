from __future__ import annotations

import base64
import json
from collections.abc import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .ical_poll import verified_tls_context


class SmsDeliveryError(RuntimeError):
    """A display-safe provider error without credentials or message contents."""


class TwilioMessagingAdapter:
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        opener: Callable[..., object] = urlopen,
    ) -> None:
        if not account_sid or not auth_token or not from_number:
            raise SmsDeliveryError("Twilio credentials are incomplete.")
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self.opener = opener

    def send(self, recipient: str, body: str) -> str:
        endpoint = (
            "https://api.twilio.com/2010-04-01/Accounts/"
            f"{self.account_sid}/Messages.json"
        )
        authorization = base64.b64encode(
            f"{self.account_sid}:{self.auth_token}".encode("utf-8")
        ).decode("ascii")
        request = Request(
            endpoint,
            data=urlencode(
                {"To": recipient, "From": self.from_number, "Body": body}
            ).encode("utf-8"),
            headers={
                "Authorization": f"Basic {authorization}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "host-ops/0.1 sms-adapter",
            },
            method="POST",
        )
        try:
            with self.opener(
                request, timeout=20, context=verified_tls_context()
            ) as response:
                result = json.loads(response.read().decode("utf-8"))
        except Exception as error:
            raise SmsDeliveryError(
                f"SMS provider request failed ({type(error).__name__})."
            ) from error
        message_sid = result.get("sid")
        if not isinstance(message_sid, str) or not message_sid:
            raise SmsDeliveryError("SMS provider response did not include a message ID.")
        return message_sid
