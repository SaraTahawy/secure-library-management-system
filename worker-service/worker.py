import logging
import time

import httpx
import pika

from common.audit import audit
from common.config import env

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("worker-service")
JOB_DELAY_SECONDS = float(env("WORKER_JOB_DELAY_SECONDS", "0"))


def connect():
    credentials = pika.PlainCredentials(env("RABBITMQ_USER"), env("RABBITMQ_PASSWORD"))
    params = pika.ConnectionParameters("rabbitmq", 5672, "/", credentials, heartbeat=30)
    return pika.BlockingConnection(params)


async def mark_processed(document_id: str) -> None:
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.post(
            f"http://library-service:8000/internal/documents/{document_id}/processed",
            headers={"X-Internal-Token": env("INTERNAL_API_KEY")},
        )
        response.raise_for_status()
    await audit(None, "background_job", "worker-service", "success", {"document_id": document_id})


async def send_notification(user_id: str, message: str) -> None:
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.post(
            "http://notification-service:8000/internal/notifications",
            headers={"X-Internal-Token": env("INTERNAL_API_KEY")},
            json={"user_id": user_id, "message": message, "type": "email"},
        )
        response.raise_for_status()
    await audit(user_id, "notification_sent", "worker-service", "success", {"message": message})


def main():
    while True:
        try:
            connection = connect()
            channel = connection.channel()
            channel.queue_declare(queue="document_jobs", durable=True)
            channel.queue_declare(queue="notification_jobs", durable=True)

            def handle_document(ch, method, properties, body):
                document_id = body.decode()
                logger.info("processing document job %s", document_id)
                try:
                    import asyncio

                    if JOB_DELAY_SECONDS > 0:
                        time.sleep(JOB_DELAY_SECONDS)
                    asyncio.run(mark_processed(document_id))
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                except Exception:
                    logger.exception("document job failed")
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

            def handle_notification(ch, method, properties, body):
                user_id, message = body.decode().split("|", 1)
                logger.info("processing notification job for user %s", user_id)
                try:
                    import asyncio

                    if JOB_DELAY_SECONDS > 0:
                        time.sleep(JOB_DELAY_SECONDS)
                    asyncio.run(send_notification(user_id, message))
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                except Exception:
                    logger.exception("notification job failed")
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(queue="document_jobs", on_message_callback=handle_document)
            channel.basic_consume(queue="notification_jobs", on_message_callback=handle_notification)
            logger.info("waiting for document and notification jobs")
            channel.start_consuming()
        except Exception:
            logger.exception("worker connection failed; retrying")
            time.sleep(5)


if __name__ == "__main__":
    main()
