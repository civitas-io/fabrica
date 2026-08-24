"""Real end-to-end test: RestPresidiumClient against an actual, running
civitas-io/presidium M7 server -- real mTLS, real GovernedRuntime, real
HTTPGateway. Not mocks. Closes the loop this session's whole mTLS
investigation was working toward: Presidium's own real server, Fabrica's
own real client, proven to actually talk to each other.

Skipped entirely if presidium/presidium-contrib/cryptography aren't
installed (the `dev` extra) -- these are real, live PyPI packages
(civitas-io/presidium v0.2.1+/v0.2.0+), not vendored or hand-rolled here.

Certificate-generation helpers mirror civitas-io/presidium's own
packages/presidium-contrib/tests/integration/test_presidium_server_mtls.py
exactly (same two real, hard-won gotchas: SubjectKeyIdentifier/
AuthorityKeyIdentifier pair, and a KeyUsage extension on the CA) -- proven
there this session, reapplied here, not rediscovered.
"""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
from collections.abc import AsyncGenerator
from types import SimpleNamespace

import pytest

pytest.importorskip("presidium")
pytest.importorskip("presidium_contrib")
pytest.importorskip("cryptography")

from civitas import Runtime, Supervisor  # noqa: E402
from civitas.config import Settings  # noqa: E402
from civitas.gateway import HTTPGateway  # noqa: E402
from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402
from presidium.model import (  # noqa: E402
    AgentRecord,
    EvaluationStage,
    Grant,
    PolicyDecision,
    PolicyRule,
)
from presidium.policy.cel import CelPolicyEngine  # noqa: E402
from presidium.registry.memory import InMemoryRegistry  # noqa: E402
from presidium.runtime import GovernedRuntime  # noqa: E402
from presidium_contrib.server import (  # noqa: E402
    HealthCheckAgent,
    PresidiumGatewayAgent,
    build_check_grant_gateway_config,
)

from fabrica.presidium.rest_client import RestPresidiumClient  # noqa: E402
from fabrica.scope import Scope  # noqa: E402

_PORT = 19663
_BASE_URL = f"https://127.0.0.1:{_PORT}"

ALLOW_CODE_MODE = PolicyRule(
    name="allow-code-mode",
    stage=EvaluationStage.PRE_TOOL,
    expression="""
        agent.grants.exists(g,
            request.resource in g.resources &&
            request.action in g.actions
        )
    """,
    decision=PolicyDecision.ALLOW,
    priority=100,
)
DENY_NO_GRANT = PolicyRule(
    name="deny-no-grant",
    stage=EvaluationStage.PRE_TOOL,
    expression="true",
    decision=PolicyDecision.DENY,
    reason="No matching grant",
    priority=0,
)

# ---------------------------------------------------------------------------
# Certificate helpers -- mirrors civitas-io/presidium's own real mTLS test
# fixture exactly (proven this session, not rediscovered here).
# ---------------------------------------------------------------------------


def _rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _key_pem(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )


def _make_ca(common_name: str) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = _rsa_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(ski, critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _make_leaf(
    ca_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
    common_name: str,
    *,
    san: str | None = None,
) -> tuple[bytes, bytes]:
    key = _rsa_key()
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Acme"),
        ]
    )
    now = datetime.datetime.now(datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
    )
    if san is not None:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(san), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
    aki = x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key())
    builder = builder.add_extension(aki, critical=False)
    cert = builder.sign(ca_key, hashes.SHA256())
    return _key_pem(key), cert.public_bytes(serialization.Encoding.PEM)


def _dn(pem: bytes) -> str:
    return x509.load_pem_x509_certificate(pem).subject.rfc4514_string()


@pytest.fixture(scope="module")
def tls_certs(tmp_path_factory: pytest.TempPathFactory) -> SimpleNamespace:
    ca_key, ca_cert = _make_ca("Fabrica<->Presidium Test CA")
    ca_pem = ca_cert.public_bytes(serialization.Encoding.PEM)
    server_key, server_cert = _make_leaf(ca_key, ca_cert, "localhost", san="localhost")
    client_key, client_cert = _make_leaf(ca_key, ca_cert, "fabrica-client")

    directory = tmp_path_factory.mktemp("fabrica-presidium-mtls")

    def _write(name: str, data: bytes) -> str:
        path = directory / name
        path.write_bytes(data)
        return str(path)

    return SimpleNamespace(
        ca_path=_write("ca.pem", ca_pem),
        server_cert_path=_write("server.pem", server_cert),
        server_key_path=_write("server.key", server_key),
        client_cert_path=_write("client.pem", client_cert),
        client_key_path=_write("client.key", client_key),
        client_dn=_dn(client_cert),
    )


