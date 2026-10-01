"""Notifier entry point (``kharcha-notifier``): Telegram polling, Kafka consumers, scheduler."""

import asyncio
import logging

import httpx
from faststream import AckPolicy
from faststream.confluent import KafkaBroker
from faststream.confluent.annotations import KafkaMessage
from prometheus_client import start_http_server
from redis.asyncio import Redis

from kharcha_common.db import make_engine, make_sessionmaker
from kharcha_common.kafka import BrokerPublisher, make_broker, run_with_retry_and_dlt
from kharcha_common.logging import configure_logging
from kharcha_common.settings import Settings, get_settings
from kharcha_common.topics import Topic
from kharcha_notifier.bot import BotDeps, handle_update
from kharcha_notifier.confirm import CONFIRM_CONSUMER, handle_cash_event
from kharcha_notifier.results import RESULTS_CONSUMER, handle_agent_result
from kharcha_notifier.scheduler import parse_hhmm, run_daily_summaries, run_weekly_reviews
from kharcha_notifier.telegram import Messenger, TelegramClient

log = logging.getLogger(__name__)
OFFSET_KEY = "tg:update-offset"


def _key(message: KafkaMessage) -> bytes | None:
    key = message.raw_message.key()  # type: ignore[union-attr]
    return key if isinstance(key, bytes) else None


def build_broker(settings: Settings, deps: BotDeps, broker: KafkaBroker) -> KafkaBroker:
    publisher = BrokerPublisher(broker)

    @broker.subscriber(
        Topic.CASH_EVENTS,
        group_id=CONFIRM_CONSUMER,
        auto_offset_reset="latest",
        ack_policy=AckPolicy.NACK_ON_ERROR,
    )
    async def on_cash(message: KafkaMessage) -> None:
        await run_with_retry_and_dlt(
            lambda body: handle_cash_event(body, deps),
            body=message.body,
            key=_key(message),
            topic=Topic.CASH_EVENTS,
            consumer=CONFIRM_CONSUMER,
            publisher=publisher,
        )

    @broker.subscriber(
        Topic.AGENT_RESULTS,
        group_id=RESULTS_CONSUMER,
        auto_offset_reset="earliest",
        ack_policy=AckPolicy.NACK_ON_ERROR,
    )
    async def on_result(message: KafkaMessage) -> None:
        await run_with_retry_and_dlt(
            lambda body: handle_agent_result(body, deps),
            body=message.body,
            key=_key(message),
            topic=Topic.AGENT_RESULTS,
            consumer=RESULTS_CONSUMER,
            publisher=publisher,
        )

    return broker


async def poll_telegram(telegram: TelegramClient, deps: BotDeps) -> None:
    """Long polling (local). The offset lives in Redis so restarts do not replay updates."""
    while True:
        try:
            stored = await deps.redis.get(OFFSET_KEY)
            updates = await telegram.get_updates(int(stored) if stored else None)
            for update in updates:
                try:
                    await handle_update(update, deps)
                except Exception:
                    log.exception("update handling failed")
                await deps.redis.set(OFFSET_KEY, int(update["update_id"]) + 1)
        except (httpx.HTTPError, OSError):
            log.warning("telegram polling error; retrying")
            await asyncio.sleep(5)


async def serve(settings: Settings) -> None:
    if not settings.telegram_bot_token:
        raise SystemExit("KHARCHA_TELEGRAM_BOT_TOKEN is not set (create a bot with @BotFather)")
    engine = make_engine(settings)
    broker = make_broker(settings, client_id="kharcha-notifier")
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    async with httpx.AsyncClient() as http:
        telegram = TelegramClient(settings.telegram_bot_token, http, settings.telegram_api_base)
        messenger: Messenger = telegram
        deps = BotDeps(make_sessionmaker(engine), BrokerPublisher(broker), redis, messenger)
        build_broker(settings, deps, broker)
        await broker.start()
        try:
            await asyncio.gather(
                poll_telegram(telegram, deps),
                run_daily_summaries(deps, parse_hhmm(settings.daily_summary_time)),
                run_weekly_reviews(deps),
            )
        finally:
            await broker.stop()
            await redis.aclose()
            await engine.dispose()


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    start_http_server(settings.metrics_port + 2)
    asyncio.run(serve(settings))
