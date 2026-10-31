import os
import sys
import psycopg2
import requests
import time
from psycopg2.extras import RealDictCursor, Json
from psycopg2.pool import SimpleConnectionPool
from flask import Flask, g, jsonify, request
from dotenv import load_dotenv
from functools import wraps
import logging
from opentelemetry import trace, metrics
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

# Configura o logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Carrega .env para desenvolvimento local
load_dotenv()

app = Flask(__name__)
meter = metrics.get_meter(os.getenv("OTEL_SERVICE_NAME", "service"))
http_server_duration = meter.create_histogram(
    name="http.server.request.duration",
    unit="s",
    description="Duração das requisições HTTP",
)


@app.before_request
def _otel_metrics_start():
    g._otel_start = time.perf_counter()


@app.after_request
def _otel_metrics_end(response):
    start = getattr(g, "_otel_start", None)
    if start is not None:
        http_server_duration.record(
            time.perf_counter() - start,
            {
                "http.request.method": request.method,
                "http.route": request.path,
                "http.response.status_code": response.status_code,
                "url.scheme": request.scheme,
            },
        )
    return response


def configure_telemetry(default_service_name: str) -> None:
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        endpoint = "http://otel-collector.monitoring:4318/v1/traces"
    elif endpoint.endswith(":4318") or endpoint.endswith(":4318/"):
        endpoint = endpoint.rstrip("/") + "/v1/traces"

    base = endpoint.replace("/v1/traces", "")
    resource = Resource.create({
        "service.name": os.getenv("OTEL_SERVICE_NAME", default_service_name)
    })

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, timeout=5)))
    trace.set_tracer_provider(provider)

    # Métricas — necessárias para o APM do New Relic
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=base + "/v1/metrics", timeout=5),
        export_interval_millis=15000,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(meter_provider))


configure_telemetry("targeting-service")
FlaskInstrumentor().instrument_app(app)
RequestsInstrumentor().instrument()

# --- Configuração ---
DATABASE_URL = os.getenv("DATABASE_URL")
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL")

if not DATABASE_URL or not AUTH_SERVICE_URL:
    log.critical("Erro: DATABASE_URL e AUTH_SERVICE_URL devem ser definidos.")
    sys.exit(1)

# --- Pool de Conexão com o Banco ---
try:
    pool = SimpleConnectionPool(1, 5, dsn=DATABASE_URL)
    log.info("Pool de conexões com o PostgreSQL (targeting) inicializado.")
except psycopg2.OperationalError as e:
    log.critical(f"Erro fatal ao conectar ao PostgreSQL: {e}")
    sys.exit(1)

# --- Middleware de Autenticação (Idêntico ao flag-service) ---


def require_auth(f):
    """ Middleware para validar a chave de API contra o auth-service """
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return jsonify({"error": "Authorization header obrigatório"}), 401

        try:
            validate_url = f"{AUTH_SERVICE_URL}/validate"
            response = requests.get(
                validate_url, headers={
                    "Authorization": auth_header}, timeout=3)

            if response.status_code != 200:
                log.warning(
                    f"Falha na validação da chave (status: {response.status_code})")
                return jsonify({"error": "Chave de API inválida"}), 401

        except requests.exceptions.Timeout:
            log.error("Timeout ao conectar com o auth-service")
            # Gateway Timeout
            return jsonify(
                {"error": "Serviço de autenticação indisponível (timeout)"}), 504
        except requests.exceptions.RequestException as e:
            log.error(f"Erro ao conectar com o auth-service: {e}")
            # Service Unavailable
            return jsonify(
                {"error": "Serviço de autenticação indisponível"}), 503

        return f(*args, **kwargs)
    return decorated

# --- Endpoints da API ---


@app.route('/health')
def health():
    return jsonify({"status": "ok"})