async def _wait_for_port_open(host: str, port: int, timeout_seconds: float = 5.0) -> None:
    async with asyncio.timeout(timeout_seconds):
        while True:
            try:
                _, writer = await asyncio.open_connection(host, port)
                writer.close()
                await writer.wait_closed()
                return
            except OSError:
                await asyncio.sleep(0.02)


@pytest.fixture
async def _running_presidium_server(
    monkeypatch: pytest.MonkeyPatch, tls_certs: SimpleNamespace
) -> AsyncGenerator[None]:
    monkeypatch.setattr(
        "civitas.gateway.mtls.settings",
        Settings(env={"CIVITAS_GATEWAY_MTLS_ALLOWED_DNS": tls_certs.client_dn}),
    )

    registry = InMemoryRegistry()
    await registry.register(
        AgentRecord(
            agent_id="presidium://acme.com/researcher",
            name="researcher",
            public_key="",
            grants=[Grant(resources=["code_mode"], actions=["invoke"], id="g1")],
        )
    )
    engine = CelPolicyEngine()
    engine.load_policies([ALLOW_CODE_MODE, DENY_NO_GRANT])
    runtime = GovernedRuntime(registry=registry, engine=engine)

    gateway_config = build_check_grant_gateway_config(
        port=_PORT,
        require_mtls=True,
        tls_cert=tls_certs.server_cert_path,
        tls_key=tls_certs.server_key_path,
        tls_ca_cert=tls_certs.ca_path,
    )
    gateway = HTTPGateway("api", config=gateway_config)
    gateway_agent = PresidiumGatewayAgent(runtime=runtime)
    health_agent = HealthCheckAgent()

    supervisor = Supervisor("root", children=[gateway, gateway_agent, health_agent])
    civitas_runtime = Runtime(supervisor=supervisor)
    await civitas_runtime.start()
    try:
        try:
            await _wait_for_port_open("127.0.0.1", _PORT)
        except (OSError, TimeoutError):
            pass
        await asyncio.sleep(0.05)
        yield
    finally:
        await civitas_runtime.stop()


class TestRestPresidiumClientAgainstRealServer:
    """The real payoff: fabrica.presidium.rest_client.RestPresidiumClient
    talking real REST+mTLS to civitas-io/presidium's own real, shipped M7
    server -- not a fake double, not a mock transport."""

    async def test_allow_over_real_mtls(
        self, _running_presidium_server: None, tls_certs: SimpleNamespace
    ) -> None:
        client = RestPresidiumClient.from_endpoint(
            _BASE_URL,
            client_cert=tls_certs.client_cert_path,
            client_key=tls_certs.client_key_path,
            ca_cert=tls_certs.ca_path,
        )
        try:
            result = await client.check_grant(
                agent_id="presidium://acme.com/researcher",
                action="code_mode",
                scope=Scope(user_id="u1"),
            )
            assert result.decision == "allow"
        finally:
            await client.close()

    async def test_deny_over_real_mtls(
        self, _running_presidium_server: None, tls_certs: SimpleNamespace
    ) -> None:
        client = RestPresidiumClient.from_endpoint(
            _BASE_URL,
            client_cert=tls_certs.client_cert_path,
            client_key=tls_certs.client_key_path,
            ca_cert=tls_certs.ca_path,
        )
        try:
            result = await client.check_grant(
                agent_id="presidium://acme.com/researcher",
                action="skill_run:pdf-extract",
                scope=Scope(),
            )
            assert result.decision == "deny"
            assert result.reason == "No matching grant"
        finally:
            await client.close()

    async def test_unresolvable_agent_denies_not_raises_over_real_mtls(
        self, _running_presidium_server: None, tls_certs: SimpleNamespace
    ) -> None:
        client = RestPresidiumClient.from_endpoint(
            _BASE_URL,
            client_cert=tls_certs.client_cert_path,
            client_key=tls_certs.client_key_path,
            ca_cert=tls_certs.ca_path,
        )
        try:
            result = await client.check_grant(
                agent_id="presidium://acme.com/ghost", action="code_mode", scope=Scope()
            )
            assert result.decision == "deny"
        finally:
            await client.close()

    async def test_no_client_cert_denies_not_raises(
        self, _running_presidium_server: None, tls_certs: SimpleNamespace
    ) -> None:
        """The real fail-closed proof: from_endpoint() with no client_cert
        at all against a client_cert_mode="required" server -- the TLS
        handshake itself fails, and check_grant() must still return a
        plain deny, never propagate the underlying SSL error."""
        client = RestPresidiumClient.from_endpoint(_BASE_URL, ca_cert=tls_certs.ca_path)
        try:
            result = await client.check_grant(agent_id="a", action="code_mode", scope=Scope())
            assert result.decision == "deny"
        finally:
            await client.close()
