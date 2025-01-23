from fastapi import FastAPI, HTTPException
import smtplib
from email.mime.text import MIMEText
from random import randint
import uvicorn
from Models import *
import os
import anyio
import os
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from prometheus_client import start_http_server
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.exporter.prometheus import PrometheusMetricReader
import psutil
import json
import logging


use_logger = False

reader = PrometheusMetricReader()
provider = MeterProvider(metric_readers=[reader])
metrics.set_meter_provider(provider)
meter = metrics.get_meter(__name__)

# Метрики использования ресурсов
cpu_usage = meter.create_observable_gauge(
    name="cpu_usage_percent",
    description="Процент использования CPU",
    callbacks=[lambda: [(psutil.cpu_percent(interval=1), {})]],
)

memory_usage = meter.create_observable_gauge(
    name="memory_usage_percent",
    description="Процент использования памяти",
    callbacks=[lambda: [(psutil.virtual_memory().percent, {})]],
)

# Запуск сервера Prometheus для экспонирования метрик
start_http_server(8000)
print("Метрики доступны по адресу http://localhost:8000/metrics")

app = FastAPI()


class ResourceUsage:
    def __init__(self, operation: str, memory_rss: int, memory_vms: int, cpu_user_time: float, cpu_system_time: float):
        self.operation = operation
        self.memory_rss = memory_rss
        self.memory_vms = memory_vms
        self.cpu_user_time = cpu_user_time
        self.cpu_system_time = cpu_system_time

    def __sub__(self, other):
        if not isinstance(other, ResourceUsage):
            raise ValueError("Cannot subtract, other is not a ResourceUsage object")
        
        return ResourceUsage(
            operation=f"Diff_{self.operation}_{other.operation}",
            memory_rss=self.memory_rss - other.memory_rss,
            memory_vms=self.memory_vms - other.memory_vms,
            cpu_user_time=self.cpu_user_time - other.cpu_user_time,
            cpu_system_time=self.cpu_system_time - other.cpu_system_time
        )
    def __str__(self):
        return (f"Operation: {self.operation}, "
                f"Memory RSS: {self.memory_rss} bytes, "
                f"Memory VMS: {self.memory_vms} bytes, "
                f"CPU User Time: {self.cpu_user_time} seconds, "
                f"CPU System Time: {self.cpu_system_time} seconds")



def setup_otel():
    trace.set_tracer_provider(TracerProvider())
    tracer_provider = trace.get_tracer_provider()
    
    tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    FastAPIInstrumentor().instrument_app(app)
    LoggingInstrumentor().instrument(set_logging_format=True)


async def monitor_resources(operation):
    return await anyio.to_thread.run_sync(monitor_resources_sync, operation)


def monitor_resources_sync(operation):
    process = psutil.Process(os.getpid())
    memory_info = process.memory_info()
    cpu_times = process.cpu_times()
    return ResourceUsage(
        operation=operation,
        memory_rss=memory_info.rss,
        memory_vms=memory_info.vms,
        cpu_user_time=cpu_times.user,
        cpu_system_time=cpu_times.system,
    )

def log_resource_usage_to_file(resources_before, resources_after, resources_logging, action, file_name="resource_usage_log.json"):
    log_entry = {
        "action": action,
        "resources_before": str(resources_before),
        "resources_logging": str(resources_logging),
        "resources_after": str(resources_after)
    }
    if os.path.exists(file_name):
        with open(file_name, "a", encoding="utf-8") as f:
            json.dump(log_entry, f, indent=4)
            f.write("\n")
    else:
        with open(file_name, "w", encoding="utf-8") as f:
            json.dump([log_entry], f, indent=4)

setup_otel()


logging.basicConfig(
    level=logging.DEBUG,  # Уровень логирования
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]  # Вывод логов в консоль
)


users_db = {}


async def send_verification_email(email: str, code: str):
    sender_email = os.environ["sender_email"]
    sender_password = os.environ["sender_password"]

    msg = MIMEText(f'Ваш код подтверждения: {code}')
    msg['Subject'] = 'Код подтверждения'
    msg['From'] = sender_email
    msg['To'] = email

    with smtplib.SMTP('smtp.gmail.com', 587) as server:
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, email, msg.as_string())



