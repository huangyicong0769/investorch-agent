"""Strict, independent JSON wire parsing for execution transport."""

import base64
import binascii
import hashlib
import json
import math
import re
from datetime import datetime
from decimal import Decimal

from investorch_qmt.rqalpha_live.contracts import RQAlphaLiveBootstrapSnapshot

from .domain import ExecutionError


def invalid(message: str):
    raise ExecutionError("INVALID_REQUEST", message, status=400)


def fields(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        invalid("Object must contain exactly the documented fields.")


def text(value):
    if type(value) is not str or not value or value.strip() != value:
        invalid("Identity must be nonempty trimmed text.")
    return value


def sequence(value):
    if type(value) is not int or value < 0:
        invalid("Sequence must be a nonnegative integer.")
    return value


def timestamp(value):
    try:
        result = datetime.fromisoformat(text(value))
        if result.utcoffset() is None:
            raise ValueError
    except ValueError:
        invalid("Timestamp must include timezone.")


def canonical_json(value):
    def validate(item):
        if item is None or type(item) in (str, int, bool):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                validate(child)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                validate(child)
            return
        invalid("Only finite JSON values are accepted.")

    try:
        validate(value)
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        invalid("Only finite JSON values are accepted.")


def parse_stage(deployment_id, body):
    text(deployment_id)
    # A wire identity is never allowed to become a path outside the artifact root.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", deployment_id):
        invalid("Deployment identity must be a safe path component.")
    fields(body, {"manifest", "strategy_source_base64", "bootstrap"})
    manifest = body["manifest"]
    fields(
        manifest,
        {
            "deployment_id",
            "portfolio_id",
            "broker_account_id",
            "strategy_source_path",
            "strategy_sha256",
            "strategy_parameters",
            "rqalpha_version",
            "created_at",
        },
    )
    for name in ("deployment_id", "portfolio_id", "broker_account_id", "strategy_source_path"):
        text(manifest[name])
    if manifest["rqalpha_version"] != "6.3.0":
        invalid("RQAlpha version must be 6.3.0.")
    if type(manifest["strategy_parameters"]) is not dict:
        invalid("Strategy parameters must be a JSON object.")
    canonical_json(manifest)
    timestamp(manifest["created_at"])
    digest = manifest["strategy_sha256"]
    if type(digest) is not str or re.fullmatch("[0-9a-f]{64}", digest) is None:
        invalid("Strategy SHA-256 must be 64 lowercase hexadecimal characters.")
    try:
        if type(body["strategy_source_base64"]) is not str:
            raise ValueError
        source = base64.b64decode(body["strategy_source_base64"], validate=True)
    except (ValueError, binascii.Error):
        invalid("Strategy must contain valid Base64 bytes.")
    if hashlib.sha256(source).hexdigest() != digest:
        raise ExecutionError("HASH_MISMATCH", "Strategy bytes do not match manifest SHA-256.", status=400)
    try:
        snapshot = RQAlphaLiveBootstrapSnapshot.from_wire(body["bootstrap"])
    except (ValueError, TypeError, KeyError) as exc:
        raise ExecutionError("INVALID_BOOTSTRAP", "Bootstrap must match the exact V1 contract.", status=400) from exc
    if (
        deployment_id != manifest["deployment_id"]
        or deployment_id != snapshot.deployment_id
        or manifest["portfolio_id"] != snapshot.portfolio_id
        or manifest["broker_account_id"] != snapshot.broker_account_id
    ):
        invalid("Deployment, Portfolio and account identities must agree.")
    return source, canonical_json(manifest), canonical_json(body["bootstrap"]), snapshot


def parse_trade(value):
    fields(
        value,
        {
            "schema_version",
            "deployment_id",
            "broker_trade_id",
            "instrument",
            "side",
            "quantity",
            "price",
            "commission",
            "tax",
            "other_fee",
            "effective_at",
        },
    )
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ExecutionError("UNSUPPORTED_SCHEMA", "Trade schema must be integer 1.", status=400)
    text(value["deployment_id"])
    text(value["broker_trade_id"])
    fields(value["instrument"], {"code", "market"})
    text(value["instrument"]["code"])
    text(value["instrument"]["market"])
    if value["side"] not in ("BUY", "SELL"):
        invalid("Trade side must be BUY or SELL.")
    for key in ("quantity", "price", "commission", "tax", "other_fee"):
        raw = value[key]
        if type(raw) is not str or re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", raw) is None:
            invalid("Trade numeric values must be decimal strings.")
        number = Decimal(raw)
        if not number.is_finite() or number < 0 or (key in ("quantity", "price") and number == 0):
            invalid("Trade quantities/prices must be positive and fees nonnegative.")
    timestamp(value["effective_at"])
    return canonical_json(value)
