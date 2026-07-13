from __future__ import annotations

from argparse import Namespace

from gpt_trader.cli.commands import orders as orders_cmd


def make_args(**overrides):
    defaults = dict(
        profile="dev",
        orders_command="preview",
        symbol="BTC-PERP",
        side="buy",
        type="limit",
        quantity="0.5",
        price="42000",
        stop="41000",
        tif="IOC",
        client_id="client-1",
        leverage=3,
        reduce_only=True,
        order_id="abc",
        preview_id="def",
    )
    defaults.update(overrides)
    return Namespace(**defaults)


def make_history_args(**overrides):
    defaults = dict(
        profile="dev",
        orders_command="history",
        history_command="list",
        limit=orders_cmd._DEFAULT_HISTORY_LIMIT,
        symbol=None,
        status=None,
        output_format="text",
        subcommand="history list",
    )
    defaults.update(overrides)
    return Namespace(**defaults)


def make_suppressed_args(**overrides):
    defaults = dict(
        profile="dev",
        orders_command="suppressed",
        limit=orders_cmd._DEFAULT_HISTORY_LIMIT,
        output_format="text",
        subcommand="suppressed",
    )
    defaults.update(overrides)
    return Namespace(**defaults)