@app.post("/login")
async def login(user: UserLogin):
    resources_before = await monitor_resources("login start");
    print(resources_before)

    logger = logging.getLogger(__name__)
    logger.info("login")
    resources_after_logging = await monitor_resources("send_verification_email after logger")

    db_user = users_db.get(user.email)
    if not db_user or db_user["password"] != user.password or not db_user["is_verified"]:
        raise HTTPException(status_code=400, detail="Неверные учетные данные или email не подтвержден")


    resources_after = await monitor_resources("login end");
    logger.info(f"Resources after login {resources_before}")
    log_resource_usage_to_file(resources_before, resources_after, resources_after_logging, "login")
    return HTTPException(status_code=200, detail="Вход выполнен успешно!")


@app.post("/delete")
async def delete(user: UserLogin):
    resources_before = await monitor_resources("delete start");
    
    logger = logging.getLogger(__name__)
    logger.info("delete")
    resources_after_logging = await monitor_resources("resources after logging");

    db_user = users_db.get(user.email)
    if not db_user or db_user["password"] != user.password:
        raise HTTPException(status_code=400, detail="Неверные учетные данные")

    users_db.pop(user.email)
    resources_after = await monitor_resources("delete end");
    logger.info(f"Resources after delete {resources_before}")
    log_resource_usage_to_file(resources_before, resources_after, resources_after_logging, "delete")
    return HTTPException(status_code=200, detail="Аккаунт удален!")


@app.post("/register")
async def register(user: UserCreate):
    resources_before = await monitor_resources("register start");
    print(resources_before)

    logger = logging.getLogger(__name__)
    logger.info("register")
    resources_after_logging = await monitor_resources("resources after logging");

    if user.email in users_db:
        raise HTTPException(status_code=400, detail="Email уже подтвержден")

    verification_code = str(randint(100000, 999999)) if user.email != test_email else test_code
    users_db[user.email] = {
        "password": user.password,
        "is_verified": False,
        "verification_code": verification_code
    }

    send_verification_email(user.email, verification_code)
    resources_after = await monitor_resources("register end");
    logger.info(f"Resources after register {resources_before}")
    log_resource_usage_to_file(resources_before, resources_after, resources_after_logging, "register")
    return HTTPException(status_code=200, detail="Пользователь зарегистрирован. "
                                                 "Проверьте вашу почту для подтверждения кода")


@app.post("/verify")
async def verify(user: UserVerify):
    resources_before = await monitor_resources("verify start");
    print(resources_before)
    logger = logging.getLogger(__name__)
    logger.info("verify")
    resources_after_logging = await monitor_resources("resources after logging");

    user_db = users_db.get(user.email)
    if not user_db or user_db["verification_code"] != user.code:
        raise HTTPException(status_code=400, detail="Неверный код подтверждения")

    user_db["is_verified"] = True
    resources_after = await monitor_resources("verify end");
    logger.info(f"Resources after verify {resources_before}")
    log_resource_usage_to_file(resources_before, resources_after, resources_after_logging, "verify")
    return HTTPException(status_code=200, detail="Email подтвержден!")


@app.post("/reset-password/request")
async def reset_password_request(request: PasswordResetRequest):
    resources_before = await monitor_resources("reset_password_request start");
    print(resources_before)
    logger = logging.getLogger(__name__)
    logger.info("reset_password_request")
    resources_after_logging = await monitor_resources("resources after logging");

    user = users_db.get(request.email)
    if not user or user["password"] != request.old_password:
        raise HTTPException(status_code=400, detail="Неверные учетные данные")

    verification_code = str(randint(100000, 999999)) if request.email != test_email else test_code
    user["verification_code"] = verification_code
    send_verification_email(request.email, verification_code)
    resources_after = await monitor_resources("reset_password_request end");
    logger.info(f"Resources after reset_password_request {resources_before}")
    log_resource_usage_to_file(resources_before, resources_after, resources_after_logging, "reset_password_request")
    return HTTPException(status_code=200, detail="Код подтверждения отправлен на почту")


@app.post("/reset-password/confirm")
async def reset_password_confirm(request: PasswordResetConfirm):
    resources_before = await monitor_resources("reset_password_confirm start");

    logger = logging.getLogger(__name__)
    logger.info("reset_password_confirm")
    resources_after_logging = await monitor_resources("resources after logging");

    user = users_db.get(request.email)
    if not user or user["verification_code"] != request.code:
        raise HTTPException(status_code=400, detail="Неверный код подтверждения")

    user["password"] = request.new_password
    resources_after = await monitor_resources("reset_password_confirm end");
    logger.info(f"Resources after reset_password_confirm {resources_before}")
    log_resource_usage_to_file(resources_before, resources_after, resources_after_logging, "reset_password_confirm")
    return HTTPException(status_code=200, detail="Пароль успешно изменен!")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5000, log_level="debug")