@app.route('/rules', methods=['POST'])
@require_auth
def create_rule():
    """ Cria uma nova regra de segmentação para uma flag """
    data = request.get_json()
    if not data or 'flag_name' not in data or 'rules' not in data:
        return jsonify(
            {"error": "'flag_name' e 'rules' (JSON) são obrigatórios"}), 400

    flag_name = data['flag_name']
    rules_obj = data['rules']  # O objeto JSON
    is_enabled = data.get('is_enabled', True)

    conn = None
    cur = None
    try:
        conn = pool.getconn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "INSERT INTO targeting_rules (flag_name, is_enabled, rules, created_at, updated_at) "
            "VALUES (%s, %s, %s, NOW(), NOW()) RETURNING *",
            # Usa Json() para serializar
            (flag_name, is_enabled, Json(rules_obj))
        )
        new_rule = cur.fetchone()
        conn.commit()
        log.info(f"Regra para '{flag_name}' criada com sucesso.")
        return jsonify(new_rule), 201
    except psycopg2.IntegrityError:
        if conn:
            conn.rollback()
        log.warning(f"Tentativa de criar regra duplicada: '{flag_name}'")
        return jsonify(
            {"error": f"Regra para a flag '{flag_name}' já existe"}), 409
    except Exception as e:
        if conn:
            conn.rollback()
        log.error(f"Erro ao criar regra: {e}")
        return jsonify(
            {"error": "Erro interno do servidor", "details": str(e)}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            pool.putconn(conn)


@app.route('/rules/<string:flag_name>', methods=['GET'])
@require_auth
def get_rule(flag_name):
    """ Busca uma regra de segmentação pelo nome da flag """
    conn = None
    cur = None
    try:
        conn = pool.getconn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT * FROM targeting_rules WHERE flag_name = %s", (flag_name,))
        rule = cur.fetchone()
        if not rule:
            return jsonify({"error": "Regra não encontrada"}), 404
        return jsonify(rule)
    except Exception as e:
        log.error(f"Erro ao buscar regra '{flag_name}': {e}")
        return jsonify(
            {"error": "Erro interno do servidor", "details": str(e)}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            pool.putconn(conn)


@app.route('/rules/<string:flag_name>', methods=['PUT'])
@require_auth
def update_rule(flag_name):
    """ Atualiza a regra de segmentação de uma flag """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Corpo da requisição obrigatório"}), 400

    fields = []
    values = []

    if 'rules' in data:
        fields.append("rules = %s")
        values.append(Json(data['rules']))  # Serializa o JSON
    if 'is_enabled' in data:
        fields.append("is_enabled = %s")
        values.append(data['is_enabled'])

    if not fields:
        return jsonify(
            {"error": "Pelo menos um campo ('rules', 'is_enabled') é obrigatório"}), 400

    values.append(flag_name)  # Adiciona o 'flag_name' para a cláusula WHERE

    query = f"UPDATE targeting_rules SET {', '.join(fields)} WHERE flag_name = %s RETURNING *"

    conn = None
    cur = None
    try:
        conn = pool.getconn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(query, tuple(values))

        if cur.rowcount == 0:
            return jsonify({"error": "Regra não encontrada"}), 404

        updated_rule = cur.fetchone()
        conn.commit()
        log.info(f"Regra para '{flag_name}' atualizada com sucesso.")
        return jsonify(updated_rule), 200
    except Exception as e:
        if conn:
            conn.rollback()
        log.error(f"Erro ao atualizar regra '{flag_name}': {e}")
        return jsonify(
            {"error": "Erro interno do servidor", "details": str(e)}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            pool.putconn(conn)


@app.route('/rules/<string:flag_name>', methods=['DELETE'])
@require_auth
def delete_rule(flag_name):
    """ Deleta a regra de segmentação de uma flag """
    conn = None
    cur = None
    try:
        conn = pool.getconn()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM targeting_rules WHERE flag_name = %s", (flag_name,))

        if cur.rowcount == 0:
            return jsonify({"error": "Regra não encontrada"}), 404

        conn.commit()
        log.info(f"Regra para '{flag_name}' deletada com sucesso.")
        return "", 204  # 204 No Content
    except Exception as e:
        if conn:
            conn.rollback()
        log.error(f"Erro ao deletar regra '{flag_name}': {e}")
        return jsonify(
            {"error": "Erro interno do servidor", "details": str(e)}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            pool.putconn(conn)


if __name__ == '__main__':
    port = int(os.getenv("PORT", 8003))
    app.run(host='0.0.0.0', port=port, debug=False)
